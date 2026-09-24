"""Image-space registration and selective compositing of Qwen edits."""

import json
import logging

import numpy as np
from scipy import ndimage
import torch
import torch.nn.functional as F


def _resize_to(img, h, w):
    if img.shape[1:3] == (h, w):
        return img
    return F.interpolate(img.movedim(-1, 1), size=(h, w), mode="bicubic",
                         align_corners=False, antialias=True).clamp(0, 1).movedim(1, -1)


def _images(source, edited):
    for name, image in (("source", source), ("edited", edited)):
        if image.ndim != 4 or image.shape[-1] not in (3, 4) or min(image.shape) < 1:
            raise ValueError(f"{name} must be a nonempty RGB or RGBA IMAGE batch.")
    count = max(len(source), len(edited))
    if len(source) not in (1, count) or len(edited) not in (1, count):
        raise ValueError("Source and edited batches must match, or one must contain a single image.")
    source = source.detach().cpu().float()
    edited = _resize_to(edited.detach().cpu().float(), *source.shape[1:3])
    source = source.expand(count, -1, -1, -1)
    edited = edited.expand(count, -1, -1, -1)
    if edited.shape[-1] < source.shape[-1]:
        edited = torch.cat((edited, source[..., 3:]), -1)
    return source, edited[..., :source.shape[-1]]


def _mask(mask, count, h, w):
    if mask is None:
        return None
    if mask.ndim == 2:
        mask = mask[None]
    if mask.ndim != 3 or len(mask) not in (1, count):
        raise ValueError("MASK batch must contain one mask or match the image batch.")
    mask = mask.detach().cpu().float().clamp(0, 1)[:, None]
    if mask.shape[-2:] != (h, w):
        mask = F.interpolate(mask, size=(h, w), mode="bilinear", align_corners=False)
    return mask.expand(count, -1, -1, -1)


def _coords(h, w, A, t):
    y, x = torch.meshgrid(torch.arange(h, dtype=torch.float32),
                          torch.arange(w, dtype=torch.float32), indexing="ij")
    X, Y = x - (w - 1) / 2, y - (h - 1) / 2
    xp = A[0, 0] * X + A[0, 1] * Y + t[0] + (w - 1) / 2
    yp = A[1, 0] * X + A[1, 1] * Y + t[1] + (h - 1) / 2
    return xp, yp


def _warp(img, A, t):
    """Sample edited pixels at A @ (source_xy - centre) + centre + t."""
    h, w = img.shape[-2:]
    xp, yp = _coords(h, w, A, t)
    grid = torch.stack(((2 * xp + 1) / w - 1, (2 * yp + 1) / h - 1), -1)[None]
    valid = ((xp >= 0) & (xp <= w - 1) & (yp >= 0) & (yp <= h - 1))[None, None]
    if torch.equal(A, torch.eye(2)) and torch.equal(t, torch.zeros(2)):
        return img, valid
    return F.grid_sample(img, grid, mode="bicubic", padding_mode="border", align_corners=False), valid


def _gray(image):
    return (image[..., :3] * torch.tensor([0.299, 0.587, 0.114])).sum(-1)[:, None]


def _phase_correlation(a, b, weight):
    h, w = a.shape[-2:]
    win = torch.hann_window(h, periodic=False)[:, None] * torch.hann_window(w, periodic=False)[None]
    win = win * weight[0, 0]
    mean_a = (a[0, 0] * win).sum() / win.sum().clamp_min(1e-8)
    mean_b = (b[0, 0] * win).sum() / win.sum().clamp_min(1e-8)
    fa = torch.fft.rfft2((a[0, 0] - mean_a) * win)
    fb = torch.fft.rfft2((b[0, 0] - mean_b) * win)
    cross = fb * fa.conj()
    corr = torch.fft.irfft2(cross / cross.abs().clamp_min(1e-9), s=(h, w))
    py, px = divmod(int(corr.argmax()), w)

    def offset(left, centre, right):
        denom = float(left - 2 * centre + right)
        return 0.0 if abs(denom) < 1e-12 else float((left - right) / (2 * denom))

    sx = px + offset(corr[py, (px - 1) % w], corr[py, px], corr[py, (px + 1) % w])
    sy = py + offset(corr[(py - 1) % h, px], corr[py, px], corr[(py + 1) % h, px])
    return torch.tensor([sx - w if sx > w / 2 else sx, sy - h if sy > h / 2 else sy])


def estimate_warp(source, edited, mask=None, mode="translation", ignore_border=8, iters=40):
    """Robust image registration; positive t means the edit drifted right/down."""
    h, w = source.shape[1:3]
    S, E = _gray(source), _gray(edited)
    weight = torch.ones_like(S)
    if mask is not None:
        weight *= 1 - F.max_pool2d(mask, 17, 1, 8)
    border = min(max(0, int(ignore_border)), max(0, (min(h, w) - 8) // 2))
    if border:
        weight[..., :border, :] = 0
        weight[..., -border:, :] = 0
        weight[..., :, :border] = 0
        weight[..., :, -border:] = 0
    A, t = torch.eye(2), torch.zeros(2)
    usable = weight > 0.5
    if int(usable.sum()) < 8 or min(h, w) < 3:
        logging.warning("[Qwen Edit Align] No usable background for registration; using zero drift.")
        return A, t
    if float(S[usable].std()) < 1e-5 or float(E[usable].std()) < 1e-5:
        logging.warning("[Qwen Edit Align] Background has no texture; using zero drift.")
        return A, t
    if torch.equal(S, E):
        return A, t

    sizes = []
    for edge in (256, 512, 1024):
        scale = min(1.0, edge / max(h, w))
        size = (max(3, round(h * scale)), max(3, round(w * scale)))
        if size not in sizes:
            sizes.append(size)
    for level, (hh, ww) in enumerate(sizes):
        s, e, weight_l = (F.interpolate(x, size=(hh, ww), mode="bilinear", align_corners=False,
                                       antialias=True) for x in (S, E, weight))
        usable_l = weight_l[0, 0] > 0.999
        if int(usable_l.sum()) < 8:
            continue
        D = torch.diag(torch.tensor([ww / w, hh / h]))
        inv_D = torch.diag(1 / D.diag())
        Al, tl = D @ A @ inv_D, D @ t
        if level == 0:
            candidate = _phase_correlation(s, e, weight_l)
            # Mask edges can create a false FFT peak. Accept it only if it improves
            # the robust background error over zero shift with sufficient overlap.
            shifted, overlap = _warp(e, Al, candidate)
            support = usable_l & overlap[0, 0]
            if int(support.sum()) >= 0.75 * int(usable_l.sum()):
                before = (e - s)[0, 0][support]
                after = (shifted - s)[0, 0][support]
                if (after - after.median()).abs().median() < (before - before.median()).abs().median():
                    tl = candidate
        # Derivatives in the edited coordinate system, then sampled at the current warp.
        gx = F.pad((e[..., 2:] - e[..., :-2]) * 0.5, (1, 1, 0, 0))
        gy = F.pad((e[..., 2:, :] - e[..., :-2, :]) * 0.5, (0, 0, 1, 1))
        fields = torch.cat((e, gx, gy), 1)
        Y, X = torch.meshgrid(torch.arange(hh), torch.arange(ww), indexing="ij")
        X, Y = X.float() - (ww - 1) / 2, Y.float() - (hh - 1) / 2
        npar = 2 if mode == "translation" else 6
        for _ in range(iters):
            warped, valid = _warp(fields, Al, tl)
            keep = usable_l & valid[0, 0]
            if int(keep.sum()) < max(8, npar * 2):
                break
            r = warped[0, 0] - s[0, 0]
            r = r - r[keep].median()  # Ignore a global brightness offset during fitting only.
            sigma = 2.5 * 1.4826 * r[keep].abs().median().clamp_min(1e-4)
            weights = 1 / (1 + (r[keep] / sigma) ** 2)
            dx, dy = warped[0, 1], warped[0, 2]
            if npar == 2:
                J = torch.stack((dx[keep], dy[keep]), -1)
            else:
                J = torch.stack((dx * X, dx * Y, dx, dy * X, dy * Y, dy), -1)[keep]
            J = J.double()
            weights = weights[:, None].double()
            H = J.T @ (J * weights) + 1e-6 * torch.eye(npar, dtype=torch.float64)
            rhs = J.T @ (weights * r[keep, None].double())
            dp = -torch.linalg.solve(H, rhs).flatten().float()
            if not bool(torch.isfinite(dp).all()):
                break
            if npar == 2:
                movement = float(dp.abs().max())
                tl += dp * min(1.0, 2 / max(movement, 1e-8))
            else:
                da = torch.stack((dp[:2], dp[3:5]))
                dt = dp[[2, 5]]
                movement = float((da.abs() @ torch.tensor([ww / 2, hh / 2]) + dt.abs()).max())
                step = min(1.0, 2 / max(movement, 1e-8))
                Al += step * da
                tl += step * dt
            if movement < 1e-3:
                break
        A, t = inv_D @ Al @ D, inv_D @ tl
    return A, t


def _params(A, t, h, w):
    return {"A": A.tolist(), "t": t.tolist(), "h": h, "w": w}


def _read_params(text, count, h, w):
    data = json.loads(text)
    entries = data.get("warps", [data])
    if len(entries) not in (1, count):
        raise ValueError("warp_params must contain one transform or one per image.")
    result = []
    for p in entries:
        A, t = torch.tensor(p["A"], dtype=torch.float32), torch.tensor(p["t"], dtype=torch.float32)
        if A.shape != (2, 2) or t.shape != (2,) or not bool(torch.isfinite(A).all() & torch.isfinite(t).all()):
            raise ValueError("warp_params requires finite A (2x2) and t (2).")
        if p.get("h", h) <= 0 or p.get("w", w) <= 0:
            raise ValueError("warp_params dimensions must be positive.")
        scale = torch.tensor([w / p.get("w", w), h / p.get("h", h)])
        result.append((A * scale[:, None] / scale[None, :], t * scale))
    return result * count if len(result) == 1 else result


def _blur(x, radius):
    if radius <= 0:
        return x
    k = torch.arange(-radius, radius + 1, dtype=torch.float32)
    k = torch.exp(-0.5 * (k / (radius / 2)) ** 2)
    k /= k.sum()
    channels = x.shape[1]
    x = F.conv2d(F.pad(x, (radius, radius, 0, 0), mode="replicate"),
                 k.view(1, 1, 1, -1).expand(channels, 1, 1, -1), groups=channels)
    return F.conv2d(F.pad(x, (0, 0, radius, radius), mode="replicate"),
                    k.view(1, 1, -1, 1).expand(channels, 1, -1, 1), groups=channels)


def _finish_mask(mask, grow, feather, valid, limit=None):
    if grow:
        radius = abs(grow)
        # Separable square morphology avoids quadratic work for large radii.
        sign = 1 if grow > 0 else -1
        mask = sign * mask
        mask = F.max_pool2d(mask, (1, 2 * radius + 1), 1, (0, radius))
        mask = sign * F.max_pool2d(mask, (2 * radius + 1, 1), 1, (radius, 0))
    mask = _blur(mask, feather).clamp(0, 1)
    # Apply limits after feathering so unsupported borders and region limits never leak.
    mask = mask * valid
    if limit is not None:
        mask = mask * limit
    return torch.where(mask < 1e-3, 0.0, mask)


def _composite(source, edited, mask):
    mask = mask.movedim(1, -1)
    return torch.where(mask > 0, source * (1 - mask) + edited * mask, source)


class QwenEditMeasureShift:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
                    "source": ("IMAGE",),
                    "edited": ("IMAGE",),
                    "mode": (["translation", "affine"],),
                    "ignore_border_px": ("INT", {"default": 8, "min": 0, "max": 256}),
                },
                "optional": {"edit_mask": ("MASK", {"tooltip": "Region that was meant to change (excluded from the fit)."})}}

    RETURN_TYPES = ("FLOAT", "FLOAT", "STRING")
    RETURN_NAMES = ("drift_x_px", "drift_y_px", "warp_params")
    FUNCTION = "measure"
    CATEGORY = "WepeNerd/Qwen Edit Align"

    DESCRIPTION = "Measure one image pair. Positive drift means right/down, in source pixels."

    def measure(self, source, edited, mode, ignore_border_px, edit_mask=None):
        if len(source) != 1 or len(edited) != 1:
            raise ValueError("Measure Drift reports one image pair. Select one image from each batch; Align Composite handles batches directly.")
        src, ed = _images(source, edited)
        h, w = src.shape[1:3]
        mask = _mask(edit_mask, 1, h, w)
        A, t = estimate_warp(src, ed, mask, mode, ignore_border_px)
        return float(t[0]), float(t[1]), json.dumps(_params(A, t, h, w))


class QwenEditAlignComposite:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
                    "source": ("IMAGE",),
                    "edited": ("IMAGE",),
                    "mode": (["translation", "affine"],),
                    "auto_mask_threshold": ("FLOAT", {"default": 0.06, "min": 0.0, "max": 1.0, "step": 0.005,
                                                      "tooltip": "Used when no mask is given: per-pixel change that counts as 'edited'."}),
                    "grow_px": ("INT", {"default": 12, "min": 0, "max": 256}),
                    "feather_px": ("INT", {"default": 8, "min": 0, "max": 256}),
                },
                "optional": {
                    "edit_mask": ("MASK",),
                    "warp_params": ("STRING", {"forceInput": True, "tooltip": "From QwenEditMeasureShift; re-measured if omitted."}),
                }}

    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("composite", "aligned_edit", "mask", "warp_params")
    FUNCTION = "run"
    CATEGORY = "WepeNerd/Qwen Edit Align"

    DESCRIPTION = "Align the edit to the source, then blend only the masked region. White selects the edit."

    def run(self, source, edited, mode, auto_mask_threshold, grow_px, feather_px, edit_mask=None, warp_params=None):
        src, ed = _images(source, edited)
        count, h, w = src.shape[:3]
        masks = _mask(edit_mask, count, h, w)
        transforms = _read_params(warp_params, count, h, w) if warp_params else None
        composites, aligned_images, output_masks, reports = [], [], [], []
        for i in range(count):
            s, e = src[i:i + 1], ed[i:i + 1]
            m = None if masks is None else masks[i:i + 1]
            A, t = transforms[i] if transforms else estimate_warp(s, e, m, mode)
            aligned, valid = _warp(e.movedim(-1, 1), A, t)
            aligned = aligned.clamp(0, 1).movedim(1, -1)
            if m is None:
                diff = (aligned - s).abs().amax(-1)[:, None]
                diff = F.avg_pool2d(diff, 5, 1, 2, count_include_pad=False)
                m = (diff > auto_mask_threshold).float()
            m = _finish_mask(m, grow_px, feather_px, valid)
            composites.append(_composite(s, aligned, m))
            aligned_images.append(aligned)
            output_masks.append(m[:, 0])
            reports.append(_params(A, t, h, w))
        report = reports[0] if count == 1 else {"warps": reports}
        return torch.cat(composites), torch.cat(aligned_images), torch.cat(output_masks), json.dumps(report)


class QwenEditDiffMask:
    """Masks where `edited` differs from `source` and pastes only that part onto the original.
    Pixels where the final mask is 0 are the original source pixels, bit for bit."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
                    "source": ("IMAGE",),
                    "edited": ("IMAGE",),
                    "threshold": ("FLOAT", {"default": 0.08, "min": 0.0, "max": 1.0, "step": 0.005,
                                            "tooltip": "How different a pixel must be to count as edited. Lower = bigger mask."}),
                    "align_first": ("BOOLEAN", {"default": True,
                                                "tooltip": "Undo the Qwen drift before diffing. Without it a 3 px shift lights up every edge."}),
                    "diff_mode": (["color (max RGB)", "luminance", "chroma"],),
                    "pre_blur_px": ("INT", {"default": 2, "min": 0, "max": 32,
                                            "tooltip": "Blur both images before diffing to ignore VAE noise / fine texture changes."}),
                    "min_region_px": ("INT", {"default": 64, "min": 0, "max": 1000000,
                                              "tooltip": "Drop isolated blobs smaller than this many pixels."}),
                    "fill_holes": ("BOOLEAN", {"default": True}),
                    "grow_px": ("INT", {"default": 2, "min": -128, "max": 256, "tooltip": "Expand (or shrink, if negative) the mask."}),
                    "feather_px": ("INT", {"default": 2, "min": 0, "max": 256, "tooltip": "Soft edge for the blend."}),
                },
                "optional": {
                    "limit_mask": ("MASK", {"tooltip": "Optional: only allow changes inside this region."}),
                }}

    RETURN_TYPES = ("IMAGE", "MASK", "IMAGE", "IMAGE")
    RETURN_NAMES = ("composite", "mask", "preview", "difference")
    FUNCTION = "run"
    CATEGORY = "WepeNerd/Qwen Edit Align"

    DESCRIPTION = (
        "Detect additions and removals, then paste changed pixels onto the source. "
        "This detects visual differences, not object semantics. Preview the mask and "
        "use limit_mask when other parts of the image were also redrawn."
    )

    def run(self, source, edited, threshold, align_first, diff_mode, pre_blur_px, min_region_px,
            fill_holes, grow_px, feather_px, limit_mask=None):
        src, ed = _images(source, edited)
        count, h, w = src.shape[:3]
        limits = _mask(limit_mask, count, h, w)
        composites, masks, previews, differences = [], [], [], []
        for i in range(count):
            s, e = src[i:i + 1], ed[i:i + 1]
            limit = None if limits is None else limits[i:i + 1]
            A, t = estimate_warp(s, e, limit) if align_first else (torch.eye(2), torch.zeros(2))
            aligned, valid = _warp(e.movedim(-1, 1), A, t)
            e = aligned.clamp(0, 1).movedim(1, -1)
            a = _blur(s[..., :3].movedim(-1, 1), pre_blur_px)
            b = _blur(e[..., :3].movedim(-1, 1), pre_blur_px)
            weights = torch.tensor([0.299, 0.587, 0.114]).view(1, 3, 1, 1)
            if diff_mode == "luminance":
                diff = ((a - b) * weights).sum(1, keepdim=True).abs()
            elif diff_mode == "chroma":
                ca = a - (a * weights).sum(1, keepdim=True)
                cb = b - (b * weights).sum(1, keepdim=True)
                diff = (ca - cb).abs().amax(1, keepdim=True)
            else:
                diff = (a - b).abs().amax(1, keepdim=True)
            if s.shape[-1] == 4:
                alpha_diff = _blur((s[..., 3:] - e[..., 3:]).movedim(-1, 1), pre_blur_px).abs()
                diff = torch.maximum(diff, alpha_diff)
            binary = ((diff > threshold) & valid)
            if limit is not None:
                binary &= limit > 0
            mb = binary[0, 0].numpy()
            if min_region_px > 0:
                labels, _ = ndimage.label(mb, structure=np.ones((3, 3)))
                keep = np.bincount(labels.ravel()) >= min_region_px
                keep[0] = False
                mb = keep[labels]
            if fill_holes:
                mb = ndimage.binary_fill_holes(mb)
            m = torch.from_numpy(mb.astype(np.float32))[None, None]
            m = _finish_mask(m, grow_px, feather_px, valid, limit)
            composites.append(_composite(s, e, m))
            masks.append(m[:, 0])
            mm = m.movedim(1, -1) * 0.6
            previews.append(s[..., :3] * (1 - mm) + torch.tensor([1.0, 0.1, 0.1]) * mm)
            differences.append((diff / max(threshold, 1e-3) * 0.5).clamp(0, 1)
                               .movedim(1, -1).expand(-1, -1, -1, 3).contiguous())
        return torch.cat(composites), torch.cat(masks), torch.cat(previews), torch.cat(differences)


NODE_CLASS_MAPPINGS = {
    "QwenEditMeasureShift": QwenEditMeasureShift,
    "QwenEditAlignComposite": QwenEditAlignComposite,
    "QwenEditDiffMask": QwenEditDiffMask,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "QwenEditMeasureShift": "Qwen Edit Measure Drift",
    "QwenEditAlignComposite": "Qwen Edit Align Composite",
    "QwenEditDiffMask": "Qwen Edit Difference Mask + Composite",
}
