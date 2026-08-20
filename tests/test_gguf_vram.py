import importlib
import sys
import types
import unittest
from unittest import mock


vram = importlib.import_module("wepenerd_testpkg.wn_gguf_vram")


class FakeMemoryManagement(types.ModuleType):
    def __init__(self):
        super().__init__("comfy.model_management")
        self.free_calls = []
        self.soft_calls = 0

    def get_torch_device(self):
        return "cuda:0"

    def get_free_memory(self, device):
        return 8 * 1024 * 1024

    def free_memory(self, amount, device):
        self.free_calls.append((amount, device))

    def unload_all_models(self):
        pass

    def soft_empty_cache(self):
        self.soft_calls += 1


class VRAMTests(unittest.TestCase):
    def test_never_mode_does_not_mutate_comfy_memory(self):
        memory = FakeMemoryManagement()
        comfy = types.ModuleType("comfy")
        comfy.model_management = memory
        with mock.patch.dict(sys.modules, {"comfy": comfy, "comfy.model_management": memory}):
            info = vram.free_vram_for_external(100, handoff_mode="never")
        self.assertEqual(memory.free_calls, [])
        self.assertEqual(memory.soft_calls, 0)
        self.assertEqual(info["handoff_mode"], "never")

    def test_auto_mode_requests_target_and_soft_empties(self):
        memory = FakeMemoryManagement()
        comfy = types.ModuleType("comfy")
        comfy.model_management = memory
        with mock.patch.dict(sys.modules, {"comfy": comfy, "comfy.model_management": memory}):
            vram.free_vram_for_external(100, handoff_mode="auto")
        self.assertEqual(memory.free_calls, [(100 * 1024 * 1024, "cuda:0")])
        self.assertEqual(memory.soft_calls, 1)
