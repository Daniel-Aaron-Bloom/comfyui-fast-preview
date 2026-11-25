// The picture on the node: one canvas, one image or two, nothing else.
//
// What it draws is whatever level of detail has arrived (see net.js): the
// first paint is a 128 px thumbnail stretched over the frame, replaced by the
// exact pixels (a lossless PNG) the moment they have been fetched and decoded.
// The exact pixels are asked for as soon as a result is shown, not when you
// zoom or resize: there is nothing in between.

import { LOD, SIDE_NAMES } from "./protocol.js";

const LABELS = SIDE_NAMES.map((n) => n[0].toUpperCase() + n.slice(1));    // Left, Right

const MODES = ["slide", "flip", "diff"];
const GAINS = [1, 4, 16];
const MAX_ZOOM = 64;

const CSS = `
.fp-root{position:relative;width:100%;height:100%;min-height:200px;overflow:hidden;background:#161616;border-radius:4px;
  user-select:none;-webkit-user-select:none;touch-action:none;font:11px/1.2 system-ui,sans-serif;color:#ddd}
.fp-canvas{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:crosshair}
.fp-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#777;pointer-events:none;text-align:center;padding:12px}
.fp-bar{position:absolute;left:6px;right:6px;display:flex;align-items:center;gap:6px;opacity:0;transition:opacity .12s;pointer-events:none}
.fp-root:hover .fp-bar,.fp-root.fp-busy .fp-bar.fp-bottom{opacity:1}
.fp-bar>*{pointer-events:auto}
.fp-top{top:6px;flex-wrap:wrap}.fp-bottom{bottom:6px;justify-content:space-between}
.fp-seg{display:flex;border-radius:5px;overflow:hidden;background:rgba(20,20,20,.78);backdrop-filter:blur(4px)}
.fp-btn{all:unset;pointer-events:auto;box-sizing:border-box;cursor:pointer;padding:4px 8px;color:#ccc;background:rgba(20,20,20,.78);border-radius:5px}
.fp-seg .fp-btn{border-radius:0;background:none}
.fp-btn:hover{color:#fff;background:rgba(70,70,70,.9)}
.fp-btn[aria-pressed=true]{color:#fff;background:#3b6fd4}
.fp-info{padding:3px 7px;border-radius:5px;background:rgba(20,20,20,.78);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.fp-nav{display:flex;align-items:center;gap:4px;border-radius:5px;background:rgba(20,20,20,.78);flex:none}
.fp-nav .fp-btn{background:none;padding:3px 8px}
.fp-spacer{flex:1}
.fp-hidden{display:none!important}
`;

let cssInjected = false;
function injectCss() {
    if (cssInjected) return;
    cssInjected = true;
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}

export class Viewer {
    /**
     * host: {
     *   requestFull(result, side, idx)   fetch the exact pixels (a no-op if already held or on the way)
     *   save(result, side, idx)          download the exact pixels of one image
     *   changed()                        view settings changed (for saving)
     *   forwardWheel(event)              scrolling that is not ours: hand it to the graph
     * }
     */
    constructor(container, host) {
        injectCss();
        this.host = host;
        this.result = null;
        this.unsubscribe = null;

        this.mode = "slide";
        this.gain = 1;
        this.split = 0.5;
        this.flipRight = false;
        this.idx = 0;
        this.zoom = 1;
        this.cx = 0.5;
        this.cy = 0.5;
        this.showStats = false;
        this.drag = null;
        this.raf = 0;
        this.size = { w: 0, h: 0, dpr: 1 };

        const root = (this.root = el("div", "fp-root"));
        const canvas = (this.canvas = el("canvas", "fp-canvas"));
        this.ctx = canvas.getContext("2d", { alpha: true });
        this.scratch = document.createElement("canvas");
        this.empty = el("div", "fp-empty", "Run the workflow to see the image");

        const top = el("div", "fp-bar fp-top");
        this.seg = el("div", "fp-seg");
        this.modeButtons = MODES.map((m) => {
            const b = el("button", "fp-btn", m[0].toUpperCase() + m.slice(1));
            b.title = { slide: "Slide: move the pointer to wipe between Left and Right", flip: "Flip: click to swap between Left and Right", diff: "Difference: click again to amplify" }[m];
            b.addEventListener("click", () => this.pickMode(m));
            this.seg.appendChild(b);
            return b;
        });
        const zoomBar = el("div", "fp-seg");
        const zbtn = (text, title, fn) => {
            const b = el("button", "fp-btn", text);
            b.title = title;
            b.addEventListener("click", fn);
            zoomBar.appendChild(b);
            return b;
        };
        zbtn("−", "Zoom out", () => this.zoomBy(1 / 1.6));
        zbtn("+", "Zoom in. Ctrl/Cmd + wheel (or pinch) also zooms; drag to pan.", () => this.zoomBy(1.6));
        zbtn("1:1", "Actual pixels (or double-click)", () => this.zoomTo(1));
        zbtn("Fit", "Fit the whole image (or double-click)", () => this.resetView());
        this.saveBar = el("div", "fp-seg");
        this.saveButtons = SIDE_NAMES.map((_, side) => {
            const b = el("button", "fp-btn", "Save");
            b.addEventListener("click", () => this.save(side));
            this.saveBar.appendChild(b);
            return b;
        });
        top.append(this.seg, el("span", "fp-spacer"), this.saveBar, zoomBar);

        const bottom = (this.bottom = el("div", "fp-bar fp-bottom"));
        this.info = el("span", "fp-info");
        this.nav = el("span", "fp-nav");
        const prev = el("button", "fp-btn", "‹");
        const next = el("button", "fp-btn", "›");
        this.counter = el("span");
        prev.addEventListener("click", () => this.step(-1));
        next.addEventListener("click", () => this.step(1));
        this.nav.append(prev, this.counter, next);
        bottom.append(this.info, this.nav);

        root.append(canvas, this.empty, top, bottom);
        root.__fastPreviewViewer = this;    // lets the window-level wheel handler find its viewer
        container.appendChild(root);

        canvas.addEventListener("pointerdown", (e) => this.onDown(e));
        canvas.addEventListener("pointermove", (e) => this.onMove(e));
        canvas.addEventListener("pointerup", (e) => this.onUp(e));
        canvas.addEventListener("pointercancel", () => (this.drag = null));
        canvas.addEventListener("dblclick", (e) => (this.zoom > 1 ? this.resetView() : this.zoomTo(1, this.toDevice(e))));
        canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });

        this.resizer = new ResizeObserver(() => this.syncSize());
        this.resizer.observe(root);
        this.syncUi();
    }

    // ---- state -----------------------------------------------------------

    /** Show a result. Zoom, pan, mode and split carry over: the point of
     *  re-running is to see what changed, in the same place. */
    adopt(result) {
        if (this.result === result) return;
        this.release();
        this.result = result;
        result.refs++;
        this.unsubscribe = result.subscribe(() => this.onResultChanged());
        this.idx = clamp(this.idx, 0, Math.max(0, result.count - 1));
        this.onResultChanged();
    }

    release() {
        this.unsubscribe?.();
        this.unsubscribe = null;
        if (this.result) {
            this.result.refs = Math.max(0, this.result.refs - 1);
            if (this.result.refs === 0) this.result.keepFull([]);    // exact pixels are big: nobody is looking, let them go
        }
        this.result = null;
    }

    destroy() {
        this.release();
        cancelAnimationFrame(this.raf);
        this.resizer.disconnect();
        this.root.remove();
    }

    snapshot() {
        return { mode: this.mode, gain: this.gain, stats: this.showStats };
    }

    restore(s) {
        if (!s) return;
        if (MODES.includes(s.mode)) this.mode = s.mode;
        if (GAINS.includes(s.gain)) this.gain = s.gain;
        this.showStats = !!s.stats;
        this.syncUi();
        this.requestDraw();
    }

    get comparing() {
        return !!this.result && this.result.sides.length > 1;
    }

    pickMode(m) {
        if (m === "diff" && this.mode === "diff") this.gain = GAINS[(GAINS.indexOf(this.gain) + 1) % GAINS.length];
        this.mode = m;
        this.syncUi();
        this.requestDraw();
        this.host.changed?.();
    }

    toggleStats() {
        this.showStats = !this.showStats;
        this.syncUi();
        this.host.changed?.();
    }

    step(d) {
        const n = this.result?.count ?? 0;
        if (n < 2) return;
        this.idx = (this.idx + d + n) % n;
        this.onResultChanged();
    }

    resetView() {
        this.zoom = 1;
        this.cx = this.cy = 0.5;
        this.requestDraw();
    }

    /** Download the exact pixels of one side's current batch item. */
    save(side) {
        const r = this.result;
        if (!r?.sides[side]) return;
        this.host.save?.(r, side, Math.min(this.idx, r.sides[side].count - 1));
    }

    onResultChanged() {
        const r = this.result;
        if (!r) return;
        const shown = r.sides.map((s, side) => [side, Math.min(this.idx, s.count - 1)]);
        r.keepFull(shown);
        for (const [side, idx] of shown) this.host.requestFull?.(r, side, idx);
        this.syncUi();
        this.requestDraw();
    }

    // ---- layout ----------------------------------------------------------

    /** Match the backing store to what is actually on screen (the graph's own
     *  zoom scales this element with a CSS transform, so measure, don't assume). */
    syncSize() {
        const rect = this.root.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const w = Math.max(1, Math.round(rect.width * dpr));
        const h = Math.max(1, Math.round(rect.height * dpr));
        const s = this.size;
        if (s.w === w && s.h === h && s.dpr === dpr) return;
        this.size = { w, h, dpr };
        this.canvas.width = w;
        this.canvas.height = h;
        this.requestDraw();
    }

    frame() {
        const { w: cw, h: ch } = this.size;
        const a = this.result?.sides[0];
        const aspect = a ? a.w / a.h : 1;
        let fw = cw;
        let fh = fw / aspect;
        if (fh > ch) {
            fh = ch;
            fw = fh * aspect;
        }
        fw *= this.zoom;
        fh *= this.zoom;
        return { x: cw / 2 - this.cx * fw, y: ch / 2 - this.cy * fh, w: fw, h: fh };
    }

    // ---- input -----------------------------------------------------------

    onDown(e) {
        if (e.button !== 0 || !this.result) return;
        this.canvas.setPointerCapture(e.pointerId);
        this.drag = { x: e.clientX, y: e.clientY, cx: this.cx, cy: this.cy, moved: false };
    }

    /** A pointer event in the canvas's own pixels, whatever the CSS transform or zoom says. */
    toDevice(e) {
        const rect = this.canvas.getBoundingClientRect();
        const k = rect.width ? this.size.w / rect.width : 1;
        return { x: (e.clientX - rect.left) * k, y: (e.clientY - rect.top) * (rect.height ? this.size.h / rect.height : 1), k };
    }

    onMove(e) {
        const p = this.toDevice(e);
        const d = this.drag;
        if (d) {
            const f = this.frame();
            const dx = (e.clientX - d.x) * p.k;
            const dy = (e.clientY - d.y) * p.k;
            if (Math.abs(dx) + Math.abs(dy) > 3 * this.size.dpr) d.moved = true;
            if (d.moved && this.zoom > 1) {
                this.cx = clamp(d.cx - dx / f.w, 0, 1);
                this.cy = clamp(d.cy - dy / f.h, 0, 1);
                this.requestDraw();
            }
        } else if (this.mode === "slide" && this.comparing) {
            const f = this.frame();
            this.split = clamp((p.x - f.x) / f.w, 0, 1);
            this.requestDraw();
        }
    }

    onUp() {
        const d = this.drag;
        this.drag = null;
        if (d && !d.moved && this.mode === "flip" && this.comparing) {
            this.flipRight = !this.flipRight;
            this.requestDraw();
        }
    }

    onWheel(e) {
        // Plain scrolling belongs to the graph. Pinch (which browsers report as ctrl+wheel) and ctrl/cmd+wheel zoom the image.
        if (!(e.ctrlKey || e.metaKey) || !this.result) {
            this.host.forwardWheel?.(e);
            return;
        }
        e.preventDefault();
        e.stopPropagation();
        this.zoomWheel(e);
    }

    zoomWheel(e) {
        const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0025) * (e.ctrlKey && !e.metaKey ? 2 : 1));
        this.zoomBy(factor, this.toDevice(e));
    }

    /** Zoom by `factor`, keeping the point under `at` (device px; default the centre) where it is. */
    zoomBy(factor, at) {
        if (!this.result) return;
        const px = at?.x ?? this.size.w / 2;
        const py = at?.y ?? this.size.h / 2;
        const f = this.frame();
        const u = (px - f.x) / f.w;
        const v = (py - f.y) / f.h;
        this.zoom = clamp(this.zoom * factor, 1, MAX_ZOOM);
        const g = this.frame();   // size at the new zoom, still centred on the old centre
        this.cx = this.zoom === 1 ? 0.5 : clamp(u - (px - this.size.w / 2) / g.w, 0, 1);
        this.cy = this.zoom === 1 ? 0.5 : clamp(v - (py - this.size.h / 2) / g.h, 0, 1);
        this.requestDraw();
    }

    /** Zoom so one source pixel is `scale` device pixels wide. */
    zoomTo(scale, at) {
        const r = this.result;
        if (!r?.sides.length) return;
        const fit = this.frame().w / this.zoom;               // frame width at zoom 1
        this.zoomBy((scale * r.sides[0].w) / fit / this.zoom, at);
    }

    // ---- drawing ---------------------------------------------------------

    requestDraw() {
        if (this.raf) return;
        this.raf = requestAnimationFrame(() => {
            this.raf = 0;
            try {
                this.draw();
            } catch (err) {
                console.warn("[FastPreview] draw failed:", err);
            }
        });
    }

    sideRect(frame, side) {
        const s = this.result.sides[side];
        const a = s.w / s.h;
        const fa = frame.w / frame.h;
        if (Math.abs(a - fa) < 1e-3) return frame;
        if (a > fa) {
            const h = frame.w / a;
            return { x: frame.x, y: frame.y + (frame.h - h) / 2, w: frame.w, h };
        }
        const w = frame.h * a;
        return { x: frame.x + (frame.w - w) / 2, y: frame.y, w, h: frame.h };
    }

    image(side) {
        const r = this.result;
        if (!r || !r.sides[side]) return null;
        const idx = Math.min(this.idx, r.sides[side].count - 1);
        const best = r.best(side, idx);
        return best ? { ...best, idx, rect: this.sideRect(this.frame(), side), side } : null;
    }

    blit(ctx, img) {
        const { w: W, h: H } = this.size;
        const { bitmap, rect: d } = img;
        const x0 = Math.max(d.x, 0), y0 = Math.max(d.y, 0);
        const x1 = Math.min(d.x + d.w, W), y1 = Math.min(d.y + d.h, H);
        if (x1 <= x0 || y1 <= y0) return;
        // device pixels per source pixel: past 2:1 on the exact pixels, show the pixels, not a blur
        const scale = d.w / this.result.sides[img.side].w;
        ctx.imageSmoothingEnabled = !(img.lod === LOD.FULL && scale >= 2);
        ctx.imageSmoothingQuality = "high";
        const sx = ((x0 - d.x) / d.w) * bitmap.width;
        const sy = ((y0 - d.y) / d.h) * bitmap.height;
        ctx.drawImage(bitmap, sx, sy, ((x1 - x0) / d.w) * bitmap.width, ((y1 - y0) / d.h) * bitmap.height, x0, y0, x1 - x0, y1 - y0);
    }

    label(ctx, text, x, y, align) {
        const dpr = this.size.dpr;
        ctx.font = `600 ${11 * dpr}px system-ui, sans-serif`;
        const w = ctx.measureText(text).width + 12 * dpr;
        const h = 18 * dpr;
        const left = align === "right" ? x - w : x;
        ctx.fillStyle = "rgba(20,20,20,.72)";
        ctx.beginPath();
        ctx.roundRect(left, y, w, h, 4 * dpr);
        ctx.fill();
        ctx.fillStyle = "#fff";
        ctx.textBaseline = "middle";
        ctx.fillText(text, left + 6 * dpr, y + h / 2 + dpr * 0.5);
    }

    draw() {
        const { w: W, h: H, dpr } = this.size;
        const ctx = this.ctx;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, W, H);
        const A = this.image(0);
        if (!A) return;
        const B = this.comparing ? this.image(1) : null;
        const pad = 6 * dpr;

        if (!B) {
            this.blit(ctx, A);
            if (this.comparing) this.label(ctx, LABELS[0], pad, pad + 24 * dpr);
        } else if (this.mode === "diff") {
            this.drawDiff(A, B);
        } else if (this.mode === "flip") {
            const shown = this.flipRight ? B : A;
            this.blit(ctx, shown);
            this.label(ctx, LABELS[this.flipRight ? 1 : 0], pad, pad + 24 * dpr);
        } else {
            const f = this.frame();
            const sx = clamp(f.x + this.split * f.w, 0, W);
            this.blit(ctx, A);
            ctx.save();
            ctx.beginPath();
            ctx.rect(sx, 0, W - sx, H);
            ctx.clip();
            this.blit(ctx, B);
            ctx.restore();
            if (sx > 0 && sx < W) {
                ctx.fillStyle = "rgba(0,0,0,.45)";
                ctx.fillRect(sx - dpr, 0, 3 * dpr, H);
                ctx.fillStyle = "#fff";
                ctx.fillRect(sx - dpr * 0.5, 0, dpr, H);
            }
            this.label(ctx, LABELS[0], Math.max(pad, 0), pad + 24 * dpr);
            this.label(ctx, LABELS[1], W - pad, pad + 24 * dpr, "right");
        }
    }

    /** |A - B|, brightened by repeated additive self-blits (x2 each), so it needs no ctx.filter. */
    drawDiff(A, B) {
        const { w: W, h: H } = this.size;
        const s = this.scratch;
        if (s.width !== W || s.height !== H) {
            s.width = W;
            s.height = H;
        }
        const c = s.getContext("2d");
        c.setTransform(1, 0, 0, 1, 0, 0);
        c.globalCompositeOperation = "source-over";
        c.fillStyle = "#000";
        c.fillRect(0, 0, W, H);
        this.blit(c, A);
        c.globalCompositeOperation = "difference";
        this.blit(c, B);
        c.globalCompositeOperation = "lighter";
        for (let g = 1; g < this.gain; g *= 2) c.drawImage(s, 0, 0);
        c.globalCompositeOperation = "source-over";
        this.ctx.drawImage(s, 0, 0);
        this.label(this.ctx, `|${LABELS[0]} − ${LABELS[1]}| ×${this.gain}`, 6 * this.size.dpr, 30 * this.size.dpr);
    }

    // ---- chrome ----------------------------------------------------------

    syncUi() {
        const r = this.result;
        const set = (node, text) => {
            if (node.textContent !== text) node.textContent = text;
        };
        this.empty.classList.toggle("fp-hidden", !!r?.sides.length && !!r.best(0, 0));
        this.seg.classList.toggle("fp-hidden", !this.comparing);
        const sides = r?.sides.length ?? 0;
        this.saveBar.classList.toggle("fp-hidden", sides === 0);
        this.saveButtons.forEach((b, side) => {
            b.classList.toggle("fp-hidden", side >= sides);
            set(b, sides > 1 ? `Save ${LABELS[side]}` : "Save");
            b.title = `Download the exact pixels${sides > 1 ? ` of the ${SIDE_NAMES[side]} image` : ""} as a PNG`;
        });
        this.modeButtons.forEach((b, n) => {
            b.setAttribute("aria-pressed", String(MODES[n] === this.mode));
            set(b, MODES[n] === "diff" && this.mode === "diff" ? `Diff ×${this.gain}` : MODES[n][0].toUpperCase() + MODES[n].slice(1));
        });
        const count = r?.count ?? 0;
        this.nav.classList.toggle("fp-hidden", count < 2);
        set(this.counter, `${Math.min(this.idx, count - 1) + 1}/${count}`);
        const text = this.describe();
        set(this.info, text);
        this.info.classList.toggle("fp-hidden", !text);
        this.root.classList.toggle("fp-busy", this.isLoadingFull());
    }

    isLoadingFull() {
        const r = this.result;
        if (!r) return false;
        return r.sides.some((_, s) => r.slot(s, Math.min(this.idx, r.sides[s].count - 1)).fullState === "loading");
    }

    describe() {
        const r = this.result;
        if (!r || !r.sides.length) return "";
        const part = (side) => {
            const s = r.sides[side];
            const slot = r.slot(side, Math.min(this.idx, s.count - 1));
            const tag = slot.bitmaps[LOD.FULL]
                ? "exact"
                : slot.bitmaps[LOD.THUMB]
                  ? slot.fullState === "loading" ? "thumbnail, loading exact…" : "thumbnail"
                  : "waiting";
            return `${this.comparing ? LABELS[side] + " " : ""}${s.w}×${s.h} ${tag}`;
        };
        let text = r.sides.map((_, i) => part(i)).join("  ·  ");
        if (this.showStats) {
            const t = r.t;
            const f = (v) => (v === null ? "–" : `${Math.round(v)} ms`);
            text += `  ·  thumb ${f(t.thumb)} · exact ${f(t.full)} after the node started${t.serverMs !== null ? ` (the server had sent the thumbnails after ${Math.round(t.serverMs)} ms)` : ""}`;
        }
        return text;
    }
}
