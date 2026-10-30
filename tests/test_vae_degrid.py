"""VAE DeGrid: lattice removal, detail preservation, clean pass-through and node outputs."""

import math
import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vae_degrid_core as core  # noqa: E402
import vae_degrid_node as node  # noqa: E402


def scene(h=192, w=256, seed=0):
    """A clean test image with gradients, hard edges, a disc and fine texture
    (noise plus a weave just below the 2px frequency). [1, H, W, 3] in 0..1."""
    g = torch.Generator().manual_seed(seed)
    yy, xx = torch.meshgrid(torch.arange(h, dtype=torch.float32),
                            torch.arange(w, dtype=torch.float32), indexing="ij")
    base = 0.25 + 0.4 * xx / w + 0.2 * (xx > w * 0.55).float()
    base = base - 0.25 * (((yy - h / 2) ** 2 + (xx - w / 3) ** 2) < (h / 4) ** 2).float()
    weave = 0.05 * torch.sin(2 * math.pi * xx / 2.4) * torch.sin(2 * math.pi * yy / 2.7)
    weave = weave * (yy > h * 0.6).float()
    noise = 0.02 * torch.randn(h, w, generator=g)
    y = (base + weave + noise).clamp(0.02, 0.98)
    return torch.stack([y, 0.9 * y + 0.05, 0.8 * y + 0.1], -1)[None]


def lattice(img, amp=1.0 / 255, follow_content=False, phase=(0, 0)):
    """'One dark pixel per 2x2 block' lattice, the shape VAE grids take."""
    _, h, w, _ = img.shape
    sx = torch.tensor([1.0 - 2 * ((i + phase[1]) % 2) for i in range(w)]).view(1, 1, w, 1)
    sy = torch.tensor([1.0 - 2 * ((i + phase[0]) % 2) for i in range(h)]).view(1, h, 1, 1)
    m = (0.3 + 1.4 * img.mean(-1, keepdim=True)) if follow_content else 1.0
    return -amp * m * (sx + sy + sx * sy) * torch.tensor([1.0, 0.9, 1.1])


def psnr(a, b):
    return 10 * math.log10(1.0 / ((a.float() - b.float()) ** 2).mean().clamp_min(1e-12).item())


class LatticeRemovalTest(unittest.TestCase):
    def test_removes_uniform_lattice(self):
        clean = scene()
        decoded = clean + lattice(clean)
        out, removed, _, stats = core.degrid(decoded)
        self.assertTrue(stats["detected"])
        self.assertGreater(psnr(out, clean) - psnr(decoded, clean), 15.0)
        self.assertAlmostEqual(stats["batch_255"], 2 * math.sqrt(3) * 1.04, delta=0.6)

    def test_follows_a_lattice_that_tracks_the_content(self):
        clean = scene(seed=1)
        decoded = clean + lattice(clean, amp=1.2 / 255, follow_content=True)
        out = core.degrid(decoded)[0]
        self.assertGreater(psnr(out, clean) - psnr(decoded, clean), 12.0)

    def test_any_lattice_phase_and_odd_sizes(self):
        clean = scene(h=131, w=197, seed=2)
        for phase in [(0, 1), (1, 0), (1, 1)]:
            decoded = clean + lattice(clean, phase=phase)
            out = core.degrid(decoded)[0]
            self.assertGreater(psnr(out, clean) - psnr(decoded, clean), 12.0, phase)

    def test_spares_fine_detail_better_than_a_notch(self):
        # With nothing to remove, forced processing must barely touch real texture.
        clean = scene(seed=3)
        out = core.degrid(clean, skip_when_clean=False)[0]
        self.assertGreater(psnr(out, clean), 55.0)
        self.assertLess((out - clean).abs().mean().item() * 255, 0.25)


class PassThroughTest(unittest.TestCase):
    def test_clean_batch_is_returned_untouched(self):
        clean = scene(seed=4)
        out, removed, _, stats = core.degrid(clean)
        self.assertFalse(stats["detected"])
        self.assertTrue(stats["skipped"])
        self.assertIs(out, clean)
        self.assertEqual(removed.abs().max().item(), 0.0)
        self.assertLess(stats["batch_255"], core.NEGLIGIBLE * 255)

    def test_resized_image_reads_as_clean(self):
        clean = scene(h=384, w=512, seed=5)
        decoded = clean + lattice(clean)
        half = torch.nn.functional.interpolate(decoded.permute(0, 3, 1, 2), scale_factor=0.5,
                                               mode="bilinear", antialias=True).permute(0, 2, 3, 1)
        self.assertFalse(core.degrid(half)[3]["detected"])

    def test_strength_zero_is_identity(self):
        decoded = scene() + lattice(scene())
        out, removed, _, stats = core.degrid(decoded, strength=0.0)
        self.assertIs(out, decoded)
        self.assertTrue(stats["detected"])
        self.assertEqual(removed.abs().max().item(), 0.0)

    def test_tiny_images_pass_through(self):
        img = torch.rand(2, 3, 3, 3)
        out, _, _, stats = core.degrid(img)
        self.assertIs(out, img)
        self.assertFalse(stats["detected"])

    def test_batch_decision_is_shared_by_all_frames(self):
        clean = scene(seed=6)
        batch = torch.cat([clean + lattice(clean, amp=1.5 / 255), clean + lattice(clean, amp=0.05 / 255)])
        out, removed, _, stats = core.degrid(batch)
        self.assertTrue(stats["detected"])
        self.assertEqual(len(stats["per_image_255"]), 2)
        self.assertGreater(removed[1].abs().max().item(), 0.0)
        self.assertGreater(psnr(out[0], clean[0]), psnr(batch[0], clean[0]) + 12)


class FormatTest(unittest.TestCase):
    def test_keeps_shape_dtype_and_alpha(self):
        clean = scene(h=96, w=128)
        rgba = torch.cat([clean + lattice(clean), torch.rand(1, 96, 128, 1)], -1).clamp(0, 1).half()
        out = core.degrid(rgba)[0]
        self.assertEqual(out.shape, rgba.shape)
        self.assertEqual(out.dtype, torch.float16)
        self.assertTrue(torch.equal(out[..., 3], rgba[..., 3]))
        self.assertGreater(psnr(out[..., :3], clean), psnr(rgba[..., :3], clean) + 10)

    def test_grayscale(self):
        clean = scene()[..., :1]
        decoded = clean + lattice(scene())[..., :1]
        out, removed, amp, _ = core.degrid(decoded)
        self.assertEqual(out.shape, decoded.shape)
        self.assertEqual(core.preview(removed, amp, "strength map").shape[-1], 3)

    def test_on_frame_called_once_per_frame(self):
        seen = []
        core.degrid(torch.rand(3, 16, 16, 3), on_frame=seen.append)
        self.assertEqual(seen, [0, 1, 2])


class NodeTest(unittest.TestCase):
    def test_outputs_and_status_for_every_preview(self):
        clean = scene(h=128, w=160)
        decoded = (clean + lattice(clean)).clamp(0, 1)
        for mode in core.PREVIEWS:
            result = node.WN_VAEDeGrid().degrid(decoded, preview=mode)
            image, shown = result["result"]
            self.assertEqual(image.shape, decoded.shape)
            self.assertEqual(shown.shape[0], 1)
            self.assertEqual(shown.shape[-1], 3)
            self.assertGreaterEqual(shown.min().item(), 0.0)
            self.assertLessEqual(shown.max().item(), 1.0)
            self.assertIn("removed", result["ui"]["text"][0])
        zoomed = node.WN_VAEDeGrid().degrid(decoded, preview="lattice 4x zoom")["result"][1]
        self.assertEqual(tuple(zoomed.shape[1:3]), (128, 160))

    def test_status_for_clean_input(self):
        text = node.WN_VAEDeGrid().degrid(scene())["ui"]["text"]
        self.assertEqual(len(text), 1)
        self.assertIn("no grid detected, passed through untouched", text[0])

    def test_inputs(self):
        spec = node.WN_VAEDeGrid.INPUT_TYPES()["required"]
        self.assertEqual(list(spec), ["image", "strength", "window", "skip_when_clean", "preview"])
        self.assertEqual(spec["strength"][1]["default"], 1.0)
        self.assertTrue(spec["skip_when_clean"][1]["default"])
        self.assertEqual(node.NODE_CLASS_MAPPINGS, {"WN_VAEDeGrid": node.WN_VAEDeGrid})


if __name__ == "__main__":
    unittest.main()
