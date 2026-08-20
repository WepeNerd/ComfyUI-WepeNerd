import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


models = importlib.import_module("wepenerd_testpkg.wn_gguf_models")


class FakeFolderPaths:
    def __init__(self, root, extra=None):
        self.models_dir = str(root)
        self.paths = {"LLM": [str(extra)] if extra else []}

    def add_model_folder_path(self, category, path, is_default=False):
        values = self.paths.setdefault(category, [])
        if path not in values:
            values.insert(0, path) if is_default else values.append(path)

    def get_folder_paths(self, category):
        if category not in self.paths:
            raise KeyError(category)
        return list(self.paths[category])

    def get_filename_list(self, category):
        names = set()
        for root in self.get_folder_paths(category):
            path = Path(root)
            if path.is_dir():
                names.update(item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file())
        return sorted(names)

    def get_full_path(self, category, name):
        for root in self.get_folder_paths(category):
            candidate = Path(root) / name
            if candidate.is_file():
                return str(candidate)
        return None


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        models._registered = False
        models.clear_discovery_cache()

    def test_registered_primary_extra_and_legacy_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "LLM"
            extra = root / "extra"
            legacy = root / "llm_gguf"
            (primary / "qwen").mkdir(parents=True)
            extra.mkdir()
            legacy.mkdir()
            (primary / "qwen" / "model.GGUF").write_bytes(b"model")
            (primary / "mmproj.gguf").write_bytes(b"projector")
            (extra / "extra.gguf").write_bytes(b"extra")
            (legacy / "old.gguf").write_bytes(b"old")
            fake = FakeFolderPaths(root, extra)
            with mock.patch.dict(sys.modules, {"folder_paths": fake}):
                self.assertEqual(
                    models.discover_models(),
                    ["LLM/extra.gguf", "LLM/qwen/model.GGUF", "llm_gguf/old.gguf"],
                )
                self.assertEqual(models.discover_projectors(), ["(none)", "LLM/mmproj.gguf"])
                self.assertTrue(models.resolve_choice("LLM/qwen/model.GGUF").endswith("model.GGUF"))

    def test_ttl_cache_requires_expiry_or_clear_for_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LLM").mkdir()
            fake = FakeFolderPaths(root)
            with mock.patch.dict(sys.modules, {"folder_paths": fake}):
                self.assertEqual(models.discover_models(), [])
                (root / "LLM" / "new.gguf").write_bytes(b"new")
                self.assertEqual(models.discover_models(), [])
                models.clear_discovery_cache()
                self.assertEqual(models.discover_models(), ["LLM/new.gguf"])

    def test_older_add_folder_signature_is_supported(self):
        class OlderFolderPaths(FakeFolderPaths):
            def add_model_folder_path(self, category, path):
                self.paths.setdefault(category, []).append(path)

        with tempfile.TemporaryDirectory() as directory:
            fake = OlderFolderPaths(directory)
            with mock.patch.dict(sys.modules, {"folder_paths": fake}):
                self.assertIs(models._register_folders(), fake)
