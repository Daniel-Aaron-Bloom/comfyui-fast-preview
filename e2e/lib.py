"""Shared plumbing for the browser tests. They drive a real ComfyUI frontend with Playwright, so they need:

    pip install playwright && playwright install chromium
    a running ComfyUI (COMFYUI_URL, default http://127.0.0.1:8188) with this node installed
    and the stock input/example.png (any LoadImage-able image named example.png)

They change one server-side setting (Comfy.VueNodes.Enabled) to test both node renderers.
"""
import io, json, os, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright
import numpy as np
from PIL import Image

URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
S = os.environ.get("E2E_OUT") or tempfile.mkdtemp(prefix="fastpreview-e2e-")   # screenshots land here

BUILD = """
async ({size, invert, jitter}) => {
  const L = window.LiteGraph, app = window.app;
  app.graph.clear();
  const mk = (type, x, y) => { const n = L.createNode(type); n.pos = [x, y]; app.graph.add(n); return n; };
  const w = (n, name) => n.widgets.find(x => x.name === name);
  const load = mk("LoadImage", 0, 0);
  const scale = mk("ImageScale", 360, 0);
  w(scale, "upscale_method").value = "lanczos"; const sz = size + (jitter ? 8 * (1 + Math.floor(Math.random() * 400)) : 0); w(scale, "width").value = sz; w(scale, "height").value = sz;
  const other = mk(invert ? "ImageInvert" : "ImageBlur", 360, 300);
  if (!invert) w(other, "blur_radius").value = 4;
  const prev = mk("FastPreview", 720, 0);
  const stock = mk("PreviewImage", 720, 600);
  load.connect(0, scale, 0);
  scale.connect(0, other, 0);
  scale.connect(0, prev, 0);
  other.connect(0, prev, 1);
  scale.connect(0, stock, 0);
  app.canvas.ds.scale = 1; app.canvas.ds.offset = [-prev.pos[0] + 80, -prev.pos[1] + 80];
  app.canvas.setDirty(true, true);
  return { prev: prev.id, stock: stock.id };
}
"""

def api_json(path):
    with urllib.request.urlopen(URL + path, timeout=30) as r:
        return json.load(r)

def api_bytes(path):
    with urllib.request.urlopen(URL + path, timeout=60) as r:
        return r.read()

class Session:
    def __init__(self, p, dsf=2, viewport=(1500, 1000), vue=False):
        self.b = p.chromium.launch(headless=True)
        self.ctx = self.b.new_context(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=dsf, accept_downloads=True)
        self.page = self.ctx.new_page()
        self.logs, self.reqs = [], []
        self.page.on("console", lambda m: self.logs.append(f"[{m.type}] {m.text}"[:300]))
        self.page.on("pageerror", lambda e: self.logs.append(f"[pageerror] {e}"[:400]))
        self.page.on("request", lambda r: self.reqs.append((time.time(), r.url)))
        self.prompt_id = None
        self.page.on("response", self._note_prompt)
        self.vue = vue
    def _note_prompt(self, r):
        """The id of the job this page queued, so a test never reads someone else's history."""
        if r.request.method == "POST" and r.url.rstrip("/").endswith("/api/prompt"):
            try:
                self.prompt_id = r.json().get("prompt_id")
            except Exception:
                pass

    def history(self):
        """History entry of the last job this page queued."""
        for _ in range(50):
            h = api_json(f"/api/history/{self.prompt_id}")
            if self.prompt_id in h:
                return h[self.prompt_id]
            time.sleep(0.1)
        raise AssertionError("job %s never reached the history" % self.prompt_id)

    def open(self):
        pg = self.page
        pg.goto(URL + "/", wait_until="domcontentloaded")
        pg.wait_for_function("() => window.app && window.app.graph && window.LiteGraph", timeout=60000)
        time.sleep(1.5)
        pg.keyboard.press("Escape"); time.sleep(0.4)
        # settings live on the server: never inherit whatever an earlier session left behind
        pg.evaluate("async (v) => { await window.app.extensionManager.setting.set('Comfy.VueNodes.Enabled', v) }", bool(self.vue))
        time.sleep(1)
    def build(self, size=2048, invert=True, jitter=False):
        ids = self.page.evaluate(BUILD, {"size": size, "invert": invert, "jitter": jitter})
        time.sleep(0.8)
        return ids
    def run_and_wait(self, timeout=60000):
        pg = self.page
        pg.evaluate("() => { window.__t0 = performance.now(); window.app.queuePrompt(0) }")
        pg.wait_for_function("() => /preview/.test(document.querySelector('.fp-info')?.textContent || '')", timeout=timeout)
        time.sleep(0.6)
    def viewer_box(self):
        return self.page.locator(".fp-canvas").first.bounding_box()
    def shot(self, name):
        self.page.locator(".fp-root").first.screenshot(path=f"{S}/{name}.png")
        return np.asarray(Image.open(f"{S}/{name}.png").convert("RGB")).astype(int)
    def info(self):
        return self.page.evaluate("() => document.querySelector('.fp-info')?.textContent || ''")
    def fp_requests(self, since=0):
        return [u for t, u in self.reqs if t >= since and "/fastpreview/" in u]
    def close(self):
        self.b.close()
