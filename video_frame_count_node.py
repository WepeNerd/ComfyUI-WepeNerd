import hashlib
import json
import os
import re
import shutil
import subprocess
from fractions import Fraction


class WN_VideoExactFramesFPS:
    """
    Create a video with an exact frame count and frame rate.

    The default mode decodes frames and writes a lossless file so the output is
    exact without adding generational loss. The stream-copy mode avoids video
    re-encoding, but container FPS metadata may not be exact for every source.
    """

    QUALITY_MODES = [
        "lossless exact (FFV1/MKV)",
        "lossless exact (H.264 RGB/MP4)",
        "stream copy best effort (no re-encode)",
    ]
    EXTENSION_MODES = ["hold_last_frame", "loop_source"]
    AUDIO_MODES = ["drop_audio", "copy_trim_audio"]

    @classmethod
    def INPUT_TYPES(cls):
        files = []
        try:
            import folder_paths

            input_dir = folder_paths.get_input_directory()
            files = [
                f
                for f in os.listdir(input_dir)
                if os.path.isfile(os.path.join(input_dir, f))
            ]
            files = folder_paths.filter_files_content_types(files, ["video"])
        except Exception:
            files = []

        if not files:
            files = [""]

        return {
            "required": {
                "video_file": (sorted(files), {"video_upload": True}),
                "target_frame_count": (
                    "INT",
                    {"default": 81, "min": 1, "max": 1000000, "step": 1},
                ),
                "target_fps": (
                    "FLOAT",
                    {"default": 24.0, "min": 0.001, "max": 1000.0, "step": 0.001},
                ),
                "quality_mode": (cls.QUALITY_MODES, {"default": cls.QUALITY_MODES[0]}),
                "extension_mode": (cls.EXTENSION_MODES, {"default": "hold_last_frame"}),
                "audio_mode": (cls.AUDIO_MODES, {"default": "drop_audio"}),
                "filename_prefix": (
                    "STRING",
                    {"default": "wepenerd/exact_video", "multiline": False},
                ),
            },
            "optional": {
                "video": ("VIDEO",),
            },
        }

    RETURN_TYPES = ("VIDEO", "STRING", "STRING")
    RETURN_NAMES = ("video", "output_path", "info")
    FUNCTION = "process"
    CATEGORY = "WepeNerd/Video"
    OUTPUT_NODE = False

    @staticmethod
    def _ffmpeg_bin(name):
        path = shutil.which(name)
        if not path:
            raise RuntimeError(
                f"WepeNerd Exact Video needs {name} on PATH. Install FFmpeg and restart ComfyUI."
            )
        return path

    @staticmethod
    def _run(command):
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            tail = "\n".join(result.stderr.splitlines()[-24:])
            raise RuntimeError(f"FFmpeg failed with exit code {result.returncode}:\n{tail}")
        return result

    @staticmethod
    def _fraction_text(value):
        fraction = Fraction(str(float(value))).limit_denominator(1000000)
        return f"{fraction.numerator}/{fraction.denominator}"

    @staticmethod
    def _fraction_float(value):
        if not value or value == "0/0":
            return 0.0
        try:
            return float(Fraction(value))
        except Exception:
            return 0.0

    @classmethod
    def _probe(cls, path):
        ffprobe = cls._ffmpeg_bin("ffprobe")
        command = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=codec_name,width,height,nb_frames,nb_read_packets,avg_frame_rate,r_frame_rate,duration",
            "-of",
            "json",
            path,
        ]
        result = cls._run(command)
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            raise ValueError(f"No video stream found in: {path}")

        stream = streams[0]
        frame_text = stream.get("nb_read_packets") or stream.get("nb_frames") or "0"
        try:
            frame_count = int(frame_text)
        except Exception:
            frame_count = 0

        return {
            "codec": stream.get("codec_name") or "unknown",
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "frames": frame_count,
            "avg_fps_text": stream.get("avg_frame_rate") or "0/0",
            "r_fps_text": stream.get("r_frame_rate") or "0/0",
            "duration": float(stream.get("duration") or 0.0),
        }

    @staticmethod
    def _safe_prefix(filename_prefix):
        cleaned = (filename_prefix or "wepenerd/exact_video").replace("\\", "/")
        parts = []
        for part in cleaned.split("/"):
            part = re.sub(r"[^A-Za-z0-9._ -]+", "_", part).strip(" .")
            if part:
                parts.append(part)
        return parts or ["wepenerd", "exact_video"]

    @classmethod
    def _output_path(cls, filename_prefix, extension):
        import folder_paths

        output_root = folder_paths.get_output_directory()
        parts = cls._safe_prefix(filename_prefix)
        subfolder = os.path.join(*parts[:-1]) if len(parts) > 1 else ""
        base = parts[-1]
        output_dir = os.path.realpath(os.path.join(output_root, subfolder))
        os.makedirs(output_dir, exist_ok=True)

        index = 1
        while True:
            path = os.path.join(output_dir, f"{base}_{index:05d}.{extension}")
            if not os.path.exists(path):
                return path
            index += 1

    @staticmethod
    def _resolve_video_file(video_file, video):
        if video is not None and hasattr(video, "get_stream_source"):
            source = video.get_stream_source()
            if isinstance(source, str):
                return os.path.realpath(os.path.expanduser(source))
            raise ValueError(
                "Connected VIDEO inputs must come from a file-backed video for exact FFmpeg processing. "
                "Use ComfyUI's Load Video node or the video_file widget."
            )

        if not video_file:
            raise ValueError("Choose a video file or connect a file-backed VIDEO input.")

        try:
            import folder_paths

            path = folder_paths.get_annotated_filepath(video_file)
        except Exception:
            path = video_file

        path = os.path.realpath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise ValueError(f"Video file not found: {video_file}")
        return path

    @classmethod
    def _build_lossless_command(
        cls,
        source_path,
        output_path,
        target_frame_count,
        target_fps,
        quality_mode,
        extension_mode,
        audio_mode,
    ):
        ffmpeg = cls._ffmpeg_bin("ffmpeg")
        fps_expr = cls._fraction_text(target_fps)
        target_duration = target_frame_count / float(target_fps)

        command = [ffmpeg, "-y", "-hide_banner"]
        if extension_mode == "loop_source":
            command += ["-stream_loop", "-1"]
        command += ["-i", source_path, "-map", "0:v:0"]

        if audio_mode == "copy_trim_audio":
            command += ["-map", "0:a?"]
        else:
            command += ["-an"]

        filters = [f"fps=fps={fps_expr}"]
        if extension_mode == "hold_last_frame":
            filters.append(f"tpad=stop_mode=clone:stop_duration={target_duration + 2.0:.6f}")
        filters += [
            f"trim=start_frame=0:end_frame={int(target_frame_count)}",
            f"setpts=N/({fps_expr}*TB)",
        ]
        command += ["-vf", ",".join(filters)]

        if quality_mode.startswith("lossless exact (H.264"):
            command += ["-c:v", "libx264rgb", "-crf", "0", "-preset", "veryslow"]
            command += ["-movflags", "+faststart", "-r", fps_expr, "-fps_mode", "cfr"]
        else:
            command += ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slicecrc", "1"]

        if audio_mode == "copy_trim_audio":
            command += ["-c:a", "copy", "-t", f"{target_duration:.9f}", "-shortest"]

        command += [output_path]
        return command

    @classmethod
    def _build_stream_copy_command(
        cls,
        source_path,
        output_path,
        target_frame_count,
        target_fps,
        extension_mode,
        audio_mode,
    ):
        ffmpeg = cls._ffmpeg_bin("ffmpeg")

        command = [ffmpeg, "-y", "-hide_banner"]
        if extension_mode == "loop_source":
            command += ["-stream_loop", "-1"]
        command += ["-i", source_path, "-map", "0:v:0"]

        if audio_mode == "copy_trim_audio":
            command += ["-map", "0:a?"]
        else:
            command += ["-an"]

        command += [
            "-c",
            "copy",
            "-frames:v",
            str(int(target_frame_count)),
            "-avoid_negative_ts",
            "make_zero",
        ]
        if audio_mode == "copy_trim_audio":
            command += ["-shortest"]
        command += [output_path]
        return command

    def process(
        self,
        video_file,
        target_frame_count,
        target_fps,
        quality_mode,
        extension_mode,
        audio_mode,
        filename_prefix,
        video=None,
    ):
        if target_frame_count < 1:
            raise ValueError("target_frame_count must be at least 1.")
        if target_fps <= 0:
            raise ValueError("target_fps must be greater than 0.")

        source_path = self._resolve_video_file(video_file, video)
        source_probe = self._probe(source_path)

        if quality_mode.startswith("lossless exact (FFV1"):
            extension = "mkv"
        elif quality_mode.startswith("lossless exact (H.264"):
            extension = "mp4"
        else:
            extension = os.path.splitext(source_path)[1].lstrip(".") or "mkv"

        output_path = self._output_path(filename_prefix, extension)

        if quality_mode.startswith("stream copy"):
            command = self._build_stream_copy_command(
                source_path,
                output_path,
                target_frame_count,
                target_fps,
                extension_mode,
                audio_mode,
            )
        else:
            command = self._build_lossless_command(
                source_path,
                output_path,
                target_frame_count,
                target_fps,
                quality_mode,
                extension_mode,
                audio_mode,
            )

        self._run(command)
        output_probe = self._probe(output_path)

        if output_probe["frames"] != int(target_frame_count):
            raise RuntimeError(
                "Output verification failed: "
                f"expected {target_frame_count} frames, got {output_probe['frames']}."
            )

        output_fps = self._fraction_float(output_probe["avg_fps_text"])
        fps_delta = abs(output_fps - float(target_fps))
        if not quality_mode.startswith("stream copy") and fps_delta > 0.001:
            raise RuntimeError(
                "Output verification failed: "
                f"expected {target_fps:g} fps, got {output_probe['avg_fps_text']}."
            )

        try:
            from comfy_api.latest import InputImpl

            video_output = InputImpl.VideoFromFile(output_path)
        except Exception as exc:
            raise RuntimeError(
                "Could not create ComfyUI VIDEO output from the generated file."
            ) from exc

        stream_note = ""
        if quality_mode.startswith("stream copy"):
            stream_note = (
                "\nMode note: stream copy does not re-encode video packets, but many containers "
                "keep the source packet timing. Exact frame count is verified; exact FPS metadata "
                "is best effort in this mode."
            )
        else:
            stream_note = (
                "\nMode note: the video is losslessly encoded for exact frame count and FPS. "
                "This avoids generational image loss, but the compressed video stream is rewritten."
            )

        info = (
            f"Source: {os.path.basename(source_path)}\n"
            f"Source video: {source_probe['width']}x{source_probe['height']}, "
            f"{source_probe['frames']} frames, {source_probe['avg_fps_text']} fps, "
            f"{source_probe['codec']}\n"
            f"Output: {output_path}\n"
            f"Output video: {output_probe['width']}x{output_probe['height']}, "
            f"{output_probe['frames']} frames, {output_probe['avg_fps_text']} fps, "
            f"{output_probe['codec']}\n"
            f"Target: {int(target_frame_count)} frames at {float(target_fps):g} fps"
            f"{stream_note}"
        )

        return (video_output, output_path, info)

    @classmethod
    def IS_CHANGED(
        cls,
        video_file,
        target_frame_count,
        target_fps,
        quality_mode,
        extension_mode,
        audio_mode,
        filename_prefix,
        video=None,
    ):
        try:
            path = cls._resolve_video_file(video_file, video)
            stat = os.stat(path)
            payload = (
                path,
                stat.st_mtime_ns,
                stat.st_size,
                target_frame_count,
                target_fps,
                quality_mode,
                extension_mode,
                audio_mode,
                filename_prefix,
            )
        except Exception:
            payload = (
                video_file,
                target_frame_count,
                target_fps,
                quality_mode,
                extension_mode,
                audio_mode,
                filename_prefix,
            )
        return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()
