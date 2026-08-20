import importlib
import unittest


payloads = importlib.import_module("wepenerd_testpkg.wn_gguf_payloads")


class PayloadTests(unittest.TestCase):
    def test_text_payload_includes_modern_sampler_fields(self):
        payload = payloads.build_chat_payload(
            "hello",
            system_prompt="system",
            max_tokens=10,
            repetition_penalty=1.2,
            min_p=0.05,
            presence_penalty=0.2,
            frequency_penalty=0.1,
            reasoning_effort="none",
            seed=4,
        )
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "hello"})
        self.assertEqual(payload["repeat_penalty"], 1.2)
        self.assertEqual(payload["min_p"], 0.05)
        self.assertEqual(payload["presence_penalty"], 0.2)
        self.assertEqual(payload["frequency_penalty"], 0.1)
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertTrue(payload["stream"])

    def test_single_image_payload_is_openai_compatible(self):
        payload = payloads.build_chat_payload(
            "describe", image_data_url="data:image/jpeg;base64,abc"
        )
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "describe"})
        self.assertEqual(content[1]["type"], "image_url")

    def test_sampled_video_payload_is_chronological(self):
        content = payloads.sampled_video_content(
            "describe", ["data:a", "data:b"], [0.0, 1.25]
        )
        self.assertIn("chronological", content[0]["text"])
        self.assertEqual(content[1]["text"], "Frame 1 — 0.00 s")
        self.assertEqual(content[2]["image_url"]["url"], "data:a")
        self.assertEqual(content[3]["text"], "Frame 2 — 1.25 s")
        self.assertEqual(content[4]["image_url"]["url"], "data:b")

    def test_native_video_payload(self):
        content = payloads.native_video_content("describe", "YWJj")
        self.assertEqual(content[1], {"type": "input_video", "input_video": {"data": "YWJj"}})

    def test_empty_prompt_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            payloads.build_chat_payload("  ")
