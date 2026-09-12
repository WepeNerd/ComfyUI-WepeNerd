import base64
from contextlib import ExitStack
from dataclasses import replace
import importlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from .test_gguf_nodes import config_in


folder = importlib.import_module("wepenerd_testpkg.wn_gguf_caption_folder")
nodes = importlib.import_module("wepenerd_testpkg.wn_gguf_nodes")
server = importlib.import_module("wepenerd_testpkg.wn_gguf_server")


class FolderCaptionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.model = config_in(self.root)
        self.images = self.root / "images"
        self.images.mkdir()
        handle = mock.Mock()
        handle.capability.return_value = True
        self.acquire = self.stack.enter_context(mock.patch.object(nodes, "_acquire_prepared", return_value=handle))
        self.finish = self.stack.enter_context(mock.patch.object(nodes, "_finish_request"))
        self.chat = self.stack.enter_context(mock.patch.object(nodes.SERVER_MANAGER, "chat_completion", return_value="A red square."))
        self.progress = self.stack.enter_context(mock.patch.object(nodes, "_progress_bar"))

    def image(self, name="image.png", **kwargs):
        path = self.images / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (80, 60), "red").save(path, **kwargs)
        return path

    def run_folder(self, **kwargs):
        return nodes.WN_FolderCaptioner().caption(self.model, str(self.images), **kwargs)

    def test_registered_output_node_executes_on_every_queue(self):
        cls = nodes.NODE_CLASS_MAPPINGS["WN_FolderCaptioner"]
        self.assertEqual(cls.CATEGORY, "WepeNerd/Local AI")
        self.assertEqual(cls.INPUT_TYPES()["required"]["model"], ("GGUF_LLM_CONFIG",))
        self.assertTrue(cls.OUTPUT_NODE)
        self.assertTrue(math.isnan(cls.IS_CHANGED()))

    def test_batch_saves_before_next_image_and_acquires_once(self):
        first = self.image("a.photo.JPG")
        second = self.image("b.png")
        original = nodes.image_file_data_url

        def encode(path, edge):
            if path == second:
                self.assertEqual(first.with_suffix(".txt").read_text(), "A red square.\n")
            return original(path, edge)

        with mock.patch.object(nodes, "image_file_data_url", side_effect=encode):
            report, written, skipped = self.run_folder()
        self.assertEqual((written, skipped), (2, 0))
        self.assertIn("Found 2 images", report)
        self.assertEqual(second.with_suffix(".txt").read_text(), "A red square.\n")
        self.assertFalse(first.with_name(first.name + ".txt").exists())
        self.assertEqual(len(list(self.images.iterdir())), 4)
        self.acquire.assert_called_once_with(self.model)
        self.finish.assert_called_once_with(self.model, None)
        self.assertEqual(self.progress.return_value.update.call_count, 2)
        for call in self.chat.call_args_list:
            payload = call.args[1]
            self.assertEqual(payload["reasoning_effort"], "none")
            self.assertEqual(payload["max_tokens"], 768)
            self.assertEqual([p["type"] for p in payload["messages"][1]["content"]], ["text", "image_url"])

    def test_skip_preserves_empty_and_existing_files_without_loading(self):
        for name, text in (("a.png", "human caption"), ("b.png", "")):
            self.image(name).with_suffix(".txt").write_text(text)
        self.assertEqual(self.run_folder()[1:], (0, 2))
        self.acquire.assert_not_called()
        self.assertEqual((self.images / "a.txt").read_text(), "human caption")
        self.assertEqual((self.images / "b.txt").read_text(), "")

    def test_requeue_discovers_new_image(self):
        first = self.image()
        self.run_folder()
        self.image("new.png")
        self.assertEqual(self.run_folder()[1:], (1, 1))
        self.assertEqual(self.chat.call_count, 2)
        self.assertEqual(first.with_suffix(".txt").read_text(), "A red square.\n")

    def test_overwrite_is_explicit_and_utf8(self):
        target = self.image().with_suffix(".txt")
        target.write_text("original")
        self.chat.return_value = "A café sign beside a red square."
        self.assertEqual(self.run_folder(existing_captions="Overwrite")[1:], (1, 0))
        self.assertEqual(target.read_bytes(), "A café sign beside a red square.\n".encode("utf-8"))

    def test_subfolders_keep_sidecars_adjacent_and_equal_stems_separate(self):
        self.image("same.png")
        nested = self.image("child/same.jpeg")
        self.assertEqual(self.run_folder()[1:], (1, 0))
        self.assertFalse(nested.with_suffix(".txt").exists())
        self.assertEqual(self.run_folder(include_subfolders=True)[1:], (1, 1))
        self.assertTrue(nested.with_suffix(".txt").exists())

    def test_same_stem_collision_fails_before_generation_or_writes(self):
        self.image("a.png")
        self.image("duplicate.jpg")
        self.image("duplicate.png")
        with self.assertRaisesRegex(ValueError, "collision"):
            self.run_folder()
        self.acquire.assert_not_called()
        self.assertEqual(list(self.images.glob("*.txt")), [])

    def test_invalid_policy_custom_skill_and_relative_path_fail_before_loading(self):
        self.image()
        for kwargs in ({"existing_captions": "../x"}, {"skill": "../x"}, {"skill": "Custom"},
                       {"image_max_edge": 0}, {"trigger_word": "first\nsecond"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_folder(**kwargs)
        with self.assertRaisesRegex(ValueError, "absolute"):
            folder.scan_caption_folder("relative", False, "Skip")
        self.acquire.assert_not_called()

    def test_empty_folder_does_not_load_model(self):
        (self.images / "notes.txt").write_text("not an image")
        self.assertEqual(self.run_folder()[1:], (0, 0))
        self.acquire.assert_not_called()

    def test_missing_projector_does_not_load_model(self):
        self.image()
        self.model = replace(self.model, mmproj_path=None)
        with self.assertRaisesRegex(ValueError, "projector"):
            self.run_folder()
        self.acquire.assert_not_called()

    def test_corrupt_image_stops_and_preserves_completed_files(self):
        self.image("a.png")
        (self.images / "b.png").write_bytes(b"broken image")
        with self.assertRaisesRegex(RuntimeError, "b.png.*cannot identify"):
            self.run_folder()
        self.assertTrue((self.images / "a.txt").is_file())
        self.assertFalse((self.images / "b.txt").exists())
        self.finish.assert_called_once()

    def test_truncation_keeps_previous_caption_when_overwriting(self):
        target = self.image().with_suffix(".txt")
        target.write_text("reviewed caption")
        self.chat.side_effect = server.RequestRejectedError("output truncated by token limit")
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            self.run_folder(existing_captions="Overwrite")
        self.assertEqual(target.read_text(), "reviewed caption")
        self.assertEqual(list(self.images.glob("*.tmp")), [])

    def test_saving_failure_closes_shared_iterator(self):
        self.image()
        with mock.patch.object(nodes, "write_caption", side_effect=PermissionError("read-only")):
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                self.run_folder()
        self.finish.assert_called_once()
        self.assertIsInstance(self.finish.call_args.args[1], GeneratorExit)

    def test_cancellation_propagates_without_wrapping_and_releases(self):
        class InterruptProcessingException(Exception):
            pass

        self.image()
        self.chat.side_effect = InterruptProcessingException("cancelled")
        with self.assertRaises(InterruptProcessingException):
            self.run_folder()
        self.finish.assert_called_once()
        self.assertFalse((self.images / "image.txt").exists())

    def test_missing_or_altered_trigger_is_not_silently_saved(self):
        self.image()
        for value in ("A red square.", "myTOKperson beside a square."):
            self.chat.return_value = value
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "trigger_word"):
                self.run_folder(trigger_word="TOK")
        self.chat.return_value = "TOK, a person beside a red square."
        self.assertEqual(self.run_folder(trigger_word="TOK")[1:], (1, 0))

    def test_skills_and_metadata_reach_model_with_correct_roles(self):
        self.image()
        context = "Known model: a 1974 Citroën DS; focus on the visible lights."
        for skill, key in folder.CAPTION_SKILLS.items():
            with self.subTest(skill=skill):
                self.run_folder(skill=skill, concept_context=context, instruction="Keep captions concise.",
                                existing_captions="Overwrite")
                messages = self.chat.call_args.args[1]["messages"]
                if key:
                    self.assertEqual(messages[0]["content"], folder.load_skill(key))
                elif skill == "Custom":
                    self.assertEqual(messages[0]["content"], "Keep captions concise.")
                settings = json.loads(messages[1]["content"][0]["text"].split("\n", 1)[1])
                self.assertEqual(settings["concept_context"], context)
                self.assertEqual(settings["instruction"], "Keep captions concise.")

    def test_atomic_publish_failure_preserves_old_file_and_cleans_temp(self):
        image = self.image()
        target = image.with_suffix(".txt")
        target.write_text("reviewed caption")
        with mock.patch.object(folder.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                folder.write_caption(self.images, image, "replacement", "Overwrite")
        self.assertEqual(target.read_text(), "reviewed caption")
        self.assertEqual(list(self.images.glob("*.tmp")), [])

    def test_skip_does_not_clobber_caption_created_during_inference(self):
        target = self.image().with_suffix(".txt")

        def chat(*args):
            target.write_text("another writer")
            return "A red square."

        self.chat.side_effect = chat
        self.assertEqual(self.run_folder()[1:], (0, 1))
        self.assertEqual(target.read_text(), "another writer")

    def test_skip_publish_race_never_clobbers_destination(self):
        image = self.image()
        target = image.with_suffix(".txt")
        operation = "rename" if folder.os.name == "nt" else "link"
        original = getattr(folder.os, operation)

        def race(source, destination):
            target.write_text("racing writer")
            return original(source, destination)

        with mock.patch.object(folder.os, operation, side_effect=race):
            self.assertFalse(folder.write_caption(self.images, image, "generated", "Skip"))
        self.assertEqual(target.read_text(), "racing writer")
        self.assertEqual(list(self.images.glob("*.tmp")), [])

    def test_invalid_destination_is_rejected(self):
        image = self.image()
        image.with_suffix(".txt").mkdir()
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.run_folder()
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            folder.write_caption(self.images, self.root / "outside.png", "caption", "Overwrite")
        self.acquire.assert_not_called()

    def test_symlinks_are_not_followed(self):
        outside = self.root / "outside.txt"
        outside.write_text("keep")
        image = self.image()
        try:
            image.with_suffix(".txt").symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.run_folder(existing_captions="Overwrite")
        self.assertEqual(outside.read_text(), "keep")

    def test_exif_orientation_applied_before_encoding(self):
        exif = Image.Exif()
        exif[274] = 6
        path = self.image("portrait.jpg", exif=exif)
        data = folder.image_file_data_url(path, 1024).split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(data))) as decoded:
            self.assertEqual(decoded.size, (60, 80))

    def test_palette_transparency_composites_over_white(self):
        path = self.images / "transparent.png"
        img = Image.new("P", (80, 60), 0)
        img.putpalette([0, 0, 0] * 256)
        img.save(path, transparency=0)
        data = folder.image_file_data_url(path, 64).split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(data))) as decoded:
            self.assertEqual(decoded.size, (64, 48))
            self.assertEqual(decoded.getpixel((0, 0)), (255, 255, 255))

    def test_multipage_image_is_not_silently_captioned_as_one_frame(self):
        path = self.images / "pages.tiff"
        first = Image.new("RGB", (80, 60), "red")
        first.save(path, save_all=True, append_images=[Image.new("RGB", (80, 60), "blue")])
        with self.assertRaisesRegex(RuntimeError, "multipage"):
            self.run_folder()
        self.chat.assert_not_called()

    def test_caption_cleanup_does_not_save_reasoning_or_wrappers(self):
        self.assertEqual(folder.clean_folder_caption("<think>reasoning</think>\nCaption: A red square.", ""), "A red square.")
        self.assertEqual(folder.clean_folder_caption("```text\nA red square.\n```", ""), "A red square.")
        for value in ("", "<think>only reasoning</think>", "Caption:", "\x00"):
            with self.subTest(value=value), self.assertRaises(server.RequestRejectedError):
                folder.clean_folder_caption(value, "")


if __name__ == "__main__":
    unittest.main()
