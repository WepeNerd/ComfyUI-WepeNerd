import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np


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
        expected = {
            "WN_LocalAIModel", "WN_PromptEnhancer", "WN_H3PromptEnhancer",
            "WN_ImageCaptioner", "WN_VideoCaptioner",
        }
        self.assertTrue(expected <= nodes.NODE_CLASS_MAPPINGS.keys())
        for node_id in expected:
            self.assertEqual(nodes.NODE_CLASS_MAPPINGS[node_id].CATEGORY, "WepeNerd/Local AI")
            self.assertNotIn("GGUF", nodes.NODE_DISPLAY_NAME_MAPPINGS[node_id])
        self.assertEqual(nodes.WN_LocalAIModel.RETURN_TYPES, ("GGUF_LLM_CONFIG",))
        self.assertEqual(nodes.WN_LocalAIModel.RETURN_NAMES, ("model",))

    def test_h3_prompt_enhancer_inputs_and_defaults(self):
        required = nodes.WN_H3PromptEnhancer.INPUT_TYPES()["required"]
        self.assertEqual(
            list(required),
            ["model", "prompt", "mode", "task", "action_detail", "enhancement"],
        )
        self.assertEqual(required["mode"][0], nodes.H3_MODES)
        self.assertEqual(required["task"][0], nodes.H3_TASKS)
        self.assertEqual(required["action_detail"][0], nodes.H3_ACTION_DETAIL)
        self.assertEqual(required["enhancement"][0], nodes.H3_ENHANCEMENT)
        self.assertEqual(required["mode"][1]["default"], "Auto")
        self.assertEqual(required["task"][1]["default"], "Auto")
        self.assertEqual(required["action_detail"][1]["default"], "Auto")
        self.assertEqual(required["enhancement"][1]["default"], "Smart")
        optional = nodes.WN_H3PromptEnhancer.INPUT_TYPES()["optional"]
        self.assertEqual(list(optional), ["duration_seconds", "reference_context", "max_tokens", "creative_freedom", "image", "image_role"])
        self.assertEqual(optional["creative_freedom"][0], ["Preserve", "Fill in details", "Develop scenario"])
        self.assertEqual(optional["creative_freedom"][1]["default"], "Preserve")
        self.assertEqual(optional["image"], ("IMAGE",))
        self.assertEqual(optional["image_role"][1]["default"], "Visual inspiration")
        self.assertEqual(nodes.WN_H3PromptEnhancer.RETURN_NAMES, ("enhanced_prompt",))
        self.assertEqual(nodes.NODE_DISPLAY_NAME_MAPPINGS["WN_H3PromptEnhancer"], "H3 Prompt Enhancer")

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

    def test_h3_prompt_enhancer_passes_selected_settings_and_stable_defaults(self):
        cases = [
            ("T2V", "Precise Action", "Auto", "Smart"),
            ("I2V", "Camera Movement", "Semantic", "Light"),
            ("Ref2V", "Character Replace", "Detailed Visible Mechanics", "Strict"),
            ("FL2VA", "General", "Auto", "Smart"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            for mode, task, action_detail, enhancement in cases:
                compiled = (
                    "subject_definitions: The source performer.\nsummary: [reference generation] Replace the performer.\n"
                    "retention_analysis: Preserve the supplied identity.\ndetailed_description: [Shot 1] He inserts a coin.\n"
                    if mode.startswith("Ref") else "integrated_multimodal_description: [Shot 1] He inserts a coin.\n"
                ) + "overall_soundscape: Coin contact.\nnon_diegetic_music: N/A"
                if mode == "I2V":
                    compiled = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + compiled
                if mode == "FL2VA":
                    compiled = "The target video begins from Picture 1 and reaches Picture 2 at the end of the final scene.\n\n" + compiled
                with self.subTest(mode=mode, task=task), mock.patch.object(
                    nodes, "load_skill", return_value="H3 SKILL V3"
                ) as load, mock.patch.object(nodes, "_run_payloads", return_value=[compiled]) as run:
                    result = nodes.WN_H3PromptEnhancer().enhance(
                        config,
                        "A man inserts a coin into an arcade machine.",
                        mode,
                        task,
                        action_detail,
                        enhancement,
                    )
                self.assertEqual(result, (compiled,))
                load.assert_called_once_with("h3")
                payload = run.call_args.args[1][0]
                self.assertEqual(payload["max_tokens"], 2048)
                self.assertEqual(payload["temperature"], 0.2)
                self.assertEqual(payload["top_p"], 0.8)
                self.assertEqual(payload["top_k"], 20)
                self.assertEqual(payload["min_p"], 0.0)
                self.assertEqual(payload["repeat_penalty"], 1.05)
                self.assertEqual(payload["reasoning_effort"], "none")
                self.assertEqual(payload["messages"][0], {"role": "system", "content": "H3 SKILL V3"})
                context = json.loads(payload["messages"][1]["content"])
                self.assertEqual(context["settings"], {"generation_mode": mode, "task": task,
                    "action_detail": action_detail, "enhancement": enhancement, "duration_seconds": 0.0,
                    "creative_freedom": "Preserve"})
                self.assertEqual(context["user_request"], "A man inserts a coin into an arcade machine.")
                self.assertTrue(run.call_args.kwargs["check_text_context"])

    def test_qwen38_enhancement_uses_explicit_non_thinking_and_instruct_sampling(self):
        for filename in ("Huihui-Qwen3.8-27B-abliterated-Q4_K.gguf", "Qwen3_8-27B-Q5.gguf"):
            payload = nodes._enhancement_payload(filename, "request", "skill")
            self.assertEqual(payload["temperature"], 0.7)
            self.assertEqual(payload["repeat_penalty"], 1.0)
            self.assertEqual(payload["presence_penalty"], 1.5)
            self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual(payload["reasoning_effort"], "none")
        other = nodes._enhancement_payload("Muse-Glimmer-30B.gguf", "request", "skill")
        self.assertEqual(other["temperature"], 0.2)
        self.assertNotIn("chat_template_kwargs", other)

    def test_h3_image_only_routes_each_role_and_keeps_one_prompt_output(self):
        base = "integrated_multimodal_description: [Shot 1] A toy boat glides across the pond.\noverall_soundscape: Water.\nnon_diegetic_music: N/A"
        first = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n" + base
        reference = ("subject_definitions: <Subject 1> is the boat in <Picture 1>.\n"
                     "summary: [reference generation] The boat sails.\n"
                     "retention_analysis: <Subject 1>: fully_preserved - retain its appearance.\n"
                     "detailed_description: [Shot 1] <Subject 1> glides across the pond.\n"
                     "overall_soundscape: Water.\nnon_diegetic_music: N/A")
        image = np.ones((1, 64, 96, 3), dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            for role, mode, compiled in (("Visual inspiration", "T2V", base), ("First frame", "I2V", first),
                                         ("Reference image", "Ref2V", reference)):
                with self.subTest(role=role), mock.patch.object(nodes, "_run_payloads", return_value=[compiled]) as run:
                    self.assertEqual(nodes.WN_H3PromptEnhancer().enhance(
                        config, "  ", image=image, image_role=role), (compiled,))
                payload = run.call_args.args[1][0]
                content = payload["messages"][1]["content"]
                request = json.loads(content[0]["text"])
                self.assertEqual(request["settings"]["generation_mode"], mode)
                self.assertEqual(request["settings"]["creative_freedom"], "Develop scenario")
                self.assertIn("## Creative freedom: Develop scenario", payload["messages"][0]["content"])
                self.assertNotIn("## Creative freedom: Preserve", payload["messages"][0]["content"])
                self.assertIn("Create one imaginative", request["user_request"])
                self.assertIn(role, content[2]["text"])
                self.assertEqual(content[3]["type"], "image_url")
                self.assertTrue(content[3]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
                self.assertEqual(run.call_args.kwargs, {"require_image": True, "check_text_context": False})

    def test_h3_image_text_preserves_explicit_mode_constraints_and_sampling(self):
        source = '[Shot 1] The boat stays still. A sign reads "HELLO".'
        compiled = "integrated_multimodal_description: " + source + "\noverall_soundscape: N/A\nnon_diegetic_music: N/A"
        image = np.ones((1, 64, 64, 3), dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            for role in nodes.ENHANCEMENT_IMAGE_ROLES:
                with self.subTest(role=role), mock.patch.object(nodes, "_run_payloads", return_value=[compiled]) as run:
                    nodes.WN_H3PromptEnhancer().enhance(config, source, "T2V", image=image, image_role=role,
                        creative_freedom="Fill in details", duration_seconds=5, reference_context="No camera movement.")
                payload = run.call_args.args[1][0]
                request = json.loads(payload["messages"][1]["content"][0]["text"])
                self.assertEqual(request["user_request"], source)
                self.assertEqual(request["settings"]["generation_mode"], "T2V")
                self.assertEqual(request["settings"]["creative_freedom"], "Fill in details")
                self.assertEqual(request["reference_context"], "No camera movement.")
                self.assertEqual(payload["reasoning_effort"], "none")
                with mock.patch.object(nodes, "_run_payloads", return_value=[compiled.replace("HELLO", "HI")]):
                    with self.assertRaisesRegex(ValueError, "quoted"):
                        nodes.WN_H3PromptEnhancer().enhance(config, source, "T2V", image=image, image_role=role)

    def test_generic_enhancers_accept_image_only_and_keep_all_batch_images(self):
        image = np.ones((2, 64, 64, 3), dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            calls = (
                lambda **kwargs: nodes.WN_PromptEnhancer().enhance(config, "", "H3", **kwargs),
                lambda **kwargs: nodes.WN_GGUFPromptEnhance().enhance(config, "", 1024, 0.2, 0, "minimax_h3", **kwargs),
            )
            for call in calls:
                with mock.patch.object(nodes, "_run_payloads", return_value=["one video prompt"]) as run:
                    self.assertEqual(call(image=image, image_role="Reference image"), ("one video prompt",))
                self.assertEqual(len(run.call_args.args[1]), 1)
                payload = run.call_args.args[1][0]
                content = payload["messages"][1]["content"]
                self.assertEqual(sum(part["type"] == "image_url" for part in content), 2)
                self.assertTrue(run.call_args.kwargs["require_image"])
                self.assertIn("Reference image", content[2]["text"])
                self.assertIn("Reference image", content[4]["text"])

    def test_image_enhancement_rejects_missing_projector_and_empty_text_without_image(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            text_only = config_module.WNGGUFConfig(model_path=config.model_path, server_executable=config.server_executable)
            calls = (
                lambda cfg, **kwargs: nodes.WN_H3PromptEnhancer().enhance(cfg, "", **kwargs),
                lambda cfg, **kwargs: nodes.WN_PromptEnhancer().enhance(cfg, "", **kwargs),
                lambda cfg, **kwargs: nodes.WN_GGUFPromptEnhance().enhance(cfg, "", 512, 0.2, 0, **kwargs),
            )
            for call in calls:
                with mock.patch.object(nodes, "_run_payloads") as run:
                    with self.assertRaisesRegex(ValueError, "mmproj"):
                        call(text_only, image=object())
                    with self.assertRaisesRegex(ValueError, "empty"):
                        call(config)
                    with self.assertRaisesRegex(ValueError, "image_role"):
                        call(config, image=object(), image_role="Unknown")
                    run.assert_not_called()

    def test_image_enhancement_checks_backend_vision_support(self):
        handle = mock.Mock()
        handle.capability.return_value = False
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            with mock.patch.object(nodes, "_acquire_prepared", return_value=handle), mock.patch.object(
                nodes.SERVER_MANAGER, "chat_completion"
            ) as chat, mock.patch.object(nodes, "_finish_request"):
                with self.assertRaisesRegex(server.RequestRejectedError, "image/vision"):
                    nodes.WN_H3PromptEnhancer().enhance(config, "", image=np.ones((1, 64, 64, 3)))
                chat.assert_not_called()

    def test_h3_optional_context_and_output_budget_reach_the_model(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            text = "integrated_multimodal_description: [Shot 1] A truck stands still.\noverall_soundscape: N/A\nnon_diegetic_music: N/A"
            with mock.patch.object(nodes, "_run_payloads", return_value=[text]) as run:
                nodes.WN_H3PromptEnhancer().enhance(config, "A truck", "T2V", duration_seconds=5.0,
                    reference_context="Keep the truck stationary.", max_tokens=3072)
            payload = run.call_args.args[1][0]
            self.assertEqual(payload["max_tokens"], 3072)
            request = json.loads(payload["messages"][1]["content"])
            self.assertEqual(request["settings"]["duration_seconds"], 5.0)
            self.assertEqual(request["reference_context"], "Keep the truck stationary.")
            self.assertIn("## Mode: T2V", payload["messages"][0]["content"])
            self.assertNotIn("## Mode: Ref2V", payload["messages"][0]["content"])

    def test_h3_invalid_settings_fail_before_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            for kwargs in ({"mode": "Other"}, {"creative_freedom": "Other"},
                           {"duration_seconds": -1}, {"duration_seconds": float("nan")}):
                with self.subTest(kwargs=kwargs), mock.patch.object(nodes, "_run_payloads") as run:
                    with self.assertRaises(ValueError):
                        nodes.WN_H3PromptEnhancer().enhance(config, "A truck", **kwargs)
                    run.assert_not_called()

    def test_h3_expansion_keeps_literal_and_scene_validation(self):
        source = '[Shot 1] A traveler says: <d>[English] Stay here.</d>'
        valid = ('integrated_multimodal_description: ' + source + ' Mist surrounds the traveler.\n'
                 'overall_soundscape: Wind.\nnon_diegetic_music: N/A')
        with tempfile.TemporaryDirectory() as directory:
            config = config_in(Path(directory))
            for freedom in ("Fill in details", "Develop scenario"):
                for enhancement in ("Light", "Strict"):
                    with self.subTest(freedom=freedom, enhancement=enhancement):
                        with mock.patch.object(nodes, "_run_payloads", return_value=[valid]) as run:
                            self.assertEqual(nodes.WN_H3PromptEnhancer().enhance(
                                config, source, "T2V", enhancement=enhancement,
                                creative_freedom=freedom), (valid,))
                        payload = run.call_args.args[1][0]
                        settings = json.loads(payload["messages"][1]["content"])["settings"]
                        self.assertEqual(settings["creative_freedom"], freedom)
                        self.assertEqual(settings["enhancement"], enhancement)
                        for broken in (valid.replace("Stay here.", "Follow me."),
                                       valid.replace("Mist surrounds", "\n[Shot 2] Mist surrounds")):
                            with mock.patch.object(nodes, "_run_payloads", return_value=[broken]):
                                with self.assertRaises(ValueError):
                                    nodes.WN_H3PromptEnhancer().enhance(
                                        config, source, "T2V", enhancement=enhancement,
                                        creative_freedom=freedom)

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
