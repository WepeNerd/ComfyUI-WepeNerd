const TILE = 128;
const SAMPLES = 4;
const coverageCount = new Uint8Array(65536);
for (let i = 1; i < coverageCount.length; i++) coverageCount[i] = coverageCount[i >> 1] + (i & 1);

// Union sixteen subpixel samples per pixel. OR is independent of event/frame
// batching: revisiting an antialiased edge cannot make a stroke darker.
export class TileStroke {
    constructor(canvas, context, point, settings, pointer, sourceCanvas = null) {
        this.canvas = canvas; this.ctx = context; this.pointer = pointer;
        this.size = settings.size; this.shape = settings.shape;
        this.opacity = settings.opacity / 100;
        this.rgb = [1, 3, 5].map(i => parseInt(settings.colour.slice(i, i + 2), 16));
        this.last = point; this.samples = []; this.version = 0; this.tiles = new Map(); this.dirty = new Set();
        this.ownsSource = !sourceCanvas;
        this.original = sourceCanvas || document.createElement('canvas');
        if (this.ownsSource) { this.original.width = canvas.width; this.original.height = canvas.height; }
        this.source = this.original.getContext('2d', { willReadFrequently: true });
        if (this.ownsSource) this.source.drawImage(canvas,0,0);
        this.scratch = document.createElement('canvas');
        this.scratch.width = this.scratch.height = TILE;
        this.mask = this.scratch.getContext('2d');
        this.painted = false;
        this.stamp(point);
    }
    diameter(p) { return this.size * (.1 + .9 * p.pressure); }
    stamp(p) {
        if (p.pressure <= 0) return;
        this.version++;
        const radius = this.diameter(p) / 2;
        const left = Math.max(0, Math.floor((p.x - radius - 1) / TILE));
        const top = Math.max(0, Math.floor((p.y - radius - 1) / TILE));
        const right = Math.min(Math.ceil(this.canvas.width / TILE) - 1, Math.floor((p.x + radius + 1) / TILE));
        const bottom = Math.min(Math.ceil(this.canvas.height / TILE) - 1, Math.floor((p.y + radius + 1) / TILE));
        for (let ty = top; ty <= bottom; ty++) for (let tx = left; tx <= right; tx++) {
            const key = `${tx},${ty}`;
            let tile = this.tiles.get(key);
            if (!tile) {
                const x = tx*TILE, y = ty*TILE;
                const w = Math.min(TILE, this.canvas.width-x), h = Math.min(TILE, this.canvas.height-y);
                const before = this.source.getImageData(x, y, w, h);
                tile = {x, y, w, h, before, after: new ImageData(new Uint8ClampedArray(before.data), w, h), coverage: new Uint16Array(w*h), bounds: [w,h,0,0]};
                this.tiles.set(key, tile);
            }
            const x = p.x-tile.x, y = p.y-tile.y;
            tile.bounds[0] = Math.max(0, Math.min(tile.bounds[0], Math.floor(x-radius-1)));
            tile.bounds[1] = Math.max(0, Math.min(tile.bounds[1], Math.floor(y-radius-1)));
            tile.bounds[2] = Math.min(tile.w, Math.max(tile.bounds[2], Math.ceil(x+radius+1)));
            tile.bounds[3] = Math.min(tile.h, Math.max(tile.bounds[3], Math.ceil(y+radius+1)));
            const firstRow = Math.max(0, Math.ceil((y-radius)*SAMPLES-.5));
            const lastRow = Math.min(tile.h*SAMPLES-1, Math.floor((y+radius)*SAMPLES-.5));
            for (let row=firstRow;row<=lastRow;row++) {
                const dy=(row+.5)/SAMPLES-y;
                const span=this.shape==='square' ? radius : Math.sqrt(Math.max(0,radius*radius-dy*dy));
                const first=Math.max(0,Math.ceil((x-span)*SAMPLES-.5));
                const last=Math.min(tile.w*SAMPLES-1,Math.floor((x+span)*SAMPLES-.5));
                const startPixel=first>>2, endPixel=last>>2, offset=(row>>2)*tile.w, shift=(row&3)*4;
                for(let pixel=startPixel;pixel<=endPixel;pixel++) {
                    const lo=pixel===startPixel ? first&3 : 0, hi=pixel===endPixel ? last&3 : 3;
                    tile.coverage[offset+pixel] |= ((1<<(hi-lo+1))-1)<<(shift+lo);
                }
            }
            this.dirty.add(tile);
        }
        this.painted ||= this.dirty.size > 0;
    }
    interpolate(p) {
        const last = this.last;
        const distance = Math.hypot(p.x-last.x,p.y-last.y);
        const steps = Math.max(1, Math.ceil(distance/Math.max(.25, Math.min(this.diameter(last),this.diameter(p))*.18)));
        for (let i=1;i<=steps;i++) {
            const t=i/steps;
            this.stamp({x:last.x+(p.x-last.x)*t,y:last.y+(p.y-last.y)*t,pressure:last.pressure+(p.pressure-last.pressure)*t});
        }
        this.last = p;
    }
    render() {
        for (const tile of this.dirty) {
            const [x,y,right,bottom] = tile.bounds, w=right-x, h=bottom-y;
            const before = tile.before.data, after = tile.after.data;
            for (let row=0;row<h;row++) for (let column=0;column<w;column++) {
                const i=(y+row)*tile.w+x+column;
                const amount = coverageCount[tile.coverage[i]]/16*this.opacity, remaining=1-amount, pixel=i*4;
                after[pixel] = Math.round(before[pixel]*remaining+this.rgb[0]*amount);
                after[pixel+1] = Math.round(before[pixel+1]*remaining+this.rgb[1]*amount);
                after[pixel+2] = Math.round(before[pixel+2]*remaining+this.rgb[2]*amount);
            }
            this.mask.putImageData(tile.after,0,0,x,y,w,h);
            this.source.putImageData(tile.after,tile.x,tile.y,x,y,w,h);
            this.ctx.drawImage(this.scratch,x,y,w,h,tile.x+x,tile.y+y,w,h);
            tile.bounds = [tile.w,tile.h,0,0];
        }
        this.dirty.clear();
    }
    finish(cancel=false) {
        if (cancel) for(const tile of this.tiles.values()) {
            this.ctx.putImageData(tile.before,tile.x,tile.y);
            this.source.putImageData(tile.before,tile.x,tile.y);
        }
        else this.render();
        this.scratch.width = this.scratch.height = 0;
        if (this.ownsSource) this.original.width = this.original.height = 0;
        const tiles = [...this.tiles.values()].map(({x,y,before,after})=>({x,y,before,after}));
        this.tiles.clear(); this.dirty.clear();
        return tiles;
    }
}
