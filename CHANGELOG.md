# Changelog

## Unreleased

- Add Save Video: a streamlined take on VHS Video Combine. Saves MP4 (H.264, H.265
  10-bit, AV1 10-bit), WebM VP9, MOV ProRes, MKV FFV1 16-bit lossless, GIF or WebP
  with PyAV in one pass: audio is muxed into the same file and trimmed to the video,
  no first-frame PNG or `-audio` duplicate is written, and the workflow is embedded
  so the file loads back into ComfyUI. Transparency comes from a `mask` input (the
  Load Video/Load Image convention) or a fourth image channel. Outputs `filepath`.
- Load Video: the readout and looping preview follow connected inputs. Driving
  `frame_load_cap`, `frame_rate` or `duration` from Duration Selector (or a primitive,
  also through reroutes and KJNodes Get/Set nodes) updates them live as you change
  that node.
- Zoom Crop: replace the position dropdown with a one-click 5×5 picker that previews
  the kept area, supports click-and-drag, hover preview and arrow keys, and never
  opens the 25-item list. Saved workflows keep their position.
- Load Video: add `start_time` and `duration` (in seconds). A late start jumps to the
  nearest keyframe instead of decoding everything before it.
- Load Video: `frame_load_cap` now defaults to 121 so a long video doesn't load whole by
  accident. The preview loops over just the clip that will load and follows changes
  to the cap, start, duration and other settings.
- Load Video: show the source and the clip that will load (frames, FPS, length, start,
  RAM) on the node before running, with a note when nothing would load or a preset's
  range is exceeded.
- Load Video: format presets now come from `frame_number_presets.json`, shared with
  Duration Selector; picking one sets the model's frame rate. Old format names in
  saved workflows map to the matching preset. Adds a Cosmos 1.0 preset to both nodes.
- Load Video: add a `mask` output (transparency, for WebM/MOV/GIF with alpha) after the
  existing ones.
- Load Video: remove `custom_width` and `custom_height`; resize with a separate node.
  Presets no longer change the image size. Saved workflows keep their other settings.
- Load Video: list input videos faster (no per-file stat on Windows, brief cache).
- Add Duration Selector (first released as Frame Number Selector): a preset-filtered
  dropdown of legal frame counts for LTX (8n+1), MiniMax H3 (17n+5), Wan and Hunyuan
  (4n+1) and Mochi (6n+1), outputting `frames`, `fps` and `seconds`. A seconds field
  stays in sync with frames: typing seconds picks the nearest legal count, and its
  arrows step one count at a time. Switching presets keeps about the same length.
  Presets are editable in `frame_number_presets.json`.
- Mask editor: add **Fill enclosed areas**, which fills the inside of closed painted
  outlines at the current Opacity (Paint Mask, Qwen Edit Mask, Load LoRA Masked).
- Add Qwen Edit Mask: paint the region a Qwen-Image 2.1 edit should change and get the
  image with an outline, tint or solid guide on the Qwen reference grid, the clean
  image, the mask, a mask image, and a ready prompt. Uses the Paint Mask editor.
- Add Sigma Curve: a graph editor that outputs SIGMAS for SamplerCustom, with presets,
  log view, step snapping, numeric point entry, undo/redo, and copy/paste of sigma lists.

## 0.2.0

- Add Liquify to core with optional IMAGE batch input and full-resolution output.
- Preserve original imports and editable strokes across workflow saves, with Undo,
  Redo, Reset, and Original comparison controls.
- Keep existing Liquify node IDs, outputs, and flattened paintings compatible.
- Include a portable Liquify example and automated backend/browser checks.
- Update Experimental to 0.2.0 or newer alongside core to avoid duplicate nodes.

## 0.1.0

- Focus the core package on resolution tools, Speedpaint, Slider, and masked Krea2 LoRA.
- Move Local AI and experimental tools to independent packages.
- Improve Speedpaint cursor tracking, incremental drawing, background saves, and undo memory use.
- Preserve saved node IDs and existing painting assets.
