import importlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock


config_module = importlib.import_module("wepenerd_testpkg.wn_gguf_config")
nodes = importlib.import_module("wepenerd_testpkg.wn_gguf_nodes")
server = importlib.import_module("wepenerd_testpkg.wn_gguf_server")


def config_in(root, release=True):
    model = root / "model.gguf"
    projector = root / "mmproj.gguf"
    executable = root / "llama-server.exe"
    for path in (model, projector, executable):
        path.write_bytes(b"")
    return config_module.WNGGUFConfig(
        model_path=str(model),
        mmproj_path=str(projector),
        server_executable=str(executable),
        release_after_generate=release,
    )


class NodeCompatibilityTests(unittest.TestCase):
    def test_existing_ids_and_new_video_id_are_registered(self):
        expected = {
            "WN_GGUFLLMConfig", "WN_GGUFLLMGenerate", "WN_GGUFPromptEnhance",
            "WN_GGUFCaptionImage", "WN_GGUFLLMRelease", "WN_GGUFLLMStatus",
            "WN_GGUFCaptionVideo",
        }
        self.assertTrue(expected <= nodes.NODE_CLASS_MAPPINGS.keys())

    def test_clean_local_ai_nodes_are_registered_and_use_clean_category(self):
        expected = {"WN_LocalAIModel", "WN_PromptEnhancer", "WN_ImageCaptioner", "WN_VideoCaptioner"}
        self.assertTrue(expected <= nodes.NODE_CLASS_MAPPINGS.keys())
        for node_id in expected:
            self.assertEqual(nodes.NODE_CLASS_MAPPINGS[node_id].CATEGORY, "WepeNerd/Local AI")
            self.assertNotIn("GGUF", nodes.NODE_DISPLAY_NAME_MAPPINGS[node_id])
        self.assertEqual(nodes.WN_LocalAIModel.RETURN_TYPES, ("GGUF_LLM_CONFIG",))
        self.assertEqual(nodes.WN_LocalAIModel.RETURN_NAMES, ("model",))

    def test_clean_model_builds_existing_config_type_with_safe_release(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = config_in(Path(directory))
            with mock.patch.object(
                nodes, "resolve_choice", side_effect=[existing.model_path, existing.mmproj_path]
            ), mock.patch.object(config_module.WNGGUFConfig, "validate"):
                built, = nodes.WN_LocalAIModel().build("LLM/model.gguf", "LLM/mmproj.gguf")
            self.assertIsInstance(built, config_module.WNGGUFConfig)
            self.assertTrue(built.release_after_generate)
            self.assertEqual(built.server_executable, "auto")

    def test_existing_required_input_prefixes_are_preserved(self):
        generate = list(nodes.WN_GGUFLLMGenerate.INPUT_TYPES()["required"])
        enhance = list(nodes.WN_GGUFPromptEnhance.INPUT_TYPES()["required"])
        caption = list(nodes.WN_GGUFCaptionImage.INPUT_TYPES()["required"])
        self.assertEqual(
            generate[:9],
            ["config", "prompt", "system_prompt", "max_tokens", "temperature", "top_p", "top_k", "repetition_penalty", "seed"],
        )
        self.assertEqual(enhance[:5], ["config", "prompt", "max_tokens", "temperature", "seed"])
        self.assertEqual(caption[:6], ["config", "image", "instruction", "max_tokens", "temperature", "seed"])

    def test_invalid_prompt_never_reaches_server_acquisition(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            with mock.patch.object(nodes, "_run_payloads") as run:
                with self.assertRaisesRegex(ValueError, "empty"):
                    nodes.WN_GGUFLLMGenerate().generate(
                        config, "", "", 10, 0.7, 0.9, 20, 1.0, 0
                    )
                run.assert_not_called()

    def test_image_batch_builds_all_payloads_for_one_run(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            with mock.patch.object(nodes, "encode_image_batch", return_value=["data:a", "data:b"]), mock.patch.object(
                nodes, "_run_payloads", return_value=["one", "two"]
            ) as run:
                result = nodes.WN_GGUFCaptionImage().caption(
                    config, object(), "describe", 100, 0.2, 0
                )
            self.assertEqual(result, (["one", "two"],))
            self.assertEqual(len(run.call_args.args[1]), 2)
            self.assertTrue(nodes.WN_GGUFCaptionImage.OUTPUT_IS_LIST[0])

    def test_batch_payloads_acquire_once_and_update_progress(self):
        class Handle:
            @staticmethod
            def capability(modality):
                return True

        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            progress = mock.Mock()
            with mock.patch.object(nodes, "_acquire_prepared", return_value=Handle()) as acquire, mock.patch.object(
                nodes, "_progress_bar", return_value=progress
            ), mock.patch.object(
                nodes.SERVER_MANAGER, "chat_completion", side_effect=["one", "two"]
            ), mock.patch.object(nodes, "_finish_request"):
                results = nodes._run_payloads(config, [{}, {}], require_image=True)
            self.assertEqual(results, ["one", "two"])
            acquire.assert_called_once_with(config)
            self.assertEqual(progress.update.call_count, 2)

    def test_clean_prompt_enhancer_routes_skills_and_forces_no_reasoning(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            with mock.patch.object(nodes, "load_skill", return_value="SKILL TEXT") as load, mock.patch.object(
                nodes, "_run_payloads", return_value=["enhanced"]
            ) as run:
                result = nodes.WN_PromptEnhancer().enhance(config, "raw prompt", "Krea 2")
            self.assertEqual(result, ("enhanced",))
            load.assert_called_once_with("krea2")
            payload = run.call_args.args[1][0]
            self.assertEqual(payload["reasoning_effort"], "none")
            self.assertEqual(payload["messages"][0]["content"], "SKILL TEXT")

    def test_system_override_wins_over_bundled_legacy_skill(self):
        with mock.patch.object(nodes, "load_skill") as load:
            self.assertEqual(
                nodes._style_prompt("minimax_h3", nodes.PROMPT_STYLES, "my override"),
                "my override",
            )
            load.assert_not_called()

    def test_caption_cleanup_preserves_commas_inside_newline_banned_phrase(self):
        cleaned = nodes._clean_caption(
            "red, blue object, watermark", "booru_tags", "prefix", "red, blue\nwatermark"
        )
        self.assertEqual(cleaned, "prefix, object")

    def test_keep_alive_preserves_server_for_request_error_but_not_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory), release=False)
            with mock.patch.object(nodes.SERVER_MANAGER, "stop") as stop, mock.patch.object(
                nodes.SERVER_MANAGER, "request_finished"
            ) as finished:
                nodes._finish_request(config, server.RequestRejectedError("bad input"))
                stop.assert_not_called()
                finished.assert_called_once_with(config)
            with mock.patch.object(nodes.SERVER_MANAGER, "stop") as stop:
                nodes._finish_request(config, server.ServerFailureError("transport"))
                stop.assert_called_once()

    def test_default_release_stops_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory), release=True)
            with mock.patch.object(nodes.SERVER_MANAGER, "stop") as stop:
                nodes._finish_request(config)
                stop.assert_called_once()

    def test_video_auto_falls_back_from_native_rejection_to_sampled_frames(self):
        class Handle:
            @staticmethod
            def supports(modality):
                return modality in ("video", "image")

        sampled = {
            "urls": ["data:a", "data:b"],
            "indices": [0, 10],
            "timestamps": [0.0, 1.0],
            "fps": 10.0,
            "frame_count": 11,
            "duration": 1.1,
        }
        native = {"base64": "YWJj", "size_bytes": 3, "fps": 10.0, "duration": 1.1, "frame_count": 11}
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            with mock.patch.object(nodes, "prepare_native_video", return_value=native), mock.patch.object(
                nodes, "prepare_sampled_frames", return_value=sampled
            ), mock.patch.object(nodes, "_acquire_prepared", return_value=Handle()), mock.patch.object(
                nodes.SERVER_MANAGER,
                "chat_completion",
                side_effect=[server.RequestRejectedError("native rejected"), "sampled caption"],
            ) as chat, mock.patch.object(nodes, "_finish_request"):
                caption, info = nodes.WN_GGUFCaptionVideo().caption(
                    config,
                    object(),
                    "describe",
                    "dataset_natural",
                    "auto",
                    "uniform",
                    12,
                    2.0,
                    24,
                    100,
                    0.2,
                    0,
                )
            self.assertEqual(caption, "sampled caption")
            self.assertIn("mode=sampled_frames", info)
            self.assertIn("timestamps=0.00s, 1.00s", info)
            self.assertEqual(chat.call_count, 2)

    def test_video_auto_native_success_does_not_prepare_sampled_frames(self):
        class Handle:
            @staticmethod
            def capability(modality):
                return True

        native = {"base64": "YWJj", "size_bytes": 3, "fps": 10.0, "duration": 1.0, "frame_count": 10}
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            with mock.patch.object(nodes, "prepare_native_video", return_value=native), mock.patch.object(
                nodes, "prepare_sampled_frames"
            ) as sampled, mock.patch.object(nodes, "_acquire_prepared", return_value=Handle()), mock.patch.object(
                nodes.SERVER_MANAGER, "chat_completion", return_value="native caption"
            ), mock.patch.object(nodes, "_finish_request"):
                result = nodes._caption_video_request(
                    config, object(), "describe", "dataset_natural", "auto", "uniform",
                    12, 2.0, 24, 100, 0.2, 0,
                )
            self.assertIn("mode=native_video", result[1])
            sampled.assert_not_called()

    def test_unknown_video_capability_uses_only_sampled_frames(self):
        class Handle:
            @staticmethod
            def capability(modality):
                return None

        sampled_media = {
            "urls": ["data:a"], "indices": [0], "timestamps": [0.0],
            "fps": 1.0, "frame_count": 1, "duration": 1.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            with mock.patch.object(nodes, "prepare_native_video") as native, mock.patch.object(
                nodes, "prepare_sampled_frames", return_value=sampled_media
            ), mock.patch.object(nodes, "_acquire_prepared", return_value=Handle()), mock.patch.object(
                nodes.SERVER_MANAGER, "chat_completion", return_value="sampled caption"
            ), mock.patch.object(nodes, "_finish_request"):
                result = nodes._caption_video_request(
                    config, object(), "describe", "dataset_natural", "auto", "uniform",
                    12, 2.0, 24, 100, 0.2, 0,
                )
            self.assertIn("mode=sampled_frames", result[1])
            native.assert_not_called()
