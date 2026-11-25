// Everything between the server and a viewer: the socket, and the cache of
// what has arrived. No DOM, no canvas.

import { LOD, MIME, KIND_IMAGE, parseFrame } from "./protocol.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const decode = (blob) => createImageBitmap(blob, { colorSpaceConversion: "none" });
const SIDE = "lr";

/**
 * Everything received for one execution of the node, keyed by the token the
 * server minted. A slot is one image (side left or right, batch index i), holding
 * the best bitmap of each level of detail that has arrived.
 */
export class Result {
    constructor(token) {
        this.token = token;
        this.sides = [];            // [{count, w, h, c}] as the server described them
        this.node = null;
        this.wf = null;
        this.feed = null;           // what the node wrote for the queue panel: "preview" | "full" | "off"
        this.slots = new Map();
        this.listeners = new Set();
        this.refs = 0;
        this.gone = false;          // the server no longer remembers it
        this.t = { begin: performance.now(), thumb: null, full: null, end: null, serverMs: null };
    }

    describe({ sides, n, wf, feed }) {
        if (sides) this.sides = sides;
        if (n !== undefined) this.node = n;
        if (wf !== undefined) this.wf = wf;
        if (feed !== undefined) this.feed = feed;
    }

    get count() {
        return this.sides.reduce((m, s) => Math.max(m, s.count), 0);
    }

    slot(side, idx) {
        const key = side * 65536 + idx;
        let slot = this.slots.get(key);
        if (!slot) this.slots.set(key, (slot = { bitmaps: [null, null], fullState: "idle", abort: null }));
        return slot;
    }

    /** { bitmap, lod } of the sharpest image held for a slot, or null. */
    best(side, idx) {
        const { bitmaps } = this.slot(side, idx);
        for (let lod = LOD.FULL; lod >= LOD.THUMB; lod--) if (bitmaps[lod]) return { bitmap: bitmaps[lod], lod };
        return null;
    }

    put(side, idx, lod, bitmap) {
        const slot = this.slot(side, idx);
        // decoding finishes in any order: a duplicate is not wanted
        if (slot.bitmaps[lod]) {
            bitmap.close();
            return;
        }
        // The thumbnail is kept once the exact pixels are here (a few KB): `best` ignores it, and it
        // is what is left to show if the exact pixels are let go because you looked at another image.
        slot.bitmaps[lod] = bitmap;
        const now = performance.now();
        if (lod === LOD.THUMB && this.t.thumb === null) this.t.thumb = now - this.t.begin;
        if (lod === LOD.FULL && this.t.full === null) this.t.full = now - this.t.begin;
        this.emit();
    }

    /** Exact pixels are large (64 MB for a 4K image): keep them only where they are being looked at,
     *  and stop fetching the ones that no longer are. `keep` is a list of [side, idx] pairs. */
    keepFull(keep) {
        const wanted = new Set(keep.map(([side, idx]) => side * 65536 + idx));
        for (const [key, slot] of this.slots) {
            if (wanted.has(key)) continue;
            slot.abort?.abort();
            slot.abort = null;
            if (slot.bitmaps[LOD.FULL]) {
                slot.bitmaps[LOD.FULL].close();
                slot.bitmaps[LOD.FULL] = null;
            }
            if (slot.fullState === "done" || slot.fullState === "loading") slot.fullState = "idle";
        }
    }

    emit() {
        for (const fn of this.listeners) fn(this);
    }

    subscribe(fn) {
        this.listeners.add(fn);
        return () => this.listeners.delete(fn);
    }

    dispose() {
        for (const slot of this.slots.values()) {
            slot.abort?.abort();
            for (const b of slot.bitmaps) b?.close();
        }
        this.slots.clear();
        this.listeners.clear();
    }

    url(side, idx, lod, extra = {}) {
        const q = new URLSearchParams({ t: this.token, s: SIDE[side], i: String(idx), lod: String(lod), ...extra });
        return `/fastpreview/img?${q}`;
    }

    /** The exact pixels: fetched once, decoded once. Safe to call as often as you like. */
    async fetchFull(api, side, idx) {
        const slot = this.slot(side, idx);
        if (slot.fullState !== "idle" || slot.bitmaps[LOD.FULL]) return;
        slot.fullState = "loading";
        const abort = (slot.abort = new AbortController());
        this.emit();
        try {
            const r = await api.fetchApi(this.url(side, idx, LOD.FULL), { signal: abort.signal });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const bitmap = await decode(await r.blob());
            if (abort.signal.aborted) {
                bitmap.close();
                return;
            }
            this.put(side, idx, LOD.FULL, bitmap);
            slot.fullState = "done";
        } catch (err) {
            if (abort.signal.aborted) return;    // keepFull has already reset the slot
            slot.fullState = "error";
            console.warn("[FastPreview] full-resolution fetch failed:", err);
        }
        slot.abort = null;
        this.emit();
    }

    /** The thumbnail over HTTP: for when the socket is not there to push it. */
    async fetchThumb(api, side, idx) {
        const r = await api.fetchApi(this.url(side, idx, LOD.THUMB));
        if (!r.ok) return false;
        this.put(side, idx, LOD.THUMB, await decode(await r.blob()));
        return true;
    }

    /** Last resort: the files the node wrote for the queue panel. The left side's first, then the right's.
     *  A "full" feed file is the exact pixels; a "preview" one is only a stand-in for them. */
    async fetchFeed(api, images) {
        const wanted = [];
        this.sides.forEach((s, side) => {
            for (let i = 0; i < s.count; i++) wanted.push([side, i]);
        });
        await Promise.all(
            wanted.map(async ([side, idx], n) => {
                const im = images[n];
                if (!im || this.best(side, idx)) return;
                const q = new URLSearchParams({ filename: im.filename, subfolder: im.subfolder ?? "", type: im.type ?? "temp" });
                try {
                    const r = await api.fetchApi(`/view?${q}`);
                    if (r.ok) this.put(side, idx, this.feed === "full" ? LOD.FULL : LOD.THUMB, await decode(await r.blob()));
                } catch {
                    /* leave the slot empty */
                }
            }),
        );
    }
}

/** The last few results, bitmaps and all. A result a viewer is showing is never evicted. */
export class Results {
    constructor(limit = 6) {    // decoded bitmaps are big; a replay only ever needs the latest few
        this.limit = limit;
        this.map = new Map();
    }

    get(token) {
        return this.map.get(token);
    }

    ensure(token) {
        let r = this.map.get(token);
        if (r) {
            this.map.delete(token);      // refresh recency
        } else {
            r = new Result(token);
        }
        this.map.set(token, r);
        this.trim();
        return r;
    }

    has(result) {
        return this.map.get(result.token) === result;
    }

    trim() {
        for (const [token, r] of this.map) {
            if (this.map.size <= this.limit) break;
            if (r.refs > 0) continue;
            r.dispose();
            this.map.delete(token);
        }
    }
}

/** The dedicated preview socket. Reconnects forever; every result also has an HTTP way in. */
export class FastSocket {
    constructor({ api, results, onBegin, onGone }) {
        this.api = api;
        this.results = results;
        this.onBegin = onBegin;
        this.onGone = onGone;
        this.ws = null;
        this.open = false;
        this.wants = new Set();
    }

    start() {
        this.loop();
    }

    async loop() {
        let delay = 250;
        for (;;) {
            while (!this.api.clientId) await sleep(100);
            const opened = await this.session(this.api.clientId);
            delay = opened ? 250 : Math.min(delay * 2, 4000);
            await sleep(delay);
        }
    }

    async url(clientId) {
        const { api } = this;
        const q = new URLSearchParams({ clientId });
        try {
            const auth = await api.socketAuthParams?.();
            auth?.forEach((v, k) => q.set(k, v));
        } catch {
            /* an older frontend, or nothing to add */
        }
        const scheme = window.location.protocol === "https:" ? "wss" : "ws";
        return `${scheme}://${api.api_host}${api.api_base}/fastpreview/ws?${q}`;
    }

    /** Resolves, once the socket has closed, to whether it ever opened. */
    async session(clientId) {
        let ws;
        try {
            ws = new WebSocket(await this.url(clientId));
        } catch {
            return false;
        }
        ws.binaryType = "arraybuffer";
        this.ws = ws;
        return new Promise((resolve) => {
            let opened = false;
            ws.onopen = () => {
                opened = true;
                this.open = true;
                for (const token of this.wants) this.send({ op: "want", t: token });
                this.wants.clear();
            };
            ws.onmessage = (ev) => {
                try {
                    if (typeof ev.data === "string") this.onText(JSON.parse(ev.data));
                    else this.onBinary(ev.data);
                } catch (err) {
                    console.warn("[FastPreview] bad frame:", err);
                }
            };
            ws.onerror = () => {};
            ws.onclose = () => {
                this.open = false;
                if (this.ws === ws) this.ws = null;
                resolve(opened);
            };
        });
    }

    send(msg) {
        this.ws?.send(JSON.stringify(msg));
    }

    /** Ask for a result to be sent again (a cached prompt; a page that reloaded). */
    want(token) {
        if (this.open) this.send({ op: "want", t: token });
        else this.wants.add(token);
    }

    onText(msg) {
        if (msg.op === "begin") {
            const r = this.results.ensure(msg.t);
            r.describe(msg);
            this.onBegin?.(r, msg);
        } else if (msg.op === "end") {
            const r = this.results.get(msg.t);
            if (r) {
                r.t.end = performance.now() - r.t.begin;
                r.t.serverMs = msg.ms ?? null;
                r.emit();
            }
        } else if (msg.op === "gone") {
            const r = this.results.get(msg.t);
            if (r) r.gone = true;
            this.onGone?.(msg.t, r);
        }
    }

    onBinary(buffer) {
        const frame = parseFrame(buffer);
        if (!frame || frame.kind !== KIND_IMAGE) return;
        const { meta, payload } = frame;
        const result = this.results.get(meta.t);
        if (!result) return;
        const blob = new Blob([payload], { type: MIME[meta.c] ?? "image/jpeg" });
        decode(blob).then(
            (bitmap) => (this.results.has(result) ? result.put(meta.s, meta.i, meta.l, bitmap) : bitmap.close()),
            () => {},
        );
    }
}
