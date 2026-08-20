"""Video byte preparation and chronological frame sampling."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from .wn_gguf_image import encode_single_image, image_batch_to_numpy


log = logging.getLogger("ComfyUI-WepeNerd.LocalAI")
SAFE_COMPONENT_FRAME_LIMIT = 4096


def uniform_sample_indices(frame_count: int, requested: int, max_frames: int) -> list[int]:
    if frame_count < 1:
        raise ValueError("Video contains no frames")
    count = min(frame_count, max(2, int(requested)), max(2, int(max_frames)))
    if count >= frame_count:
        return list(range(frame_count))
    indices = [round(i * (frame_count - 1) / (count - 1)) for i in range(count)]
    return list(dict.fromkeys(indices))


def fixed_fps_sample_indices(
    frame_count: int,
    source_fps: float,
    sample_fps: float,
    max_frames: int,
) -> list[int]:
    if frame_count < 1:
        raise ValueError("Video contains no frames")
    if source_fps <= 0 or sample_fps <= 0:
        raise ValueError("Source FPS and sample_fps must be positive")
    step = source_fps / sample_fps
    indices = [0]
    position = step
    while position < frame_count - 1:
        indices.append(min(frame_count - 1, round(position)))
        position += step
    if frame_count > 1:
        indices.append(frame_count - 1)
    indices = list(dict.fromkeys(indices))
    limit = max(2, int(max_frames))
    if len(indices) > limit:
        positions = uniform_sample_indices(len(indices), limit, limit)
        indices = [indices[position] for position in positions]
    return indices


def timestamps_for_indices(indices: list[int], source_fps: float) -> list[float]:
    fps = source_fps if source_fps > 0 else 1.0
    return [index / fps for index in indices]


def video_metadata(video) -> dict:
    result = {"fps": None, "duration": None, "frame_count": None}
    for key, method_name in (
        ("fps", "get_frame_rate"),
        ("duration", "get_duration"),
        ("frame_count", "get_frame_count"),
    ):
        try:
            value = getattr(video, method_name)()
            result[key] = float(value) if key != "frame_count" else int(value)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, ZeroDivisionError):
            pass
    return result


def prepare_sampled_frames(
    video,
    sampling_mode: str,
    sample_frames: int,
    sample_fps: float,
    max_frames: int,
    image_max_edge: int,
    jpeg_quality: int,
) -> dict:
    stream_error = None
    try:
        source = video.get_stream_source()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        source = None
    if isinstance(source, (str, Path)) or (
        source is not None and hasattr(source, "read") and hasattr(source, "seek")
    ):
        try:
            return _prepare_seek_sampled_frames(
                video, source, sampling_mode, sample_frames, sample_fps, max_frames,
                image_max_edge, jpeg_quality,
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            stream_error = exc

    metadata = video_metadata(video)
    reported_count = metadata.get("frame_count")
    if reported_count is None:
        detail = f" PyAV error: {stream_error}" if stream_error else ""
        raise ValueError(
            "Cannot safely sample this non-seekable VIDEO because its frame count is unknown. "
            f"Use a file-backed VIDEO or a clip reporting at most {SAFE_COMPONENT_FRAME_LIMIT} frames.{detail}"
        )
    if int(reported_count) > SAFE_COMPONENT_FRAME_LIMIT:
        detail = f" PyAV error: {stream_error}" if stream_error else ""
        raise ValueError(
            f"Refusing to materialize {reported_count} video frames in RAM. Use a seekable, "
            f"file-backed VIDEO for clips over {SAFE_COMPONENT_FRAME_LIMIT} frames.{detail}"
        )
    return _prepare_component_sampled_frames(
        video, sampling_mode, sample_frames, sample_fps, max_frames,
        image_max_edge, jpeg_quality,
    )


def _sample_indices(
    sampling_mode: str,
    frame_count: int,
    source_fps: float,
    sample_frames: int,
    sample_fps: float,
    max_frames: int,
) -> list[int]:
    if sampling_mode == "uniform":
        return uniform_sample_indices(frame_count, sample_frames, max_frames)
    if sampling_mode == "fixed_fps":
        return fixed_fps_sample_indices(frame_count, source_fps, sample_fps, max_frames)
    raise ValueError(f"Unknown video sampling mode: {sampling_mode}")


def _prepare_component_sampled_frames(
    video, sampling_mode, sample_frames, sample_fps, max_frames,
    image_max_edge, jpeg_quality,
) -> dict:
    components = video.get_components()
    frames = image_batch_to_numpy(components.images)
    frame_count = int(frames.shape[0])
    if frame_count > SAFE_COMPONENT_FRAME_LIMIT:
        raise ValueError(
            f"Refusing to materialize {frame_count} video frames in RAM; use a file-backed VIDEO."
        )
    try:
        source_fps = float(components.frame_rate)
    except (TypeError, ValueError, ZeroDivisionError):
        source_fps = 1.0
    if source_fps <= 0:
        source_fps = 1.0
    indices = _sample_indices(
        sampling_mode, frame_count, source_fps, sample_frames, sample_fps, max_frames
    )
    timestamps = timestamps_for_indices(indices, source_fps)
    urls = [
        encode_single_image(frames[index], image_max_edge, "JPEG", jpeg_quality)
        for index in indices
    ]
    return {
        "urls": urls,
        "indices": indices,
        "timestamps": timestamps,
        "fps": source_fps,
        "frame_count": frame_count,
        "duration": frame_count / source_fps,
        "extraction": "components",
    }


def _positive_rate(*values) -> float | None:
    for value in values:
        try:
            rate = float(value)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    return None


def _prepare_seek_sampled_frames(
    video, source, sampling_mode, sample_frames, sample_fps, max_frames,
    image_max_edge, jpeg_quality,
) -> dict:
    try:
        import av
    except ImportError as exc:
        raise ImportError("PyAV is required for memory-safe file-backed video sampling") from exc

    original_position = None
    if hasattr(source, "tell"):
        try:
            original_position = source.tell()
            source.seek(0)
        except (OSError, ValueError):
            original_position = None
    container = None
    try:
        container = av.open(str(source) if isinstance(source, Path) else source)
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("VIDEO source contains no video stream")

        source_fps = _positive_rate(
            getattr(stream, "average_rate", None),
            getattr(stream, "base_rate", None),
            getattr(stream, "guessed_rate", None),
            video_metadata(video).get("fps"),
        ) or 1.0
        start_time = 0.0
        trim_duration = 0.0
        try:
            start_time, trim_duration = video.get_active_trim_window()
            start_time = max(0.0, float(start_time or 0.0))
            trim_duration = max(0.0, float(trim_duration or 0.0))
        except (AttributeError, TypeError, ValueError):
            pass

        stream_duration = None
        if getattr(stream, "duration", None) is not None and getattr(stream, "time_base", None):
            stream_duration = float(stream.duration * stream.time_base)
        elif getattr(container, "duration", None):
            stream_duration = float(container.duration / av.time_base)
        duration = trim_duration or stream_duration
        frame_count = int(getattr(stream, "frames", 0) or 0)
        if trim_duration:
            frame_count = max(1, round(trim_duration * source_fps))
        elif frame_count < 1 and duration:
            frame_count = max(1, round(duration * source_fps))
        if frame_count < 1:
            raise ValueError("VIDEO stream does not report enough duration/frame metadata for seek sampling")
        if not duration:
            duration = frame_count / source_fps

        indices = _sample_indices(
            sampling_mode, frame_count, source_fps, sample_frames, sample_fps, max_frames
        )
        target_times = timestamps_for_indices(indices, source_fps)
        urls = []
        actual_times = []
        time_base = float(stream.time_base)
        for relative_time in target_times:
            absolute_time = start_time + relative_time
            seek_time = max(start_time, absolute_time - max(1.0 / source_fps, 0.05))
            container.seek(max(0, int(seek_time / time_base)), stream=stream, backward=True)
            selected = None
            selected_time = absolute_time
            for frame in container.decode(stream):
                frame_time = float(frame.time) if frame.time is not None else absolute_time
                selected = frame
                selected_time = frame_time
                if frame_time >= absolute_time - (0.5 / source_fps):
                    break
                if frame_time > absolute_time + 2.0:
                    break
            if selected is None:
                raise ValueError(f"Could not decode a video frame near {relative_time:.3f}s")
            rgb = selected.to_ndarray(format="rgb24").astype("float32") / 255.0
            urls.append(encode_single_image(rgb, image_max_edge, "JPEG", jpeg_quality))
            actual_times.append(max(0.0, selected_time - start_time))

        return {
            "urls": urls,
            "indices": indices,
            "timestamps": actual_times,
            "fps": source_fps,
            "frame_count": frame_count,
            "duration": duration,
            "extraction": "pyav_seek",
        }
    finally:
        if container is not None:
            container.close()
        if original_position is not None:
            try:
                source.seek(original_position)
            except (OSError, ValueError):
                pass


def _read_limited(stream, limit_bytes: int) -> bytes:
    data = stream.read(limit_bytes + 1)
    if len(data) > limit_bytes:
        raise ValueError(
            f"Native video payload exceeds the configured {limit_bytes // (1024 * 1024)} MB limit"
        )
    return data


def prepare_native_video(video, max_payload_mb: int) -> dict:
    limit_bytes = int(max_payload_mb) * 1024 * 1024
    if limit_bytes < 1:
        raise ValueError("Native video payload limit must be positive")

    try:
        start_time, duration = video.get_active_trim_window()
    except (AttributeError, TypeError, ValueError):
        start_time, duration = 0.0, 0.0

    if start_time or duration:
        buffer = io.BytesIO()
        video.save_to(buffer)
        buffer.seek(0)
        data = _read_limited(buffer, limit_bytes)
    else:
        source = video.get_stream_source()
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError(f"Video source was not found: {path}")
            if path.stat().st_size > limit_bytes:
                raise ValueError(
                    f"Native video payload exceeds the configured {max_payload_mb} MB limit"
                )
            with path.open("rb") as stream:
                data = _read_limited(stream, limit_bytes)
        elif hasattr(source, "read"):
            original_position = source.tell() if hasattr(source, "tell") else None
            try:
                if hasattr(source, "seek"):
                    source.seek(0)
                data = _read_limited(source, limit_bytes)
            finally:
                if original_position is not None and hasattr(source, "seek"):
                    source.seek(original_position)
        else:
            raise TypeError("VIDEO stream source must be a file path or binary stream")

    if not data:
        raise ValueError("Video source is empty")
    return {
        "base64": base64.b64encode(data).decode("ascii"),
        "size_bytes": len(data),
        **video_metadata(video),
    }
