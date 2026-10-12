// Pure helpers for the Frame Number Selector. Labels come from frame_number_node.py
// and start with the frame count, e.g. "121  (5.04s)".

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
