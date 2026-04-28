import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "WepeNerd.DragResolution",

    async nodeCreated(node) {
        if (node.comfyClass !== "WN_DragResolution") return;

        // ── Grab widget references ──────────────────────────────
        const wW  = node.widgets.find(w => w.name === "width");
        const wH  = node.widgets.find(w => w.name === "height");
        const wAR = node.widgets.find(w => w.name === "aspect_ratio");
        const wDV = node.widgets.find(w => w.name === "divisor");

        if (!wW || !wH || !wAR || !wDV) return;

        // ── Constants ───────────────────────────────────────────
        const CANVAS_H     = 220;
        const PAD          = 12;
        const HANDLE_SIZE  = 8;
        const MIN_PX       = 64;
        const MAX_PX       = 8192;
        const BOX_COLOR    = "rgba(100, 160, 255, 0.35)";
        const BOX_BORDER   = "rgba(100, 160, 255, 0.9)";
        const HANDLE_COLOR = "rgba(255, 255, 255, 0.95)";
        const TEXT_COLOR   = "rgba(255, 255, 255, 0.9)";
        const BG_COLOR     = "rgba(0, 0, 0, 0.25)";
        const GRID_COLOR   = "rgba(255, 255, 255, 0.04)";

        // ── State ───────────────────────────────────────────────
        let drag = null;         // "r" | "b" | "br" | "move" | null
        let dragStartMouse = null;
        let dragStartW = 0;
        let dragStartH = 0;
        let dragStartBoxX = 0;
        let dragStartBoxY = 0;

        // ── Helpers ─────────────────────────────────────────────
        function snap(val, div) {
            return Math.max(div, Math.round(val / div) * div);
        }

        function parseRatio(str) {
            if (str === "Free") return null;
            const parts = str.split(":");
            if (parts.length !== 2) return null;
            const a = parseFloat(parts[0]);
            const b = parseFloat(parts[1]);
            if (isNaN(a) || isNaN(b) || b === 0) return null;
            return a / b;
        }

        function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

        function gcd(a, b) { while (b) { [a, b] = [b, a % b]; } return a; }

        function ratioLabel(w, h) {
            const g = gcd(w, h);
            return `${w / g}:${h / g}`;
        }

        // Returns {x, y, bw, bh, scale} for the box within the canvas area
        function getBoxLayout(nodeWidth) {
            const canvasW = nodeWidth - PAD * 2;
            const canvasH = CANVAS_H;
            const w = wW.value;
            const h = wH.value;

            // Scale so the box fits inside the canvas area with margin
            const margin = 30;
            const availW = canvasW - margin * 2;
            const availH = canvasH - margin * 2;
            const scale = Math.min(availW / w, availH / h);

            const bw = w * scale;
            const bh = h * scale;
            const x = PAD + (canvasW - bw) / 2;
            const y = (canvasH - bh) / 2;

            return { x, y, bw, bh, scale };
        }

        // ── Custom widget (adds the drawing area) ───────────────
        const canvasWidget = {
            name: "drag_canvas",
            type: "custom",
            value: 0,  // unused
            computeSize() {
                return [node.size[0], CANVAS_H];
            },
            draw(ctx, _node, width, posY /*, height*/) {
                const canvasW = width - PAD * 2;
                const { x, y, bw, bh, scale } = getBoxLayout(width);
                const ofsY = posY;

                // ── Background ──
                ctx.fillStyle = BG_COLOR;
                ctx.beginPath();
                ctx.roundRect(PAD, ofsY, canvasW, CANVAS_H, 6);
                ctx.fill();

                // ── Grid lines ──
                ctx.strokeStyle = GRID_COLOR;
                ctx.lineWidth = 1;
                const div = wDV.value || 32;
                const gridStep = div * (scale || 1);
                if (gridStep > 4) {
                    const boxLeft   = x;
                    const boxTop    = ofsY + y;
                    const boxRight  = x + bw;
                    const boxBottom = ofsY + y + bh;

                    for (let gx = boxLeft + gridStep; gx < boxRight; gx += gridStep) {
                        ctx.beginPath();
                        ctx.moveTo(gx, boxTop);
                        ctx.lineTo(gx, boxBottom);
                        ctx.stroke();
                    }
                    for (let gy = boxTop + gridStep; gy < boxBottom; gy += gridStep) {
                        ctx.beginPath();
                        ctx.moveTo(boxLeft, gy);
                        ctx.lineTo(boxRight, gy);
                        ctx.stroke();
                    }
                }

                // ── Box fill ──
                ctx.fillStyle = BOX_COLOR;
                ctx.beginPath();
                ctx.roundRect(x, ofsY + y, bw, bh, 3);
                ctx.fill();

                // ── Box border ──
                ctx.strokeStyle = BOX_BORDER;
                ctx.lineWidth = 2;
                ctx.stroke();

                // ── Drag handles ──
                const handles = getHandlePositions(x, ofsY + y, bw, bh);
                ctx.fillStyle = HANDLE_COLOR;
                for (const h of Object.values(handles)) {
                    ctx.beginPath();
                    ctx.arc(h.x, h.y, HANDLE_SIZE / 2, 0, Math.PI * 2);
                    ctx.fill();
                }

                // ── Dimension label ──
                const w = wW.value;
                const h2 = wH.value;
                const label = `${w} \u00d7 ${h2}`;
                const ratioLbl = ratioLabel(w, h2);

                ctx.fillStyle = TEXT_COLOR;
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";

                ctx.font = "bold 14px sans-serif";
                ctx.fillText(label, x + bw / 2, ofsY + y + bh / 2 - 9);

                ctx.font = "11px sans-serif";
                ctx.fillStyle = "rgba(255,255,255,0.65)";
                ctx.fillText(ratioLbl, x + bw / 2, ofsY + y + bh / 2 + 9);
            },
            mouse(event, pos, _node) {
                return handleMouse(event, pos);
            },
        };
        node.addCustomWidget(canvasWidget);

        // Ensure the node is wide enough
        if (node.size[0] < 300) node.size[0] = 300;

        // ── Handle positions ────────────────────────────────────
        function getHandlePositions(bx, by, bw, bh) {
            return {
                r:  { x: bx + bw,       y: by + bh / 2 },      // right edge
                b:  { x: bx + bw / 2,   y: by + bh },           // bottom edge
                br: { x: bx + bw,       y: by + bh },           // bottom-right
                tl: { x: bx,            y: by },                 // top-left (move)
                tr: { x: bx + bw,       y: by },                 // top-right
                bl: { x: bx,            y: by + bh },            // bottom-left
                l:  { x: bx,            y: by + bh / 2 },       // left edge
                t:  { x: bx + bw / 2,   y: by },                // top edge
            };
        }

        // ── Find widget Y offset (where the canvas widget starts) ──
        function getWidgetY() {
            let y = 0;
            for (const w of node.widgets) {
                if (w === canvasWidget) return y;
                y += (w.computeSize ? w.computeSize()[1] : LiteGraph.NODE_WIDGET_HEIGHT) + 4;
            }
            return y;
        }

        // ── Mouse handling ──────────────────────────────────────
        function handleMouse(event, pos) {
            // pos is relative to the widget's top-left
            const widgetY = 0; // pos is already widget-relative
            const layout = getBoxLayout(node.size[0]);
            const bx = layout.x;
            const by = layout.y;
            const bw = layout.bw;
            const bh = layout.bh;
            const scale = layout.scale;
            const mx = pos[0];
            const my = pos[1];

            if (event.type === "pointerdown" || event.type === "mousedown") {
                // Check handles (generous hit area)
                const hitR  = 12;
                const handles = getHandlePositions(bx, by, bw, bh);

                // Priority: corners first, then edges, then move (inside box)
                if (dist(mx, my, handles.br.x, handles.br.y) < hitR) drag = "br";
                else if (dist(mx, my, handles.tr.x, handles.tr.y) < hitR) drag = "tr";
                else if (dist(mx, my, handles.bl.x, handles.bl.y) < hitR) drag = "bl";
                else if (dist(mx, my, handles.tl.x, handles.tl.y) < hitR) drag = "tl";
                else if (dist(mx, my, handles.r.x,  handles.r.y)  < hitR) drag = "r";
                else if (dist(mx, my, handles.l.x,  handles.l.y)  < hitR) drag = "l";
                else if (dist(mx, my, handles.b.x,  handles.b.y)  < hitR) drag = "b";
                else if (dist(mx, my, handles.t.x,  handles.t.y)  < hitR) drag = "t";
                else if (mx >= bx && mx <= bx + bw && my >= by && my <= by + bh) drag = "move";
                else drag = null;

                if (drag) {
                    dragStartMouse = [mx, my];
                    dragStartW = wW.value;
                    dragStartH = wH.value;
                    return true; // consume event
                }
                return false;
            }

            if (event.type === "pointermove" || event.type === "mousemove") {
                if (!drag || !dragStartMouse) return false;

                const dx = mx - dragStartMouse[0];
                const dy = my - dragStartMouse[1];
                const div = wDV.value || 32;
                const ratio = parseRatio(wAR.value);

                // Convert pixel movement to resolution change
                const pxPerCanvasPx = 1 / scale;

                let newW = dragStartW;
                let newH = dragStartH;

                if (drag === "br" || drag === "r" || drag === "tr") {
                    newW = dragStartW + dx * pxPerCanvasPx;
                }
                if (drag === "br" || drag === "b" || drag === "bl") {
                    newH = dragStartH + dy * pxPerCanvasPx;
                }
                if (drag === "tl" || drag === "l" || drag === "bl") {
                    newW = dragStartW - dx * pxPerCanvasPx;
                }
                if (drag === "tl" || drag === "t" || drag === "tr") {
                    newH = dragStartH - dy * pxPerCanvasPx;
                }

                // Clamp and snap
                newW = clamp(newW, MIN_PX, MAX_PX);
                newH = clamp(newH, MIN_PX, MAX_PX);
                newW = snap(newW, div);
                newH = snap(newH, div);

                // Apply aspect ratio lock
                if (ratio && drag !== "move") {
                    // Determine which dimension to derive from which
                    if (drag === "r" || drag === "l" || drag === "tr" || drag === "tl") {
                        newH = snap(newW / ratio, div);
                    } else if (drag === "b" || drag === "t") {
                        newW = snap(newH * ratio, div);
                    } else {
                        // Corner drag: use the larger delta to drive
                        if (Math.abs(dx) >= Math.abs(dy)) {
                            newH = snap(newW / ratio, div);
                        } else {
                            newW = snap(newH * ratio, div);
                        }
                    }
                }

                newW = clamp(newW, div, MAX_PX);
                newH = clamp(newH, div, MAX_PX);

                wW.value = newW;
                wH.value = newH;
                node.setDirtyCanvas(true, true);
                return true;
            }

            if (event.type === "pointerup" || event.type === "mouseup") {
                if (drag) {
                    drag = null;
                    dragStartMouse = null;
                    return true;
                }
                return false;
            }

            return false;
        }

        function dist(x1, y1, x2, y2) {
            return Math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2);
        }

        // ── Sync aspect ratio when dropdown changes ─────────────
        const origARCallback = wAR.callback;
        wAR.callback = function(value) {
            origARCallback?.call(this, value);
            const ratio = parseRatio(value);
            if (ratio) {
                const div = wDV.value || 32;
                const newH = snap(wW.value / ratio, div);
                wH.value = clamp(newH, div, MAX_PX);
                node.setDirtyCanvas(true, true);
            }
        };
    },
});
