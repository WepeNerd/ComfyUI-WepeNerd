import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock


config_module = importlib.import_module("wepenerd_testpkg.wn_gguf_config")
server = importlib.import_module("wepenerd_testpkg.wn_gguf_server")


def make_config(root, extra_args=(), **kwargs):
    executable = root / "llama-server.exe"
    executable.write_bytes(b"")
    model = root / "model.gguf"
    model.write_bytes(b"")
    return config_module.WNGGUFConfig(
        model_path=str(model), server_executable=str(executable), extra_args=extra_args, **kwargs
    )


class FakeProcess:
    pid = 123

    def poll(self):
        return None


class FakeResponse:
    def __init__(self, status=200, data=b""):
        self.status = status
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data

    def close(self):
        pass


class ServerTests(unittest.TestCase):
    def test_command_uses_current_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(
                Path(directory), flash_attn="on", cache_type_k="q8_0",
                image_min_tokens=64, image_max_tokens=1024,
            )
            command = server.build_server_command(config, 12345)
            self.assertEqual(command[command.index("-m") + 1], config.model_path)
            self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
            self.assertEqual(command[command.index("-ngl") + 1], "all")
            self.assertIn("--jinja", command)
            self.assertEqual(command[command.index("--flash-attn") + 1], "on")
            self.assertEqual(command[command.index("-ctk") + 1], "q8_0")
            self.assertEqual(command[command.index("--image-max-tokens") + 1], "1024")

    def test_default_optional_flags_are_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            command = server.build_server_command(make_config(Path(directory)), 12345)
            self.assertNotIn("--flash-attn", command)
            self.assertNotIn("-ctk", command)
            self.assertNotIn("-ctv", command)

    def test_extra_args_cannot_override_managed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory), ("--host", "0.0.0.0"))
            with self.assertRaisesRegex(ValueError, "cannot override"):
                server.build_server_command(config, 12345)

    def test_child_cuda_environment_does_not_change_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            before = os.environ.get("CUDA_VISIBLE_DEVICES")
            config = make_config(Path(directory), cuda_visible_devices="1")
            environment = server.child_environment(config)
            self.assertEqual(environment["CUDA_VISIBLE_DEVICES"], "1")
            self.assertEqual(os.environ.get("CUDA_VISIBLE_DEVICES"), before)

    def test_reasoning_is_never_returned_as_visible_content(self):
        self.assertEqual(server.clean_visible_content("final answer"), "final answer")
        self.assertEqual(
            server.clean_visible_content("<think>\nsecret\n</think>\nfinal answer", True),
            "final answer",
        )
        self.assertEqual(
            server.clean_visible_content([{"type": "text", "text": "final"}]), "final"
        )
        self.assertEqual(
            server.clean_visible_content([{"type": "output_text", "text": "final"}]), "final"
        )
        with self.assertRaises(server.ReasoningOnlyError):
            server.clean_visible_content("<think>secret</think>")
        with self.assertRaises(server.ReasoningOnlyError):
            server.clean_visible_content("", reasoning_present=True)

    def test_readiness_uses_health_then_verifies_props(self):
        with tempfile.TemporaryDirectory() as directory:
            model = str((Path(directory) / "model.gguf").resolve())
            Path(model).write_bytes(b"")
            handle = server.ServerHandle(FakeProcess(), "127.0.0.1", 1234, (), model, server.deque())
            health_calls = 0

            def fake_urlopen(url, timeout=0):
                nonlocal health_calls
                self.assertNotIn("/v1/models", url)
                if url.endswith("/health"):
                    health_calls += 1
                    if health_calls == 1:
                        raise urllib.error.HTTPError(url, 503, "loading", {}, None)
                    return FakeResponse(200)
                if url.endswith("/props"):
                    return FakeResponse(200, json.dumps({"model_path": model, "modalities": {"vision": True}}).encode())
                raise AssertionError(url)

            with mock.patch.object(server.urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch.object(server.time, "sleep"):
                props = server.LlamaServerManager._wait_ready(handle, 2.0)
            self.assertTrue(props["modalities"]["vision"])
            self.assertEqual(health_calls, 2)

    def test_props_model_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected.gguf"
            other = Path(directory) / "other.gguf"
            expected.write_bytes(b"")
            other.write_bytes(b"")
            with self.assertRaises(server.PortIdentityError):
                server.LlamaServerManager._verify_model_identity(
                    str(expected), {"model_path": str(other)}
                )

    def test_missing_props_model_path_is_tolerated(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.gguf"
            model.write_bytes(b"")
            server.LlamaServerManager._verify_model_identity(str(model), {})

    def test_capabilities_are_tri_state(self):
        handle = server.ServerHandle(FakeProcess(), "127.0.0.1", 1234, (), "model.gguf", server.deque())
        self.assertIsNone(handle.capability("image"))
        handle.props = {"modalities": {"vision": False, "video": True}}
        self.assertIs(handle.capability("image"), False)
        self.assertIs(handle.capability("video"), True)

    def test_auto_port_retries_identity_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory), port=0)
            manager = server.LlamaServerManager()
            expected = object()
            with mock.patch.object(server, "find_free_port", side_effect=[12001, 12002]), mock.patch.object(
                manager,
                "_spawn_and_wait",
                side_effect=[server.PortIdentityError("wrong model"), expected],
            ) as spawn:
                self.assertIs(manager._start(config), expected)
            self.assertEqual(spawn.call_count, 2)

    def test_keep_alive_watchdog_stops_expired_handle(self):
        manager = server.LlamaServerManager()
        handle = server.ServerHandle(FakeProcess(), "127.0.0.1", 1234, (), "model.gguf", server.deque())
        handle.idle_deadline = time.monotonic() - 1
        manager._handle = handle
        with mock.patch.object(manager, "stop") as stop:
            manager._idle_expired(handle)
            stop.assert_called_once()
        manager._handle = None

    def test_streaming_collects_visible_content(self):
        handle = server.ServerHandle(FakeProcess(), "127.0.0.1", 1234, (), "model.gguf", server.deque())

        def fake_worker(request, timeout, events, state):
            for text in ("<think>secret</think>", "final"):
                chunk = {"choices": [{"delta": {"content": text}}]}
                events.put(("line", ("data: " + json.dumps(chunk)).encode()))
            events.put(("line", b"data: [DONE]"))
            events.put(("done", None))

        with mock.patch.object(server.LlamaServerManager, "_stream_worker", side_effect=fake_worker):
            result = server.LlamaServerManager.chat_completion(handle, {"messages": []}, 2.0)
        self.assertEqual(result, "final")

    def test_interrupt_propagates_from_stream_poll(self):
        class FakeInterrupt(BaseException):
            pass

        class FakeModelManagement:
            @staticmethod
            def throw_exception_if_processing_interrupted():
                raise FakeInterrupt()

        handle = server.ServerHandle(FakeProcess(), "127.0.0.1", 1234, (), "model.gguf", server.deque())
        with mock.patch.object(server, "_comfy_model_management", FakeModelManagement()), mock.patch.object(
            server.LlamaServerManager, "_stream_worker", return_value=None
        ):
            with self.assertRaises(FakeInterrupt):
                server.LlamaServerManager.chat_completion(handle, {"messages": []}, 2.0)

    def test_comfy_interrupt_is_non_fatal_and_closes_active_stream(self):
        class InterruptProcessingException(BaseException):
            pass

        ready = threading.Event()

        class FakeResponseWithClose:
            closed = False

            def close(self):
                self.closed = True

        response = FakeResponseWithClose()

        class FakeModelManagement:
            @staticmethod
            def throw_exception_if_processing_interrupted():
                ready.wait(1.0)
                raise InterruptProcessingException()

        FakeModelManagement.InterruptProcessingException = InterruptProcessingException

        def fake_worker(request, timeout, events, state):
            state["response"] = response
            ready.set()

        handle = server.ServerHandle(FakeProcess(), "127.0.0.1", 1234, (), "model.gguf", server.deque())
        with mock.patch.object(server, "_comfy_model_management", FakeModelManagement()), mock.patch.object(
            server.LlamaServerManager, "_stream_worker", side_effect=fake_worker
        ):
            with self.assertRaises(InterruptProcessingException) as caught:
                server.LlamaServerManager.chat_completion(handle, {"messages": []}, 2.0)
        self.assertTrue(response.closed)
        self.assertFalse(server.LlamaServerManager.is_fatal_error(caught.exception))

    def test_error_classification_preserves_only_request_errors(self):
        self.assertFalse(server.LlamaServerManager.is_fatal_error(server.RequestRejectedError("400")))
        self.assertTrue(server.LlamaServerManager.is_fatal_error(server.ServerFailureError("500")))
