# Example workflows

## Liquify

Open [liquify.json](liquify.json) and Queue to preview its saved warp. The original
image is embedded in the workflow. Undo restores its outline; Redo reapplies the
stroke. Try the brush and hold Original to compare. No external assets are needed.

To try an upstream image, connect Load Image to Liquify's **image** input, then
Queue once to load its preview. Further edits apply at the source resolution.

## Resolution and Speedpaint

Open [resolution-speedpaint.json](resolution-speedpaint.json) in ComfyUI. Resolution
Suggest connects to Speedpaint's width and height; Speedpaint connects to Preview
Image. Paint, then click **Queue**.

The blank example needs no external asset. Once you save painted work, keep
`ComfyUI/input/wepenerd_speedpaint` with the workflow when moving machines.
