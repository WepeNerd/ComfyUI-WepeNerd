# Speedpaint validation

Implemented in `speedpaint_node.py` and `js/wn_speedpaint.js`, registered as
`WN_Speedpaint` / **Speedpaint** under `WepeNerd/Image`.

## Environment

- ComfyUI 0.34.0; frontend 1.51.10; Python 3.12.13.
- Windows, Chromium 152 in the Codex browser; graph zoom checked at 100% and 82%.
- A separate CPU server on port 8199 with disposable input/output/user folders.
- `NUMBA_DISABLE_JIT=1` was used for the test server because unrelated built-in
  mesh-node imports attempted to create a cache outside the sandbox.

## Checks

- `python -B -m unittest discover -s . -p 'test_*.py'`: 121 tests passed,
  including 12 Speedpaint tests for RGB/tensor layout, exact dimensions,
  Lanczos crops, EXIF orientation, ICC handling, transparency, immutable assets,
  painted composition resizing, recovery PNGs and invalid inputs.
- `node --check js/wn_speedpaint.js` and browser-runner syntax checks passed.
- `tests/validate_speedpaint_comfy.py`: actual runtime-computed INT dimensions,
  exact RGB output, route error handling, and real VAE Encode → VAE Decode with
  `vae-ft-mse-840000-ema-pruned.safetensors` passed.
- Browser checks cover both brush shapes, continuous strokes, event-density
  independent opacity, synthetic pressure, mouse fallback, live size readout,
  eyedropper, document undo, workflow restoration, clone isolation, independent
  width/height connections, disconnect defaults, runtime preview adoption,
  stale-result rejection, failed-save recovery, imports, crop repositioning,
  and queueing during pending imports/resizes.
- Native browser mouse dragging beyond the node preserved its position and
  completed the stroke. Keyboard size adjustment and Ctrl+Z / Ctrl+Shift+Z
  worked while painting. Painting marked a saved workflow modified; Ctrl+S
  saved durable asset references and retained editor settings.

`tests/validate_speedpaint_frontend.js` is a manual browser regression runner.
Copy it temporarily into `js/` only for an isolated test server on port 8199;
click **Run Speedpaint checks**, then remove that copy. It replaces that test
session's workflow. The production extension does not load this test file.

## Limits

Physical tablet hardware, tablet drivers, different OS display scales, and a
full sampler denoising workflow were not tested. Synthetic pressure events
verify brush logic, not hardware compatibility. Workflow portability requires
copying `input/wepenerd_speedpaint`; undo history is session-local.

The implementation follows the installed DOM-widget and workflow-command APIs;
the [ComfyUI extension documentation](https://docs.comfy.org/custom-nodes/js/javascript_overview)
describes registration and frontend asset loading.
