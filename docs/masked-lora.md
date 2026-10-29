# Masked LoRA reference

For node controls and workflow usage, see [Load LoRA Masked](nodes.md#load-lora-masked).

## Supported models and adapters

Load LoRA Masked supports native Krea2 floating-point and INT8 ConvRot models with
linear LoRA or full/factored LoKr adapters. Use ComfyUI's native diffusion model
loader. FP8, NVFP4, GGUF, custom quantized loaders, other model families and video
are unsupported.

Spatial contributions apply to supported image projections. Text, timestep,
modulation and normalization adapters are omitted and reported. Unsupported
adapter tensors fail validation before patches are installed. A full mask can
therefore differ from a global LoRA loader; attention and denoising can also
spread changes outside the painted region.

Native KSampler, CFGGuider and BasicGuider supply the sampling metadata used for
mask batches, sampling ranges and positive-only mode. Cropped conditioning,
tiled/context sampling, sample rearrangement and multi-GPU sampling are unsupported.

## Cache settings

Set these integer environment variables before starting ComfyUI. Units are MiB.

| Variable | Default | Purpose |
|---|---:|---|
| `WEPENERD_MASKED_LORA_CACHE_MB` | 256 | Combined adapter/mask cache per sampling run; 0 streams each use. |
| `WEPENERD_MASKED_LORA_HEADROOM_MB` | 1024 | Free-memory headroom before cache-miss device allocations. |
| `WEPENERD_MASKED_LORA_FILE_CACHE_MB` | 256 | CPU parsed-file cache; 0 disables retained entries. |

The run cache holds at most 512 entries and is released on completion, error or
interruption. Admitted adapters remain resident while overflow streams. New
projected masks displace adapters first; device-memory pressure can evict entries.
Streaming reduces retained memory but cannot eliminate adapter working memory.
Limits exclude the base model, activations, temporary calculations and allocator
reservations. Invalid values produce a warning and use the defaults.

The CPU cache retains at most four files within its byte limit. File metadata
changes invalidate a cached adapter when the workflow is queued. If a file was
replaced while preserving all metadata, restart ComfyUI or clear the cache from
the running ComfyUI Python process:

```python
import nodes

nodes.NODE_CLASS_MAPPINGS["WepeNerdLoadLoraMasked"].clear_file_cache()
```

## Validation

Run from this repository with ComfyUI's Python environment and its root on
`PYTHONPATH`:

```sh
python -B -m unittest discover -s tests -p 'test_masked_lora*.py'
node --test tests/mask_editor.test.mjs tests/mask_stroke.test.mjs
```

Set `WEPENERD_TEST_CUDA=1` to include optional CUDA checks. Serve the repository
with `python -m http.server 8793 --bind 127.0.0.1` and open
`http://127.0.0.1:8793/tests/mask_editor.browser.html` for the browser fixture.

[benchmark_masked_lora.py](../tests/benchmark_masked_lora.py) measures layer-level
runtime costs. [validate_masked_lora_schedule.py](../tests/validate_masked_lora_schedule.py)
runs fixed-seed generations with supplied local model files; use `--help` for its
arguments and `--baseline-only` to render at zero adapter strength.

Earlier local generation checks showed grid/banding artifacts even with zero
adapter strength. A clean visual-quality baseline has not been established.
Numerical, scheduling and editor tests do not establish generated image quality
or a preferred sampling range. Benchmark timings are specific to their hardware
and tensor shapes.
