// Result (web/net.js) with a fake api and a fake createImageBitmap. Exits non-zero on the first failed assertion.
// net.js is a browser module that imports ./protocol.js: load both as text, so no package.json "type" is needed.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (name) => readFileSync(fileURLToPath(new URL(`../web/${name}`, import.meta.url)), "utf8");
const dataUrl = (src) => "data:text/javascript;base64," + Buffer.from(src).toString("base64");
const protocol = dataUrl(read("protocol.js"));
const { Result } = await import(dataUrl(read("net.js").replace('"./protocol.js"', `"${protocol}"`)));
const { LOD } = await import(protocol);

const bitmaps = [];
globalThis.createImageBitmap = async () => {
    const b = { closed: false, close() { this.closed = true; } };
    bitmaps.push(b);
    return b;
};

const ok = (body = "x") => ({ ok: true, status: 200, blob: async () => new Blob([body]) });

/** An api whose fetches stay open until `release()`d, and reject like fetch does when aborted. */
function slowApi() {
    const urls = [];
    const waiting = [];
    return {
        urls,
        fetchApi(url, opts = {}) {
            urls.push(url);
            return new Promise((resolve, reject) => {
                opts.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
                waiting.push(() => resolve(ok()));
            });
        },
        release: () => waiting.splice(0).forEach((f) => f()),
    };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const fresh = () => {
    const r = new Result("tok");
    r.describe({ sides: [{ count: 2, w: 100, h: 50, c: 3 }], n: "1", wf: "w", feed: "preview" });
    return r;
};
const asyncTest = async (name, fn) => {
    bitmaps.length = 0;
    await fn();
    console.log("ok", name);
};

await asyncTest("the exact pixels replace the thumbnail on screen, and the thumbnail stays behind them", async () => {
    const r = fresh();
    r.put(0, 0, LOD.THUMB, await createImageBitmap());
    assert.equal(r.best(0, 0).lod, LOD.THUMB);
    await r.fetchFull({ fetchApi: async () => ok() }, 0, 0);
    assert.equal(r.best(0, 0).lod, LOD.FULL);
    assert.equal(r.slot(0, 0).bitmaps[LOD.THUMB].closed, false);
    assert.notEqual(r.t.full, null);
});

await asyncTest("the exact pixels can arrive before the thumbnail", async () => {
    const r = fresh();
    await r.fetchFull({ fetchApi: async () => ok() }, 0, 0);
    r.put(0, 0, LOD.THUMB, await createImageBitmap());
    assert.equal(r.best(0, 0).lod, LOD.FULL);
    assert.equal(r.slot(0, 0).bitmaps[LOD.THUMB].closed, false);
});

await asyncTest("asking again while it is held or on its way fetches nothing", async () => {
    const api = slowApi();
    const r = fresh();
    const first = r.fetchFull(api, 0, 0);
    await r.fetchFull(api, 0, 0);
    assert.equal(api.urls.length, 1);
    api.release();
    await first;
    await r.fetchFull(api, 0, 0);
    assert.equal(api.urls.length, 1);
    assert.equal(r.slot(0, 0).fullState, "done");
});

await asyncTest("looking away aborts the fetch, and looking back fetches again", async () => {
    const api = slowApi();
    const r = fresh();
    const pending = r.fetchFull(api, 0, 0);
    assert.equal(r.slot(0, 0).fullState, "loading");
    r.keepFull([[0, 1]]);                         // now looking at image 1
    await pending;
    assert.equal(r.slot(0, 0).fullState, "idle");
    assert.equal(r.slot(0, 0).bitmaps[LOD.FULL], null);
    const again = r.fetchFull(api, 0, 0);
    api.release();
    await again;
    assert.equal(api.urls.length, 2);
    assert.equal(r.best(0, 0).lod, LOD.FULL);
});

await asyncTest("a fetch that finished decoding after being aborted is not shown, and not leaked", async () => {
    const r = fresh();
    let resolveBlob;
    const api = { fetchApi: async () => ({ ok: true, status: 200, blob: () => new Promise((res) => (resolveBlob = res)) }) };
    const pending = r.fetchFull(api, 0, 0);
    await tick();
    r.keepFull([]);
    resolveBlob(new Blob(["x"]));
    await pending;
    assert.equal(r.best(0, 0), null);
    assert.equal(bitmaps.length, 1);
    assert.equal(bitmaps[0].closed, true);
});

await asyncTest("moving away lets go of the exact pixels but keeps the thumbnail to show on the way back", async () => {
    const r = fresh();
    r.put(0, 0, LOD.THUMB, await createImageBitmap());
    await r.fetchFull({ fetchApi: async () => ok() }, 0, 0);
    const full = r.slot(0, 0).bitmaps[LOD.FULL];
    r.keepFull([[0, 1]]);
    assert.equal(full.closed, true);
    assert.equal(r.best(0, 0).lod, LOD.THUMB);
    assert.equal(r.slot(0, 0).fullState, "idle");
});

await asyncTest("a failed fetch is an error that is not retried on every redraw", async () => {
    const r = fresh();
    let calls = 0;
    const api = { fetchApi: async () => (calls++, { ok: false, status: 404 }) };
    const warn = console.warn;
    console.warn = () => {};
    try {
        await r.fetchFull(api, 0, 0);
        await r.fetchFull(api, 0, 0);
    } finally {
        console.warn = warn;
    }
    assert.equal(calls, 1);
    assert.equal(r.slot(0, 0).fullState, "error");
});

await asyncTest("the feed fallback: a preview file is a stand-in, a full file is the exact pixels, and nothing is overwritten", async () => {
    for (const [feed, lod] of [["preview", LOD.THUMB], ["full", LOD.FULL]]) {
        const r = fresh();
        r.describe({ feed });
        r.put(0, 1, LOD.THUMB, await createImageBitmap());         // image 1 already has something
        const fetched = [];
        const api = { fetchApi: async (url) => (fetched.push(url), ok()) };
        await r.fetchFeed(api, [
            { filename: "a.jpg", subfolder: "", type: "temp" },
            { filename: "b.jpg", subfolder: "", type: "temp" },
        ]);
        assert.equal(r.best(0, 0).lod, lod, feed);
        assert.equal(fetched.length, 1, feed);                     // only image 0 was fetched
        assert.match(fetched[0], /filename=a\.jpg/);
    }
});

await asyncTest("a thumbnail over HTTP asks for lod 0", async () => {
    const r = fresh();
    const urls = [];
    assert.equal(await r.fetchThumb({ fetchApi: async (u) => (urls.push(u), ok()) }, 0, 1), true);
    assert.match(urls[0], /lod=0/);
    assert.match(urls[0], /i=1/);
    assert.equal(r.best(0, 1).lod, LOD.THUMB);
});

await asyncTest("disposing a result aborts what is still being fetched", async () => {
    const api = slowApi();
    const r = fresh();
    const pending = r.fetchFull(api, 0, 0);
    r.dispose();
    await pending;
    assert.equal(r.slots.size, 0);
});
