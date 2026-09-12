import unittest

from wepenerd_testpkg.h3_prompt import validate_h3_prompt, select_h3_skill
from wepenerd_testpkg.wn_gguf_skills import load_skill


BASE = "integrated_multimodal_description: [Shot 1] A woman walks through the alley.\noverall_soundscape: Footsteps.\nnon_diegetic_music: N/A"
REF = (
    "subject_definitions: <Subject 1> is the woman from <Picture 1>. <Video 1> supplies her motion.\n"
    "summary: [video editing + reference generation] Replace the performer in <Video 1>.\n"
    "retention_analysis: <Subject 1>: fully_preserved - retain identity.\n"
    "detailed_description: [Shot 1] <Subject 1> follows the performance from <Video 1>.\n"
    "overall_soundscape: Footsteps.\nnon_diegetic_music: N/A"
)


class H3PromptTests(unittest.TestCase):
    def test_same_scene_can_contain_multiple_cuts(self):
        output = BASE.replace("\noverall_soundscape", "\nAt 00:02.000, cut to her face.\nAt 00:04.000, cut to a front view in the same alley.\noverall_soundscape")
        self.assertEqual(validate_h3_prompt(output, "Two cuts in the same alley", "T2V", 5), output)

    def test_different_scenes_use_sequential_blocks(self):
        output = BASE.replace("\noverall_soundscape", "\n[Shot 2] At 00:04.000, she is home later that day.\noverall_soundscape")
        self.assertEqual(validate_h3_prompt(output, "Two scenes", "T2V", 8), output)
        with self.assertRaisesRegex(ValueError, "sequential"):
            validate_h3_prompt(output.replace("[Shot 2]", "[Shot 3]"), "Two scenes", "T2V", 8)

    def test_authored_scene_blocks_and_speaker_labels_cannot_be_dropped(self):
        source = "[Shot 1] One alley scene with two cuts."
        output = BASE.replace("\noverall_soundscape", "\n[Shot 2] At 00:04.000, cut to her face.\noverall_soundscape")
        with self.assertRaisesRegex(ValueError, "scene-block plan"):
            validate_h3_prompt(output, source, "T2V", 8)
        for request in ("The woman (S2) speaks.", "<Subject 2> walks."):
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, "subject/speaker"):
                validate_h3_prompt(BASE, request, "T2V", 0)

    def test_mode_selection_keeps_common_rules_and_required_appendix(self):
        skill = load_skill("h3")
        for mode in ("T2V", "I2V", "FL2V", "FL2VA", "Ref2V", "Ref2VA"):
            selected = select_h3_skill(skill, mode)
            self.assertEqual(selected.count("## Mode:"), 1)
            self.assertIn("## Scene blocks and timing", selected)
            self.assertIn("## Speech and audio", selected)
        self.assertEqual(select_h3_skill(skill, "Auto"), skill)

    def test_creative_selection_excludes_other_permissions_but_keeps_shared_constraints(self):
        skill = load_skill("h3")
        for mode in ("Auto", "T2V", "Ref2VA"):
            for freedom in ("Preserve", "Fill in details", "Develop scenario"):
                with self.subTest(mode=mode, freedom=freedom):
                    selected = select_h3_skill(skill, mode, freedom)
                    self.assertEqual(selected.count("## Creative freedom:"), 1)
                    self.assertIn("## Creative freedom: " + freedom, selected)
                    for heading in ("Settings and precedence", "Scene blocks and timing",
                                    "Speech and audio", "Final check"):
                        self.assertIn("## " + heading, selected)
                    self.assertEqual(selected.count("## Mode:"), 4 if mode == "Auto" else 1)

    def test_bad_structure_and_commentary_are_rejected(self):
        for output in ("Here is your prompt:\n" + BASE, BASE.replace("overall_soundscape: Footsteps.", "overall_soundscape:"),
                       BASE.replace("non_diegetic_music: N/A", ""), BASE + "\nnon_diegetic_music: N/A"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                validate_h3_prompt(output, "Walk", "T2V", 0)
        self.assertEqual(validate_h3_prompt("```text\n" + BASE + "\n```", "Walk", "T2V", 0), BASE)

    def test_supplied_dialogue_language_and_punctuation_survive(self):
        line = "<d>[French] Attends… ne pars pas !</d>"
        output = BASE.replace("A woman walks through the alley.", "The woman (S1) says " + line)
        self.assertEqual(validate_h3_prompt(output, "She says " + line, "T2V", 0), output)
        with self.assertRaisesRegex(ValueError, "dialogue"):
            validate_h3_prompt(output.replace("Attends…", "Attends,"), line, "T2V", 0)
        with self.assertRaisesRegex(ValueError, "unclosed"):
            validate_h3_prompt(output.replace("</d>", ""), "She speaks", "T2V", 0)

    def test_quoted_dialogue_and_visible_text_are_protected(self):
        for request in ('She says "Stay here!"', 'A sign reading "营业中"'):
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, "quoted"):
                validate_h3_prompt(BASE, request, "T2V", 0)
        # Quoted concepts are not necessarily immutable dialogue or visible text.
        validate_h3_prompt(BASE, 'Change the mood from "grim" to "hopeful"', "T2V", 0)

    def test_asset_invention_and_numbering_changes_are_rejected(self):
        context = "<Picture 1>: identity. <Video 1>: source motion."
        self.assertEqual(validate_h3_prompt(REF, "Replace the performer", "Ref2V", 0, context), REF)
        with self.assertRaisesRegex(ValueError, "invented"):
            validate_h3_prompt(REF.replace("<Picture 1>", "<Picture 2>"), "Replace", "Ref2V", 0, context)
        with self.assertRaisesRegex(ValueError, "dropped"):
            validate_h3_prompt(BASE, "Use <Picture 0>", "Auto", 0)
        with self.assertRaisesRegex(ValueError, "invented"):
            validate_h3_prompt(REF, "Replace", "Ref2V", 0)

    def test_endpoint_alignment_requires_real_duration_and_preserves_alias(self):
        i2v = "For the target video, at 0.00 seconds into the target video, <Picture 0> (from [Shot 1]) is fully referenced.\n\n" + BASE
        validate_h3_prompt(i2v, "Animate <Picture 0>", "I2V", 0)
        with self.assertRaisesRegex(ValueError, "alignment"):
            validate_h3_prompt(BASE, "Animate", "I2V", 0)
        fl = "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.\n\n" + BASE
        validate_h3_prompt(fl, "Open to closed", "FL2VA", 8)
        with self.assertRaisesRegex(ValueError, "duration_seconds"):
            validate_h3_prompt(fl, "Open to closed", "FL2VA", 5)
        fallback = "The target video begins from Picture 1 and reaches Picture 2 at the end of the final scene.\n\n" + BASE
        validate_h3_prompt(fallback, "Open to closed", "FL2V", 0)

    def test_attached_reference_images_allow_only_real_defaults_or_supplied_aliases(self):
        context = "<Video 1>: source motion."
        self.assertEqual(validate_h3_prompt(REF, "Replace", "Ref2V", 0, context, image_count=1), REF)
        with self.assertRaisesRegex(ValueError, "invented"):
            validate_h3_prompt(REF.replace("<Picture 1>", "<Picture 2>"), "Replace", "Ref2V", 0, context, image_count=1)
        validate_h3_prompt(REF.replace("<Picture 1>", "<Picture 2>"), "Replace", "Ref2V", 0, context, image_count=2)
        numbered = context + " Attached image 1 is <Picture 0>: identity."
        validate_h3_prompt(REF.replace("<Picture 1>", "<Picture 0>"), "Replace", "Ref2V", 0, numbered, image_count=1)
        with self.assertRaisesRegex(ValueError, "invented"):
            validate_h3_prompt(REF, "Replace", "Ref2V", 0, numbered, image_count=1)
        with self.assertRaisesRegex(ValueError, "invented"):
            validate_h3_prompt(BASE.replace("A woman", "The woman from <Picture 1>"), "Create a scene", "T2V", 0, image_count=1)

    def test_timestamps_are_checked_only_on_the_visual_timeline(self):
        for events in ("At 00:05.000, cut.", "At 00:04.000, cut. At 00:02.000, cut.", "At 00:02.000, cut. At 00:02.000, cut."):
            with self.subTest(events=events), self.assertRaisesRegex(ValueError, "timestamps"):
                validate_h3_prompt(BASE.replace("the alley.", "the alley. " + events), "Walk", "T2V", 5)
        output = BASE.replace("the alley.", "the alley. She says <d>[English] At 00:30, meet me.</d>")
        validate_h3_prompt(output, "She speaks", "T2V", 5)
        ending = BASE.replace("the alley.", "the alley. At 00:05.000, she comes to rest.")
        validate_h3_prompt(ending, "She stops at the end", "T2V", 5)
