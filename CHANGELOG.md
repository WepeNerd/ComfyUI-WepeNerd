# Changelog

## Unreleased

- Add H3 Ripple (from the Experimental review build): **Ripple Clip**, **Ripple Apply** and
  **Ripple Output** propagate an edit made to one frame (or a few) of a shot through the whole shot with
  MiniMax H3 FL2VA and the `H3_Ripple_v1` LoRA, plus an example workflow. Compared with the
  review build:
  - Edits can be dropped onto a marker card or pasted with Ctrl+V, not only uploaded; a
    pasted image no longer becomes a stray Load Image node. Each card warns at once when
    an edit's aspect ratio differs from the frame.
  - Re-running after changing only the edits reuses the decoded window, the encoded prompt
    (the 32B text encoder is not run again) and the encoded source clip.
  - Ripple Output saves the shot (with source audio) and the comparison as MP4 through
    Save Video and previews them; the example workflow drops four Create/Save Video nodes.
  - `resolution` is a plain short edge (352, 384, 544, 768) and `length` a plain frame count
    with its duration; workflows saved with the older longer labels still load and run.
  - Changing the length keeps unedited markers at the same place in the window and edited
    ones on their frame. Apply warns when an edit was made for a frame the marker has
    since left, and when no edit differs from the source.
  - Thumbnails are cached by the browser until the video changes and keep showing while a
    marker or the window is dragged; the marker panel redraws at most once per frame.
  - The video list is shared with Load Video (cached), instead of walking the whole input
    folder each time the node list refreshes.

- Sigma Curve: presets now match ComfyUI's schedulers step for step. Poly-exponential
  was mirrored (it held high sigmas, then dropped sharply). Flow shift 3/6 and Align
  Your Steps ended on `sigma_min` instead of ComfyUI's last real step, and Align Your
  Steps used rounded noise levels. Presets are fitted more tightly (up to 12 points;
  Karras error drops from about 2% to 0.4%), and flow/AYS are fitted for the current
  step count.
- Sigma Curve: add an optional `model` input. When connected, the output uses the
  model's sigma range (1.0 for flow models, shift patches included) and the
  `sigma_max`/`sigma_min` widgets update to match after a run.
- Sigma Curve: Paste now reads scientific notation and printed tensors; it previously
  turned `1e-05` into `1, -5`. A curve with exactly 64 points no longer drops its end
  point in the graph, so the preview again matches the output.
- Add Frame Slicer: keeps frames `start` up to `stop` of an IMAGE batch, with the same
  inputs as RES4LYF's Frames Slice. A range that would keep no frames gives a clear
  error instead of an empty batch.
- Add Save Video: a streamlined take on VHS Video Combine. Saves MP4 (H.264, H.265
  10-bit, AV1 10-bit), WebM VP9, MOV ProRes, MKV FFV1 16-bit lossless, GIF or WebP
  with PyAV in one pass: audio is muxed into the same file and trimmed to the video,
  no first-frame PNG or `-audio` duplicate is written, and the workflow is embedded
  so the file loads back into ComfyUI. Transparency comes from a `mask` input (the
  Load Video/Load Image convention) or a fourth image channel. Outputs `filepath`.
- Load Video: fix "Video file not found or unsupported" sometimes showing after dragging
  a video onto the node. The node asked about the file before its upload finished; it
  now waits and asks again, and re-reads the file once the preview has loaded.
- Load Video: remove `start_time` and `duration`; pick the clip with
  `skip_first_frames` and `frame_load_cap`. A large skip now jumps to the keyframe
  before it instead of decoding every skipped frame (about 10× faster deep into a
  long video), keeping exactly the same frames. Workflows saved with `start_time` or
  `duration` get them converted to skip and cap.
- Load Video: the readout and looping preview follow connected inputs. Driving
  `frame_load_cap` or `frame_rate` from Duration Selector (or a primitive, also through
  reroutes, Number Convert and KJNodes Get/Set nodes) updates them live as you change
  that node. A connected value only known at run time uses the setting's own value,
  so the preview still follows the cap instead of looping the whole video.
- Zoom Crop: replace the position dropdown with a one-click 5×5 picker that previews
  the kept area, supports click-and-drag, hover preview and arrow keys, and never
  opens the 25-item list. Saved workflows keep their position.
- Load Video: `frame_load_cap` now defaults to 121 so a long video doesn't load whole by
  accident. The preview loops over just the clip that will load and follows changes
  to the cap, skip and other settings.
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
