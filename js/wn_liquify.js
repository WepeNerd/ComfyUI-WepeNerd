import { app } from "../../scripts/app.js";

/*
 * Liquify Image node - interactive push-warp brush.
 *
 * How it works:
 *  - The original loaded image lives in `srcData` (never mutated).
 *  - A displacement field (dispX/dispY, one vector per working-resolution pixel)
 *    records, for each output pixel, where to sample FROM in the source image.
 *  - Dragging the brush pushes pixels along the cursor motion by subtracting the
 *    motion vector from the displacement field, attenuated by a smoothstep falloff.
 *  - Rendering bilinearly samples the source through the displacement field.
 *  - On pointer-up the visible canvas is exported as a base64 PNG into the hidden
 *    `image_data` widget, which the Python backend decodes at run time.
 */

const MAX_DIM = 1536;
// Images above MAX_DIM are downscaled before editing so browser canvas updates
// stay responsive and memory use remains predictable.

function styleButton(btn) {
  Object.assign(btn.style, {
    flex: "1",
    padding: "5px 6px",
    background: "#353535",
    color: "#dcdcdc",
    border: "1px solid #565656",
    borderRadius: "4px",
    cursor: "pointer",
    fontSize: "11px",
    fontFamily: "inherit",
  });
  btn.onmouseenter = () => (btn.style.background = "#444");
  btn.onmouseleave = () => (btn.style.background = "#353535");
}

function labeledSlider(labelText, min, max, value, step) {
  const wrap = document.createElement("div");
  Object.assign(wrap.style, { flex: "1", display: "flex", flexDirection: "column", gap: "2px" });

  const label = document.createElement("label");
  label.style.cssText = "font-size:10px;color:#aaa;display:flex;justify-content:space-between;";
  const name = document.createElement("span");
  name.textContent = labelText;
  const val = document.createElement("span");
  val.textContent = value;
  label.appendChild(name);
  label.appendChild(val);

  const input = document.createElement("input");
  input.type = "range";
  input.min = min;
  input.max = max;
  input.step = step;
  input.value = value;
  input.style.cssText = "width:100%;margin:0;";
  input.addEventListener("input", () => (val.textContent = input.value));

  wrap.appendChild(label);
  wrap.appendChild(input);
  return { wrap, input };
}

function setupLiquify(node) {
  if (node.__wepenerdLiquifyInitialized) return;
  node.__wepenerdLiquifyInitialized = true;

  const dataWidget = node.widgets?.find((w) => w.name === "image_data");
  if (dataWidget) {
    dataWidget.hidden = true;
    dataWidget.computeSize = () => [0, -4];
    dataWidget.type = "hidden_liquify_data";
  }

  const root = document.createElement("div");
  root.style.cssText =
    "display:flex;flex-direction:column;gap:6px;width:100%;height:100%;box-sizing:border-box;padding:4px;font-family:sans-serif;";

  const rowBtns = document.createElement("div");
  rowBtns.style.cssText = "display:flex;gap:6px;";
  const loadBtn = document.createElement("button");
  loadBtn.textContent = "Load image";
  const resetBtn = document.createElement("button");
  resetBtn.textContent = "Reset warp";
  styleButton(loadBtn);
  styleButton(resetBtn);
  rowBtns.appendChild(loadBtn);
  rowBtns.appendChild(resetBtn);

  const rowSliders = document.createElement("div");
  rowSliders.style.cssText = "display:flex;gap:10px;";
  const size = labeledSlider("Brush size", 8, 400, 80, 1);
  const strength = labeledSlider("Strength", 0.05, 1, 0.5, 0.05);
  rowSliders.appendChild(size.wrap);
  rowSliders.appendChild(strength.wrap);

  const stage = document.createElement("div");
  stage.style.cssText =
    "position:relative;flex:1;min-height:120px;display:flex;align-items:center;justify-content:center;background:#222;border:1px solid #444;border-radius:4px;overflow:hidden;";
  const stageIdleStyle = {
    background: "#222",
    borderColor: "#444",
  };
  const stageDropStyle = {
    background: "#26313f",
    borderColor: "#7aa7ff",
  };

  const canvas = document.createElement("canvas");
  canvas.style.cssText = "max-width:100%;max-height:100%;display:block;touch-action:none;cursor:crosshair;";
  const overlay = document.createElement("canvas");
  overlay.style.cssText =
    "position:absolute;max-width:100%;max-height:100%;pointer-events:none;";

  const hint = document.createElement("div");
  hint.textContent = "Load or drop an image";
  hint.style.cssText = "position:absolute;color:#777;font-size:12px;pointer-events:none;";

  stage.appendChild(canvas);
  stage.appendChild(overlay);
  stage.appendChild(hint);

  root.appendChild(rowBtns);
  root.appendChild(rowSliders);
  root.appendChild(stage);

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/*";
  fileInput.style.display = "none";
  root.appendChild(fileInput);

  node.addDOMWidget("liquify_ui", "liquify", root, { serialize: false, hideOnZoom: false });

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const octx = overlay.getContext("2d");
  let W = 0;
  let H = 0;
  let srcData = null;
  let outImage = null;
  let dispX = null;
  let dispY = null;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  function initFromImageElement(img) {
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    const scale = Math.min(1, MAX_DIM / Math.max(w, h));
    W = Math.max(1, Math.round(w * scale));
    H = Math.max(1, Math.round(h * scale));

    canvas.width = W;
    canvas.height = H;
    overlay.width = W;
    overlay.height = H;

    ctx.clearRect(0, 0, W, H);
    ctx.drawImage(img, 0, 0, W, H);
    const snapshot = ctx.getImageData(0, 0, W, H);
    srcData = new Uint8ClampedArray(snapshot.data);
    outImage = ctx.createImageData(W, H);

    dispX = new Float32Array(W * H);
    dispY = new Float32Array(W * H);

    renderRegion(0, 0, W, H);
    hint.style.display = "none";
    exportToWidget();
  }

  function loadFromURL(url) {
    const img = new Image();
    img.onload = () => initFromImageElement(img);
    img.onerror = () => console.error("[WepeNerd Liquify] could not load image");
    img.src = url;
  }

  function loadFile(file) {
    if (!file || !file.type?.startsWith("image/")) return false;
    const reader = new FileReader();
    reader.onload = () => loadFromURL(reader.result);
    reader.readAsDataURL(file);
    return true;
  }

  function getDraggedImageFile(dataTransfer) {
    const files = Array.from(dataTransfer?.files || []);
    const droppedFile = files.find((file) => file.type?.startsWith("image/"));
    if (droppedFile) return droppedFile;

    const items = Array.from(dataTransfer?.items || []);
    const imageItem = items.find((item) => item.kind === "file" && item.type?.startsWith("image/"));
    return imageItem?.getAsFile?.() || null;
  }

  function isFileDrag(dataTransfer) {
    const types = Array.from(dataTransfer?.types || []);
    if (types.includes("Files")) return true;

    const items = Array.from(dataTransfer?.items || []);
    return items.some((item) => item.kind === "file");
  }

  function setDropActive(active) {
    Object.assign(stage.style, active ? stageDropStyle : stageIdleStyle);
    if (!srcData) {
      hint.textContent = active ? "Drop image to load" : "Load or drop an image";
    }
  }

  function renderRegion(x0, y0, x1, y1) {
    if (!srcData) return;
    x0 = Math.max(0, x0 | 0);
    y0 = Math.max(0, y0 | 0);
    x1 = Math.min(W, x1 | 0);
    y1 = Math.min(H, y1 | 0);
    const out = outImage.data;
    const src = srcData;
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) {
        const i = y * W + x;
        let sx = x + dispX[i];
        let sy = y + dispY[i];
        if (sx < 0) sx = 0;
        else if (sx > W - 1) sx = W - 1;
        if (sy < 0) sy = 0;
        else if (sy > H - 1) sy = H - 1;

        const x0i = sx | 0;
        const y0i = sy | 0;
        const x1i = x0i + 1 < W ? x0i + 1 : x0i;
        const y1i = y0i + 1 < H ? y0i + 1 : y0i;
        const fx = sx - x0i;
        const fy = sy - y0i;

        const p00 = (y0i * W + x0i) * 4;
        const p10 = (y0i * W + x1i) * 4;
        const p01 = (y1i * W + x0i) * 4;
        const p11 = (y1i * W + x1i) * 4;
        const w00 = (1 - fx) * (1 - fy);
        const w10 = fx * (1 - fy);
        const w01 = (1 - fx) * fy;
        const w11 = fx * fy;

        const o = i * 4;
        out[o] = src[p00] * w00 + src[p10] * w10 + src[p01] * w01 + src[p11] * w11;
        out[o + 1] = src[p00 + 1] * w00 + src[p10 + 1] * w10 + src[p01 + 1] * w01 + src[p11 + 1] * w11;
        out[o + 2] = src[p00 + 2] * w00 + src[p10 + 2] * w10 + src[p01 + 2] * w01 + src[p11 + 2] * w11;
        out[o + 3] = src[p00 + 3] * w00 + src[p10 + 3] * w10 + src[p01 + 3] * w01 + src[p11 + 3] * w11;
      }
    }
    ctx.putImageData(outImage, 0, 0, x0, y0, x1 - x0, y1 - y0);
  }

  function applyBrush(px, py, mvx, mvy) {
    const r = parseFloat(size.input.value) * 0.5;
    const s = parseFloat(strength.input.value);
    const r2 = r * r;
    const minX = Math.max(0, Math.floor(px - r));
    const maxX = Math.min(W, Math.ceil(px + r));
    const minY = Math.max(0, Math.floor(py - r));
    const maxY = Math.min(H, Math.ceil(py + r));

    for (let y = minY; y < maxY; y++) {
      for (let x = minX; x < maxX; x++) {
        const dx = x - px;
        const dy = y - py;
        const d2 = dx * dx + dy * dy;
        if (d2 > r2) continue;
        const t = 1 - Math.sqrt(d2) / r;
        const f = t * t * (3 - 2 * t);
        const i = y * W + x;
        dispX[i] -= mvx * f * s;
        dispY[i] -= mvy * f * s;
      }
    }
    return [minX - 1, minY - 1, maxX + 1, maxY + 1];
  }

  function drawCursor(px, py) {
    octx.clearRect(0, 0, W, H);
    if (px == null) return;
    const r = parseFloat(size.input.value) * 0.5;
    octx.beginPath();
    octx.arc(px, py, r, 0, Math.PI * 2);
    octx.strokeStyle = "rgba(255,255,255,0.85)";
    octx.lineWidth = Math.max(1, W / canvas.clientWidth);
    octx.stroke();
    octx.beginPath();
    octx.arc(px, py, r, 0, Math.PI * 2);
    octx.strokeStyle = "rgba(0,0,0,0.5)";
    octx.lineWidth = octx.lineWidth * 0.5;
    octx.stroke();
  }

  function toCanvasCoords(e) {
    const rect = canvas.getBoundingClientRect();
    const sx = W / rect.width;
    const sy = H / rect.height;
    return [(e.clientX - rect.left) * sx, (e.clientY - rect.top) * sy];
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (!srcData) return;
    e.preventDefault();
    e.stopPropagation();
    canvas.setPointerCapture(e.pointerId);
    dragging = true;
    [lastX, lastY] = toCanvasCoords(e);
    drawCursor(lastX, lastY);
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!srcData) return;
    const [cx, cy] = toCanvasCoords(e);
    drawCursor(cx, cy);
    if (!dragging) return;
    e.preventDefault();
    e.stopPropagation();

    const mvx = cx - lastX;
    const mvy = cy - lastY;
    const dist = Math.hypot(mvx, mvy);
    const r = parseFloat(size.input.value) * 0.5;
    const steps = Math.max(1, Math.ceil(dist / Math.max(1, r * 0.25)));
    let dirty = null;
    for (let k = 1; k <= steps; k++) {
      const t = k / steps;
      const ix = lastX + mvx * t;
      const iy = lastY + mvy * t;
      const rect = applyBrush(ix, iy, mvx / steps, mvy / steps);
      if (!dirty) dirty = rect;
      else {
        dirty[0] = Math.min(dirty[0], rect[0]);
        dirty[1] = Math.min(dirty[1], rect[1]);
        dirty[2] = Math.max(dirty[2], rect[2]);
        dirty[3] = Math.max(dirty[3], rect[3]);
      }
    }
    if (dirty) renderRegion(dirty[0], dirty[1], dirty[2], dirty[3]);
    lastX = cx;
    lastY = cy;
  });

  function endStroke(e) {
    if (!dragging) return;
    dragging = false;
    try {
      canvas.releasePointerCapture(e.pointerId);
    } catch (_) {
      // The pointer may already be released when ComfyUI steals focus.
    }
    exportToWidget();
  }
  canvas.addEventListener("pointerup", endStroke);
  canvas.addEventListener("pointercancel", endStroke);
  canvas.addEventListener("pointerleave", () => drawCursor(null));

  loadBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    loadFile(file);
    fileInput.value = "";
  });

  let dragDepth = 0;
  function handleDragEnter(e) {
    if (!isFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepth += 1;
    setDropActive(true);
  }

  function handleDragOver(e) {
    if (!isFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  }

  function handleDragLeave(e) {
    if (!isFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) setDropActive(false);
  }

  function handleDrop(e) {
    if (!isFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepth = 0;
    setDropActive(false);

    const imageFile = getDraggedImageFile(e.dataTransfer);
    if (!imageFile) {
      console.warn("[WepeNerd Liquify] dropped file was not an image");
      return;
    }
    loadFile(imageFile);
  }

  for (const target of [root, stage]) {
    target.addEventListener("dragenter", handleDragEnter, true);
    target.addEventListener("dragover", handleDragOver, true);
    target.addEventListener("dragleave", handleDragLeave, true);
    target.addEventListener("drop", handleDrop, true);
  }

  const originalOnDragOver = node.onDragOver;
  node.onDragOver = function (e) {
    if (isFileDrag(e?.dataTransfer)) {
      setDropActive(true);
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
      return true;
    }
    return originalOnDragOver?.apply(this, arguments);
  };

  const originalOnDragDrop = node.onDragDrop;
  node.onDragDrop = function (e) {
    const imageFile = getDraggedImageFile(e?.dataTransfer);
    if (imageFile) {
      setDropActive(false);
      loadFile(imageFile);
      return true;
    }
    return originalOnDragDrop?.apply(this, arguments);
  };

  const originalOnDropFile = node.onDropFile;
  node.onDropFile = function (file) {
    if (loadFile(file)) {
      setDropActive(false);
      return true;
    }
    return originalOnDropFile?.apply(this, arguments);
  };

  resetBtn.addEventListener("click", () => {
    if (!dispX) return;
    dispX.fill(0);
    dispY.fill(0);
    renderRegion(0, 0, W, H);
    exportToWidget();
  });

  function exportToWidget() {
    if (!dataWidget || !srcData) return;
    try {
      dataWidget.value = canvas.toDataURL("image/png");
      node.setDirtyCanvas?.(true, true);
    } catch (err) {
      console.error("[WepeNerd Liquify] export failed:", err);
    }
  }

  function restoreFromWidget() {
    if (dataWidget?.value) {
      loadFromURL(dataWidget.value);
    }
  }

  setTimeout(restoreFromWidget, 50);

  if (node.size[0] < 320) node.size[0] = 340;
  if (node.size[1] < 420) node.size[1] = 460;
}

app.registerExtension({
  name: "WepeNerd.LiquifyImage",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const className = nodeData?.name || nodeData?.comfyClass;
    if (className !== "WN_LiquifyImage") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
      setupLiquify(this);
      return result;
    };
  },
});
