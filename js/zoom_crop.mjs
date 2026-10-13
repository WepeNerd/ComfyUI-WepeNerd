// Pure helpers for the Zoom Crop position picker. Mirrors POSITIONS in zoom_crop_node.py.

export const ROWS = ["top", "upper", "center", "lower", "bottom"];
export const COLUMNS = ["left", "mid-left", "center", "mid-right", "right"];
export const POSITIONS = ROWS.flatMap((vertical) => COLUMNS.map((horizontal) =>
    [vertical, horizontal].filter((part) => part !== "center").join(" ") || "center"));

// Fraction of the preview frame the "kept" box covers on each axis.
export const KEEP = 0.6;

const clampCell = (value) => Math.min(4, Math.max(0, Math.trunc(value)));

export function cellOf(value) {
    const index = POSITIONS.indexOf(value);
    return index < 0 ? { column: 2, row: 2 } : { column: index % 5, row: Math.floor(index / 5) };
}

export function valueAt(column, row) {
    return POSITIONS[clampCell(row) * 5 + clampCell(column)];
}

// Map a point inside a width x height picker to the nearest of the 5x5 cells.
// Points outside are clamped to the edge so dragging past the frame still works.
export function valueFromPoint(x, y, width, height) {
    if (!(width > 0) || !(height > 0)) return null;
    return valueAt(Math.floor((x / width) * 5), Math.floor((y / height) * 5));
}

// Kept-region rectangle, as fractions of the frame, for a position value.
export function keptRect(value) {
    const { column, row } = cellOf(value);
    const travel = 1 - KEEP;
    return { left: (travel * column) / 4, top: (travel * row) / 4, width: KEEP, height: KEEP };
}

const MOVES = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

// New value for a keyboard key, or null when the key is not a picker key.
export function valueForKey(value, key) {
    if (key === "Home" || key === "c" || key === "C") return "center";
    const move = MOVES[key];
    if (!move) return null;
    const { column, row } = cellOf(value);
    return valueAt(column + move[0], row + move[1]);
}

export function labelOf(value) {
    const text = POSITIONS.includes(value) ? value : "center";
    return text.replace(/\b\w/g, (letter) => letter.toUpperCase());
}
