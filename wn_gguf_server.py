"""Managed localhost llama.cpp server lifecycle and streaming chat client."""

from __future__ import annotations

import atexit
from collections import deque
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from .wn_gguf_config import WNGGUFConfig


try:
    import comfy.model_management as _comfy_model_management
except ImportError:
    _comfy_model_management = None


log = logging.getLogger("ComfyUI-WepeNerd.GGUF")
_RESERVED_ARGS = {
    "-m", "--model", "--model-url", "--host", "--port", "-c", "--ctx-size",
    "-ngl", "--gpu-layers", "--n-gpu-layers", "--mmproj", "--mmproj-url",
    "--flash-attn", "-ctk", "--cache-type-k", "-ctv", "--cache-type-v",
    "--image-min-tokens", "--image-max-tokens", "--jinja", "--no-jinja",
    "--no-webui", "--webui",
}
_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_UNCLOSED_THINK = re.compile(r"<think\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_THINK_TAG = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)


class RequestRejectedError(RuntimeError):
    """The server is healthy, but rejected the current request."""


class ReasoningOnlyError(RequestRejectedError):
    pass


class ServerFailureError(RuntimeError):
    """The server connection or protocol is no longer trustworthy."""


class PortIdentityError(ServerFailureError):
    pass


def is_user_cancel(error: BaseException) -> bool:
    cancel_type = getattr(_comfy_model_management, "InterruptProcessingException", None)
    return bool(
        (cancel_type is not None and isinstance(error, cancel_type))
        or error.__class__.__name__ == "InterruptProcessingException"
    )


def clean_visible_content(content, reasoning_present: bool = False) -> str:
    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text", ""), str)
        )
    if not isinstance(content, str):
        content = ""
    had_thinking = bool(_THINK_BLOCK.search(content) or _UNCLOSED_THINK.search(content))
    visible = _THINK_BLOCK.sub("", content)
    visible = _UNCLOSED_THINK.sub("", visible)
    visible = _THINK_TAG.sub("", visible).strip()
    if not visible and (reasoning_present or had_thinking):
        raise ReasoningOnlyError(
            "Model produced reasoning but no final answer. Disable reasoning for this request "
            "or increase max_tokens."
        )
    if not visible:
        raise RequestRejectedError("llama-server returned no visible completion content")
    return visible


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def build_server_command(config: WNGGUFConfig, port: int) -> list[str]:
    for token in config.extra_args:
        option = token.split("=", 1)[0].lower()
        if option in _RESERVED_ARGS:
            raise ValueError(
                f"extra_server_args cannot override {option}; use the matching Config widget instead."
            )

    command = [
        config.resolved_executable(),
        "-m", config.model_path,
        "--host", config.host,
        "--port", str(port),
        "-c", str(config.context_size),
        "-ngl", "all" if config.gpu_layers == -1 else str(config.gpu_layers),
        "--jinja",
        "--no-webui",
    ]
    if config.flash_attn != "auto":
        command.extend(("--flash-attn", config.flash_attn))
    if config.cache_type_k != "f16":
        command.extend(("-ctk", config.cache_type_k))
    if config.cache_type_v != "f16":
        command.extend(("-ctv", config.cache_type_v))
    if config.mmproj_path:
        command.extend(("--mmproj", config.mmproj_path))
    if config.image_min_tokens:
        command.extend(("--image-min-tokens", str(config.image_min_tokens)))
    if config.image_max_tokens:
        command.extend(("--image-max-tokens", str(config.image_max_tokens)))
    command.extend(config.extra_args)
    return command


def child_environment(config: WNGGUFConfig) -> dict[str, str]:
    environment = os.environ.copy()
    if config.cuda_visible_devices.strip():
        environment["CUDA_VISIBLE_DEVICES"] = config.cuda_visible_devices.strip()
    return environment


def _check_interrupted() -> None:
    if _comfy_model_management is not None:
        _comfy_model_management.throw_exception_if_processing_interrupted()


def _create_windows_kill_job(process):
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        )
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
            kernel32.CloseHandle(job)
            return None
        return job
    except (AttributeError, OSError):
        log.warning("Could not attach llama-server to a Windows kill-on-close Job Object")
        return None


def _close_windows_handle(handle) -> None:
    if not handle or os.name != "nt":
        return
    try:
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
    except (AttributeError, OSError):
        pass


@dataclass
class ServerHandle:
    process: subprocess.Popen
    host: str
    port: int
    server_key: tuple
    model_path: str
    log_tail: deque[str]
    props: dict = field(default_factory=dict)
    job_handle: object = None
    keep_alive_seconds: int = 0
    idle_deadline: float | None = None
    idle_timer: threading.Timer | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def modalities(self) -> dict:
        value = self.props.get("modalities", {})
        return value if isinstance(value, dict) else {}

    def capability(self, modality: str) -> bool | None:
        raw = self.props.get("modalities")
        if not isinstance(raw, dict):
            return None
        keys = ("image", "vision") if modality == "image" else (modality,)
        present = [raw[key] for key in keys if key in raw]
        if not present:
            return None
        return any(bool(value) for value in present)

    def supports(self, modality: str) -> bool:
        """Compatibility boolean; use capability() when unknown matters."""
        return self.capability(modality) is True

    def is_alive(self) -> bool:
        return self.process.poll() is None


class LlamaServerManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._handle: ServerHandle | None = None
        atexit.register(self.stop)

    @staticmethod
    def _drain(stream, tail: deque[str], label: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                tail.append(f"{label}: {line.rstrip()}")
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def is_compatible(self, config: WNGGUFConfig) -> bool:
        with self._lock:
            return bool(
                self._handle
                and self._handle.is_alive()
                and self._handle.server_key == config.server_key()
            )

    def acquire(self, config: WNGGUFConfig) -> ServerHandle:
        config.validate()
        with self._lock:
            if self.is_compatible(config):
                handle = self._handle
                self._cancel_idle_timer(handle)
                handle.keep_alive_seconds = config.keep_alive_seconds
                log.info("Reusing llama-server PID %d", handle.process.pid)
                return handle
            self.stop()
            return self._start(config)

    def _start(self, config: WNGGUFConfig) -> ServerHandle:
        attempts = 3 if config.port == 0 else 1
        last_error = None
        for attempt in range(attempts):
            port = config.port or find_free_port(config.host)
            try:
                return self._spawn_and_wait(config, port)
            except BaseException as exc:
                last_error = exc
                self.stop()
                retryable = isinstance(exc, PortIdentityError) or self._looks_like_port_conflict(str(exc))
                if attempt + 1 >= attempts or not retryable:
                    raise
                log.warning("llama-server port %d was unavailable; retrying", port)
        raise last_error

    def _spawn_and_wait(self, config: WNGGUFConfig, port: int) -> ServerHandle:
        command = build_server_command(config, port)
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        log.info("Starting local llama-server for %s on %s:%d", config.model_path, config.host, port)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=child_environment(config),
        )
        tail: deque[str] = deque(maxlen=200)
        handle = ServerHandle(
            process,
            config.host,
            port,
            config.server_key(),
            config.model_path,
            tail,
            job_handle=_create_windows_kill_job(process),
            keep_alive_seconds=config.keep_alive_seconds,
        )
        self._handle = handle
        if process.stdout:
            threading.Thread(target=self._drain, args=(process.stdout, tail, "stdout"), daemon=True).start()
        if process.stderr:
            threading.Thread(target=self._drain, args=(process.stderr, tail, "stderr"), daemon=True).start()
        handle.props = self._wait_ready(handle, config.startup_timeout_s)
        log.info("llama-server PID %d is ready", process.pid)
        return handle

    @staticmethod
    def _looks_like_port_conflict(message: str) -> bool:
        lowered = message.lower()
        return "address already in use" in lowered or "failed to bind" in lowered

    @staticmethod
    def _wait_ready(handle: ServerHandle, timeout_s: float) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            _check_interrupted()
            if not handle.is_alive():
                time.sleep(0.05)
                detail = "\n".join(handle.log_tail)
                raise ServerFailureError(f"llama-server exited during startup.\n{detail}")
            try:
                with urllib.request.urlopen(handle.base_url + "/health", timeout=1.0) as response:
                    if response.status == 200:
                        try:
                            props = LlamaServerManager._get_json(handle.base_url + "/props", 5.0)
                        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                            log.warning("llama-server /props metadata is unavailable; capabilities are unknown: %s", exc)
                            props = {}
                        LlamaServerManager._verify_model_identity(handle.model_path, props)
                        return props
            except urllib.error.HTTPError as exc:
                if exc.code != 503:
                    time.sleep(0.25)
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                pass
            time.sleep(0.25)
        detail = "\n".join(handle.log_tail)
        raise ServerFailureError(f"llama-server was not ready after {timeout_s:.1f}s.\n{detail}")

    @staticmethod
    def _get_json(url: str, timeout_s: float) -> dict:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _verify_model_identity(requested_model: str, props: dict) -> None:
        reported = props.get("model_path")
        if not isinstance(reported, str) or not reported:
            log.warning("llama-server /props did not report model_path; model identity is unknown")
            return
        requested = Path(requested_model).resolve()
        candidate = Path(reported)
        if not candidate.is_absolute():
            candidate = candidate.resolve()
        else:
            candidate = candidate.resolve()
        if os.path.normcase(str(candidate)) != os.path.normcase(str(requested)):
            raise PortIdentityError(
                f"Port answered for a different model: expected {requested}, got {candidate}"
            )

    def request_finished(self, config: WNGGUFConfig) -> None:
        with self._lock:
            handle = self._handle
            if not handle or not handle.is_alive():
                return
            handle.keep_alive_seconds = config.keep_alive_seconds
            self._cancel_idle_timer(handle)
            if config.keep_alive_seconds > 0:
                handle.idle_deadline = time.monotonic() + config.keep_alive_seconds
                timer = threading.Timer(config.keep_alive_seconds, self._idle_expired, args=(handle,))
                timer.daemon = True
                handle.idle_timer = timer
                timer.start()

    def _idle_expired(self, expected: ServerHandle) -> None:
        with self._lock:
            if self._handle is expected and expected.idle_deadline and time.monotonic() >= expected.idle_deadline:
                self.stop()

    @staticmethod
    def _cancel_idle_timer(handle: ServerHandle | None) -> None:
        if handle and handle.idle_timer:
            handle.idle_timer.cancel()
            handle.idle_timer = None
        if handle:
            handle.idle_deadline = None

    def stop(self) -> int | None:
        with self._lock:
            handle = self._handle
            self._handle = None
            if handle is None:
                return None
            self._cancel_idle_timer(handle)
            process = handle.process
            pid = process.pid
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    if handle.job_handle:
                        _close_windows_handle(handle.job_handle)
                        handle.job_handle = None
                        try:
                            process.wait(timeout=5)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    if process.poll() is None and os.name == "nt":
                        try:
                            subprocess.run(
                                ("taskkill", "/PID", str(pid), "/T", "/F"),
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False,
                                timeout=10,
                            )
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    elif process.poll() is None:
                        try:
                            process.kill()
                        except OSError:
                            pass
                    if process.poll() is None:
                        try:
                            process.wait(timeout=5)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
            _close_windows_handle(handle.job_handle)
            log.info("Released llama-server PID %d", pid)
            return pid

    def current(self) -> ServerHandle | None:
        with self._lock:
            if self._handle and not self._handle.is_alive():
                self._cancel_idle_timer(self._handle)
                self._handle = None
            return self._handle

    @staticmethod
    def is_fatal_error(error: BaseException) -> bool:
        return not isinstance(error, RequestRejectedError) and not is_user_cancel(error)

    @staticmethod
    def health(handle: ServerHandle) -> bool:
        try:
            with urllib.request.urlopen(handle.base_url + "/health", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    @staticmethod
    def _stream_worker(request, timeout_s: float, events: queue.Queue, state: dict) -> None:
        try:
            response = urllib.request.urlopen(request, timeout=timeout_s)
            state["response"] = response
            for raw_line in response:
                events.put(("line", raw_line))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:8000]
            error_type = ServerFailureError if exc.code >= 500 else RequestRejectedError
            events.put(("error", error_type(f"llama-server HTTP {exc.code}: {detail}")))
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            events.put(("error", ServerFailureError(f"llama-server connection failed: {exc}")))
        except BaseException as exc:
            events.put(("error", ServerFailureError(f"llama-server stream failed: {exc}")))
        finally:
            response = state.get("response")
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass
            events.put(("done", None))

    @staticmethod
    def chat_completion(handle: ServerHandle, payload: dict, timeout_s: float) -> str:
        payload = dict(payload)
        payload["stream"] = True
        request = urllib.request.Request(
            handle.base_url + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        events: queue.Queue = queue.Queue()
        state: dict = {}
        worker = threading.Thread(
            target=LlamaServerManager._stream_worker,
            args=(request, timeout_s, events, state),
            daemon=True,
        )
        worker.start()
        deadline = time.monotonic() + timeout_s
        visible_parts: list[str] = []
        reasoning_present = False
        try:
            done = False
            while not done:
                _check_interrupted()
                if time.monotonic() >= deadline:
                    raise ServerFailureError(f"llama-server request timed out after {timeout_s:.1f}s")
                try:
                    kind, value = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if kind == "error":
                    raise value
                if kind == "done":
                    break
                line = value.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    done = True
                    continue
                try:
                    chunk = json.loads(data_text)
                except json.JSONDecodeError as exc:
                    raise ServerFailureError("llama-server returned malformed SSE JSON") from exc
                if "error" in chunk:
                    error_data = chunk["error"]
                    code = error_data.get("code") if isinstance(error_data, dict) else None
                    error_type = ServerFailureError if isinstance(code, int) and code >= 500 else RequestRejectedError
                    raise error_type(f"llama-server stream error: {error_data}")
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                item = choices[0]
                message = item.get("delta") or item.get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    visible_parts.append(content)
                elif isinstance(content, list):
                    visible_parts.extend(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    )
                if message.get("reasoning_content"):
                    reasoning_present = True
        except BaseException:
            response = state.get("response")
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass
            raise
        return clean_visible_content("".join(visible_parts), reasoning_present)


SERVER_MANAGER = LlamaServerManager()
