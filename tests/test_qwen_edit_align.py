"""CPU regression tests for registration, mask containment and source preservation."""

import json
from pathlib import Path
import sys
import unittest

import numpy as np
from scipy import ndimage
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qwen_edit_align as q


def texture(h=127, w=191):
    rng = np.random.default_rng(42)
    pixels = ndimage.gaussian_filter(rng.random((h, w, 3)), (1.6, 1.6, 0))
    pixels = (pixels - pixels.min()) / (pixels.max() - pixels.min())
    return torch.from_numpy(pixels.astype(np.float32))[None]


def transformed(source, A, t):
    """Independent SciPy generator: move source content by A and t about its centre."""
    h, w = source.shape[1:3]
    inv = np.linalg.inv(np.asarray(A))
    centre = np.array([(w - 1) / 2, (h - 1) / 2])
    offset = centre - inv @ (centre + np.asarray(t))
    channels = [ndimage.affine_transform(source[0, ..., c].numpy(), inv[::-1, ::-1],
                                        offset[::-1], order=3, mode="nearest") for c in range(3)]
    return torch.from_numpy(np.stack(channels, -1))[None].clamp(0, 1)


def difference(source, edited, **kwargs):
    options = dict(threshold=0.08, align_first=False, diff_mode="color (max RGB)",
                   pre_blur_px=0, min_region_px=0, fill_holes=False, grow_px=0, feather_px=0)
    options.update(kwargs)
    return q.QwenEditDiffMask().run(source, edited, **options)


class QwenEditAlignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads = torch.get_num_threads()
        torch.set_num_threads(2)

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.threads)

    def test_translation_sign_and_subpixel_precision_with_object_edit(self):
        src = texture()
        for t in ([3, -2], [-2.4, 3.2]):
            with self.subTest(t=t):
                ed = transformed(src, np.eye(2), t)
                ed[:, 40:65, 70:100] = 1
                x, y, report = q.QwenEditMeasureShift().measure(src, ed, "translation", 8)
                np.testing.assert_allclose([x, y], t, atol=0.12)
                p = json.loads(report)
                self.assertEqual((p["h"], p["w"]), src.shape[1:3])

    def test_affine_fit_on_odd_rectangular_image(self):
        src = texture(259, 387)
        A = np.array([[1.015, 0.008], [-0.006, 0.985]])
        t = np.array([3.2, -1.8])
        ed = transformed(src, A, t)
        ed[:, 90:135, 150:200] = 0.95
        actual_A, actual_t = q.estimate_warp(src, ed, mode="affine")
        np.testing.assert_allclose(actual_A, A, atol=0.002)
        np.testing.assert_allclose(actual_t, t, atol=0.15)

    def test_alignment_with_brightness_offset_and_exclusion(self):
        src = texture()
        ed = transformed(src, np.eye(2), [3.2, -2.1]) * 0.92 + 0.035
        ed[:, 30:90, 70:130] = 0.95
        mask = torch.zeros(1, 1, 127, 191)
        mask[..., 25:95, 65:135] = 1
        _, t = q.estimate_warp(src, ed, mask)
        np.testing.assert_allclose(t, [3.2, -2.1], atol=0.15)

    def test_blank_fully_excluded_and_small_images_are_finite(self):
        for size in ((1, 1), (4, 9), (64, 80)):
            src = torch.full((1, *size, 3), 0.2)
            for mask in (None, torch.ones(1, 1, *size)):
                A, t = q.estimate_warp(src, src + 0.2, mask, ignore_border=256)
                self.assertTrue(torch.equal(A, torch.eye(2)))
                self.assertTrue(torch.equal(t, torch.zeros(2)))

    def test_measure_rejects_batches_instead_of_silently_dropping_images(self):
        src = texture()
        with self.assertRaisesRegex(ValueError, "one image pair"):
            q.QwenEditMeasureShift().measure(src.expand(2, -1, -1, -1), src, "translation", 8)

    def test_addition_and_removal_preserve_every_unmasked_pixel(self):
        src = texture(64, 80)
        src[:, 10:20, 10:20] = 1
        ed = src.clone()
        ed[:, 10:20, 10:20] = 0.1  # Removed object, replaced by edited background.
        ed[:, 35:45, 50:60] = 0.95  # Added object.
        comp, mask, preview, diff = difference(src, ed)
        self.assertTrue(bool((mask[:, 10:20, 10:20] == 1).all()))
        self.assertTrue(bool((mask[:, 35:45, 50:60] > 0).any()))
        self.assertTrue(torch.equal(comp[mask == 0], src[mask == 0]))
        self.assertTrue(torch.equal(comp[mask == 1], ed[mask == 1]))
        self.assertEqual(preview.shape, src.shape)
        self.assertEqual(diff.shape, src.shape)

    def test_identical_images_produce_empty_mask_and_exact_source(self):
        src = texture()
        comp, mask, _, _ = difference(src, src, align_first=True, pre_blur_px=2,
                                      min_region_px=64, fill_holes=True, grow_px=2, feather_px=2)
        self.assertEqual(float(mask.max()), 0)
        self.assertTrue(torch.equal(src, comp))

    def test_limit_mask_cannot_leak_after_growth_hole_fill_and_feather(self):
        src = torch.zeros(1, 64, 80, 3)
        ed = torch.ones_like(src)
        limit = torch.zeros(1, 64, 80)
        limit[:, 15:45, 20:60] = 1
        limit[:, 25:35, 30:40] = 0
        comp, mask, _, _ = difference(src, ed, limit_mask=limit, grow_px=10,
                                      feather_px=8, fill_holes=True)
        self.assertEqual(float(mask[limit == 0].max()), 0)
        self.assertTrue(torch.equal(comp[limit == 0], src[limit == 0]))

    def test_component_cleanup_and_hole_filling(self):
        src = torch.zeros(1, 64, 80, 3)
        ed = src.clone()
        ed[:, 10:30, 10:30] = 1
        ed[:, 15:20, 15:20] = 0
        ed[:, 50:52, 50:52] = 1
        _, mask, _, _ = difference(src, ed, min_region_px=10, fill_holes=True)
        self.assertEqual(float(mask[:, 15:20, 15:20].min()), 1)
        self.assertEqual(float(mask[:, 50:52, 50:52].max()), 0)

    def test_image_and_mask_batches_broadcast_without_truncation(self):
        src = torch.zeros(1, 40, 50, 3)
        ed = src.expand(2, -1, -1, -1).clone()
        ed[0, 5:15, 5:15] = 1
        ed[1, 20:30, 25:35] = 1
        comp, mask, _, _ = difference(src, ed, limit_mask=torch.ones(40, 50))
        self.assertEqual(len(comp), 2)
        self.assertTrue(torch.equal(comp, ed))
        self.assertFalse(torch.equal(mask[0], mask[1]))
        with self.assertRaisesRegex(ValueError, "batches must match"):
            difference(src.expand(3, -1, -1, -1), ed)

    def test_affine_parameters_scale_axes_independently(self):
        p = json.dumps({"A": [[1, 0.1], [0.2, 1]], "t": [3, 4], "h": 100, "w": 200})
        A, t = q._read_params(p, 1, 300, 400)[0]
        np.testing.assert_allclose(A, [[1, 0.1 * 2 / 3], [0.2 * 3 / 2, 1]], atol=1e-7)
        np.testing.assert_allclose(t, [6, 12])
        with self.assertRaisesRegex(ValueError, "finite"):
            q._read_params(json.dumps({"A": [[1, 0], [0, 1]], "t": [float("nan"), 0]}), 1, 10, 10)

    def test_aligner_preserves_invalid_borders_and_zero_mask_pixels(self):
        src = texture(64, 80)
        ed = torch.ones_like(src)
        params = json.dumps({"A": [[1, 0], [0, 1]], "t": [4, -3], "h": 64, "w": 80})
        comp, _, mask, _ = q.QwenEditAlignComposite().run(
            src, ed, "translation", 0.06, 4, 3, torch.ones(1, 64, 80), params)
        self.assertEqual(float(mask[:, :3].max()), 0)
        self.assertEqual(float(mask[:, :, -4:].max()), 0)
        self.assertTrue(torch.equal(comp[mask == 0], src[mask == 0]))

    def test_batch_transform_report_roundtrip(self):
        src = texture()
        ed = torch.cat([transformed(src, np.eye(2), t) for t in ([3, 0], [0, -2])])
        node = q.QwenEditAlignComposite()
        first = node.run(src, ed, "translation", 0.08, 0, 0)
        self.assertEqual(len(first[0]), 2)
        self.assertEqual(len(json.loads(first[3])["warps"]), 2)
        second = node.run(src, ed, "translation", 0.08, 0, 0, warp_params=first[3])
        self.assertTrue(torch.equal(first[0], second[0]))

    def test_alignment_reduces_false_edge_mask(self):
        src = texture()
        ed = transformed(src, np.eye(2), [3, -2])
        ed[:, 45:65, 80:100] = 1
        off = difference(src, ed)[1]
        on = difference(src, ed, align_first=True)[1]
        self.assertLess(float(on.sum()), float(off.sum()) * 0.25)
        self.assertGreater(float(on[:, 47:67, 77:97].mean()), 0.8)

    def test_metrics_small_images_and_mixed_channels(self):
        src = torch.zeros(1, 8, 9, 4)
        src[..., 3] = 0.7
        ed = torch.ones(1, 4, 5, 3)
        for mode in ("color (max RGB)", "luminance", "chroma"):
            comp, mask, _, _ = difference(src, ed, diff_mode=mode, pre_blur_px=2)
            self.assertEqual(comp.shape, src.shape)
            self.assertTrue(torch.equal(comp[..., 3], src[..., 3]))
        alpha_edit = src.clone()
        alpha_edit[..., 3] = 0
        self.assertEqual(float(difference(src, alpha_edit)[1].min()), 1)


if __name__ == "__main__":
    unittest.main()
