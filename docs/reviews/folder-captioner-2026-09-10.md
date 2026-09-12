# Folder Captioner validation

## Implementation

- Registered `WN_FolderCaptioner` under `WepeNerd/Local AI` with the existing
  `GGUF_LLM_CONFIG` connection. Existing node IDs and inputs are preserved.
- `_run_payloads` retains its list-returning contract and delegates to a shared
  iterator. The folder node consumes that iterator incrementally, keeping one
  model acquisition and writing each result before encoding the next image.
- Folder discovery preflights caption collisions and destinations. Skip mode
  preserves existing files, including a file created during inference. Writes
  publish completed UTF-8 files atomically; incomplete responses are not saved.
- Bundled character, style, and refiner skills use the existing cached Markdown
  skill loader. Research provenance and untested training assumptions are in
  [krea2-captioning.md](../krea2-captioning.md).

## Automated checks

`C:\SD\ComfyUI\venv\Scripts\python.exe -B -m unittest discover -s tests -t . -p 'test_*.py'`

151 tests ran: 150 passed, one skipped. The skipped test needs permission to create
a Windows symlink. All 24 other folder tests passed, including sequential saves,
resume, subfolders, collisions, exact stems, cancellation, empty folders, corrupt
and multipage images, EXIF orientation, transparency, UTF-8, overwrite failure,
two concurrent-writer cases, and failure to preserve the requested trigger.

Full package import registered Folder Captioner and 24 total nodes. The import
check supplied an aiohttp route table for unrelated existing frontend routes;
this checks package registration, not a running ComfyUI queue or browser UI.
`git diff --check` passed.

## Real vision-model smoke check

Used the installed Qwen 3.8 27B Q4 GGUF and matching projector, with only eight GPU
layers and no ComfyUI VRAM handoff. This avoids unloading the user's existing
ComfyUI models. The disposable dataset contained copies of Matplotlib's bundled
Grace Hopper portrait. No user dataset was modified.

The first run saved two character captions, one style caption, and one refiner
caption. Every repeat run skipped all existing captions. Exact character/style
markers were preserved. The process released its own llama-server on completion.
Raw output: [initial smoke report](folder-captioner-2026-09-10-smoke.json).

Manual review found limitations: the style caption loosely assigned the flag's
stars to the background and transcribed only part of the name badge; the refiner
described the eyeglasses as wire-rimmed despite visually ambiguous construction.
The templates were tightened to keep attributes on the correct object and avoid
uncertain material/construction claims. These are semantic model errors; exact
trigger checks and complete-file writes cannot detect every such error.

The [follow-up report](folder-captioner-2026-09-10-followup.json) contains two more
successful writes and skip-on-repeat checks with the revised templates. The style
caption separated the flag from the blue backdrop more clearly, but still inferred
a studio and used ambiguous left/right descriptions. The refiner still asserted
wire-rimmed construction. Prompt refinements did not eliminate hallucinations;
review small structural details and OCR before using these captions for training.

No Krea2 LoRA was trained, and no comparison across caption policies, different
captioning models, car datasets, ethnicity labels, or anatomy datasets was run.
Results validate the local execution path and basic template behavior, not a
universal captioning optimum or complete factual accuracy.
