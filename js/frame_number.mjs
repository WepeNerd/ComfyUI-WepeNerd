// Pure helpers for the Duration Selector (node id WN_FrameNumberSelector). Labels come from
// frame_number_node.py and are plain frame numbers such as "121". framesOf also reads the
// leading number of older labels like "121  (5.04s)" so saved workflows map onto the new list.

export function framesOf(label) {
    return Number.parseInt(String(label), 10);
}

// Keep the current value if the preset allows it; otherwise snap to the nearest legal
// count (ties go up, matching the Python node) so switching models keeps the length.
export function chooseValue(labels, current, fallback) {
    if (!Array.isArray(labels) || !labels.length) return current;
    if (labels.includes(current)) return current;
    const target = framesOf(current);
    if (!Number.isFinite(target)) return fallback ?? labels[0];
    let best = labels[0];
    for (const label of labels) {
        const d = Math.abs(framesOf(label) - target);
        const bestD = Math.abs(framesOf(best) - target);
        if (d < bestD || (d === bestD && framesOf(label) > framesOf(best))) best = label;
    }
    return best;
}

// The seconds field moves in steps of SECONDS_STEP; one step always moves to the next legal count.
export const SECONDS_STEP = 0.1;

// Seconds shown for a frame count, to two decimals (frames / fps).
export function secondsOf(frames, fps) {
    return Math.round((frames / fps) * 100) / 100;
}

// The frame count for typed or stepped seconds: the legal count nearest seconds × fps (ties go
// up). If that is the current count but the field moved by a whole step or more (the arrows or a
// drag), move one count in that direction so the arrows never get stuck between legal counts.
// A smaller typed change snaps back to the current count, showing it is the nearest legal one.
export function framesForSeconds(labels, fps, seconds, currentLabel, shownSeconds) {
    if (!Array.isArray(labels) || !labels.length || !(fps > 0) || !Number.isFinite(seconds)) return currentLabel;
    const frames = labels.map(framesOf);
    const target = Math.max(0, seconds) * fps;
    let index = 0;
    frames.forEach((count, i) => {
        const d = Math.abs(count - target), best = Math.abs(frames[index] - target);
        if (d < best || (d === best && count > frames[index])) index = i;
    });
    const current = frames.indexOf(framesOf(currentLabel));
    const moved = seconds - shownSeconds;
    if (index === current && Math.abs(moved) >= SECONDS_STEP - 1e-6) {
        index = Math.min(frames.length - 1, Math.max(0, index + Math.sign(moved)));
    }
    return labels[index];
}
