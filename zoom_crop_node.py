"""Crop an image batch by pixel amounts at one of 25 positions."""


POSITIONS = {
    " ".join(part for part in (vertical, horizontal) if part != "center") or "center": (column, row)
    for row, vertical in enumerate(("top", "upper", "center", "lower", "bottom"))
    for column, horizontal in enumerate(("left", "mid-left", "center", "mid-right", "right"))
}


class WN_ZoomCrop:
    CATEGORY = "WepeNerd/Image"
    FUNCTION = "crop"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    DESCRIPTION = (
        "Remove a total number of pixels from the width and height. "
        "Choose where to keep the crop on a 5 by 5 grid. No resizing; "
        "at least one pixel is retained on each axis."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "crop_width": ("INT", {
                    "default": 0, "min": 0, "max": 16384, "step": 1,
                    "tooltip": "Total pixels to remove from the width, not per side.",
                }),
                "crop_height": ("INT", {
                    "default": 0, "min": 0, "max": 16384, "step": 1,
                    "tooltip": "Total pixels to remove from the height, not per side.",
                }),
                "position": (list(POSITIONS), {
                    "default": "center",
                    "tooltip": "Position of the retained crop: left/top keeps that edge; center splits removal between both sides.",
                }),
            }
        }

    def crop(self, image, crop_width, crop_height, position):
        column, row = POSITIONS[position]
        height, width = image.shape[1:3]
        remove_x = min(max(int(crop_width), 0), width - 1)
        remove_y = min(max(int(crop_height), 0), height - 1)
        if remove_x == 0 and remove_y == 0:
            return (image,)
        x = remove_x * column // 4
        y = remove_y * row // 4
        return (image[:, y:y + height - remove_y, x:x + width - remove_x, :].clone(),)
