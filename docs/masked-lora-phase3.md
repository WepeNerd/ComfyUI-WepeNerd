# Load LoRA Masked / Paint Mask: Phase 3 editor validation

Implemented 25 September 2026 on the Phase 2 working tree. This phase changes the
shared editor, adds a pure stroke helper, and preserves the backend adapter math,
node identifiers, positional widgets, MODEL/MASK and Paint Mask IMAGE/MASK outputs.
No packages were installed. No commit or push was made.

## Painting and persistence

- Compact, labeled Size (source pixels), Softness and Opacity controls use node
  properties. Legacy workflows retain their brush size and default to 0% softness,
  100% opacity. Rectangle disables brush-only Size/Softness; opacity still applies.
- The helper snapshots affected 128-pixel tiles at gesture start. The brush is a
  continuously swept radial footprint: each pixel retains the maximum coverage
  along the gesture. Collinear subdivisions and event/frame/coalesced batching
  cannot repeatedly deposit opacity. This avoids stamp-spacing scallops as well
  as event-rate-dependent darkening.
- For radius `r`, hard coverage is `clamp(r + .5 - distance, 0, 1)`. A soft brush
  has a flat center at `max(0, min(r - .5, r * (1 - softness)))`, then smoothstep
  falloff to zero at `r + .5`. The half-pixel border antialiases the edge. Fully
  hard brushes are handled separately. Rectangle coverage uses pixel-area overlap.
- Paint uses `M0 + (1 - M0) * opacity * coverage`; erase uses
  `M0 * (1 - opacity * coverage)`. One 50% gesture on black yields 128/255; a
  second yields 192/255. Final alpha is rounded to eight bits. Pure-helper tests
  require byte-identical coverage for subdivisions of the same polyline, and
  equation checks allow 1/255 plus floating-point tolerance.
- One changed gesture creates one undo entry. Zero-opacity gestures and unchanged
  white paint/black erasure create none. Cancel, lost capture, Escape and window
  blur restore touched pixels. Undo reuses the already committed PNG rather than
  encoding a full snapshot at pointerdown. History keeps the existing 20-action /
  64 MiB encoded-data policy.
- Historical Phase 3 behavior, superseded by the [review fixes](masked-lora-review-fixes.md):
  PNG encoding is synchronous at commit. Save/Export/Queue serialization hooks
  finish an active gesture before reading it; no pending asynchronous PNG can race
  the workflow. Stale asynchronous restore operations cannot overwrite a newer
  configure. Duplicated nodes own their settings, painting and session history.
- Grayscale preview shows coverage directly; the magenta/reference composite
  remains a fixed 45% visualization. Connected external MASK is prominently
  identified, with a notice that the editor is changing only the saved painting.

## Rendering

Reference, tint, composite, cursor and thumbnail canvases persist. Display surfaces
resize only when their actual dimensions/DPR change. One dirty animation-frame
request coalesces updates; there is no permanent loop. Hover redraws only the
cursor, while changed painting refreshes the composite and thumbnail. Source
mask resolution remains independent of the display and graph zoom. Node removal
cancels scheduled work and removes window handlers.

The Speedpaint tile engine informed the affected-area snapshot design. The new
[mask helper](../js/mask_stroke.mjs) is independent: it stores alpha coverage and
uses soft swept segments, without changing Speedpaint's RGB/pressure behavior.

## Executed checks

Environment: Windows, Chromium **153.0.0.0**, DPR **1**, Node.js **22.18.0**;
ComfyUI **0.37.0** (`b0f4b7b294ce482a2e071d9d762c133d38c7aa07`), frontend
**1.53.6**, Python **3.12.13**, PyTorch **2.11.0+cu130**, CUDA **13.0**, Comfy
Kitchen **0.2.35**, RTX **5090**. Native UI validation used an isolated CPU server
with only this custom-node package enabled and separate input/output/user paths.

| Gate | Actual result |
| --- | --- |
| Pure brush equations, hardness endpoints, stationary/revisited samples, corners, fast paths, large brushes, clipping, rectangles, cancellation | Pass; [mask_stroke.test.mjs](../tests/mask_stroke.test.mjs) |
| Legacy settings/widget order, Clear/Undo/empty geometry, override, rendering counters, DPR 1/2 display geometry, configure races | Pass; [mask_editor.test.mjs](../tests/mask_editor.test.mjs). DPR 2 is a DOM fixture check, not a physical high-DPI display test |
| Real Canvas checks on both editors | Pass: brush, soft eraser, rectangle opacity, undo, pointer cancel/lost capture/blur, zero opacity, grayscale, external override, reference replacement/undo, export/queue hooks during a stroke, PNG reload, independent duplicates |
| Native ComfyUI pointer painting, Save, Export, reload | Pass for both nodes at 1536 × 1024, size 180, softness 80%, opacity 50%. Both PNGs contain 129 alpha values (0–128); settings and alpha hashes survive exported-file validation after reload |
| Native Paint Mask Queue | Pass through MaskToImage and two PreviewImage nodes. Queued grayscale pixels exactly equal the saved PNG alpha; IMAGE remains the unchanged black reference at 1536 × 1024 |
| Complete regression suites | **64 JavaScript and 103 Python tests pass**, including all Phase 1/2 CPU and opt-in CUDA gates; modified JavaScript syntax and diff whitespace checks pass |
| Pretrained masked-LoRA image generation | Not rerun in this editor phase. Earlier full-generation quality/performance gates remain pending; the native LoRA editor was disconnected from execution in the UI fixture |

The versioned [browser fixture](../tests/mask_editor.browser.html) runs the production
editor with real Canvas/PNG APIs and a small graph/API host. Its queue/export hook
checks are distinct from the native ComfyUI Save/Export/Queue checks above.
The in-app browser did not expose a download event for native Export; the actual
downloaded JSON was read and its two alpha hashes/settings verified directly.

## Measured responsiveness

The browser fixture renders four 4096 × 3072 editors and records Performance API
frame/callback traces. Across **100 hover moves per editor**, after layout settled:

| Hover work | Count |
| --- | ---: |
| PNG encodes | 0 |
| Pixel readbacks | 0 |
| Canvas dimension assignments | 0 |
| New canvas allocations | 0 |
| Composite/reference/thumbnail image draws | 0 |

In the final run, frame intervals had a **16.7 ms median / 18.0 ms maximum**.
Instrumented hover callbacks took at most **0.1 ms**, totaling **6.9 ms** across
the run. Frame intervals include browser scheduling and are not a universal FPS
claim.

A size-256, 75%-soft, 50%-opacity stroke with 60 samples delivered in 15 groups
took **427.3 ms** including frame waits. Nonzero drawing callbacks were **17.6–51.6
ms** (mostly 23–28 ms). Pointer-up raster flush plus persistence took **55.4 ms**,
including **35.8 ms** for the single PNG encode. The stroke made 84 pixel readbacks
(affected tiles plus final serialization), no new canvases and no canvas-size
resets. These results show cheap hover and bounded gesture work; large-brush
rasterization and full-resolution PNG commit can still cause visible pauses.
No asynchronous persistence or fixed frame-rate promise is introduced.

## Reproduction and remaining scope

```powershell
node --test tests/*.test.mjs
node --check js/wn_masked_lora.js
node --check js/mask_stroke.mjs
$env:WEPENERD_TEST_CUDA = '1'
& C:\SD\ComfyUI\venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*.py'
& C:\SD\ComfyUI\venv\Scripts\python.exe -B -m http.server 8793 --bind 127.0.0.1
```

Open `/tests/mask_editor.browser.html` on that local server and run both buttons.
Performance results include per-frame samples and renderer counters. Local native
workflow/export evidence, preview PNGs, performance summary, server log and test
logs are retained under the ignored `.release-preparation/masked-lora-phase3/`.

Phase 4 can add sampling-range/CFG policies on the Phase 2 runtime while retaining
this editor's properties and positional widget compatibility. Native floating-point
and INT8 ConvRot Krea2 remain the supported backend boundary. FP8/NVFP4, new model
families, video, pressure, editor zoom/pan and morphology are not enabled here.
