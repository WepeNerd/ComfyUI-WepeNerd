// Ripple preview, crop controls, timeline and edited-frame attachments.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    DURATION_OPTIONS, FPS, MAX_DURATION_FRAMES, aspectMismatch, cropForAspect, cropMatches, dragCrop, durationFrames, fitWindow,
    gridFrames, gridIndexAt, hitMarker, hitTest, makeLayout, moveMarker, outputGeometry, parseCrop, parseMarkers,
    resizeWindow, retainSourceFrames, seekTime, suggestFrame, tickStep, timecode,
} from "./ripple_timeline.mjs";

const MARGIN = 14;
const TRACK_TOP = 50;
const TRACK_HEIGHT = 30;
const HEIGHT = 112;
const COLORS = ["#ffcc33", "#ff7a59", "#5fd0a0", "#c58cff"];
const SETTINGS = ["duration", "megapixels", "crop", "allow_upscale", "resize_method", "output_width", "output_height"];
const SAVED = ["window_start", "markers", ...SETTINGS];
const widgetOf = (node, name) => node.widgets?.find((widget) => widget.name === name);
const stylesheet = document.createElement("link");
stylesheet.rel = "stylesheet";
stylesheet.href = new URL("./ripple.css", import.meta.url).href;
document.head.append(stylesheet);

function hideWidget(widget) {
    if (!widget || widget._wnHidden) return;
    widget._wnHidden = true;
    widget.hidden = true;
    widget.computeSize = () => [0, -4];
    widget.draw = () => {};
    widget.mouse = () => false;
}

function setValue(node, name, value) {
    const widget = widgetOf(node, name);
    if (!widget || widget.value === value) return;
    widget.value = value;
    widget.callback?.(value, app.canvas, node);
}

// Keep the selection in node.properties too, so it survives reloads whatever the widget order.
function remember(node) {
    node.properties ??= {};
    node.properties.wn_ripple = Object.fromEntries(SAVED.map((name) => [name, widgetOf(node, name)?.value]));
}

function restore(node) {
    const saved = node.properties?.wn_ripple;
    if (!saved) return;
    for (const name of SAVED) {
        const widget = widgetOf(node, name);
        if (!widget || saved[name] === undefined) continue;
        widget.value = saved[name];
    }
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (key === "style") Object.assign(node.style, value);
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value);
    }
    for (const child of children) if (child != null) node.append(child);
    if (tag === "input" && attrs.type === "number" && attrs.onchange) node.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault(); event.stopPropagation();
        attrs.onchange({ target: node });
        node.blur();
    });
    return node;
}

function installRipple(node) {
    const state = {
        info: null, video: null, request: 0, player: null, playing: false, drag: null, active: 0,
        executed: null, pendingSeek: null, thumbTimer: null, panelHeight: 110, advanced: false,
        thumbs: new Map(), editSizes: new Map(), renderQueued: false, durationRequest: 0,
        cropEditing: false, cropRatio: "Original", cropDrag: null, previewObservers: [], removed: false,
    };
    for (const name of SAVED) hideWidget(widgetOf(node, name));
    const settings = () => Object.fromEntries(SETTINGS.map((name) => [name, widgetOf(node, name)?.value]));
    const geometry = () => state.info && !state.info.error ? outputGeometry(state.info.width, state.info.height, settings()) : null;
    const cropValue = () => parseCrop(widgetOf(node, "crop")?.value);
    const sourceChanged = (marker) => marker.edited && marker.edited_source &&
        (marker.edited_source.video !== state.video || marker.edited_source.version !== String(state.info?.version ?? ""));
    const applyConsumers = () => {
        const slot = node.outputs?.findIndex((output) => output.name === "ripple") ?? -1;
        return slot >= 0 ? [...new Set(node.getOutputNodes(slot) ?? [])].filter((target) => target.type === "WN_RippleApply") : [];
    };
    const connectedEdit = (target, id) => target.inputs?.some((input) => input.link != null &&
        (input.name === `edited_anchor_${id}` || input.name === "edited_images"));
    let cropOverlay = null, cropBox = null, cropObserver = null;

    const current = () => {
        const available = state.info && !state.info.error ? gridFrames(state.info.duration, state.info.first_time) : 0;
        const requested = Math.min(MAX_DURATION_FRAMES, durationFrames(widgetOf(node, "duration")?.value));
        const start = Number(widgetOf(node, "window_start")?.value) || 0;
        const fitted = available ? fitWindow(available, start, requested) : { start, length: requested };
        const markers = parseMarkers(widgetOf(node, "markers")?.value, fitted.length);
        return { available, requested, ...fitted, markers, frames: markers.map((m) => m.frame) };
    };

    function commit(start, markers, active = state.active) {
        const now = current();
        const fitted = now.available ? fitWindow(now.available, start, now.length) : { start, length: now.length };
        const length = fitted.length ?? now.length;
        let list = markers.map((m) => ({ ...m, frame: Math.min(Math.max(0, m.frame), length - 1) }));
        const activeMarker = list[active];
        list = parseMarkers(JSON.stringify(list), length);
        state.active = Math.max(0, activeMarker ? list.findIndex((m) => m.id === activeMarker.id) : 0);
        state.durationRequest = 0;
        setValue(node, "duration", length / FPS);
        setValue(node, "window_start", fitted.start);
        setValue(node, "markers", JSON.stringify(list));
        remember(node);
        if (!state.playing && list[state.active]) showFrame(fitted.start + list[state.active].frame);
        requestRender();
        node.setDirtyCanvas?.(true, true);
    }

    function resizeShot(next, before = current()) {
        const activeId = before.markers[state.active]?.id;
        setValue(node, "duration", next.length / FPS);
        const markers = retainSourceFrames(before.markers, before.start, next.start, next.length);
        commit(next.start, markers, Math.max(0, markers.findIndex((marker) => marker.id === activeId)));
    }

    function changeDuration(seconds) {
        if (!(Number(seconds) > 0)) return;
        const now = current();
        const requested = durationFrames(seconds);
        resizeShot(resizeWindow(now.available, now.start, now.length, "right", now.start + requested), now);
        state.durationRequest = requested;
    }

    function changeSetting(name, value) {
        setValue(node, name, value);
        if (name === "megapixels") { setValue(node, "output_width", 0); setValue(node, "output_height", 0); }
        remember(node); updateCropOverlay(); requestRender(); node.setDirtyCanvas?.(true, true);
    }

    // The marker panel is rebuilt at most once per animation frame (dragging commits on every mouse move).
    function requestRender() {
        if (state.renderQueued || state.removed) return;
        state.renderQueued = true;
        const run = () => { if (!state.renderQueued || state.removed) return; state.renderQueued = false; renderPanel(); };
        requestAnimationFrame(run);
        setTimeout(run, 120); // background tabs pause animation frames
    }

    // ---- preview player ----------------------------------------------------------------------
    function showFrame(k) {
        const player = state.player;
        if (!player || !state.info || state.info.error) return;
        const time = seekTime(k, state.info);
        if (player.seeking) { state.pendingSeek = time; return; }
        player.currentTime = time;
    }

    function updateCropOverlay() {
        if (!cropOverlay || !state.player) return;
        const host = cropOverlay.parentElement;
        const aspect = state.info?.width / state.info?.height || state.player.videoWidth / state.player.videoHeight || 16 / 9;
        const width = Math.min(host.clientWidth, host.clientHeight * aspect), height = width / aspect;
        Object.assign(cropOverlay.style, { width: `${width}px`, height: `${height}px`, left: `${(host.clientWidth - width) / 2}px`, top: `${(host.clientHeight - height) / 2}px` });
        cropOverlay.hidden = !state.cropEditing && !widgetOf(node, "crop")?.value;
        cropOverlay.classList.toggle("is-editing", state.cropEditing);
        const [x, y, w, h] = cropValue();
        Object.assign(cropBox.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
    }

    function setCrop(crop) {
        setValue(node, "crop", crop ? JSON.stringify(parseCrop(crop).map((v) => Number(v.toFixed(8)))) : "");
        remember(node);
        updateCropOverlay();
        requestRender();
        node.setDirtyCanvas?.(true, true);
    }

    function attachCropOverlay(player) {
        const host = player.parentElement;
        if (!host || host === cropOverlay?.parentElement) { updateCropOverlay(); return; }
        cropObserver?.disconnect();
        cropOverlay?.remove();
        host.classList.add("wn-ripple-video");
        cropBox = el("div", { class: "ripple-crop-box", "aria-label": "Crop selection" });
        for (const handle of ["nw", "ne", "sw", "se"]) cropBox.append(el("span", { class: `ripple-crop-handle ${handle}`, "data-crop-handle": handle }));
        cropOverlay = el("div", { class: "ripple-crop-overlay" }, cropBox);
        host.append(cropOverlay);
        cropOverlay.addEventListener("pointerdown", (event) => {
            if (!state.cropEditing || event.button !== 0) return;
            event.preventDefault(); event.stopPropagation();
            const bounds = cropOverlay.getBoundingClientRect();
            state.cropDrag = { crop: cropValue(), handle: event.target.dataset.cropHandle || "move", x: event.clientX, y: event.clientY, width: bounds.width, height: bounds.height };
            cropOverlay.setPointerCapture(event.pointerId);
        });
        cropOverlay.addEventListener("pointermove", (event) => {
            if (!state.cropDrag) return;
            event.preventDefault(); event.stopPropagation();
            const drag = state.cropDrag;
            const preset = state.cropRatio === "Original" ? state.info.width / state.info.height : state.cropRatio === "Custom" ? null : state.cropRatio.split(":").map(Number).reduce((a, b) => a / b);
            const ratio = preset ? preset / (state.info.width / state.info.height) : null;
            setCrop(dragCrop(drag.crop, drag.handle, (event.clientX - drag.x) / drag.width, (event.clientY - drag.y) / drag.height, ratio));
        });
        const finish = (event) => {
            if (!state.cropDrag) return;
            event.stopPropagation(); state.cropDrag = null;
            if (cropOverlay.hasPointerCapture(event.pointerId)) cropOverlay.releasePointerCapture(event.pointerId);
            scheduleThumbs();
        };
        cropOverlay.addEventListener("pointerup", finish);
        cropOverlay.addEventListener("pointercancel", finish);
        cropObserver = new ResizeObserver(updateCropOverlay);
        cropObserver.observe(host);
        updateCropOverlay();
    }

    function attachPlayer(player) {
        if (state.player === player) { attachCropOverlay(player); return; }
        state.player = player;
        player.autoplay = false; player.muted = true; player.loop = false; player.controls = false; player.playsInline = true;
        player.pause();
        attachCropOverlay(player);
        player.addEventListener("seeked", () => {
            if (state.pendingSeek != null) { const time = state.pendingSeek; state.pendingSeek = null; player.currentTime = time; }
        });
        player.addEventListener("loadeddata", () => {
            const now = current();
            showFrame(now.start + (now.frames[state.active] ?? 0));
            updateCropOverlay();
        });
        player.addEventListener("timeupdate", () => {
            if (!state.playing) return;
            const { start, length } = current();
            const k = gridIndexAt(player.currentTime, state.info);
            if (k >= start + length || k < start) player.currentTime = seekTime(start, state.info);
            node.setDirtyCanvas?.(true, false);
        });
    }

    function placePreview(widget) {
        const timeline = node.widgets?.findIndex((w) => w.name === "ripple_timeline");
        const position = node.widgets?.indexOf(widget);
        if (timeline >= 0 && position > timeline) {
            node.widgets.splice(position, 1);
            node.widgets.splice(timeline, 0, widget);
            node.setDirtyCanvas?.(true, true);
        }
    }

    function sizePreview(widget) {
        if (widget._wnRippleSized) return;
        widget._wnRippleSized = true;
        queueMicrotask(() => {
            widget.computeLayoutSize = () => {
                const video = state.player;
                const aspect = video?.videoWidth && video.videoHeight ? video.videoWidth / video.videoHeight : 16 / 9;
                const margin = widget.margin ?? 10;
                const height = Math.max(120, Math.min(260, (node.size[0] - 2 * margin) / aspect)) + 2 * margin;
                return { minHeight: height, maxHeight: height, minWidth: 0 };
            };
            node.setSize?.([node.size[0], node.computeSize()[1]]);
            node.setDirtyCanvas?.(true, true);
        });
    }

    function togglePlay() {
        const player = state.player;
        if (!player) return;
        if (state.playing) {
            state.playing = false;
            player.pause();
            const now = current();
            showFrame(now.start + (now.frames[state.active] ?? 0));
        } else {
            state.playing = true;
            player.currentTime = seekTime(current().start, state.info);
            player.play().catch(() => { state.playing = false; updatePlayback(); });
            const tick = () => { if (!state.playing) return; node.setDirtyCanvas?.(true, false); requestAnimationFrame(tick); };
            requestAnimationFrame(tick);
        }
        updatePlayback();
        node.setDirtyCanvas?.(true, true);
    }

    // The core video_upload combo adds a "video" DOM widget with a <video>; take it over.
    const addDOMWidget = node.addDOMWidget;
    node.addDOMWidget = function (name, type, element) {
        const widget = addDOMWidget.apply(this, arguments);
        if (type === "video") {
            const grab = () => {
                const player = element.querySelector?.("video") ?? (element.tagName === "VIDEO" ? element : null);
                if (!player) return;
                attachPlayer(player);
                sizePreview(widget);
            };
            const observer = new MutationObserver(grab);
            observer.observe(element, { childList: true, subtree: true });
            state.previewObservers.push(observer);
            grab();
            placePreview(widget);
        }
        return widget;
    };
    const findExisting = () => {
        for (const widget of node.widgets ?? []) {
            const player = widget.element?.querySelector?.("video");
            if (player) { attachPlayer(player); placePreview(widget); sizePreview(widget); }
        }
    };

    // ---- source facts --------------------------------------------------------------------------
    function fetchInfo(video, attempt = 0) {
        if (state.removed) return;
        const request = ++state.request;
        state.video = video;
        state.info = null;
        requestRender();
        if (!video) return;
        api.fetchApi(`/wepenerd/ripple/info?video=${encodeURIComponent(video)}`)
            .then(async (response) => ({ ok: response.ok, data: await response.json().catch(() => ({})) }))
            .catch(() => ({ ok: false, data: { error: "Couldn't reach ComfyUI." } }))
            .then(({ ok, data }) => {
                if (request !== state.request || state.removed) return;
                if (!ok && attempt < 8) {
                    setTimeout(() => { if (request === state.request) fetchInfo(video, attempt + 1); }, 300 * (attempt + 1));
                    return;
                }
                state.info = ok ? data : { error: data.error || "Couldn't read this video." };
                const now = current();
                commit(now.start, now.markers);
                state.durationRequest = now.requested;
                findExisting();
                updateCropOverlay();
            });
    }

    // ---- marker panel (DOM): one row per edited frame ---------------------------------------------
    const panel = el("div", { class: "wn-ripple-panel" });
    const content = el("div", { class: "ripple-content" });
    const fileInput = el("input", { type: "file", accept: "image/*", style: { display: "none" } });
    panel.append(fileInput, content);
    let attachTarget = null;
    // Where an edit for marker `index` belongs; checked again when the upload returns.
    function targetFor(index) {
        const now = current();
        const marker = now.markers[index];
        if (!marker) return null;
        return { index, id: marker.id, frame: marker.frame, start: now.start, video: state.video, version: String(state.info?.version ?? ""), crop: cropValue() };
    }
    async function uploadEdit(target, file) {
        if (!file || target == null || state.uploading != null) return;
        if (!file.type?.startsWith("image/")) {
            state.panelError = "That isn't an image. Drop or paste a PNG, JPEG or WebP edit.";
            requestRender();
            return;
        }
        state.panelError = null;
        state.uploading = target.index;
        requestRender();
        const body = new FormData();
        const name = file.name && file.name !== "image.png" ? file.name : `ripple_edit_f${String(target.start + target.frame).padStart(5, "0")}.png`;
        body.append("image", file, name);
        body.append("subfolder", "ripple_edits");
        body.append("type", "input");
        try {
            const response = await api.fetchApi("/upload/image", { method: "POST", body });
            if (!response.ok) throw new Error(`upload failed (${response.status})`);
            const data = await response.json();
            const now = current();
            const markers = now.markers.map((m) => ({ ...m }));
            const index = markers.findIndex((marker) => marker.id === target.id);
            if (state.video !== target.video || String(state.info?.version ?? "") !== target.version || !cropMatches(cropValue(), target.crop) ||
                now.start !== target.start || markers[index]?.frame !== target.frame) {
                throw new Error("the selection changed while uploading. Add the edit again for the intended frame.");
            }
            markers[index].edited = `${data.subfolder ? `${data.subfolder}/` : ""}${data.name}`;
            markers[index].edited_at = now.start + markers[index].frame;
            markers[index].edited_crop = target.crop;
            markers[index].edited_source = { video: target.video, version: target.version };
            state.uploading = null;
            commit(now.start, markers, index);
        } catch (error) {
            state.uploading = null;
            state.panelError = `Couldn't attach the image: ${error.message}`;
            requestRender();
        }
    }
    fileInput.addEventListener("change", () => {
        const file = fileInput.files?.[0];
        fileInput.value = "";
        uploadEdit(attachTarget, file);
    });
    const imageFile = (list) => [...(list ?? [])].find((item) => item.type?.startsWith("image/"));

    function frameURL(absolute, download = false) {
        const video = widgetOf(node, "video")?.value;
        const query = new URLSearchParams({ video, frame: String(absolute), v: String(state.info?.version ?? "") });
        const preview = settings();
        if (Boolean(Number(preview.output_width)) !== Boolean(Number(preview.output_height))) {
            preview.output_width = 0; preview.output_height = 0;
        }
        for (const [key, value] of Object.entries(preview)) if (key !== "duration" && value != null) query.set(key, String(value));
        if (download) query.set("download", "1");
        // The version token lets the browser keep thumbnails until the file changes.
        return api.apiURL(`/wepenerd/ripple/frame?${query}`);
    }
    function inputURL(relative) {
        const parts = relative.split("/");
        const name = parts.pop();
        return api.apiURL(`/view?filename=${encodeURIComponent(name)}&subfolder=${encodeURIComponent(parts.join("/"))}&type=input`);
    }

    function updatePlayback() {
        const button = panel.querySelector("[data-play]");
        if (button) {
            button.textContent = state.playing ? "Pause shot" : "Play shot";
            button.setAttribute("aria-pressed", String(state.playing));
        }
    }

    function sizingControls(now, canvas) {
        const options = (values, selected) => values.map(([value, label]) => el("option", { value, ...(String(value) === String(selected) ? { selected: "" } : {}) }, label));
        const value = Number(widgetOf(node, "megapixels")?.value) || 0.25;
        const preset = [0.25, 0.5, 1].includes(value) ? String(value) : "custom";
        const size = el("label", { class: "ripple-field" }, el("span", {}, "Size"), el("select", {
            "aria-label": "Size in megapixels", "data-ripple-focus": "size", onchange: (event) => {
                if (event.target.value === "custom") { state.customMP = true; requestRender(); }
                else { state.customMP = false; changeSetting("megapixels", Number(event.target.value)); }
            },
        }, ...options([["0.25", "0.25 MP"], ["0.5", "0.5 MP"], ["1", "1 MP"], ["custom", "Custom"]], state.customMP ? "custom" : preset)));
        if (state.customMP || preset === "custom") size.append(el("input", { type: "number", min: "0.01", step: "0.05", value,
            "aria-label": "Custom megapixels", "data-ripple-focus": "megapixels", onchange: (event) => {
                const next = Number(event.target.value); if (next > 0) changeSetting("megapixels", next);
            } }));
        const duration = el("label", { class: "ripple-field" }, el("span", {}, "Duration"), el("select", {
            "aria-label": "Duration in frames and seconds", "data-ripple-focus": "duration",
            onchange: (event) => changeDuration(Number(event.target.value) / FPS),
        }, ...options(DURATION_OPTIONS, now.length)));
        const cropped = Boolean(widgetOf(node, "crop")?.value);
        const framing = el("label", { class: "ripple-field" }, el("span", {}, "Framing"), el("select", {
            "data-ripple-focus": "framing", onchange: (event) => {
                state.cropEditing = event.target.value === "crop";
                setCrop(state.cropEditing ? cropValue() : null);
            },
        }, ...options([["original", "Original"], ["crop", "Crop"]], cropped ? "crop" : "original")));
        const config = settings();
        const uncapped = outputGeometry(state.info.width, state.info.height, { ...config, allow_upscale: true });
        const sourceLimited = !config.allow_upscale && (uncapped.width > canvas.width || uncapped.height > canvas.height);
        const group = el("div", { class: "ripple-sizing" }, el("div", { class: "ripple-settings" }, size, framing, duration),
            el("span", { class: "ripple-meta" }, `${canvas.width} × ${canvas.height} · ${(canvas.width * canvas.height / 1e6).toFixed(2)} MP · ${now.length} frames at ${FPS} fps${sourceLimited ? " · limited to source size" : ""}`));
        if (Math.max(now.requested, state.durationRequest) > now.length) group.append(el("span", { class: "ripple-meta" },
            `Source has ${((now.available - now.start) / FPS).toFixed(2)} s remaining; using ${now.length} frames · ${(now.length / FPS).toFixed(2)} s.`));
        if (cropped) {
            const controls = el("div", { class: "ripple-actions" }, el("button", {
                type: "button", "data-ripple-focus": "crop-edit", onclick: () => { state.cropEditing = !state.cropEditing; updateCropOverlay(); requestRender(); },
            }, state.cropEditing ? "Done cropping" : "Adjust crop"));
            if (state.cropEditing) controls.append(el("select", { "aria-label": "Crop aspect ratio", "data-ripple-focus": "crop-aspect", onchange: (event) => {
                state.cropRatio = event.target.value;
                if (state.cropRatio !== "Custom") {
                    const source = state.info.width / state.info.height;
                    const ratio = state.cropRatio === "Original" ? source : state.cropRatio.split(":").map(Number).reduce((a, b) => a / b);
                    setCrop(cropForAspect(source, ratio));
                }
            } }, ...options(["Original", "16:9", "9:16", "1:1", "Custom"].map((item) => [item, item]), state.cropRatio)));
            group.append(controls);
        }
        return group;
    }

    function renderPanel() {
        const now = current();
        const focusKey = panel.contains(document.activeElement) ? document.activeElement?.dataset.rippleFocus : null;
        content.replaceChildren();
        if (!state.info || state.info.error) {
            content.append(el("div", { class: "ripple-empty" },
                el("strong", {}, state.info?.error ? "Video unavailable" : "Start with a short video"),
                el("span", {}, state.info?.error ?? "Upload a clip above, choose a shot, then edit a frame.")));
            resize();
            return;
        }
        const canvas = geometry();
        const consumers = applyConsumers();
        content.append(sizingControls(now, canvas));
        const controls = el("div", { class: "ripple-transport" },
            el("div", { class: "ripple-actions" },
                el("button", { type: "button", "data-ripple-focus": "previous", title: "Move selected frame back by one", onclick: () => stepMarker(-1) }, "Previous frame"),
                el("button", { type: "button", "data-play": "", "aria-pressed": String(state.playing),
                    onclick: togglePlay }, state.playing ? "Pause shot" : "Play shot"),
                el("button", { type: "button", "data-ripple-focus": "next", title: "Move selected frame forward by one", onclick: () => stepMarker(1) }, "Next frame")),
            el("span", { class: "ripple-meta" }, `${canvas ? `${canvas.width}×${canvas.height} · ` : ""}${now.length} frames · ${(now.length / FPS).toFixed(2)} s`));
        content.append(controls);
        const add = el("button", { type: "button", "data-ripple-focus": "add", onclick: () => {
            const at = suggestFrame(now.frames, now.length);
            if (at < 0) return;
            let id = 1; while (now.markers.some((marker) => marker.id === id)) id++;
            const markers = [...now.markers, { id, frame: at, edited: "", edited_at: null }];
            commit(now.start, markers, markers.length - 1);
        } }, "+ Add frame");
        add.disabled = now.markers.length >= now.length;
        content.append(el("div", { class: "ripple-section" },
            el("div", {}, el("strong", {}, now.markers.length > 1 ? "2. Edit your frames" : "2. Edit your frame"),
                el("span", { class: "ripple-meta" }, now.markers.length > 1 ? "Give every edited frame the same change"
                    : "Add a frame only if the edit needs another anchor")), add));
        for (const id of state.thumbs.keys()) if (!now.markers.some((marker) => marker.id === id)) state.thumbs.delete(id);
        const cards = el("div", { class: `ripple-frames${now.markers.length === 1 ? " is-single" : ""}` });
        now.markers.forEach((marker, index) => {
            const absolute = now.start + marker.frame;
            const stale = marker.edited && marker.edited_at != null && marker.edited_at !== absolute;
            const changedCrop = marker.edited && !cropMatches(marker.edited_crop, cropValue());
            const changedSource = sourceChanged(marker);
            const connectedCount = consumers.filter((target) => connectedEdit(target, marker.id)).length;
            const overridden = consumers.length === 1 && connectedCount === 1;
            const select = () => {
                state.active = index;
                showFrame(absolute);
                renderPanel();
                node.setDirtyCanvas?.(true, false);
            };
            const badge = el("span", { class: "ripple-number", style: { background: COLORS[(marker.id - 1) % COLORS.length] } }, String(marker.id));
            const selectButton = el("button", { type: "button", class: "ripple-select", "aria-pressed": String(index === state.active),
                "data-ripple-focus": `select-${marker.id}`, "aria-label": `Select anchor ${marker.id}`, onclick: select }, badge,
                el("span", {}, `Frame ${marker.frame + 1}`, el("span", { class: "ripple-meta" }, timecode(absolute / FPS))));
            const header = el("div", { class: "ripple-card-head" }, selectButton);
            if (now.markers.length > 1) header.append(el("button", { type: "button", class: "ripple-quiet", title: "Remove this frame marker",
                "aria-label": `Remove anchor ${marker.id}`, onclick: () => {
                    commit(now.start, now.markers.filter((_, i) => i !== index), Math.max(0, index - 1));
                } }, "Remove"));
            const original = el("img", { alt: `Source frame ${marker.frame + 1}`, "data-thumb": marker.id, "data-want": frameURL(absolute) });
            if (state.thumbs.has(marker.id)) original.src = state.thumbs.get(marker.id);
            const editURL = marker.edited ? inputURL(marker.edited) : null;
            const edited = editURL ? el("img", { src: editURL, alt: `Edited frame ${marker.frame + 1}` })
                : el("div", { class: "ripple-placeholder" }, connectedCount ? "Connected editor supplies this image when you run" : "Drop, paste or upload your edit");
            if (editURL && !state.editSizes.has(editURL)) edited.addEventListener("load", () => {
                state.editSizes.set(editURL, [edited.naturalWidth, edited.naturalHeight]);
                requestRender();
            }, { once: true });
            const editSize = editURL ? state.editSizes.get(editURL) : null;
            const reshaped = editSize && aspectMismatch(editSize[0], editSize[1], canvas);
            const upload = el("button", { type: "button", class: "ripple-primary", onclick: () => {
                attachTarget = targetFor(index);
                fileInput.click();
            } }, state.uploading === index ? "Uploading…" : marker.edited ? "Replace edit" : "Upload edit");
            upload.disabled = state.uploading != null;
            const previews = el("div", { class: "ripple-pair" },
                el("div", {}, el("span", { class: "ripple-label" }, "Source"), original,
                    el("a", { href: frameURL(absolute, true), download: "", class: "ripple-download" }, "Download frame")),
                el("div", {}, el("span", { class: "ripple-label" }, overridden && marker.edited ? "Saved upload" : "Edited"), edited, upload));
            const warn = !overridden && (stale || changedCrop || changedSource || reshaped);
            const status = el("div", { class: `ripple-card-status${warn ? " ripple-warning" : ""}` },
                el("span", {}, overridden ? (marker.edited ? "Connected image takes precedence; upload retained" : "Connected editor · no upload needed")
                    : changedSource ? "Source changed · upload a matching edit"
                    : stale ? "Frame moved · upload a matching edit"
                    : changedCrop ? "Crop changed · review or replace this edit"
                    : reshaped ? `Edit is ${editSize[0]}×${editSize[1]}; it will be fitted to ${canvas.width}×${canvas.height}`
                    : connectedCount ? `Connected in ${connectedCount} of ${consumers.length} Apply nodes · see each Apply`
                        : marker.edited ? "Edit attached" : "Download → edit → drop here"));
            if (marker.edited) status.append(el("button", { type: "button", class: "ripple-quiet", onclick: () => {
                const markers = now.markers.map((m) => ({ ...m }));
                markers[index].edited = ""; markers[index].edited_at = null;
                delete markers[index].edited_crop; delete markers[index].edited_source;
                commit(now.start, markers, index);
            } }, "Clear"));
            const card = el("div", { class: `ripple-frame${index === state.active ? " is-active" : ""}` }, header, previews, status);
            card.addEventListener("click", (event) => { if (!event.target.closest("button,a")) select(); });
            // Drop an image file onto a card to attach it (and keep ComfyUI from opening it as a new node).
            card.addEventListener("dragover", (event) => {
                if (![...(event.dataTransfer?.types ?? [])].includes("Files")) return;
                event.preventDefault();
                event.stopPropagation();
                event.dataTransfer.dropEffect = "copy";
                card.classList.add("is-drop");
            });
            card.addEventListener("dragleave", () => card.classList.remove("is-drop"));
            card.addEventListener("drop", (event) => {
                if (![...(event.dataTransfer?.types ?? [])].includes("Files")) return;
                event.preventDefault();
                event.stopPropagation();
                card.classList.remove("is-drop");
                state.active = index;
                uploadEdit(targetFor(index), imageFile(event.dataTransfer.files) ?? event.dataTransfer.files[0]);
            });
            cards.append(card);
        });
        content.append(cards);
        const connected = consumers.length === 1 ? now.markers.filter((m) => connectedEdit(consumers[0], m.id)).length : 0;
        const attached = now.markers.filter((m) => m.edited || (consumers.length === 1 && connectedEdit(consumers[0], m.id))).length;
        const moved = now.markers.some((m) => m.edited && !(consumers.length === 1 && connectedEdit(consumers[0], m.id)) &&
            ((m.edited_at != null && m.edited_at !== now.start + m.frame) || !cropMatches(m.edited_crop, cropValue()) || sourceChanged(m)));
        const next = consumers.length > 1 ? `Used by ${consumers.length} Apply nodes. Check each Apply for its connected images and uploads.`
            : moved ? "Review changed source, frames or crop before running."
            : connected === now.markers.length ? "Connected editors supply your edits. Use ComfyUI Run to generate the selected shot."
            : connected ? `${connected} connected · ${attached - connected} uploaded · ${now.markers.length - attached} awaiting edits. Use ComfyUI Run when ready.`
            : attached === now.markers.length ? "Edits attached. Use ComfyUI Run to generate the selected shot."
            : `${attached} of ${now.markers.length} edits attached. Attach your edits, then use ComfyUI Run.`;
        content.append(el("div", { class: "ripple-next", "aria-live": "polite" },
            el("strong", {}, "3. Preview your result"),
            el("span", { class: moved ? "ripple-warning" : "ripple-meta" }, next)));
        if (state.panelError) content.append(el("div", { class: "ripple-warning", role: "alert" }, state.panelError));
        const advanced = el("details", { class: "ripple-advanced" }, el("summary", {}, "Advanced · sizing, editors & shortcuts"));
        advanced.open = state.advanced;
        const fitInputs = ["output_width", "output_height"].map((name) => el("label", { class: "ripple-field" },
            el("span", {}, name === "output_width" ? "Fit within width" : "Fit within height"), el("input", {
                type: "number", min: "0", step: "2", placeholder: "Auto", value: Number(widgetOf(node, name)?.value) || "",
                "data-ripple-focus": name, onchange: (event) => changeSetting(name, Math.max(0, Number(event.target.value) || 0)),
            })));
        const method = el("select", { "data-ripple-focus": "resize-method", onchange: (event) => changeSetting("resize_method", event.target.value) },
            ...["lanczos", "bicubic", "bilinear", "nearest-exact", "area"].map((value) => el("option", {
                value, ...(widgetOf(node, "resize_method")?.value === value ? { selected: "" } : {}),
            }, value)));
        const upscale = el("input", { type: "checkbox", "data-ripple-focus": "upscale", onchange: (event) => changeSetting("allow_upscale", event.target.checked) });
        upscale.checked = Boolean(widgetOf(node, "allow_upscale")?.value);
        const incompleteFit = Boolean(Number(widgetOf(node, "output_width")?.value)) !== Boolean(Number(widgetOf(node, "output_height")?.value));
        advanced.append(el("div", { class: "ripple-settings ripple-fit" }, ...fitInputs,
            el("label", { class: "ripple-field" }, el("span", {}, "Resize method"), method)),
            el("label", { class: "ripple-check" }, upscale, "Allow upscaling"),
            el("span", { class: incompleteFit ? "ripple-warning" : "ripple-meta" }, incompleteFit
                ? "Enter both fit dimensions, or clear both before running."
                : "Set both fit dimensions to override the MP target. Original uploads are retained."));
        const exec = state.executed;
        const changed = exec && (exec[0] !== now.start || exec[1] !== now.length || exec[3] !== state.video || JSON.stringify(exec[2]) !== JSON.stringify(now.frames));
        advanced.append(el("div", { class: "ripple-meta" },
            el("p", {}, "Local editors: connect anchor_1 or anchor_2 to an image editor, then edited_anchor_1 or edited_anchor_2 on Ripple Apply. Individual inputs override the batch and uploads. frames_to_edit / edited_images remains a batch in time order."),
            el("p", {}, changed ? "Selection changed: run the frame extraction again for your connected editor."
                : exec ? "Frame extraction matches this selection." : "Run the Clip node once to extract frames for a connected editor."),
            el("p", {}, "On the canvas: ← / → step a frame · Shift + arrows move the shot · Tab switches markers · Ctrl+V pastes an edit onto the selected frame. You can also drop an image onto a card."),
            el("p", {}, `Source: ${state.info.width} × ${state.info.height} · ${state.info.fps.toFixed(2)} fps · ${timecode(state.info.duration)}`)));
        advanced.addEventListener("toggle", () => { state.advanced = advanced.open; resize(); });
        content.append(advanced);
        updateCropOverlay();
        scheduleThumbs();
        resize();
        if (focusKey) panel.querySelector(`[data-ripple-focus="${focusKey}"]`)?.focus({ preventScroll: true });
    }
    // Exact-frame thumbnails come from the backend decoder. A card keeps showing its last thumbnail while
    // the frame moves; the new one is loaded off-screen once dragging pauses and then swapped in.
    function scheduleThumbs() {
        clearTimeout(state.thumbTimer);
        state.thumbTimer = setTimeout(() => {
            if (state.drag || state.cropDrag) { scheduleThumbs(); return; }
            for (const img of panel.querySelectorAll("img[data-thumb]")) {
                const want = img.dataset.want;
                const index = Number(img.dataset.thumb);
                if (!want || state.thumbs.get(index) === want) continue;
                const loader = new Image();
                loader.onload = () => {
                    state.thumbs.set(index, want);
                    for (const shown of panel.querySelectorAll(`img[data-thumb="${index}"]`)) {
                        if (shown.dataset.want === want) shown.src = want;
                    }
                };
                loader.src = want;
            }
        }, 150);
    }
    const panelWidget = node.addDOMWidget?.("ripple_markers", "div", panel, {
        serialize: false, hideOnZoom: false,
        getMinHeight: () => state.panelHeight, getMaxHeight: () => state.panelHeight,
    });
    function resize() {
        const previousHeight = state.panelHeight;
        if (content.offsetHeight) state.panelHeight = content.offsetHeight + 16;
        if (panelWidget) panelWidget.computeSize = (width) => [width ?? 360, state.panelHeight];
        const size = node.computeSize?.();
        if (size && node.size) {
            const height = Math.max(size[1], node.size[1] + (state.measured ? state.panelHeight - previousHeight : 0));
            if (height !== node.size[1]) node.setSize?.([node.size[0], height]);
        }
        state.measured = Boolean(content.offsetHeight);
        node.setDirtyCanvas?.(true, true);
    }
    const panelObserver = new ResizeObserver(resize);
    panelObserver.observe(content);

    // ---- timeline (canvas) -----------------------------------------------------------------------
    const widget = {
        name: "ripple_timeline", type: "wn_ripple_timeline", value: "", serialize: false, options: { serialize: false },
        computeSize: (width) => [width ?? 360, HEIGHT],
        draw(ctx, _node, width, y) {
            const video = widgetOf(node, "video")?.value;
            if (video !== state.video) fetchInfo(video);
            if (!state.player) findExisting();
            this._y = y;
            const now = current();
            const layout = makeLayout(MARGIN, width - MARGIN * 2, Math.max(now.available, now.start + now.length));
            this._layout = layout;
            const styles = getComputedStyle(panel);
            const theme = { text: styles.color, muted: styles.getPropertyValue("--ripple-muted").trim(),
                track: styles.getPropertyValue("--ripple-surface").trim() };
            ctx.save();
            ctx.font = "11px Arial, sans-serif";
            ctx.textBaseline = "middle";
            if (!state.info || state.info.error) {
                ctx.fillStyle = state.info?.error ? "#e0a050" : "#8a8a8a";
                ctx.fillText(state.info?.error ?? (video ? "Reading video…" : "Load a video"), MARGIN, y + HEIGHT / 2);
                ctx.restore();
                return;
            }
            const top = y + TRACK_TOP;
            ctx.fillStyle = theme.track;
            ctx.fillRect(layout.left, top, layout.width, TRACK_HEIGHT);
            const seconds = now.available / FPS;
            const step = tickStep(seconds, layout.width);
            ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "11px sans-serif";
            for (let s = 0; s <= seconds + 1e-6; s += step) {
                const x = layout.x(s * FPS);
                ctx.fillRect(x, top + TRACK_HEIGHT - 5, 1, 5);
                ctx.fillText(timecode(s).replace(/\.00$/, ""), Math.min(Math.max(x, layout.left + 14), layout.left + layout.width - 14), y + 27);
            }
            const wx = layout.x(now.start);
            const ww = Math.max(3, layout.x(now.start + now.length) - wx);
            ctx.fillStyle = "rgba(80, 150, 255, 0.28)";
            ctx.fillRect(wx, top, ww, TRACK_HEIGHT);
            ctx.strokeStyle = "rgba(120, 180, 255, 0.95)"; ctx.lineWidth = 1.5;
            ctx.strokeRect(wx + 0.5, top + 0.5, ww - 1, TRACK_HEIGHT - 1);
            ctx.fillStyle = "rgba(120, 180, 255, 0.95)";
            ctx.fillRect(wx, top + 9, 4, TRACK_HEIGHT - 18);
            ctx.fillRect(wx + ww - 4, top + 9, 4, TRACK_HEIGHT - 18);
            if (state.playing && state.player) {
                const k = gridIndexAt(state.player.currentTime, state.info);
                ctx.fillStyle = "rgba(255,255,255,0.75)";
                ctx.fillRect(layout.x(k + 0.5) - 0.5, top, 1, TRACK_HEIGHT);
            }
            now.markers.forEach((marker, index) => {
                const mx = layout.x(now.start + marker.frame + 0.5);
                const active = index === state.active;
                ctx.fillStyle = COLORS[(marker.id - 1) % COLORS.length];
                ctx.globalAlpha = active ? 1 : 0.75;
                ctx.fillRect(mx - (active ? 1.5 : 1), top - 4, active ? 3 : 2, TRACK_HEIGHT + 8);
                ctx.beginPath();
                ctx.moveTo(mx - 7, top - 15); ctx.lineTo(mx + 7, top - 15); ctx.lineTo(mx, top - 3); ctx.closePath();
                ctx.fill();
                ctx.globalAlpha = 1;
                ctx.fillStyle = "#111"; ctx.font = "bold 9px Arial, sans-serif"; ctx.textAlign = "center";
                ctx.fillText(String(marker.id), mx, top - 11);
                if (marker.edited) { ctx.fillStyle = "#5fd0a0"; ctx.fillRect(mx - 3, top + TRACK_HEIGHT + 5, 6, 3); }
            });
            ctx.textAlign = "left"; ctx.fillStyle = theme.text; ctx.font = "12px sans-serif";
            ctx.fillText("1. Choose your shot", MARGIN, y + 9);
            ctx.textAlign = "right"; ctx.fillStyle = theme.muted; ctx.font = "11px sans-serif";
            ctx.fillText(`${timecode(now.start / FPS)}–${timecode((now.start + now.length) / FPS)}`, width - MARGIN, y + 9);
            ctx.textAlign = "left";
            ctx.fillText("Drag the shot · drag its ends to trim · numbered markers pick frames", MARGIN, y + 100, width - MARGIN * 2);
            ctx.restore();
        },
        mouse(event, pos) {
            const now = current();
            const layout = this._layout;
            if (!layout || !state.info || state.info.error) return false;
            const localY = pos[1] - (this._y ?? 0);
            const type = event.type;
            if (type === "pointerdown" || type === "mousedown") {
                if (localY < TRACK_TOP - 18 || localY > TRACK_TOP + TRACK_HEIGHT + 6) return false;
                if (state.playing) togglePlay();
                const hit = hitMarker(pos[0], layout, now.start, now.frames);
                const onBody = localY >= TRACK_TOP + 7 && localY <= TRACK_TOP + TRACK_HEIGHT - 7;
                const edge = onBody && Math.abs(pos[0] - layout.x(now.start)) <= 7 ? "left"
                    : onBody && Math.abs(pos[0] - layout.x(now.start + now.length)) <= 7 ? "right" : null;
                if (edge) {
                    state.drag = { kind: "resize", edge, before: now };
                } else if (hit >= 0) {
                    state.active = hit;
                    state.drag = { kind: "marker" };
                    renderPanel();
                } else if (hitTest(pos[0], layout, now.start, now.length, -1e9) === "window") {
                    state.drag = { kind: "window", grab: layout.frame(pos[0]) - now.start };
                } else {
                    const frame = Math.round(layout.frame(pos[0]));
                    commit(frame - Math.floor(now.length / 2), now.markers);
                    state.drag = { kind: "window", grab: Math.floor(now.length / 2) };
                }
                return true;
            }
            if (type === "pointermove" || type === "mousemove") {
                if (!state.drag) return false;
                const frame = layout.frame(pos[0]);
                if (state.drag.kind === "resize") {
                    const before = state.drag.before;
                    resizeShot(resizeWindow(now.available, before.start, before.length, state.drag.edge, Math.round(frame)), before);
                } else if (state.drag.kind === "window") commit(Math.round(frame - state.drag.grab), now.markers);
                else {
                    const target = moveMarker(now.frames, state.active, Math.floor(frame - now.start), now.length);
                    const markers = now.markers.map((m, i) => (i === state.active ? { ...m, frame: target } : m));
                    commit(now.start, markers, state.active);
                }
                return true;
            }
            if (type === "pointerup" || type === "mouseup") {
                const dragging = Boolean(state.drag);
                state.drag = null;
                if (dragging) scheduleThumbs();
                return dragging;
            }
            return false;
        },
    };
    node.addCustomWidget ? node.addCustomWidget(widget) : node.widgets?.push(widget);
    if (node.widgets && panelWidget) {
        const ti = node.widgets.indexOf(widget), pi = node.widgets.indexOf(panelWidget);
        if (ti > pi && pi >= 0) { node.widgets.splice(ti, 1); node.widgets.splice(pi, 0, widget); }
    }

    function stepMarker(delta) {
        const now = current();
        if (!now.markers[state.active]) return;
        const target = moveMarker(now.frames, state.active, now.frames[state.active] + delta, now.length);
        const markers = now.markers.map((m, i) => (i === state.active ? { ...m, frame: target } : m));
        commit(now.start, markers, state.active);
        scheduleThumbs();
    }

    // Arrow keys step the selected marker; Shift+arrows move the window. Only while this node is selected.
    const onKey = (event) => {
        if (!["ArrowLeft", "ArrowRight", "Tab"].includes(event.key)) return;
        const tag = document.activeElement?.tagName;
        if (panel.contains(document.activeElement) || ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(tag) || document.activeElement?.isContentEditable) return;
        const selected = app.canvas?.selected_nodes;
        if (!selected || !selected[node.id] || Object.keys(selected).length !== 1) return;
        const now = current();
        if (event.key === "Tab") {
            if (now.markers.length < 2) return;
            state.active = (state.active + (event.shiftKey ? -1 : 1) + now.markers.length) % now.markers.length;
            showFrame(now.start + now.frames[state.active]);
            renderPanel();
        } else {
            const delta = event.key === "ArrowLeft" ? -1 : 1;
            if (event.shiftKey) commit(now.start + delta * (event.altKey ? FPS : 1), now.markers);
            else stepMarker(delta);
        }
        event.preventDefault();
        event.stopPropagation();
    };
    window.addEventListener("keydown", onKey, true);
    // Ctrl+V with this node selected attaches a copied image to the selected marker, instead of ComfyUI
    // pasting it as a new Load Image node.
    const onPaste = (event) => {
        const selected = app.canvas?.selected_nodes;
        if (!selected || !selected[node.id] || Object.keys(selected).length !== 1) return;
        const tag = document.activeElement?.tagName;
        if (["INPUT", "TEXTAREA"].includes(tag) || document.activeElement?.isContentEditable) return;
        const item = [...(event.clipboardData?.items ?? [])].find((entry) => entry.kind === "file" && entry.type.startsWith("image/"));
        const file = item?.getAsFile();
        if (!file || !state.info || state.info.error) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        uploadEdit(targetFor(state.active), file);
    };
    window.addEventListener("paste", onPaste, true);
    const onRemoved = node.onRemoved;
    node.onRemoved = function () {
        window.removeEventListener("keydown", onKey, true);
        window.removeEventListener("paste", onPaste, true);
        state.playing = false;
        state.removed = true;
        state.player?.pause();
        clearTimeout(state.thumbTimer);
        panelObserver.disconnect();
        for (const observer of state.previewObservers) observer.disconnect();
        cropObserver?.disconnect();
        cropOverlay?.remove();
        return onRemoved?.apply(this, arguments);
    };
    const onExecuted = node.onExecuted;
    node.onExecuted = function (message) {
        const binding = message?.binding;
        if (Array.isArray(binding) && binding.length >= 4) state.executed = binding;
        renderPanel();
        node.setDirtyCanvas?.(true, true);
        return onExecuted?.apply(this, arguments);
    };
    for (const name of SETTINGS) {
        const target = widgetOf(node, name);
        const callback = target?.callback;
        if (target) target.callback = function () {
            const result = callback?.apply(this, arguments);
            remember(node);
            updateCropOverlay();
            requestRender();
            return result;
        };
    }
    return { refresh: requestRender, configure: () => { state.cropRatio = widgetOf(node, "crop")?.value ? "Custom" : "Original"; requestRender(); } };
}

// Apply / Output: show the node's messages (edit checks, aspect warnings).
function installMessages(node) {
    const state = { lines: [] };
    const lines = () => {
        const connected = (name) => node.inputs?.some((input) => input.name === name && input.link != null);
        const source = [];
        for (const id of [1, 2]) if (connected(`edited_anchor_${id}`)) source.push(`Anchor ${id}: connected image`);
        if (connected("edited_images")) source.push("Batch: remaining anchors in time order");
        if (!source.length) source.push("Edits: uploads on Ripple Clip");
        return [...source, ...state.lines];
    };
    const widget = {
        name: "ripple_messages", type: "wn_ripple_messages", value: "", serialize: false, options: { serialize: false },
        computeSize: (width) => [width ?? 300, 16 * lines().length * 2 + 6],
        draw(ctx, _node, width, y) {
            ctx.save();
            ctx.font = "11px Arial, sans-serif";
            ctx.textBaseline = "top";
            let line = 0;
            for (const [index, text] of lines().entries()) {
                ctx.fillStyle = index === 0 ? "#cfcfcf" : "#e0a050";
                const words = String(text).split(" ");
                let row = "";
                for (const word of words) {
                    if (ctx.measureText(`${row} ${word}`).width > width - 28 && row) {
                        ctx.fillText(row, 14, y + 3 + 16 * line++);
                        row = word;
                    } else row = row ? `${row} ${word}` : word;
                }
                if (row) ctx.fillText(row, 14, y + 3 + 16 * line++);
            }
            ctx.restore();
        },
        mouse: () => false,
    };
    node.addCustomWidget ? node.addCustomWidget(widget) : node.widgets?.push(widget);
    const onExecuted = node.onExecuted;
    node.onExecuted = function (message) {
        state.lines = Array.isArray(message?.text) ? message.text : [];
        node.setSize?.([node.size[0], node.computeSize()[1]]);
        node.setDirtyCanvas?.(true, true);
        return onExecuted?.apply(this, arguments);
    };
}

app.registerExtension({
    name: "WepeNerd.H3Ripple",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!["WN_RippleClip", "WN_RippleApply"].includes(nodeData.name)) return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            if (nodeData.name === "WN_RippleClip") this.wnRipple = installRipple(this);
            else installMessages(this);
            return result;
        };
        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = onConnectionsChange?.apply(this, arguments);
            queueMicrotask(() => {
                for (const clip of this.graph?.findNodesByType("WN_RippleClip") ?? []) clip.wnRipple?.refresh();
                if (nodeData.name === "WN_RippleApply") this.setSize?.([this.size[0], this.computeSize()[1]]);
            });
            return result;
        };
        if (nodeData.name !== "WN_RippleClip") return;
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            restore(this);
            setTimeout(() => { restore(this); this.wnRipple?.configure(); this.setDirtyCanvas?.(true, true); }, 0);
            return result;
        };
    },
});
