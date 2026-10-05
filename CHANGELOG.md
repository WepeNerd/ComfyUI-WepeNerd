# Changelog

## Unreleased

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
