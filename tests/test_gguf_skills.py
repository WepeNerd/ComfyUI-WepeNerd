import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


skills = importlib.import_module("wepenerd_testpkg.wn_gguf_skills")


class SkillTests(unittest.TestCase):
    def tearDown(self):
        skills.clear_skill_cache()

    def test_bundled_h3_and_krea2_load(self):
        self.assertIn("MiniMax H3", skills.load_skill("h3"))
        self.assertIn("Krea 2", skills.load_skill("Krea 2"))

    def test_traversal_is_rejected(self):
        with mock.patch.dict(skills.BUILTIN_SKILLS, {"escape": "../outside.md"}):
            with self.assertRaisesRegex(ValueError, "escaped"):
                skills.load_skill("escape")

    def test_missing_and_empty_files_are_actionable(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            skills, "_SKILLS_DIR", Path(directory).resolve()
        ), mock.patch.dict(skills.BUILTIN_SKILLS, {"missing": "missing.md", "empty": "empty.md"}):
            with self.assertRaisesRegex(FileNotFoundError, "Reinstall"):
                skills.load_skill("missing")
            Path(directory, "empty.md").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty"):
                skills.load_skill("empty")

    def test_cache_refreshes_after_file_change(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            skills, "_SKILLS_DIR", Path(directory).resolve()
        ), mock.patch.dict(skills.BUILTIN_SKILLS, {"test": "test.md"}):
            path = Path(directory, "test.md")
            path.write_text("first", encoding="utf-8")
            self.assertEqual(skills.load_skill("test"), "first")
            previous = path.stat().st_mtime_ns
            path.write_text("second version", encoding="utf-8")
            os.utime(path, ns=(previous + 1_000_000, previous + 1_000_000))
            self.assertEqual(skills.load_skill("test"), "second version")
