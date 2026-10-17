// Pure helpers for Load Video: predict the loaded clip from the source's metadata, migrate
// workflows saved before the update, and loop the preview over exactly the clip being loaded.
// planClip and largestValid mirror plan_clip and largest_valid in load_video_node.py; the
// shared cases in tests/load_video_plans.json keep the two in step.

// Saved with every node, so a workflow saved with an earlier layout of the settings can be told apart.
export const VALUES_MARKER = "wn_load_video_v3";
export const V2_MARKER = "wn_load_video_v2";
export const WATCHED = ["video", "frame_load_cap", "frame_rate", "skip_first_frames", "select_every_nth", "format"];

// Format names from before the presets were shared with Duration Selector.
export const LEGACY_FORMATS = {
    AnimateDiff: "None",
    Mochi: "Mochi 1 (6n+1, 30 fps)",
    LTXV: "LTX-Video 0.9.x (8n+1, 25 fps)",
    Hunyuan: "HunyuanVideo (4n+1, 24 fps)",
    Cosmos: "Cosmos 1.0 (8n+1, 24 fps)",
    Wan: "Wan 2.1 / 2.2 14B (4n+1, 16 fps)",
    H3: "MiniMax H3 (17n+5, 24 fps)",
};

const mod = (a, b) => ((a % b) + b) % b;

export function largestValid(count, preset) {
    if (!preset || count <= 0) return Math.max(0, count);
    if (preset.rule) {
        const [step, offset] = preset.rule;
        const valid = count - mod(count - offset, step);
        return valid >= 1 ? valid : 0;
    }
    let best = 0;
    for (const frames of preset.frames ?? []) if (frames <= count && frames > best) best = frames;
    return best;
}

export function planClip(info, settings, preset = null) {
    const { frame_rate: frameRate = 0, skip_first_frames: skip = 0, select_every_nth: nth = 1,
        frame_load_cap: frameCap = 0 } = settings;
    const rate = frameRate || info.fps;
    const available = Math.max(0, frameRate ? Math.floor(info.duration * frameRate + 0.5) : info.frames);
    let fromEnd = true;
    let selected = Math.max(0, Math.ceil((available - skip) / nth));
    const cap = frameCap ? largestValid(frameCap, preset) : 0;
    if (frameCap && !cap) {
        selected = 0;
        fromEnd = false;
    } else if (cap && cap <= selected) {
        fromEnd = available - skip < (cap + 1) * nth;
        selected = cap;
    }
    const count = largestValid(selected, preset);
    const outputRate = rate / nth;
    const clipStart = skip / rate;
    const seconds = count / outputRate;
    return { count, frame_rate: outputRate, seconds, clip_start: clipStart,
        clip_end: clipStart + seconds, estimate: fromEnd };
}

// Map a saved format value onto the current list (old short names, or a renamed preset).
export function resolveFormatName(value, names) {
    if (names.includes(value)) return value;
    const mapped = LEGACY_FORMATS[value];
    return mapped && names.includes(mapped) ? mapped : value;
}

// Workflows saved before the settings were reorganised stored widgets by position. The first layout
// was video, frame_rate, frame_load_cap, skip_first_frames, select_every_nth, format, custom_width,
// custom_height, load_audio. Returns the values by name, or null if the array doesn't look like that.
export function migrateLegacyValues(values) {
    if (!Array.isArray(values)) return null;
    for (let i = 5; i + 3 < values.length; i++) {
        const [w, h, audio] = [values[i + 1], values[i + 2], values[i + 3]];
        if (typeof values[i] !== "string" || typeof w !== "number" || typeof h !== "number"
            || typeof audio !== "boolean" || typeof values[i - 5] !== "string") continue;
        const numbers = values.slice(i - 4, i);
        if (!numbers.every((v) => typeof v === "number")) continue;
        const [frameRate, cap, skip, nth] = numbers;
        return { video: values[i - 5], frame_load_cap: cap, frame_rate: frameRate,
            skip_first_frames: skip, select_every_nth: nth, format: values[i], load_audio: audio };
    }
    return null;
}

// The second layout (marked V2_MARKER) was video, start_time, duration, frame_load_cap, frame_rate,
// skip_first_frames, select_every_nth, format, load_audio. start_time and duration are returned
// too; windowToFrames turns them into skip_first_frames and frame_load_cap.
export function migrateV2Values(values) {
    if (!Array.isArray(values)) return null;
    for (let i = 0; i + 8 < values.length; i++) {
        const row = values.slice(i, i + 9);
        if (typeof row[0] !== "string" || !row.slice(1, 7).every((v) => typeof v === "number")
            || typeof row[7] !== "string" || typeof row[8] !== "boolean") continue;
        const [video, start, duration, cap, frameRate, skip, nth, format, audio] = row;
        return { video, start_time: start, duration, frame_load_cap: cap, frame_rate: frameRate,
            skip_first_frames: skip, select_every_nth: nth, format, load_audio: audio };
    }
    return null;
}

// The old start_time/duration window as skip and cap, at fps (the frame rate setting, or the
// source's): the old order was start, duration, skip, every nth, cap.
export function windowToFrames({ start_time: start = 0, duration = 0, frame_load_cap: cap = 0,
    skip_first_frames: skip = 0, select_every_nth: nth = 1 }, fps) {
    const result = { skip_first_frames: skip + Math.round(start * fps), frame_load_cap: cap };
    if (duration > 0) {
        const selected = Math.max(1, Math.ceil((Math.round(duration * fps) - skip) / Math.max(1, nth)));
        result.frame_load_cap = cap ? Math.min(cap, selected) : selected;
    }
    return result;
}

const trim = (value, digits = 2) => String(Number(value.toFixed(digits)));

export function formatSeconds(seconds) {
    if (!Number.isFinite(seconds)) return "?";
    if (seconds >= 3600) {
        const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
        return `${h}h ${String(m).padStart(2, "0")}m`;
    }
    if (seconds >= 60) {
        const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
        return `${m}m ${String(s).padStart(2, "0")}s`;
    }
    return `${trim(seconds)} s`;
}

function formatBytes(bytes) {
    if (bytes >= 1024 ** 3) return `${trim(bytes / 1024 ** 3, 1)} GB`;
    return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}

// Three short lines for the node: the source file, what will load, and a note if something needs attention.
export function describe(info, settings, preset, formatName = "None") {
    if (!info) return { source: "", output: "", note: "" };
    if (info.error) return { source: "", output: "", note: info.error, warn: true };
    const source = [`${info.width}×${info.height}`, `${trim(info.fps)} fps`, formatSeconds(info.duration),
        `${info.frames_exact ? "" : "≈"}${info.frames} frames`];
    if (info.audio) source.push("audio");
    if (info.alpha) source.push("alpha");
    const plan = planClip(info, settings, preset);
    // Connected settings whose value the browser can't read use the setting's current value.
    const guessed = settings.approximate ?? [];
    const approx = plan.estimate || guessed.length ? "≈" : "";
    let note = "", warn = false;
    if (!plan.count) {
        warn = true;
        const empty = planClip(info, settings, null).count === 0;
        note = empty ? "Nothing to load: skip_first_frames is past the end of the video."
            : `Too few frames for ${formatName}. Select more frames or use None.`;
        return { source: `Source  ${source.join(" · ")}`, output: "Loads nothing", note, warn, plan };
    }
    const bytes = plan.count * info.width * info.height * 3 * 4;
    const output = [`${approx}${plan.count} frames`, `${trim(plan.frame_rate)} fps`, formatSeconds(plan.seconds),
        `from ${formatSeconds(plan.clip_start)}`, formatBytes(bytes)];
    if (preset && preset.min && (plan.count < preset.min || plan.count > preset.max)) {
        warn = true;
        note = `${plan.count} frames is outside this preset's range (${preset.min}–${preset.max}).`;
    } else if (!settings.frame_load_cap && bytes > 8 * 1024 ** 3) {
        warn = true;
        note = `No cap: the whole video needs ${formatBytes(bytes)} of RAM.`;
    } else if (guessed.length) {
        const values = guessed.map((name) => `${name} ${settings[name]}`).join(", ");
        note = `Connected ${guessed.join(", ")} is only known when the workflow runs; using ${values}.`;
    }
    return { source: `Source  ${source.join(" · ")}`, output: `Loads  ${output.join(" · ")}`, note, warn, plan };
}

// Loops video players over [start, end) seconds. While playing, a frame callback snaps the
// playhead back to the start at the end of the clip, or when playback is outside the clip.
// Paused players may be scrubbed anywhere. With no range, players loop the whole file.
export class ClipLooper {
    constructor(schedule = (callback) => requestAnimationFrame(callback),
        cancel = (id) => cancelAnimationFrame(id)) {
        this.schedule = schedule;
        this.cancel = cancel;
        this.players = new Map();
        this.range = null;
    }

    attach(player) {
        if (this.players.has(player)) return;
        const entry = { frame: null, listeners: {} };
        const on = (type, handler) => {
            entry.listeners[type] = handler;
            player.addEventListener?.(type, handler);
        };
        on("play", () => { this.#enforce(player); this.#tick(player, entry); });
        on("pause", () => this.#stop(entry));
        on("ended", () => {
            if (!this.range) return;
            player.currentTime = this.range.start;
            player.play?.()?.catch?.(() => {});
        });
        on("loadedmetadata", () => this.#enforce(player, true));
        this.players.set(player, entry);
        this.#apply(player);
        if (!player.paused) this.#tick(player, entry);
    }

    detach(player) {
        const entry = this.players.get(player);
        if (!entry) return;
        this.#stop(entry);
        for (const [type, handler] of Object.entries(entry.listeners)) player.removeEventListener?.(type, handler);
        this.players.delete(player);
    }

    clear() {
        for (const player of [...this.players.keys()]) this.detach(player);
    }

    // A new range moves each player to its start, so the preview shows the clip that will load.
    setRange(start, end) {
        const valid = Number.isFinite(start) && Number.isFinite(end) && end > start;
        const next = valid ? { start: Math.max(0, start), end } : null;
        const changed = !this.range !== !next || (next && (next.start !== this.range.start || next.end !== this.range.end));
        this.range = next;
        if (!changed) return;
        for (const player of this.players.keys()) {
            this.#apply(player);
            this.#enforce(player, true);
        }
    }

    #apply(player) {
        player.loop = !this.range;
    }

    #outside(player) {
        const { start, end } = this.range;
        const length = Number.isFinite(player.duration) ? player.duration : Infinity;
        // Ending within ~a frame of the file's end is left to the "ended" event.
        return player.currentTime < start - 0.05 || (player.currentTime >= end - 0.001 && end < length - 0.001);
    }

    #enforce(player, force = false) {
        if (!this.range) return;
        if (force ? player.currentTime !== this.range.start : this.#outside(player)) {
            player.currentTime = this.range.start;
        }
    }

    #tick(player, entry) {
        this.#stop(entry);
        const step = () => {
            entry.frame = null;
            if (player.paused || !this.players.has(player)) return;
            if (this.range && this.#outside(player)) player.currentTime = this.range.start;
            entry.frame = this.schedule(step);
        };
        entry.frame = this.schedule(step);
    }

    #stop(entry) {
        if (entry.frame !== null) this.cancel(entry.frame);
        entry.frame = null;
    }
}

// Values a connected input will receive, read from the node it is connected to (through reroutes,
// Number Convert and KJNodes Get/Set pairs), so the readout
// and preview can follow another node (such as Duration Selector) before the workflow runs.
// Returns { linked: false } for a plain widget, { linked: true, value } when the source node's
// value can be read in the browser, and { linked: true, unknown: true } when it is only known at
// run time (a math node, say).
const DURATION_SELECTOR = "WN_FrameNumberSelector";
const REROUTES = new Set(["Reroute", "RerouteNode"]);

const typeOf = (node) => node?.comfyClass ?? node?.type;

function linkById(graph, id) {
    const links = graph?.links;
    if (!links || id === null || id === undefined) return null;
    return typeof links.get === "function" ? links.get(id) : links[id];
}

function inputFor(node, name) {
    return node?.inputs?.find((input) => input.widget?.name === name || input.name === name) ?? null;
}

function sourceValue(graph, node, slot, presets, depth) {
    if (!node || depth > 32) return { unknown: true };
    const type = typeOf(node);
    if (REROUTES.has(type)) {
        const link = linkById(graph, node.inputs?.[0]?.link);
        if (!link) return { unknown: true };
        return sourceValue(graph, graph.getNodeById?.(link.origin_id), link.origin_slot, presets, depth + 1);
    }
    if (type === "GetNode") {
        // KJNodes Get/Set: follow the Set node with the same name to whatever feeds it.
        const name = node.widgets?.[0]?.value;
        const nodes = graph._nodes ?? graph.nodes ?? [];
        const setter = nodes.find((item) => typeOf(item) === "SetNode" && item.widgets?.[0]?.value === name);
        const link = linkById(graph, setter?.inputs?.[0]?.link);
        if (!link) return { unknown: true };
        return sourceValue(graph, graph.getNodeById?.(link.origin_id), link.origin_slot, presets, depth + 1);
    }
    const widget = (name) => node.widgets?.find((item) => item.name === name);
    if (type === "ComfyNumberConvert") {
        // Core Number Convert: FLOAT keeps the value, INT truncates it (Python's int()).
        const input = inputFor(node, "value");
        const link = linkById(graph, input?.link);
        const source = link
            ? sourceValue(graph, graph.getNodeById?.(link.origin_id), link.origin_slot, presets, depth + 1)
            : widget("value") ? { value: widget("value").value } : { unknown: true };
        if (source.unknown) return source;
        const number = typeof source.value === "boolean" ? Number(source.value) : Number(source.value);
        if (!Number.isFinite(number)) return { unknown: true };
        const kind = node.outputs?.[slot]?.type ?? node.outputs?.[slot]?.name;
        return { value: kind === "INT" ? Math.trunc(number) : number };
    }
    if (type === DURATION_SELECTOR) {
        const frames = Number.parseInt(String(widget("frames")?.value), 10);
        const fps = presets?.[widget("preset")?.value]?.fps;
        if (slot === 0) return Number.isFinite(frames) ? { value: frames } : { unknown: true };
        if (!(fps > 0)) return { unknown: true };
        if (slot === 1) return { value: fps };
        if (slot === 2 && Number.isFinite(frames)) return { value: frames / fps };
        return { unknown: true };
    }
    // A node that just passes on one of its widgets: an output named after the widget, or a primitive
    // (PrimitiveNode, Int, Float, ...) whose value is its "value" widget or, failing that, its first one.
    // Primitives also carry a control widget (fixed/increment/...), whose name differs between versions.
    const output = node.outputs?.[slot];
    const passThrough = (item) => item && !inputFor(node, item.name)?.link;
    const named = widget(output?.name);
    if (passThrough(named)) return { value: named.value };
    if (type === "PrimitiveNode" || /^Primitive/.test(type ?? "")) {
        const value = widget("value") ?? node.widgets?.find((item) => item.type !== "button");
        if (passThrough(value)) return { value: value.value };
    }
    return { unknown: true };
}

export function linkedValue(graph, node, name, presets = {}) {
    const input = inputFor(node, name);
    const link = linkById(graph, input?.link);
    if (!link) return { linked: false };
    const result = sourceValue(graph, graph.getNodeById?.(link.origin_id), link.origin_slot, presets, 0);
    return { linked: true, ...result };
}
