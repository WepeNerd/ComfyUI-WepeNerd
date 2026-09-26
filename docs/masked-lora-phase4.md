# Phase 4: sampling range and CFG modes

Implemented 25 September 2026 on the local Phase 1–3 changes. The supported
formats remain native floating-point and native INT8 ConvRot Krea2, with ordinary
linear LoRA and supported full/factored LoKr. No new model family, quantization
format, attention isolation or global omitted-layer mode is enabled.

## Behavior and implementation

- `start_percent=0`, `end_percent=1`, `apply_to="Both"` preserve the default path.
  These optional backend widgets follow the existing widgets and inputs. Native
  Save/Export and old positional editor placeholders have migration tests.
- Percentages must be finite in [0, 1], with start ≤ end. Equal endpoints return
  the independent mask and unchanged model clone without reading adapter weights;
  the editor displays **Adapter disabled: start and end are equal.**
- `APPLY_MODEL` captures each concatenated row's raw sigma before
  `model_sampling.timestep()` or `process_timestep()`. The diffusion wrapper
  verifies the same logical-batch/chunk geometry used for mask projection. It
  never interprets model timesteps or counts solver calls as progress.
- Each scheduled forward converts endpoints using the **live** model sampling
  object. This avoids cached thresholds surviving replacement or flow-shift
  changes. Activation is `sigma_end ≤ sigma ≤ sigma_start`; each boundary has
  tolerance `1e-7 * max(1, abs(boundary))` to include float32 endpoint rounding.
  The full 0–1 default bypasses conversion, comparisons and sigma readback.
- A nondefault range reads back only the small sigma vector once per model call.
  The row gate is computed once and shared by all adapted layers in that call.
  Fully inactive regions skip mask projection/transfer, adapter conversion and
  adapter math. Active adapter tensors stay resident across boundary crossings
  within the Phase 2 budget; the spatial mask cache does not include sigma.
- `Positive only` verifies exact native `CFGGuider`/`Guider_Basic` conventions:
  conditioning list 0 is positive, list 1 is negative. Reordered/repeated chunks,
  separate calls and CFG=1 are handled from actual metadata. Unknown guider
  subclasses, custom conditioning evaluators, and custom prediction/conditioning
  wrappers fail with an instruction to use a native guider or Both. Both retains
  the existing strict layout checks; missing chunk metadata is not guessed.
- Range and mode live in the immutable descriptor and clone identity. Native
  execution caching includes the new inputs. Raw sigma, row gates and converted
  tensors are execution/forward state, never serialized descriptor state.
  Context variables and residency are restored/cleared on errors and interruption.

The node explains: **Range follows the model's denoising progression; a
partial-denoise run may use only part of it.** A repeated sigma has the same
activation decision; a custom nonmonotonic schedule can leave and re-enter a
range. Positive-only changes direct spatial injection and interaction with CFG,
not CLIP conditioning or other nodes' global LoRAs. No timing or mode is claimed
to improve quality universally.

## Executed checks

Environment: Windows, Python 3.12.13, Torch 2.11.0+cu130, CUDA 13.0,
RTX 5090 (32 GiB), ComfyUI 0.37.0 at
`b0f4b7b294ce482a2e071d9d762c133d38c7aa07`, frontend 1.53.6,
Chromium 153.0.0.0. No packages, user workflows or ComfyUI core files changed.

| Check | Result |
| --- | --- |
| Full Python discovery, CUDA enabled | 117 passed |
| Full JavaScript discovery | 66 passed |
| Modified frontend syntax and `git diff --check` | Passed |
| Transformed timesteps, distinct per-row sigmas, exact endpoints | Passed |
| Repeated/nonmonotonic sigma, partial-range semantics, live shift/replacement | Passed |
| CFG permutations, repeated labels, split calls, native CFG=1/BasicGuider | Passed |
| Unknown guiders, missing metadata, custom conditioning/prediction wrappers | Helpful errors verified |
| Default black/full/soft masks, negative strength, independent overlapping windows | Passed |
| Inactive transfers/math, shared residency, exception/context cleanup | Passed |
| Native legacy workflow load, Save, reload, Export, API Export | Passed |

The native UI check used an isolated CPU ComfyUI server at port 8189. An actual
Phase 3 workflow loaded at 0/1/Both, including its old empty editor slot. Editing
to 0.25/0.25 showed the disabled status; 0.25/0.75/Positive only survived Save,
reload, workflow Export and API Export. The original mask payload and existing
widget values were byte-for-byte unchanged. This UI graph did not execute the
LoRA model; the independent GPU runs below did.

## Fixed-seed GPU generations

Native `comfy.sd.load_diffusion_model`, native `CFGGuider` (0 positive, 1 negative),
Euler/simple, 16 steps, CFG 3, seed 25092026, 768×512, strength 0.8. A horizontal
coverage gradient includes white, soft and black regions. The model selected
split CFG evaluations, yielding 32 model calls and 224 adapted layers per call.

| Asset | File / SHA-256 |
| --- | --- |
| Krea2 checkpoint | `gtmsKrea2Wowser_v11Int8Bf16_int8.safetensors` / `8b8ac46762f59cb930747c7cf1098fac127517dd1e69b28d4706209248175fe6` |
| Rank-32 LoRA | `80s_Fantasy_Movie_Style_Krea2.safetensors` / `56b38b0455b452e943abe41626336d66588deb1ece3ed3d38ed598b4dd1019bc` |
| Text encoder | `qwen3-vl-4b-heretic_int8_dynamic_convrot.safetensors` / `c766f6f078bbfe7474699faaceb9484251b0c7c8dd4bf2d5730b320feca54778` |
| VAE | `wan_2.1_vae.safetensors` / `2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b` |

The checkpoint passed native spatial-layer preflight with floating-point and
`int8_tensorwise`/`TensorWiseINT8Layout` ConvRot layers; activations were bfloat16.
The selected adapter supplied 224 compatible spatial projections. File hashes,
complete sigma lists, prompt, latents, PNGs, per-call timing and runtime counters
are retained locally in the evidence directory.

| Mode | Percent | Sigma interval | Adapter layer calls | Sampling s | Forward s excluding first call | With decode s | Peak allocated / reserved GiB |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| Both, full | 0–1 | All, explicit fast path | 7168 | 15.548 | 6.572 | 18.654 | 13.15 / 16.30 |
| Both, early | 0–0.5 | 0.7595109169–1 | 4032 | 5.677 | 5.191 | 8.052 | 13.39 / 19.98 |
| Both, late | 0.5–1 | 0–0.7595109169 | 3584 | 5.486 | 5.262 | 7.823 | 13.39 / 17.11 |
| Positive only, full | 0–1 | All positive rows | 3584 | 5.670 | 5.184 | 7.958 | 13.39 / 17.11 |

The midpoint is an actual sampled sigma and both endpoints are inclusive:
early has nine active evaluations per branch, late has eight. All four runs made
448 adapter transfers totaling 214,695,936 bytes (one transfer per adapter tensor),
with no eviction or full-output clone. Their runtimes closed successfully and
all four latent hashes differ. Full default has no scheduling readback.

These are single runs in the listed order, not a performance comparison: the
first includes model loading/cold execution, later runs reuse the loaded model,
and CUDA reservation includes earlier allocations. Sampling time includes node
preflight and sampling; “with decode” adds VAE preparation/decode. Text encoding
and initial checkpoint/file hashing are excluded. Forward time excludes only the
first model call; it is not a separately repeated warm-generation benchmark.

**Visual-quality limit:** all four decoded images show pronounced grid/banding
artifacts. A fifth fixed-seed run at **zero strength, with zero adapter patches**
shows the same issue. Re-decoding the full-range latent with float32 Wan VAE and
the installed float32 HDR VAE also retains it. The root cause in this local
model/encoder/VAE setup has not been isolated; this is not attributed to the
new gates. The runs demonstrate reproducible scheduling, different outputs and
correct activation counts, but do **not** establish good image quality or a
preferred range/mode. Clean visual-quality comparisons remain a follow-up gate
with a verified baseline setup. The baseline and decode checks are retained
under `baseline/` and `decode_check.py` in the local evidence directory.

## Reproduction and remaining gates

```powershell
$env:WEPENERD_TEST_CUDA = '1'
& C:\SD\ComfyUI\venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
node --check js/wn_masked_lora.js
git diff --check
& C:\SD\ComfyUI\venv\Scripts\python.exe -B tests/validate_masked_lora_schedule.py `
  --checkpoint <native-krea2.safetensors> --text-encoder <qwen3-vl-4b.safetensors> `
  --vae <wan-vae.safetensors> --adapter <linear-lora-or-lokr.safetensors> `
  --output <local-evidence-directory>
```

Add `--baseline-only` to render the same seed/prompt at zero adapter strength.
The runner installs/downloads nothing and leaves model files unchanged. Debug
logs record converted endpoints and the verified guider convention.

Evidence is in ignored `.release-preparation/masked-lora-phase4/`: full test logs,
native workflow/API exports, `generation/results.json`, `generation/debug.log`,
the four PNGs and `generation/comparison.png`. Numerical tests cover LoKr and
negative strengths; these real generations used one rank-32 LoRA, one checkpoint,
one text encoder and one scene. FP8/NVFP4 remain unadvertised. Phase 5 must retain
these tests and independently establish native FP8 and NVFP4 numerical, lifecycle
and real-generation gates before adding either format.
