"""Sigma Curve: draw a sampler sigma schedule with draggable control points.

The curve math mirrors ``js/sigma_curve.mjs`` so the graph preview and the
executed SIGMAS match. Points are ``[x, y]`` with ``x`` the 0..1 position along
the schedule and ``y`` the sigma as a fraction of ``sigma_max``.
"""

import json
import math

MIN_GAP = 1e-4
MAX_POINTS = 64
LOG_FLOOR = 1e-5
MODES = ["log smooth", "smooth", "linear"]
DEFAULT_SIGMA_MAX = 14.614642
DEFAULT_SIGMA_MIN = 0.0291675
DEFAULT_CURVE = (
    '{"v":1,"points":[[0,1],[0.2,0.416169],[0.575,0.05547],[0.85,0.007782],'
    '[1,0.001996]],"log":false,"snap":true}'
)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _finite(value, fallback):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) else fallback


def normalize_points(points):
    items = []
    for p in points if isinstance(points, (list, tuple)) else []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            items.append([_clamp(_finite(p[0], 0.0), 0.0, 1.0),
                          _clamp(_finite(p[1], 0.0), 0.0, 1.0)])
    items.sort(key=lambda p: p[0])  # stable, like Array.prototype.sort
    out = []
    for p in items:
        if out and p[0] - out[-1][0] < MIN_GAP:
            continue
        out.append(p)
    if not out:
        return [[0.0, 1.0], [1.0, 0.0]]
    if len(out) == 1:
        return [[0.0, out[0][1]], [1.0, out[0][1]]]
    out[0][0] = 0.0
    out[-1][0] = 1.0
    if len(out) > MAX_POINTS:
        out = out[:MAX_POINTS - 1] + [out[-1]]
    return out


def monotone_tangents(points):
    n = len(points)
    d = [(points[i + 1][1] - points[i][1]) / (points[i + 1][0] - points[i][0])
         for i in range(n - 1)]
    m = [0.0] * n
    m[0] = d[0]
    m[-1] = d[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = 0.0
            m[i + 1] = 0.0
            continue
        a = m[i] / d[i]
        b = m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            t = 3 / math.sqrt(s)
            m[i] = t * a * d[i]
            m[i + 1] = t * b * d[i]
    return m


def _segment(points, x):
    i = 0
    while i < len(points) - 2 and x > points[i + 1][0]:
        i += 1
    return i


def _hermite(points, x, m):
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    i = _segment(points, x)
    x0, y0 = points[i]
    x1, y1 = points[i + 1]
    h = x1 - x0
    t = (x - x0) / h
    t2 = t * t
    t3 = t2 * t
    return ((2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * h * m[i]
            + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * h * m[i + 1])


def make_evaluator(points, mode="log smooth"):
    pts = normalize_points(points)
    if mode == "linear":
        def linear(x):
            if x <= pts[0][0]:
                return pts[0][1]
            if x >= pts[-1][0]:
                return pts[-1][1]
            i = _segment(pts, x)
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        return linear
    if mode == "smooth":
        m = monotone_tangents(pts)
        return lambda x: _clamp(_hermite(pts, x, m), 0.0, 1.0)
    log_pts = [[x, math.log(max(y, LOG_FLOOR))] for x, y in pts]
    m = monotone_tangents(log_pts)

    def log_smooth(x):
        y = math.exp(_hermite(log_pts, x, m))
        return 0.0 if y <= LOG_FLOOR * 1.000001 else _clamp(y, 0.0, 1.0)
    return log_smooth


def sample_positions(steps, end_at_zero=True):
    steps = max(1, int(round(_finite(steps, 1))))
    count = steps if end_at_zero else steps + 1
    return [0.0 if count == 1 else i / (count - 1) for i in range(count)]


def sample_sigmas(points, steps, sigma_max, mode="log smooth", end_at_zero=True):
    f = make_evaluator(points, mode if mode in MODES else "log smooth")
    out = [f(x) * sigma_max for x in sample_positions(steps, end_at_zero)]
    if end_at_zero:
        out.append(0.0)
    return out


def parse_curve(text):
    try:
        data = json.loads(text) if isinstance(text, str) and text.strip() else {}
    except (TypeError, ValueError):
        data = {}
    if isinstance(data, list):
        data = {"points": data}
    if not isinstance(data, dict):
        data = {}
    return normalize_points(data.get("points"))


class WN_SigmaCurve:
    """Visual sigma schedule editor that outputs SIGMAS for SamplerCustom."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "steps": ("INT", {"default": 20, "min": 1, "max": 1000}),
                "sigma_max": ("FLOAT", {"default": DEFAULT_SIGMA_MAX, "min": 0.0001,
                                        "max": 1000.0, "step": 0.001, "round": False}),
                "sigma_min": ("FLOAT", {"default": DEFAULT_SIGMA_MIN, "min": 0.00001,
                                        "max": 1000.0, "step": 0.0001, "round": False,
                                        "tooltip": "Used by presets and as the floor of the log view."}),
                "interpolation": (MODES, {"default": "log smooth"}),
                "end_at_zero": ("BOOLEAN", {"default": True,
                                            "tooltip": "Append a final 0 sigma after the curve's steps."}),
                "curve": ("STRING", {"default": DEFAULT_CURVE, "multiline": False}),
            },
        }

    RETURN_TYPES = ("SIGMAS",)
    RETURN_NAMES = ("sigmas",)
    FUNCTION = "build"
    CATEGORY = "WepeNerd/Sampling"

    def build(self, steps, sigma_max, sigma_min, interpolation, end_at_zero, curve):
        import torch

        sigma_max = max(_finite(sigma_max, DEFAULT_SIGMA_MAX), 0.0)
        values = sample_sigmas(parse_curve(curve), steps, sigma_max,
                               interpolation, bool(end_at_zero))
        return (torch.FloatTensor(values),)
