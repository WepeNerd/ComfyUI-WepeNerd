# Load LoRA Masked: Phase 2 runtime and measurements

Implemented and measured 25 September 2026 on top of the uncommitted Phase 1
changes. Environment E1 is unchanged: Windows, Python 3.12.13, PyTorch
2.11.0+cu130, CUDA 13.0, Comfy Kitchen 0.2.35, RTX 5090, ComfyUI 0.37.0
(`b0f4b7b294ce482a2e071d9d762c133d38c7aa07`), frontend 1.53.6, Node.js 22.18.0.
Repository baseline remains `d31bd02f6fb1c31f78dee0804fe29add2b4a3612`.
No packages, node sockets, widget order or supported model formats changed.

## Runtime and ownership

Each patched layer has one dispatcher with an immutable tuple of contributions.
It calls the original forward once, then adds each region's delta from the
original input. Ordinary global weight patches and regional instances of the
same adapter remain additive. Identical file content/layer identities share CPU
data and device conversions, while strengths and masks remain independent.
Chaining creates new dispatchers and a branch identity covering the ordered
descriptor tuple; it does not edit a parent clone's contributions.

One Krea2 layout context per branch supplies the native logical batch/chunk map.
Projected masks are cached by canonical mask identity/shape, latent geometry,
circular padding, row mapping, device and dtype. Sigma is not part of this key.
Effective all-zero masks skip adapters using CPU zero knowledge cached for the
run. Sparse nonzero masks still use dense native adapter math; token gathering
is deferred, so sparse coverage is not advertised as faster.

`OUTER_SAMPLE` creates the runtime without allocating device tensors. The
installed native call path prepares/loads the model inside this wrapper;
conversion happens at first layer use, using the actual activation device/dtype.
Native sampling supplies `ModelPatcher.get_free_memory`, which includes memory
reclaimable by ComfyUI's dynamic offloader. Standalone fixtures use the ordinary
device query. The node uses a residency cap and headroom policy instead of
overriding ComfyUI's model-memory estimates. Cleanup in `finally` clears cache
references and the patcher query, and restores nested ContextVars on success,
errors and interruptions. Calls without the sampling/layout lifetime fail with
an actionable error. No per-layer/per-step `empty_cache()` is used.

Verified native floating-point/INT8 linears use target-view accumulation only
with autograd disabled, a plain output tensor and storage distinct from the input.
A native linear can return a view of its own fresh allocation. Arbitrary patched
forwards, retained buffers, input views and autograd calls use one output copy
for the entire dispatcher. Masks and deltas explicitly use the output accumulation
dtype; text/reference rows and the original layer input remain untouched.

## Bounded policies and file refresh

Set these integer environment variables before launching ComfyUI. Units are MiB.

| Variable | Default | Effect |
| --- | ---: | --- |
| `WEPENERD_MASKED_LORA_CACHE_MB` | 256 | Combined adapter/mask residency cap per sampling run; 0 streams each use |
| `WEPENERD_MASKED_LORA_HEADROOM_MB` | 1024 | Free-memory headroom checked before cache-miss device allocations |
| `WEPENERD_MASKED_LORA_FILE_CACHE_MB` | 256 | Process CPU parsed-file cache cap; 0 disables retained file entries |

The run cache also has a 512-entry limit. Following the
[review fixes](masked-lora-review-fixes.md), admitted entries stay resident when
the budget is full and adapter overflow streams. New masks displace adapters
first to fit the same byte/entry limits, avoiding repeated projection after a
layout change. Actual device-memory pressure can also evict entries before a
miss allocation. Oversized entries stream;
insufficient working memory fails before adapter conversion with guidance to
reduce batch/resolution or adapter size. Streaming reduces retained memory,
but cannot remove the largest adapter's temporary working-memory requirement.
Cache limits cover tensor payloads, not the base model, activations, native
adapter intermediates or PyTorch's reserved allocator memory. CPU master tensors
referenced by active graph branches remain alive independently of file-cache
eviction. Nested runs have separate budgets.

The original measurements below predate this admission change and used LRU
eviction at the byte/entry limit. Invalid environment values now warn and fall
back to defaults. File hashing uses chunked SHA-256, compatible with Python 3.10.

The CPU file LRU retains at most four files within its byte cap. Canonical path,
file identity/device, size, nanosecond mtime and change time identify a hit.
Hashing/parsing checks metadata before and after reading, retries once on a
concurrent edit, and caches no failed parse. Identical file bytes can share parsed
CPU weights. Model-specific mapping and module/shape validation run on every node
execution, including file-cache hits. Mask-only edits reuse unchanged file data.
Evicted files, files larger than the CPU cap and a disabled file cache require
another read/parse; increase the CPU cap when repeated large-file edits warrant it.

`IS_CHANGED` includes the file signature and a refresh generation, so ordinary
same-path replacement is observed when the workflow is queued. A writer that
deliberately preserves *all* metadata can evade this stat cache. Restart ComfyUI
to force a fresh read, or run this through tooling inside the running ComfyUI
Python process, then queue again:

```python
import nodes
nodes.NODE_CLASS_MAPPINGS["WepeNerdLoadLoraMasked"].clear_file_cache()
```

Debug logging reports scalar runtime counters at cleanup. Transfer bytes count
the destination tensor payload of actual CPU-to-device conversions; they are
**not measured PCIe traffic** or a promise based on the file's storage dtype.

## Executed gates and compatibility

| Case | Loader / adapter / masks | Result and evidence |
| --- | --- | --- |
| Phase 1 algebra/layout, E1 CPU | Native LoRA and all supported LoKr factor forms; negative/overlapping/zero strengths; full/gray/black; repeated/split CFG chunks, padding and references | Pass; [Phase 1 tests](../tests/test_masked_lora.py) retained with the new runtime interface |
| Small initialized Krea2, E1 CPU; no checkpoint | Native BaseModel/ModelPatcher/CFGGuider, Euler `[1, .5, 0]`, CFG 2, seed 42; rank-2 LoRA, full mask, always active | Pass; preparation sees an empty runtime, later calls reuse entries, exit clears it |
| Native INT8 ConvRot linear, E1 CPU and RTX 5090 | Synthetic 256 × 256, group 256, rank-3 LoRA/alpha 5, gray .4, strength -.7; CPU float32 and CUDA float32/float16/bfloat16 | Pass including in-place inference path; base dequantization prohibited, original INT8 data and text rows preserved |
| Cache/lifecycle/ownership, E1 CPU and CUDA | Same-content reuse, mixed dtypes, eviction, zero cache, pressure rejection, nested/repeated/error/KeyboardInterrupt cleanup, alias/view outputs, global patches and clone switching | Pass; [runtime tests](../tests/test_masked_lora_runtime.py), including CUDA weak-reference cleanup |
| File reuse and invalidation, E1 CPU | Mask-only edits, same-path replacement, identical copies, changed reads, parse failure, explicit clear, changed model mapping | Pass; runtime tests |
| Pretrained Krea2 image generation | Real checkpoint/adapters, visual seams, strength, reference fidelity and complete denoising | **Pending**; the layer microbenchmark below does not establish image quality or end-to-end speed |
| Krea2 FP8/NVFP4; Qwen; Flux; video | Any | **Unsupported**, unchanged from Phase 1 |

All **103 Python tests** pass, including **47 focused masked-adapter tests** and
the CUDA gates. All **51 JavaScript tests** pass. Native INT8 errors remain within
the Phase 1 tolerances. Phase 2 has no frontend changes; the Phase 1 real-canvas
fixture remains the browser evidence. No hosted CI, commit, push or release was run.

## RTX 5090 layer microbenchmark

The reproducible [benchmark](../tests/benchmark_masked_lora.py) uses one native
6144 × 6144 linear, bfloat16, batch 1, seed 42, 1024 × 1024 target geometry
(128 × 128 latent, patch 2, 4096 target tokens), 256 text and 1024 reference tokens.
Adapter seeds are 41–43. Each region uses a distinct adapter; strengths are
`.7, -.4, .2`. Sparse masks cover exactly 1/16 of target tokens; full masks cover
all target tokens. No pretrained checkpoint or real adapter file is loaded.

Rank-32 LoRA has 1.5 MiB of CPU float32 factors / .75 MiB in bfloat16 per layer.
Large direct/direct LoKr uses 2 × 2 and 3072 × 3072 factors: approximately 36 MiB
CPU / 18 MiB bfloat16 per region. LoKr factor bytes exclude any native working
intermediates. The script retains Phase 1's transfer/clone algorithm as a reference;
its masks are prepared outside timing, conservatively excluding Phase 1's repeated
diffusion-wrapper projection overhead.

Times below are synchronized wall-time medians of ten warm calls after two warmups.
These are single-layer results, **not sampling-step or total generation times**.

| Adapter | Regions | Mask | Phase 1 ms | Resident ms | Streaming ms |
| --- | ---: | --- | ---: | ---: | ---: |
| LoRA rank 32 | 1 | Sparse | 2.452 | 2.014 | 2.587 |
| LoRA rank 32 | 1 | Full | 2.666 | 2.103 | 2.612 |
| LoRA rank 32 | 3 | Sparse | 4.701 | 2.807 | 5.866 |
| LoRA rank 32 | 3 | Full | 4.372 | 2.367 | 5.948 |
| LoKr | 1 | Sparse | 6.302 | 3.445 | 6.409 |
| LoKr | 1 | Full | 6.013 | 3.114 | 6.043 |
| LoKr | 3 | Sparse | 15.718 | 5.654 | 14.978 |
| LoKr | 3 | Full | 14.207 | 5.733 | 13.640 |

Peak CUDA allocated / reserved MiB, including the base layer/input; both mask
coverages measured the same peaks. Idle allocated baseline was 144.125 MiB.
Allocator caches are cleared only between independent benchmark cases.

| Adapter | Regions | Phase 1 allocated / reserved | Resident allocated / reserved | Streaming allocated / reserved |
| --- | ---: | ---: | ---: | ---: |
| LoRA rank 32 | 1 | 368.9 / 382 | 305.1 / 318 | 305.1 / 318 |
| LoRA rank 32 | 3 | 368.9 / 382 | 306.6 / 320 | 305.1 / 318 |
| LoKr | 1 | 418.1 / 496 | 418.1 / 432 | 418.1 / 432 |
| LoKr | 3 | 418.1 / 496 | 454.1 / 480 | 418.1 / 432 |

Across 14 calls per case (first, two warmups, ten measured, one parity readback),
resident adapters perform **2 / 6 tensor transfers for 1 / 3 regions**, versus
**28 / 84** in Phase 1 or streaming. Logical adapter destination bytes are
.75 / 2.25 MiB for resident LoRA and about 18 / 54 MiB for resident LoKr; the
other modes transfer 14 times these payloads. All modes call the base exactly
14 times. Resident/streaming native dispatchers make zero full-output copies;
Phase 1 makes 14 / 42. Results were bit-identical in all 24 cases, and device
allocated memory returned to the baseline after every runtime cleanup.

CPU hashing/loading/parsing medians over three fresh node-cache reads were
**3.215 ms LoRA / 51.348 ms LoKr**; unchanged stat hits were **.223 ms** for both.
OS disk caches were not flushed, and these figures exclude model mapping and
base-model loading. JSON includes first-call times, warm min/max, mask transfers,
cache counters and exact peaks. First-call measurements include startup effects
(the first Phase 1 call took 1473 ms); fixed case order and ten calls are not a
statistical performance guarantee. The measured tradeoff is faster resident reuse
with higher retained LoKr memory; streaming remains available at greater transfer
and projection cost.

## Reproduction and next phase

```powershell
$env:WEPENERD_TEST_CUDA = '1'
& C:\SD\ComfyUI\venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
node --check js/wn_masked_lora.js
& C:\SD\ComfyUI\venv\Scripts\python.exe -B tests/benchmark_masked_lora.py --output .release-preparation/masked-lora-phase2/benchmark.json
git diff --check
```

Local raw results are in the ignored `.release-preparation/masked-lora-phase2/`
directory: `benchmark.json`, `python-tests.txt`, `frontend-tests.txt`. The numerical
tables above are retained in versioned documentation. Backend syntax and package
registration are also checked locally. Full-generation timing/VRAM and visual
validation remain pending. Phase 3 can build its editor work on this backend;
retain these tests and the existing serialized node contract. Later scheduling
must apply gates separately from geometric mask caches.
