"""Time from the node starting to execute until pixels are on screen, against stock PreviewImage."""
import os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

GRAPH = """
async ({size, kind}) => {
  const L = window.LiteGraph, app = window.app;
  app.graph.clear();
  app.graph.last_node_id = window.__ctr = (window.__ctr || 0) + 100;   // fresh ids: the output store is keyed by id
  const mk = (type, x, y) => { const n = L.createNode(type); n.pos = [x, y]; app.graph.add(n); return n; };
  const w = (n, name) => n.widgets.find(x => x.name === name);
  const load = mk("LoadImage", 0, 0), scale = mk("ImageScale", 360, 0);
  w(scale, "upscale_method").value = "nearest-exact"; w(scale, "width").value = size; w(scale, "height").value = size;
  load.connect(0, scale, 0);
  // a seed input so each run is a real execution, not a cache hit: noise via ImageBlur radius changes
  const blur = mk("ImageBlur", 360, 300); w(blur, "blur_radius").value = 1 + Math.floor(Math.random() * 6); w(blur, "sigma").value = 0.1 + Math.random() * 9.8; scale.connect(0, blur, 0);
  let prev;
  if (kind === "fast") { prev = mk("FastPreview", 720, 0); blur.connect(0, prev, 0); }
  else if (kind === "fast2") { prev = mk("FastPreview", 720, 0); blur.connect(0, prev, 0); scale.connect(0, prev, 1); }
  else { prev = mk("PreviewImage", 720, 0); blur.connect(0, prev, 0); }
  app.canvas.ds.scale = 1; app.canvas.ds.offset = [-prev.pos[0] + 80, -prev.pos[1] + 80]; app.canvas.setDirty(true, true);
  window.__prev = prev; return prev.id;
}
"""

MEASURE = """
() => new Promise((resolve) => {
  const app = window.app, api = window.comfyAPI.api.api, prev = window.__prev, isFast = prev.type === "FastPreview";
  const t = { exec: null, first: null, final: null };
  const onExec = (e) => { if (String(e.detail) === String(prev.id) && t.exec === null) t.exec = performance.now(); };
  api.addEventListener("executing", onExec);
  const poll = () => {
    const now = performance.now();
    if (isFast) {
      const txt = document.querySelector(".fp-info")?.textContent || "";
      const exact = /exact/.test(txt) && !/thumbnail|waiting/.test(txt);    // every image on screen is exact
      if (t.first === null && (exact || /thumbnail/.test(txt))) t.first = now;
      if (t.final === null && exact) t.final = now;
    } else {
      const im = prev.imgs && prev.imgs[0];
      if (im && im.complete && im.naturalWidth > 0) { if (t.first === null) t.first = now; if (t.final === null) t.final = now; }
    }
    if (t.final !== null) { api.removeEventListener("executing", onExec); resolve(t.exec === null ? null : { first: t.first - t.exec, final: t.final - t.exec }); return; }
    requestAnimationFrame(poll);
  };
  requestAnimationFrame(poll);
  app.queuePrompt(0);
})
"""

def run(p, size, kind, reps):
    s = Session(p, dsf=1); s.open()
    out = []
    for i in range(reps + 1):                       # first rep is warm-up (JIT, caches)
        s.page.evaluate(GRAPH, {"size": size, "kind": kind}); time.sleep(0.5)
        r = s.page.evaluate(MEASURE)
        time.sleep(0.8)
        if i and r is not None: out.append(r)
        elif i: print("   (discarded a cached run)", file=sys.stderr)
    s.close()
    return out

with sync_playwright() as p:
    print(f"{'':28s}{'first pixels':>16s}{'final image':>16s}   (ms from the node starting to execute, median of 5)")
    for size in (1024, 2048, 4096):
        for kind, label in (("stock", "stock PreviewImage"), ("fast", "FastPreview, one image"), ("fast2", "FastPreview, compare A+B")):
            rs = run(p, size, kind, 5)
            if not rs: print(size, kind, 'no valid runs'); continue
            f, l = statistics.median(r["first"] for r in rs), statistics.median(r["final"] for r in rs)
            print(f"{size:>5}² {label:22s}{f:14.0f} ms{l:14.0f} ms")
