# Masked LoRA review fixes

Follow-up to Phases 1–4, validated locally on 2026-09-25. The implementation keeps
Python >=3.10 and the native Krea2 floating-point/INT8 ConvRot boundary.

## Correctness and loading

- SHA-256 reads files in 1 MiB chunks; no production use of `hashlib.file_digest`.
  The test covers multiple chunks with the 3.11-only function disabled. Python
  3.12.13 was used for execution; a Python 3.10 interpreter was not tested.
- Save, Export and Queue checkpoint current brush pixels without ending the
  gesture or releasing capture. Pointerup commits one undo step. Cancel, lost
  capture, blur and Escape restore the starting mask, including after checkpoints.
- Layer validation runs once per patched projection. An unsupported untouched
  projection no longer rejects an otherwise supported adapter. This does not
  establish support for an FP8/NVFP4 model or other custom loaders.
- Boolean schedule endpoints are rejected. Invalid cache/headroom environment
  values warn and fall back to defaults. Extra mask batch rows warn once per
  mask/layout per run; the existing repeat/truncate mapping remains explicit.
- A file that changes on both parse attempts reports the file-change error,
  including when both parser calls fail. Stable parser errors retain their cause.
- LoRA execution no longer encodes or writes an unused mask asset. Reference
  image uploads/snapshots and Paint Mask's source-image behavior still use assets.

## Runtime changes and measurements

The combined 256 MiB adapter/mask budget keeps admitted adapters and streams
adapter overflow, including at the 512-entry cap. New masks have admission
priority: they displace adapters first, then the least recently used masks if
necessary. This lets a changed batch layout reuse its projection and device
copy across layers after the adapter cache fills. The same byte/entry limits
apply; disabled caching or an individually oversized mask still streams.
Actual device-memory pressure can also evict entries, and cleanup still runs
on completion/error/interruption.
Device headroom is queried once on a normal allocation miss and again after a
pressure eviction. It remains a live safety check. A follow-up measurement of
the native `get_free_memory(cuda:0)` on this RTX 5090/Torch 2.11.0+cu130 found:

| Query workload | Median CPU wall time |
| --- | ---: |
| One query, idle GPU, 2,000 trials | 0.0643 ms (95th percentile 0.1152 ms) |
| 54 queries, 25 trials | 3.9205 ms |
| 210 queries, 25 trials | 15.5061 ms |
| One query with eight 4096 × 4096 fp32 GEMMs queued, 40 trials | 0.1374 ms |

The trailing CUDA event remained unfinished after all 40 busy-GPU queries, so
these queries did not wait for all preceding GPU work on this driver/backend.
The CPU overhead is measurable; it is retained to check current headroom before
streamed allocations. These timings do not establish costs or synchronization
behavior on other devices. The local measurement script/results are
`free_memory.py` and `free_memory.json` in the evidence directory below.

A CUDA residency microbenchmark visited 224 layers in the same order four times,
with one shared projected mask. CPU weights were shared between distinct layer
identities to isolate conversion/residency costs. It used RTX 5090, Torch
2.11.0+cu130 and bf16, excluding base-model and adapter arithmetic:

| Adapter | Old warm hit count / 224 | New warm hit count / 224 | Copies per warm pass, old → new | Median warm pass, old → new |
| --- | ---: | ---: | ---: | ---: |
| Rank-64 LoRA, 336 MiB total | 0 | 170 | 448 → 108 | 106.0 → 25.4 ms |
| Direct LoKr, about 3.94 GiB total | 0 | 14 | 448 → 420 | 544.3 → 511.6 ms |

New-policy runs had zero budget-driven evictions, retained the shared mask and
stayed within the byte cap. These are transfer/cache measurements, not generation
speedups. A large adapter still requires temporary working memory.

Mixed CFG/range batches gather active rows before calling the native adapter and
copy the updated rows back, retaining fused `addcmul` arithmetic. Inactive rows
remain bit-for-bit unchanged. Both LoRA and LoKr CPU/autograd and CUDA cases pass.
An injection-only benchmark at width 6144, 4096 target tokens and one active row
out of two measured 10.61 → 0.49 ms for rank-32 LoRA and 2.85 → 1.60 ms for LoKr.
The large LoRA timing difference is specific to these matrix shapes and selected
GPU kernels; it is not a promise of that speedup in a model run.

Changing GEMM batch size can change rounding. In that synthetic bf16 LoRA case,
the largest old/new difference was 0.015625, with relative RMS errors against
fp32 of 0.338% (old) and 0.321% (active rows). LoKr matched exactly in this case.
The equation is preserved, but bit-identical active-row output is not guaranteed.

## Editor changes and measurements

Changed gestures use `toBlob` plus asynchronous PNG reading. Versioned document
records prevent older encodes from overwriting newer paint, Clear, configure or
removal. Undo can await an earlier pending record. A failed encode or image decode
keeps the current pixels and history entry and displays the error, without an
unhandled promise rejection. A transient image-read failure can be retried;
retaining a failed encode entry does not reconstruct a PNG that was never encoded.
The entry is removed only after successful restoration.
Forced synchronous serialization
captures the latest pixels when the asynchronous PNG is not ready. It can still
pause on a large mask, including when host autosave requests it during a drag.

The stroke engine tracks the exact change in nonzero pixels, including erasure;
full-canvas alpha scanning is limited to PNG restoration. Pointerdown references
the previous document directly rather than parsing its base64 text. Duplicate
samples and exact forward collinear samples within a frame are collapsed. Turns
and reversals remain intact; already saturated gesture coverage skips further math.

The real Canvas fixture with four 4096 × 3072 editors measured:

- 100 hover moves per editor: no PNG encoding, pixel reads, canvas allocations,
  resizing or image composition.
- Size-256, softness-75%, opacity-50% curved stroke, 60 samples over 15 frames:
  pointerup 1.3 ms, one asynchronous PNG, zero synchronous PNG encodes and zero
  full-mask scans. PNG completion including frame waits took 85.5 ms. There were
  137 tile reads; the complete gesture took 448 ms, so rasterization still matters.
- Node.js size-512/softness-100% raster microbenchmark, 33 closely spaced samples:
  exact straight-path merging reduced the median from 163.7 to 11.0 ms with
  identical pixels. Curved samples remained about 156–157 ms. Approximate
  radius-based sample dropping was not adopted because it changes the path.

These are local measurements, not hardware-independent latency guarantees.

## Validation

- Full Python discovery with CUDA enabled: **128 passed**.
- Full JavaScript suite: **70 passed**; changed frontend syntax and diff checks pass.
- Mask admission regressions cover full-cache layout changes on CPU/CUDA, one
  transfer per new mask layout, subsequent streamed adapter visits, and the
  byte/entry/disabled limits. GPU headroom rejection and eviction checks still pass.
- Real Chromium 153 Canvas checks pass for both editors: continued drag after
  save/queue, one undo across checkpoints, cancellation, full erase, zero pointerdown
  JSON parses, commit notification after a checkpoint, out-of-order encodes,
  pending-record undo, Clear/undo, configure/removal races, references and reloads.
- Browser failed-PNG-encode tests preserve current pixels and the undo entry
  across repeated attempts, show the error, and produce no unhandled rejection
  for both editors. JavaScript tests also cover successful retry after a
  transient image decode failure.
- Native ComfyUI 0.37.0/frontend 1.53.6: pointer painting and Save/reload/Export
  on both editors; Paint Mask → MaskToImage → PreviewImage queues successfully.
  Preview grayscale pixels equal the saved PNG alpha exactly.
- No full diffusion generation was repeated for this review. The baseline grid/
  banding quality issue documented in [Phase 4](masked-lora-phase4.md) remains
  outside this validation; these changes do not claim to resolve it.

Regression sources: [runtime](../tests/test_masked_lora_runtime.py),
[schedule](../tests/test_masked_lora_schedule.py), [stroke](../tests/mask_stroke.test.mjs),
and [real Canvas fixture](../tests/mask_editor.browser.html). Local native exports,
benchmark script/results and test logs are in the ignored
`.release-preparation/masked-lora-review/` directory.
