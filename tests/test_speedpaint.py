import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import speedpaint_node as speedpaint


class SpeedpaintTests(unittest.TestCase):
    def test_existing_socket_order_and_optional_image(self):
        inputs = speedpaint.WN_Speedpaint.INPUT_TYPES()
        self.assertEqual(list(inputs["required"]), ["width", "height", "document"])
        self.assertEqual(inputs["optional"]["image"], ("IMAGE", {"lazy": True}))
        self.assertEqual(speedpaint.WN_Speedpaint.RETURN_TYPES, ("IMAGE",))

    def test_queue_preserves_imported_painting_with_connected_image(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(speedpaint, "asset_root", return_value=Path(directory)):
            raw = io.BytesIO()
            Image.new("RGB", (128, 64), (255, 0, 0)).save(raw, format="PNG")
            document = speedpaint.import_document(raw.getvalue(), 64, 64, "#ffffff")
            serialized = json.dumps(document)
            node = speedpaint.WN_Speedpaint()
            self.assertEqual(node.check_lazy_status(64, 64, serialized), [])
            result = node.render(64, 64, serialized, image=torch.zeros((1, 64, 64, 3)))
            pixels = result["result"][0]
            self.assertEqual(tuple(pixels.shape), (1, 64, 64, 3))
            self.assertEqual(pixels.dtype, torch.float32)
            np.testing.assert_array_equal(pixels[0, 0, 0].numpy(), [1, 0, 0])
            self.assertEqual(result["ui"]["speedpaint"][0]["document"], serialized)

    def test_existing_blank_workflow_still_renders(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(speedpaint, "asset_root", return_value=Path(directory)):
            result = speedpaint.WN_Speedpaint().render(64, 96)
            self.assertEqual(tuple(result["result"][0].shape), (1, 96, 64, 3))


if __name__ == "__main__":
    unittest.main()
