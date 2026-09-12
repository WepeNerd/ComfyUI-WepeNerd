"""H3-specific instruction selection and text-output checks."""

from __future__ import annotations

import re


BASE_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_FIELDS = (
    "subject_definitions", "summary", "retention_analysis", "detailed_description",
    "overall_soundscape", "non_diegetic_music",
)
_ASSET = re.compile(r"\b(Picture|Video|Audio)\s+(\d+)\b")
_DIALOGUE = re.compile(r"<d>(.*?)</d>", re.DOTALL)
_QUOTED_LITERAL = re.compile(
    r'\b(say|says|said|reply|replies|asks|shouts|whispers|dialogue|lyrics|reading|reads|text|label|sign)'
    r'\s*[:,]?\s*["“]([^"”\n]+)["”]', re.IGNORECASE,
)
_TIMESTAMP = re.compile(r"\bAt\s+(\d+):([0-5]\d(?:\.\d+)?)\b", re.IGNORECASE)
_SHOT = re.compile(r"(?:^|\n)\s*\[Shot (\d+)\]", re.MULTILINE)
_SUBJECT = re.compile(r"<Subject\s+\d+>")
_SPEAKER = re.compile(r"\(S\d+(?:,\s*S\d+)*\)")


def select_h3_skill(skill: str, mode: str, creative_freedom: str | None = None) -> str:
    """Keep common rules and the selected mode and creative permission."""
    if mode == "Auto" and creative_freedom is None:
        return skill
    selected = []
    permission = []
    for section in re.split(r"(?=^## )", skill, flags=re.MULTILINE):
        if not section.strip():
            continue
        heading = section.splitlines()[0]
        if heading.startswith("## Mode: ") and mode != "Auto":
            if mode not in heading.removeprefix("## Mode: ").split(" / "):
                continue
        if heading.startswith("## Creative freedom: ") and creative_freedom is not None:
            if creative_freedom == heading.removeprefix("## Creative freedom: "):
                permission.append(section.strip())
            continue
        selected.append(section.strip())
    return "\n\n".join(selected + permission)


def validate_h3_prompt(output: str, source: str, mode: str, duration: float, reference_context: str = "", image_count: int = 0) -> str:
    """Reject structurally incomplete rewrites and changes to identifiable literals."""
    text = output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1] == "```":
            text = "\n".join(lines[1:-1]).strip()
    all_fields = tuple(dict.fromkeys((*BASE_FIELDS, *REF_FIELDS)))
    headers = list(re.finditer(r"^(" + "|".join(all_fields) + r"):[ \t]*", text, re.MULTILINE))
    fields = tuple(match[1] for match in headers)
    expected = REF_FIELDS if mode.startswith("Ref") else BASE_FIELDS
    if mode == "Auto" and fields == REF_FIELDS:
        expected = REF_FIELDS
    if fields != expected:
        raise ValueError("H3 rewrite has missing, repeated, or out-of-order sections. Run the enhancer again.")
    bodies = {
        match[1]: text[match.end():headers[index + 1].start() if index + 1 < len(headers) else len(text)].strip()
        for index, match in enumerate(headers)
    }
    if any(not body for body in bodies.values()):
        raise ValueError("H3 rewrite contains an empty section. Run the enhancer again.")
    prefix = text[:headers[0].start()].strip()
    if prefix and not prefix.startswith((
        "For the target video,", "How the reference pictures align with the target video",
        "The target video begins from",
    )):
        raise ValueError("H3 rewrite contains commentary before the prompt. Run the enhancer again.")
    if mode == "I2V" and not prefix.startswith("For the target video, at 0.00 seconds"):
        raise ValueError("H3 I2V rewrite is missing the first-frame alignment instruction.")
    if mode in ("FL2V", "FL2VA"):
        if not prefix:
            raise ValueError("H3 first/last-frame rewrite is missing endpoint alignment.")
        if duration > 0 and f"{duration:.2f}-second mark" not in prefix:
            raise ValueError("H3 last-frame alignment must match duration_seconds.")
    description = bodies["detailed_description" if expected == REF_FIELDS else "integrated_multimodal_description"]
    shots = [int(match[1]) for match in _SHOT.finditer(description)]
    if not shots or shots != list(range(1, len(shots) + 1)):
        raise ValueError("H3 rewrite must start with [Shot 1] and use sequential scene blocks.")
    source_body = re.sub(r"^(?:integrated_multimodal_description|detailed_description):[ \t]*", "", source, flags=re.MULTILINE)
    source_shots = [int(match[1]) for match in _SHOT.finditer(source_body)]
    if source_shots and shots != source_shots:
        raise ValueError("H3 rewrite changed the supplied scene-block plan. Keep same-scene cuts inside their existing block.")
    for pattern in (_SUBJECT, _SPEAKER):
        if set(pattern.findall(source)) - set(pattern.findall(text)):
            raise ValueError("H3 rewrite changed or dropped a supplied subject/speaker label. Run the enhancer again.")

    for literal in _DIALOGUE.findall(source):
        if literal not in _DIALOGUE.findall(text):
            raise ValueError("H3 rewrite changed or dropped supplied <d> dialogue. Run the enhancer again.")
    for _, literal in _QUOTED_LITERAL.findall(source):
        if literal not in text:
            raise ValueError("H3 rewrite changed or dropped quoted dialogue or visible text. Run the enhancer again.")
    if text.count("<d>") != text.count("</d>"):
        raise ValueError("H3 rewrite contains an unclosed dialogue block. Run the enhancer again.")

    known_assets = set(_ASSET.findall(source + "\n" + reference_context))
    implicit_frames = {("Picture", "1")} if mode == "I2V" else set()
    if mode in ("FL2V", "FL2VA"):
        implicit_frames = {("Picture", "1"), ("Picture", "2")}
    if mode == "Auto" and expected == BASE_FIELDS:
        # Auto may infer an endpoint task from ordinary image descriptions.
        implicit_frames = {("Picture", "1"), ("Picture", "2")}
    if mode.startswith("Ref") or mode in ("I2V", "FL2V", "FL2VA"):
        implicit_frames.update(("Picture", str(index)) for index in range(1, image_count + 1))
    if any(kind == "Picture" for kind, _ in known_assets):
        implicit_frames = set()
    generated_assets = set(_ASSET.findall(text))
    if generated_assets - known_assets - implicit_frames:
        raise ValueError("H3 rewrite invented a reference asset. Supply its alias and role in reference_context.")
    if set(_ASSET.findall(source)) - generated_assets:
        raise ValueError("H3 rewrite dropped a supplied reference alias. Run the enhancer again.")

    timeline = _DIALOGUE.sub("", description)
    times, cuts = [], []
    for match in _TIMESTAMP.finditer(timeline):
        timestamp = int(match[1]) * 60 + float(match[2])
        times.append(timestamp)
        if re.match(r"\s*[:,]?\s*(?:(?:the\s+)?(?:camera|shot)\s+)?(?:hard\s*)?cuts?\b", timeline[match.end():], re.IGNORECASE):
            cuts.append(timestamp)
    if times != sorted(times) or cuts != sorted(set(cuts)) or (
        duration > 0 and (any(t > duration for t in times) or any(t >= duration for t in cuts))
    ):
        raise ValueError("H3 timestamps must follow playback order and fit duration_seconds; cuts must occur before the end.")
    return text
