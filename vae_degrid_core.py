"""VAE DeGrid core: lock-in removal of the 2px VAE pixel grid.

Pure torch with no ComfyUI imports, so it can be tested on its own.

Stride-2 VAE decoders (Qwen Image, Qwen Image 2.1, Wan 2.1 and models built on
them, such as Krea2) leave a faint 2px lattice whose phase is pinned to the
pixel grid. Any 2-periodic pattern is a sum of three carriers:

    grid(y, x) = Av * (-1)^x  +  Ah * (-1)^y  +  Ac * (-1)^(x+y)
                 (V-stripes)     (H-stripes)     (checkerboard)

A notch filter (the usual fix) removes everything near those frequencies,
real fine texture included, and must be clamped to spare edges, so it both
softens texture and leaves some grid beside strong edges. Here each carrier is
demodulated instead (lock-in detection): multiplying by the carrier turns the
lattice into a slowly varying amplitude and real detail into fast-varying
noise. A robust, edge-aware local average measures the amplitudes at every
pixel, and only the measured lattice is subtracted. A guided fit (a local
linear model on a grid-free copy of the image) lets the amplitude change where
the content does, since the grid is usually stronger on bright, busy areas.
"""

import math

import torch
import torch.nn.functional as F

# Batch-level strength (≈ peak-to-peak, 0..1 scale) below which no grid is reported.
# Gridded decodes measure roughly 1.5-5/255; clean or resized images 0-0.5/255.
NEGLIGIBLE = 0.5 / 255.0
DEFAULT_WINDOW = 8.0
MIN_WINDOW, MAX_WINDOW = 3.0, 32.0
PREVIEWS = ("strength map", "lattice 4x zoom", "lattice 8x zoom", "lattice full frame")

_ROBUST_K = 2.0          # Cauchy weights: residuals past ~k robust sigmas count less
_ROBUST_ITERS = 2
_SCALE_FLOOR = 0.25 / 255.0
_GUIDE_EPS = 3e-3        # guided fit regulariser (variance units of the 0..1 image)
_MEDIAN_SAMPLES = 250_000


def _gauss_kernel(sigma, dtype, device):
    r = max(1, int(math.ceil(3.0 * sigma)))
    t = torch.arange(-r, r + 1, dtype=torch.float64)
    k = torch.exp(-0.5 * (t / sigma) ** 2)
    return (k / k.sum()).to(dtype=dtype, device=device), r


def _blur(x, sigma):
    """Separable Gaussian with zero padding (callers normalise). x: [N, C, H, W]."""
    k, r = _gauss_kernel(sigma, x.dtype, x.device)
    c = x.shape[1]
    x = F.conv2d(F.pad(x, (r, r, 0, 0)), k.view(1, 1, 1, -1).expand(c, 1, 1, -1), groups=c)
    return F.conv2d(F.pad(x, (0, 0, r, r)), k.view(1, 1, -1, 1).expand(c, 1, -1, 1), groups=c)


class _Smoother:
    """Gaussian local means on a decimated grid (fast guided filter style).

    Full-resolution products are sum-pooled by ``step``, blurred at low
    resolution, and only the resulting smooth fields are brought back to full
    size. The fields are smooth at the scale of the window, so this matches a
    full-resolution blur closely at a fraction of the cost.
    """

    def __init__(self, h, w, window, dtype, device):
        self.step = max(1, min(4, int(window // 2.5)))
        self.h, self.w = h, w
        self.sigma = window / self.step
        self.hl, self.wl = -(-h // self.step), -(-w // self.step)
        one = torch.ones(1, 1, h, w, dtype=dtype, device=device)
        self.frac = self.down(one) / (self.step * self.step)  # share of each cell inside the image
        self.frac_sum = _blur(self.frac, self.sigma)

    def down(self, x):
        s = self.step
        if s == 1:
            return x
        x = F.pad(x, (0, self.wl * s - self.w, 0, self.hl * s - self.h))
        return F.avg_pool2d(x, s) * (s * s)

    def sum(self, x):
        """Local Gaussian sum of a full-resolution field, on the low-res grid."""
        return _blur(self.down(x), self.sigma)

    def mean_lo(self, x_lo):
        """Local mean of a field that already lives on the low-res grid."""
        return _blur(x_lo * self.frac, self.sigma) / self.frac_sum

    def up(self, x_lo):
        if self.step == 1:
            return x_lo
        x = F.interpolate(x_lo, scale_factor=self.step, mode="bilinear", align_corners=False)
        return x[..., : self.h, : self.w]


def _carriers(h, w, dtype, device):
    sx = (1 - 2 * (torch.arange(w, device=device) % 2)).to(dtype).view(1, 1, 1, w)
    sy = (1 - 2 * (torch.arange(h, device=device) % 2)).to(dtype).view(1, 1, h, 1)
    return sx, sy


def _hp_x(x):
    # [-1, 2, -1] / 4: gain exactly 1 at the 2px frequency and 0 at DC
    p = F.pad(x, (1, 1, 0, 0), mode="reflect")
    return (2.0 * p[..., 1:-1] - p[..., :-2] - p[..., 2:]) * 0.25


def _hp_y(x):
    p = F.pad(x, (0, 0, 1, 1), mode="reflect")
    return (2.0 * p[..., 1:-1, :] - p[..., :-2, :] - p[..., 2:, :]) * 0.25


def _demodulate(x, sx, sy):
    """[1, 1, H, W] -> [1, 3, H, W]: V-stripe, H-stripe and checker moved to baseband."""
    hx = _hp_x(x)
    return torch.cat([hx * sx, _hp_y(x) * sy, _hp_y(hx) * (sx * sy)], dim=1)


def _remodulate(a, sx, sy):
    return a[:, 0:1] * sx + a[:, 1:2] * sy + a[:, 2:3] * (sx * sy)


def _robust_scale(e):
    flat = e.abs().reshape(e.shape[1], -1)
    n = flat.shape[1]
    if n > _MEDIAN_SAMPLES:
        flat = flat[:, :: n // _MEDIAN_SAMPLES + 1]
    return (1.4826 * flat.median(dim=1).values).clamp_min(_SCALE_FLOOR).view(1, -1, 1, 1)


def _grid_free(x):
    """2x2 box mean: exactly zero response to all three carriers."""
    return F.avg_pool2d(F.pad(x, (0, 1, 0, 1), mode="replicate"), 2, stride=1)


def estimate_channel(x, window=DEFAULT_WINDOW):
    """Measure the lattice in one image channel.

    x: [1, 1, H, W] float32, H and W >= 4.
    Returns (grid, amplitude, power, floor):
      grid       [1, 1, H, W] the lattice to subtract
      amplitude  [1, 3, H, W] local carrier amplitudes (V-stripe, H-stripe, checker)
      power      [3] mean squared amplitude per carrier
      floor      [3] part of ``power`` that incoherent texture alone would give
    """
    _, _, h, w = x.shape
    sx, sy = _carriers(h, w, x.dtype, x.device)
    d = _demodulate(x, sx, sy)
    sm = _Smoother(h, w, window, x.dtype, x.device)

    # Plain local mean, then robust re-weighting (IRLS, Cauchy) so edges and
    # isolated sharp detail don't leak into the amplitude estimate.
    one = torch.ones_like(d)
    a = sm.up(sm.sum(d) / sm.sum(one))
    wgt = one
    for _ in range(_ROBUST_ITERS):
        e = d - a
        wgt = 1.0 / (1.0 + (e / (_ROBUST_K * _robust_scale(e))) ** 2)
        a = sm.up(sm.sum(wgt * d) / sm.sum(wgt).clamp_min(1e-8))

    # Guided fit: amplitude ≈ slope * local brightness + intercept, per window.
    g = _grid_free(x).expand_as(d)
    wsum = sm.sum(wgt).clamp_min(1e-8)
    mg = sm.sum(wgt * g) / wsum
    md = sm.sum(wgt * d) / wsum
    cov = sm.sum(wgt * g * d) / wsum - mg * md
    var = (sm.sum(wgt * g * g) / wsum - mg * mg).clamp_min(0.0)
    slope = cov / (var + _GUIDE_EPS)
    icpt = md - slope * mg
    a = sm.up(sm.mean_lo(slope)) * g + sm.up(sm.mean_lo(icpt))

    # Detection: coherent power vs. what incoherent texture would leave behind
    # after averaging over the same window.
    e = d - a
    resid = (wgt * e * e).mean(dim=(2, 3)) / wgt.mean(dim=(2, 3)).clamp_min(1e-8)
    floor = resid / (4.0 * math.pi * window * window)
    power = (a * a).mean(dim=(2, 3))
    return _remodulate(a, sx, sy), a, power.view(3), floor.view(3)


def degrid(image, strength=1.0, window=DEFAULT_WINDOW, skip_when_clean=True,
           device=None, on_frame=None):
    """Remove the 2px lattice from a ComfyUI IMAGE batch [B, H, W, C] in 0..1.

    The grid/no-grid decision is made once for the whole batch, so the frames
    of a video are never handled differently from one another. Work runs on
    ``device`` (default: the image's) one channel at a time; results stay on
    the image's device. ``on_frame(i)`` is called after each frame.

    Returns (cleaned, removed, amplitude, stats):
      cleaned    same shape, dtype and device as ``image``
      removed    [B, H, W, C'] float32 lattice that was subtracted (C' = up to 3 colour channels)
      amplitude  [B, H, W, C'] float32 local lattice strength (≈ peak-to-peak)
      stats      dict of measurements (strengths in /255 units)
    """
    b, h, w, c = image.shape
    cc = min(c, 3)  # a 4th (alpha) channel is passed through untouched
    window = float(min(max(window, MIN_WINDOW), MAX_WINDOW))
    device = image.device if device is None else torch.device(device)
    removed = torch.zeros(b, cc, h, w, dtype=torch.float32, device=image.device)
    amplitude = torch.zeros_like(removed)
    power = torch.zeros(b, cc, 3, dtype=torch.float64)
    floor = torch.zeros_like(power)
    if h >= 4 and w >= 4:
        for i in range(b):
            for ch in range(cc):
                x = image[i:i + 1, :, :, ch].unsqueeze(1).to(device=device, dtype=torch.float32)
                grid, amp, p, f = estimate_channel(x, window)
                removed[i, ch] = grid[0, 0].to(image.device)
                amplitude[i, ch] = (2.0 * amp[0].pow(2).sum(0).sqrt()).to(image.device)
                power[i, ch] = p.double().cpu()
                floor[i, ch] = f.double().cpu()
            if on_frame is not None:
                on_frame(i)

    coherent = (power - floor).clamp_min(0.0)               # [B, C, 3]
    per_image = 2.0 * coherent.sum(-1).sqrt().amax(-1)      # [B]   worst channel
    per_carrier = 2.0 * coherent.mean(0).sqrt().amax(0)     # [3]   batch, worst channel
    batch = float(2.0 * coherent.mean(0).sum(-1).sqrt().amax()) if b else 0.0
    detected = batch >= NEGLIGIBLE
    skipped = bool(skip_when_clean) and not detected
    applied = 0.0 if skipped else float(strength)

    stats = {
        "batch_255": batch * 255.0,
        "per_image_255": [float(v) * 255.0 for v in per_image],
        "vstripe_255": float(per_carrier[0]) * 255.0,
        "hstripe_255": float(per_carrier[1]) * 255.0,
        "checker_255": float(per_carrier[2]) * 255.0,
        "detected": detected,
        "skipped": skipped,
        "strength": applied,
        "frames": b,
    }
    removed = removed.permute(0, 2, 3, 1) * applied
    amplitude = amplitude.permute(0, 2, 3, 1)
    if applied == 0.0:
        return image, removed, amplitude, stats
    cleaned = (image[..., :cc].float() - removed).clamp(0.0, 1.0).to(image.dtype)
    if c > cc:
        cleaned = torch.cat([cleaned, image[..., cc:]], dim=-1)
    return cleaned, removed, amplitude, stats


def _zoom_center(x, factor):
    """Nearest-neighbour magnification of the centre crop. x: [B, H, W, C]."""
    _, h, w, _ = x.shape
    ch, cw = max(2, h // factor) // 2 * 2, max(2, w // factor) // 2 * 2
    y0, x0 = (h - ch) // 4 * 2, (w - cw) // 4 * 2  # even offsets keep the lattice phase
    crop = x[:, y0:y0 + ch, x0:x0 + cw]
    return crop.repeat_interleave(factor, dim=1).repeat_interleave(factor, dim=2)


def preview(removed, amplitude, mode):
    """Preview image (3 channels, 0..1) of what was removed.

    'strength map' shows where the grid was strong, scaled so the strongest 1%
    is white. The lattice views show the removed pattern itself around mid gray,
    auto-scaled, either whole or as a magnified centre crop where the 2px
    pattern is visible at node-preview size.
    """
    if mode == "strength map":
        ref = amplitude.amax(-1)
        top = _quantile(ref, 0.99)
        out = amplitude / max(top, 1.0 / 255.0)
    else:
        top = _quantile(removed.abs(), 0.99)
        out = removed / (2.0 * max(top, 0.25 / 255.0)) + 0.5
        if mode == "lattice 4x zoom":
            out = _zoom_center(out, 4)
        elif mode == "lattice 8x zoom":
            out = _zoom_center(out, 8)
    if out.shape[-1] == 1:
        out = out.expand(*out.shape[:-1], 3)
    return out.clamp(0.0, 1.0).float().cpu()


def _quantile(x, q):
    flat = x.reshape(-1).float()
    if flat.numel() > 1_000_000:
        flat = flat[:: flat.numel() // 1_000_000 + 1]
    return float(torch.quantile(flat, q)) if flat.numel() else 0.0


def status_lines(stats):
    s = stats
    strength = f"grid ≈ {s['batch_255']:.2f}/255"
    if s["frames"] > 1:
        per = s["per_image_255"]
        strength += f" (frames {min(per):.2f}–{max(per):.2f})"
    if not s["detected"]:
        if s["skipped"]:
            state = "passed through untouched"
        elif s["strength"] > 0:
            state = "removed anyway (skip_when_clean is off)"
        else:
            state = "not removed (strength 0)"
        return [f"{strength} — no grid detected, {state}"]
    kinds = (("checker", s["checker_255"]), ("V-stripe", s["vstripe_255"]),
             ("H-stripe", s["hstripe_255"]))
    parts = " · ".join(f"{name} {val:.2f}" for name, val in kinds)
    verb = "removed" if s["strength"] > 0 else "found (strength 0: not removed)"
    if 0 < s["strength"] != 1.0:
        verb += f" at strength {s['strength']:.2f}"
    return [f"{strength} — {verb}", parts]
