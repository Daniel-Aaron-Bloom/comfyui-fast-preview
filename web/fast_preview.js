import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { DEFAULT_TEMPLATE, SETTING_ID, SYNTAX_HELP, expandName } from "./filename.js";
import { LOD, SIDE_NAMES } from "./protocol.js";
import { FastSocket, Results } from "./net.js";
import { Viewer } from "./viewer.js";

const NODE = "FastPreview";
const MIN_WIDTH = 440;
const MIN_HEIGHT = 470;

const results = new Results();
const attached = new Set();    // { node, viewer }
let socket = null;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(cond, timeout, step = 30) {
    for (let waited = 0; waited < timeout && !cond(); waited += step) await sleep(step);
    return cond();
}

function toast(severity, summary, detail) {
    const t = app.extensionManager?.toast;
    if (t?.add) t.add({ severity, summary, detail, life: 5000 });
    else console[severity === "error" ? "error" : "log"](`[FastPreview] ${summary}: ${detail}`);
}

// ---- getting at the exact pixels -------------------------------------------

async function fetchPng(result, side, idx, download) {
    const res = await api.fetchApi(result.url(side, idx, LOD.FULL, download ? { dl: "1" } : {}));
    if (res.status === 404) throw new Error("The full image is no longer in memory. Run the workflow again.");
    if (!res.ok) throw new Error(`The server answered ${res.status}.`);
    return res.blob();
}

function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The "Download file name" setting, falling back to the default if it is blank or unreadable. */
function nameTemplate() {
    let v;
    try {
        v = app.extensionManager?.setting?.get?.(SETTING_ID) ?? app.ui?.settings?.getSettingValue?.(SETTING_ID);
    } catch {
        /* no settings store (yet): use the default */
    }
    return typeof v === "string" && v.trim() ? v : DEFAULT_TEMPLATE;
}

function downloadName(node, result, side, idx) {
    const s = result.sides[side];
    return expandName(nameTemplate(), { side: SIDE_NAMES[side], index: idx + 1, width: s.w, height: s.h, node: node.id, title: node.title });
}

const actions = {
    async save(result, side, idx, name) {
        try {
            saveBlob(await fetchPng(result, side, idx, true), name);
        } catch (err) {
            toast("error", "Save Image", err.message);
        }
    },
    async open(result, side, idx) {
        const tab = window.open("", "_blank");    // opened now, while the click still counts
        try {
            const url = URL.createObjectURL(await fetchPng(result, side, idx, false));
            if (tab) tab.location.href = url;
            else window.open(url, "_blank");
        } catch (err) {
            tab?.close();
            toast("error", "Open Image", err.message);
        }
    },
    async copy(result, side, idx) {
        try {
            // a promise, not a blob: the clipboard write must start inside the click
            await navigator.clipboard.write([new ClipboardItem({ "image/png": fetchPng(result, side, idx, false) })]);
        } catch (err) {
            toast("error", "Copy Image", err.message);
        }
    },
};

// ---- a result that did not arrive by itself --------------------------------

/** For a fresh run the frames are here before `executed` is. When they are not
 *  (a cached prompt, a reload, a socket that was down) there are three ways in,
 *  fastest first: ask the socket to say it again, the HTTP endpoint, the feed files.
 *
 *  `announced`: the server already told us about this result, so its frames are
 *  merely in flight; give them a moment. If it never did, a cached prompt is
 *  replaying and nothing is coming: ask at once. */
async function recover(state, result, images, announced) {
    if (state.recovering === result) return;
    state.recovering = result;
    if (announced) await sleep(150);
    if (result.best(0, 0)) return;
    if (socket.open) {
        socket.want(result.token);
        await until(() => result.best(0, 0) || result.gone, 1500);
        if (result.best(0, 0)) return;
    } else {
        try {
            await Promise.all(result.sides.flatMap((s, side) => Array.from({ length: s.count }, (_, i) => result.fetchThumb(api, side, i))));
        } catch {
            /* fall through to the feed */
        }
        if (result.best(0, 0)) return;
    }
    await result.fetchFeed(api, images);
}

// ---- the node --------------------------------------------------------------

function attach(node) {
    // The queue panel needs `images` in the node's output; the stock preview
    // would draw them (and fetch every file) on top of ours. Switch it off.
    node.hideOutputImages = true;
    const state = { node, viewer: null, recovering: null };
    node.onDrawBackground = function () {
        // The stock handler does nothing else for a node like this one: it only loads and lays out output images.
        // (The classic renderer calls this on every draw; the Vue-nodes renderer never does: see onDrawForeground below.)
        state.viewer.nudge();
    };

    const container = document.createElement("div");
    Object.assign(container.style, { position: "relative", width: "100%", height: "100%", minHeight: "220px", overflow: "hidden" });

    state.viewer = new Viewer(container, {
        requestFull: (result, side, idx) => result.fetchFull(api, side, idx),
        save: (result, side, idx) => actions.save(result, side, idx, downloadName(node, result, side, idx)),
        changed: () => {
            node.properties = { ...node.properties, fastPreview: state.viewer.snapshot() };
        },
        // A DOM element eats the wheel before the graph sees it; replay it on the graph's canvas
        // so zooming/panning works over this node whichever navigation mode is set.
        forwardWheel: (e) => {
            const target = app.canvas?.canvas;
            if (!target) return;
            e.preventDefault();
            target.dispatchEvent(new WheelEvent("wheel", {
                bubbles: true, cancelable: true, deltaX: e.deltaX, deltaY: e.deltaY, deltaZ: e.deltaZ, deltaMode: e.deltaMode,
                clientX: e.clientX, clientY: e.clientY, screenX: e.screenX, screenY: e.screenY,
                ctrlKey: e.ctrlKey, shiftKey: e.shiftKey, altKey: e.altKey, metaKey: e.metaKey,
            }));
        },
    });
    const viewer = state.viewer;

    const widget = node.addDOMWidget("fast_preview_view", "fast_preview", container, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 220,
    });
    widget.serialize = false;

    const [w, h] = node.size;
    node.setSize([Math.max(w, MIN_WIDTH), Math.max(h, MIN_HEIGHT)]);
    attached.add(state);

    const onConfigure = node.onConfigure;
    node.onConfigure = function () {
        onConfigure?.apply(this, arguments);
        viewer.restore(this.properties?.fastPreview);
    };

    const onExecuted = node.onExecuted;
    node.onExecuted = function (output) {
        onExecuted?.apply(this, arguments);
        const raw = output?.fast_preview?.[0];
        if (typeof raw !== "string") return;
        let info;
        try {
            info = JSON.parse(raw);
        } catch {
            return;
        }
        const announced = !!results.get(info.t)?.sides.length;
        const result = results.ensure(info.t);
        result.describe(info);
        viewer.adopt(result);
        if (!result.best(0, 0)) recover(state, result, output.images ?? [], announced);
    };

    const getExtraMenuOptions = node.getExtraMenuOptions;
    node.getExtraMenuOptions = function (canvas, options) {
        const out = getExtraMenuOptions?.apply(this, arguments);
        const result = viewer.result;
        if (result?.sides.length) {
            const at = (side) => [result, side, Math.min(viewer.idx, result.sides[side].count - 1)];
            const items = [];
            if (result.sides.length > 1) {
                // two images: every action, once for each side, named for the side it is drawn on
                const label = (verb, side) => `${verb} ${SIDE_NAMES[side][0].toUpperCase()}${SIDE_NAMES[side].slice(1)}`;
                for (const side of [0, 1]) items.push({ content: label("Save", side), callback: () => viewer.save(side) });
                for (const side of [0, 1]) items.push({ content: label("Copy", side), callback: () => actions.copy(...at(side)) });
                for (const side of [0, 1]) items.push({ content: label("Open", side), callback: () => actions.open(...at(side)) });
            } else {
                items.push(
                    { content: "Open Image", callback: () => actions.open(...at(0)) },
                    { content: "Copy Image", callback: () => actions.copy(...at(0)) },
                    { content: "Save Image", callback: () => viewer.save(0) },
                );
            }
            items.push({ content: viewer.showStats ? "Hide preview timings" : "Show preview timings", callback: () => viewer.toggleStats() }, null);
            options.unshift(...items);
        }
        return out;
    };

    // A DOM element swallows the canvas's right-click: hand it back, so the node's menu (and Save Image) opens.
    viewer.canvas.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        app.canvas?.processContextMenu?.(node, e);
    });

    const onRemoved = node.onRemoved;
    node.onRemoved = function () {
        onRemoved?.apply(this, arguments);
        viewer.destroy();
        attached.delete(state);
    };
}

// ComfyUI's newer node renderer claims every wheel event over a node for the graph before an element inside
// it can see one. Image zoom is ctrl/cmd+wheel (or a trackpad pinch) over the viewer only, so take exactly
// that, at the window, before anyone else does. Everything else is left alone.
window.addEventListener(
    "wheel",
    (e) => {
        if (!(e.ctrlKey || e.metaKey)) return;
        const viewer = e.target?.closest?.(".fp-root")?.__fastPreviewViewer;
        if (!viewer?.result) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        viewer.zoomWheel(e);
    },
    { capture: true, passive: false },
);

app.registerExtension({
    name: "FastPreview.Compare",

    settings: [
        {
            id: SETTING_ID,
            name: "Download file name",
            type: "text",
            defaultValue: DEFAULT_TEMPLATE,
            tooltip: SYNTAX_HELP,
            category: ["Fast Preview", "Downloads", "File name"],
        },
    ],

    async setup() {
        socket = new FastSocket({
            api,
            results,
            // The first frames land before `executed` does. Show them now, when the node can be
            // told for certain: it is in the graph on screen, and this run came from that workflow.
            onBegin(result) {
                const graphId = app.rootGraph?.id ?? app.graph?.id;
                if (!result.wf || result.wf !== graphId) return;
                for (const { node, viewer } of attached) {
                    if (node.graph === app.rootGraph && String(node.id) === String(result.node)) viewer.adopt(result);
                }
            },
        });
        socket.start();

        // Zooming the graph scales every viewer with a CSS transform, which nothing announces to them.
        // The graph canvas redraws on every zoom and pan, in either renderer: that is the cue to look again.
        if (await until(() => app.canvas, 5000)) {
            const previous = app.canvas.onDrawForeground;
            app.canvas.onDrawForeground = function () {
                for (const { viewer } of attached) viewer.nudge();
                return previous?.apply(this, arguments);
            };
        }
    },

    nodeCreated(node) {
        if (node.constructor?.comfyClass === NODE) attach(node);
    },
});
