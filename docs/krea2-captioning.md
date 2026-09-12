# Krea2 captioning for Local AI datasets

Researched 2026-09-10. The Folder Captioner prepares local training data; it does
not train a LoRA or run Krea2. It connects directly to the existing Local AI Model
or Local AI Model (Advanced), using the same localhost inference and VRAM lifecycle.
No redesign of existing nodes is needed.

## What the published evidence establishes

Krea's technical report describes OCR followed by a captioning model with access
to metadata such as known entities. Its captions begin as detailed natural
language and are reformatted into different lengths. Long captions predominate,
with shorter examples retained. This supports factual natural-language captions
and supplied concept metadata; it does not establish an optimal caption length or
identity-feature policy for small LoRA datasets. The folder node uses one local
VLM with user-supplied context, not Krea's separate OCR and recaptioning pipeline.
[Krea technical report, Captioning](https://www.krea.ai/blog/krea-2-technical-report).

Krea's hosted training announcement requires captions and explains their role in
separating the intended concept from incidental backgrounds and props. Those
requirements describe Krea's hosted service; local trainers have their own rules.
[Krea LoRA training announcement](https://www.krea.ai/blog/krea-2-lora-training).

Krea identifies RAW as the checkpoint for fine-tuning and supplies examples of
adapters trained on RAW for use with Turbo. These caption templates do not depend
on a particular trainer or adapter format.
[Krea2 RAW model card](https://huggingface.co/krea/Krea-2-Raw).

AI Toolkit documents adjacent image/caption pairs with matching stems and `.txt`
extensions, containing only the caption. It also supports a `[trigger]` placeholder
when a trigger is configured. Other trainers may interpret that placeholder
differently. The node always uses literal text and does no trainer-specific
substitution. Check your trainer's supported image formats independently of the
captioner's format support.
[AI Toolkit dataset preparation](https://github.com/ostris/ai-toolkit#dataset-preparation).

## Recommended starting policies

These are implementation choices informed by the sources, not Krea-published
character/style/refiner templates or results of a controlled Krea2 ablation.
Captions influence learning; they do not precisely mask which pixels a LoRA learns.

### Character likeness

Use a consistent marker and class noun for the target. Describe what should be
changeable: pose, action, expression, outfit, accessories, framing, background,
and lighting. By default, leave fixed facial geometry, eye color, skin tone, and
permanent body traits implicit. This is an identity-binding hypothesis to test,
not proof that detailed likeness captions perform worse on Krea2.

Specify ambiguous choices in `concept_context`: should hairstyle, tattoos, or a
signature outfit be inseparable from the character, or independently controllable?
Name the medium when useful to keep a character separable from photographic or
illustrated presentation. Use one identity per folder unless the target is
unambiguous from your supplied description.

Illustrative caption, only for an image that actually shows these details:

> WNperson, a woman in a yellow raincoat, stands beside a station window with one
> hand on a suitcase. She looks toward image-left in a waist-up side view, lit by
> soft daylight through the glass.

### Style

Describe subjects, objects, relationships, and setting. Leave the shared target
style implicit: brushwork, medium, rendering texture, overall palette, grading,
and characteristic lighting treatment. Concrete object colors can still matter.
Allow explicit variant labels when the goal is to control those variants later.

Black Forest Labs' FLUX.2 klein tutorial recommends this content-first policy,
including exceptions for controllable style variants. Applying it to Krea2 is an
inference across models, not a demonstrated Krea2 optimum.
[BFL style-captioning guidance](https://huggingface.co/blog/black-forest-labs/flux-2-klein-lora#caption-the-content-never-the-style).

Illustrative caption:

> In WNstyle, a cyclist crosses a bridge above a narrow river. Trees line the far
> bank, with two houses behind them and open sky above.

### Refiner

Use the established semantic concept name and describe its visible distinctions.
An optional marker supplements that name rather than replacing it. This differs
from leaving fixed identity or style traits implicit: here the desired result is
stronger correspondence between specific words and visible structure.

The rationale follows first-hand LoRA captioning observations about consistent
concept terms and naming features intended for prompt control. Those observations
were largely about SDXL; they motivate a Krea2 experiment rather than proving it.
[Minta Carlson's captioning observations](https://huggingface.co/blog/alvdansen/enhancing-lora-training-through-effective-captions).

Examples of context and the resulting emphasis:

| Scenario | Supply in context | Caption emphasis |
|---|---|---|
| Specific car | Verified make, model, generation/year where known | Visible silhouette, lamps, grille, wheels, trim, materials, and view; no invented engine or hidden parts |
| Ethnicity | Explicit dataset label provided by the user | That label where applicable, plus individual visible appearance, clothing, pose, and light; never infer the label from appearance or universalize traits |
| Anatomy | Target part or articulation, e.g. hands gripping handles | Visible fingers, joint angles, contact, occlusion, and viewpoint; no guessed hidden anatomy or medical diagnosis |

Illustrative anatomy caption:

> A close-up of a hand holding a ceramic mug by its handle. Two visible fingers
> curl through the handle while the thumb rests against its upper edge. The mug
> sits on a wooden table, with the wrist entering from image-right.

## Trigger and length choices

Use a consistent marker you will actually prompt with. No reviewed primary Krea
source establishes that a random string always beats a natural name, or that every
local training method needs a trigger. Leaving `trigger_word` empty is supported;
this is useful for natural-word refinement or trainers that insert the marker.
If using `[trigger]`, configure its interpretation in your trainer. Avoid having
both the node and trainer independently insert duplicate markers.

Bundled templates suggest a few useful sentences, with more structural detail
for Refiner. These are practical defaults, not measured optimum lengths. Override
with `instruction`, and adjust the output/context budget if needed. Accurate short
captions are preferable to padded descriptions containing invented details.

## Review and compare

Review a representative sample before training: exact trigger, correct target,
visible-only claims, missing important details, and unwanted identity/style leakage.
Known model names and user-supplied labels still need checking against each image;
folder-wide context cannot encode different per-image labels automatically.

For a useful comparison, hold images, trainer settings, training steps, sample
prompts, and seeds fixed while varying the caption policy. Assess likeness across
new outfits/backgrounds, style across unseen subjects, or refiner details across
poses/viewpoints. Neither successful caption generation nor a passing structure
check demonstrates better trained Krea2 output.
