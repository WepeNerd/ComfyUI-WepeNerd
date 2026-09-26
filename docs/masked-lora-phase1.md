# Load LoRA Masked: Phase 1 validation

Executed 25 September 2026 against repository baseline
`d31bd02f6fb1c31f78dee0804fe29add2b4a3612`, which matches the handoff. The installed
ComfyUI is **0.37.0**, commit `b0f4b7b294ce482a2e071d9d762c133d38c7aa07`.
Its sampler, repeat/truncate helper, Krea2 model, padding and adapter APIs were
inspected directly; those files have no local modifications. The handoff's older
ComfyUI commit is not in this local Git database. No host/runtime packages changed.

## Changes and retained contracts

- Coverage projection uses area/adaptive averaging for shrinking axes and bilinear
  interpolation for expanding axes. Mixed resizing shrinks first. The 1024-wide
  stripe at columns 489–502 produces exactly `7/16` in columns 30 and 31 at width 64.
  Circular right/bottom patch padding mirrors the native Krea2 helper.
- Version 1 empty markers retain dimensions after Clear. Existing empty strings
  still mean 1024 × 1024. PNG/header/schema/size checks precede allocation; PNG alpha
  is coverage. External masks become independent, finite CPU float32 masters,
  clamped to `[0, 1]` with a warning. Hashes cover canonical shape and values.
- `CALC_COND_BATCH` verifies the logical image batch before the diffusion wrapper
  constructs an immutable `CallLayout`: actual chunk labels, chunk size, mask row
  mapping and model geometry. Context variables restore on exceptions. Cropped
  conditioning, custom model wrappers, context/tile handlers, multi-GPU layouts,
  missing metadata and mismatched batches fail explicitly.
- All mapped adapters are preflighted before object patching. Unsupported raw
  tensors are detected even on omitted/unmapped layers. Duplicate aliases and
  mixed LoRA/LoKr tensors for one layer are rejected instead of being overwritten.
  Supported nonspatial omissions are logged separately from unknown adapter keys;
  unrelated metadata and optimizer entries are ignored.
- The installed LoKr weight path uses the second factor's rank when both are
  factored; native `h()` uses the first. The normalization helper runs `h()` without
  its alpha scaling and supplies the weight-path scale. Direct/direct ignores alpha,
  including zero. A small preflight probe rejects changed upstream scaling behavior.
- Node ID, required input order, positional widgets, MODEL/MASK outputs, optional
  IMAGE/MASK sockets, saved painting under external override and chaining remain
  unchanged. No schedule/CFG controls or model-format expansion were introduced.

## Environment and results

Environment **E1**: Windows, Python **3.12.13** from
`C:\SD\ComfyUI\venv\Scripts\python.exe`, PyTorch **2.11.0+cu130**, CUDA runtime
**13.0**, Comfy Kitchen **0.2.35**, NVIDIA **GeForce RTX 5090**. ComfyUI commit is
the one above. Installed frontend package: **1.53.6**. Node.js: **22.18.0**.
Browser fixture: Chromium **153.0.0.0** with actual Canvas/PNG APIs and the production
editor source; the ComfyUI graph host was a small fixture, not a live workflow.

| Architecture / checkpoint | Loader / format | Environment | Adapter / rank | Mask and CFG/schedule case | Actual result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Nonsquare 6 × 12 linear fixture; no checkpoint | Native ComfyUI parser and `calculate_weight`, float32 | E1 CPU | LoRA rank 2; LoKr direct/direct, factored/direct, direct/factored, factored/factored, unequal ranks 2/3 | Full, black, gray; negative/fractional/zero strength; absent/zero/nondefault alpha; two adapters; target vs text/reference rows | Pass | `AlgebraTests` in [test_masked_lora.py](../tests/test_masked_lora.py) |
| Small native Krea2, initialized fixture weights, seed 13 | Native BaseModel/ModelPatcher/sampler, float32 | E1 CPU | LoRA rank 2 on first, attention query and last | Batch 3 with two masks; native CFG chunks; 4D/5D still-image inputs; 5 × 7 latent with patch padding; 0/1/2 references; always active | Pass; native model forward exercised | `NativeIntegrationTests` in the same file |
| Native linear 256 × 256, synthetic weights, seed 42 | Native mixed-precision operations; INT8 ConvRot, group 256 | E1 CPU and RTX 5090 | LoRA rank 3, alpha 5 | Gray 0.4, strength -0.7, target rows only; always active | Pass in CPU float32 and CUDA float32/float16/bfloat16; base dequantization forbidden and original INT8 data unchanged | `NativeInt8Tests` in the same file |
| Geometry and layout fixtures; no model weights | CPU float32 | E1 CPU | N/A | Neighboring stripes, portrait, circle, soft ramp, mixed/noninteger resize, padding, batch sizes 1/2/3 and mask batches 1/2/3/4; reversed/repeated/split chunks and invalid metadata | Pass | `ProjectionTests`, `BatchTests`, `MaskTests` |
| Both shared editors | Production JavaScript in Node VM and browser host fixture | E1 Node.js / Chromium | N/A | Clear/Undo/reload, 1536 × 1024 empty geometry, legacy positional widget values, external override, untinted alpha | Pass | [mask_editor.test.mjs](../tests/mask_editor.test.mjs); local browser fixture below |
| Pretrained Krea2, full image generation | Native float / INT8 ConvRot | Not executed in this phase | Real adapters | Visual seams, quality, reference fidelity and full denoising | **Pending**; numerical/fixture checks do not establish image quality | No generated-image evidence |
| Krea2 FP8/NVFP4; Qwen; Flux | Any | Not enabled | Any | Any | **Unsupported in Phase 1** | Later phases require independent gates |

Synthetic fixtures do not redistribute or load checkpoint weights; checkpoint and
real-adapter hashes are therefore not applicable to these runs. Full generation
time, warm sampling time, peak allocated/reserved VRAM, and adapter transfer
count/bytes were **not measured**. No performance improvement is claimed.

Float32 algebra uses `atol=1e-5, rtol=1e-4`. In the bounded INT8 CUDA fixture, maximum
absolute errors against the original quantized base plus an independent float32
delta were **1.1175871e-8** (float32), **5.332008e-5** (float16), and
**3.5243481e-4** (bfloat16). Float16 uses `atol=2e-4, rtol=2e-3`; bfloat16 uses
`atol=2e-3, rtol=2e-2` for activation rounding. These tolerances are specific to
these bounded fixtures, not a general full-model parity promise.

## Commands and evidence

Run from the repository with ComfyUI available on `PYTHONPATH` (the tests also find
the host when this repository is under `ComfyUI/custom_nodes`). CPU mode is selected
before importing ComfyUI. CUDA tests are opt-in and assert availability when requested.

```powershell
$env:WEPENERD_TEST_CUDA = '1'
& C:\SD\ComfyUI\venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
node --check js/wn_masked_lora.js
git diff --check
```

- Python: **83 passed**, including **27 focused masked-LoRA tests** and the GPU gate.
- JavaScript: **51 passed**, including **5 shared-editor regressions**.
- Real browser fixture: both nodes passed Clear, Undo, reload, PNG alpha and widget
  order checks. Backend/frontend syntax and diff whitespace checks passed.
- Local raw test logs and browser harness are retained under the ignored
  `.release-preparation/masked-lora-phase1/` directory (`python-tests.txt`,
  `frontend-tests.txt`, `editor.html`). Serve the repository on localhost and open
  the harness to repeat the real-canvas check. It reads `js/wn_masked_lora.js` directly.
- Hosted CI was not run; no commits, pushes or releases were made for this phase.

## Phase 2 prerequisites and remaining limits

Keep these regressions passing before adding caches or consolidating layer wrappers.
Use the immutable call layout as the batch/geometry contract. Scope runtime/device
caches to sampling lifecycle, preserve branch isolation and exception cleanup, and
measure transfers/VRAM separately from total and warm sampling time.

This phase retains one wrapper per region and per-call projection/transfers. Native
full-generation visual checks remain pending. Arbitrary image permutations cannot
be inferred from `cond_or_uncond`; future integrations need explicit sample indices
and global crop coordinates. A full mask matches only the spatial-layer reference,
not an unrestricted global adapter. Attention can still spread changes outside the
mask. FP8/NVFP4 and new architectures remain disabled.
