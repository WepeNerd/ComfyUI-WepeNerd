"""Frame Slicer: cut a contiguous run of frames out of an IMAGE batch.

Same job and inputs as RES4LYF's Frames Slice: returns ``frames[start:stop]``,
so ``start`` is the first frame kept and ``stop`` is the first frame dropped.
A ``stop`` past the end of the batch keeps everything from ``start`` on. A range
that would keep no frames is an error instead of an empty batch, which otherwise
fails later in a node that has nothing to do with the cause.
"""

MAX_INDEX = 1_000_000


def slice_bounds(count, start, stop):
    """Clamp a [start, stop) range to a batch of ``count`` frames, or explain why it's empty."""
    start, stop = int(start), int(stop)
    if start < 0 or stop < 0:
        raise ValueError("start and stop can't be negative.")
    if start >= count:
        raise ValueError(
            f"start is {start} but the batch only has {count} frame(s) (indices 0-{count - 1})."
        )
    if stop <= start:
        raise ValueError(
            f"stop ({stop}) must be greater than start ({start}); stop is the first frame "
            f"left out, so start {start}, stop {start + 1} keeps one frame."
        )
    return start, min(stop, count)


class WN_FrameSlicer:
    """Keep frames start..stop-1 of an IMAGE batch."""

    CATEGORY = "WepeNerd/Video"
    FUNCTION = "slice"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    OUTPUT_TOOLTIPS = ("The frames from start up to, but not including, stop.",)
    DESCRIPTION = (
        "Cut a run of frames out of an IMAGE batch, like frames[start:stop]: start is the "
        "first frame kept, stop the first frame left out (frames count from 0). A stop past "
        "the end keeps the rest of the batch. Same inputs as RES4LYF's Frames Slice."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "frames": ("IMAGE", {"tooltip": "The batch to slice, usually a video's frames."}),
                "start": ("INT", {
                    "default": 0, "min": 0, "max": MAX_INDEX,
                    "tooltip": "First frame to keep. Frames are numbered from 0.",
                }),
                "stop": ("INT", {
                    "default": 1, "min": 1, "max": MAX_INDEX,
                    "tooltip": "First frame to leave out. A value past the end keeps the rest of the batch.",
                }),
            }
        }

    def slice(self, frames, start=0, stop=1):
        start, stop = slice_bounds(frames.shape[0], start, stop)
        return (frames[start:stop].clone(),)


NODE_CLASS_MAPPINGS = {"WN_FrameSlicer": WN_FrameSlicer}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_FrameSlicer": "Frame Slicer (WepeNerd)"}
