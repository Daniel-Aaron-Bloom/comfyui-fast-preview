"""Cached run after a reload, socket down, everything down, batches of mixed sizes, Vue nodes, zoom buttons."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

ok = True
def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if (not cond and str(detail)) else ""))
    ok &= bool(cond)

# a fixed graph: same ids, same values -> the same prompt every time, so the server can cache it
FIXED = """
async ({batch, bsize}) => {
  const L = window.LiteGraph, app = window.app;
  app.graph.clear(); app.graph.last_node_id = 0;
  const mk = (type, x, y) => { const n = L.createNode(type); n.pos = [x, y]; app.graph.add(n); return n; };
  const w = (n, name) => n.widgets.find(x => x.name === name);
  const load = mk("LoadImage", 0, 0), sa = mk("ImageScale", 360, 0);
  w(sa, "upscale_method").value = "bilinear"; w(sa, "width").value = 1024; w(sa, "height").value = 1024;
  load.connect(0, sa, 0);
  let a = sa;
  if (batch > 1) { a = mk("RepeatImageBatch", 360, 200); w(a, "amount").value = batch; sa.connect(0, a, 0); }
  const sb = mk("ImageScale", 360, 400);
  w(sb, "upscale_method").value = "bilinear"; w(sb, "width").value = bsize; w(sb, "height").value = bsize;
  load.connect(0, sb, 0);
  const prev = mk("FastPreview", 720, 0);
  a.connect(0, prev, 0); sb.connect(0, prev, 1);
  app.canvas.ds.scale = 1; app.canvas.ds.offset = [-prev.pos[0] + 80, -prev.pos[1] + 80]; app.canvas.setDirty(true, true);
  return prev.id;
}
"""
SPY = """
window.__sent = [];
const send = WebSocket.prototype.send;
WebSocket.prototype.send = function (d) { try { window.__sent.push(String(d)); } catch {} return send.call(this, d); };
"""
BLOCK_WS = """
const Orig = window.WebSocket;
window.WebSocket = new Proxy(Orig, { construct(t, args) { if (String(args[0]).includes('/fastpreview/ws')) args[0] = 'ws://127.0.0.1:9/nope'; return new t(...args); } });
"""

def info(s): return s.info()

with sync_playwright() as p:
    # ---------- 1. a cached run after a reload: the server replays it over the socket ----------
    s = Session(p); s.page.add_init_script(SPY); s.open()
    s.page.evaluate(FIXED, {"batch": 1, "bsize": 1024}); time.sleep(0.5)
    s.run_and_wait()
    check("first run ends on the exact pixels", "exact" in info(s), info(s))
    s.page.reload(wait_until="domcontentloaded")
    s.page.wait_for_function("() => window.app && window.app.graph && window.LiteGraph", timeout=60000); time.sleep(1.5)
    s.page.keyboard.press("Escape")
    s.page.evaluate(FIXED, {"batch": 1, "bsize": 1024}); time.sleep(0.5)
    t0 = time.time()
    s.page.evaluate("() => window.app.queuePrompt(0)")
    s.page.wait_for_function(WAIT_JS["exact"], timeout=30000)
    cached_log = [l for l in s.page.evaluate("() => window.__sent") if '"want"' in l]
    check("after a reload the cached prompt shows its exact pixels (by asking the server to replay it)", bool(cached_log), cached_log)
    check("... without going to the feed files", not [u for t, u in s.reqs if t >= t0 and "/view?" in u and "fastpreview_" in u])
    s.close()

    # ---------- 2. socket down: the HTTP endpoint carries it ----------
    s = Session(p); s.page.add_init_script(BLOCK_WS); s.open()
    s.page.evaluate(FIXED, {"batch": 1, "bsize": 1024}); time.sleep(0.5)
    t0 = time.time(); s.run_and_wait()
    http = [u for t, u in s.reqs if t >= t0 and "/fastpreview/img" in u]
    full = [u for u in http if "lod=1" in u]
    check("with no socket, the exact pixels arrive over HTTP", "Left 1024×1024 exact" in info(s) and len(full) == 2, (info(s), http))
    s.close()

    # ---------- 3. everything down: fall back to the files written for the feed ----------
    s = Session(p); s.page.add_init_script(BLOCK_WS)
    s.page.route("**/fastpreview/img**", lambda r: r.abort()); s.open()
    s.page.evaluate(FIXED, {"batch": 1, "bsize": 1024}); time.sleep(0.5)
    t0 = time.time(); s.run_and_wait(until="any")    # the exact pixels cannot arrive: the feed's file stands in
    feed = [u for t, u in s.reqs if t >= t0 and "/view?" in u and "fastpreview_" in u]
    check("with the socket and endpoint down, the feed files are used", "thumbnail" in info(s) and len(feed) >= 2, (info(s), feed))
    s.close()

    # ---------- 4. batch of 3 against a single, smaller B ----------
    s = Session(p); s.open()
    s.page.evaluate(FIXED, {"batch": 3, "bsize": 512}); time.sleep(0.5)
    s.run_and_wait()
    check("batch: counter shows 1/3", s.page.locator(".fp-nav span").first.text_content() == "1/3", s.page.locator(".fp-nav").first.text_content())
    check("batch: sizes differ and are both reported", "Left 1024×1024" in info(s) and "Right 512×512" in info(s), info(s))
    s.page.locator(".fp-nav .fp-btn").nth(1).click(); time.sleep(0.3)
    check("batch: › goes to 2/3", s.page.locator(".fp-nav span").first.text_content() == "2/3")
    s.page.locator(".fp-nav .fp-btn").nth(0).click(); s.page.locator(".fp-nav .fp-btn").nth(0).click(); time.sleep(0.3)
    check("batch: ‹ wraps around to 3/3", s.page.locator(".fp-nav span").first.text_content() == "3/3")
    box = s.viewer_box(); s.page.mouse.move(box["x"] + box["width"] * 0.9, box["y"] + 40); time.sleep(0.3)
    img = s.shot("e2e_batch"); H, W = img.shape[:2]
    check("batch: the 512 px right image is drawn over the same frame as the left (no black hole)", img[int(H*0.5), int(W*0.5)].sum() > 60)
    s.close()

    # ---------- 5. Vue nodes mode ----------
    s = Session(p, vue=True); s.open()
    s.page.evaluate(FIXED, {"batch": 1, "bsize": 1024}); time.sleep(1.0)
    vue_on = s.page.evaluate("() => window.app.extensionManager.setting.get('Comfy.VueNodes.Enabled')")
    print("INFO vue nodes enabled:", vue_on)
    s.page.screenshot(path=f"{S}/shot_vue_built.png")
    try:
        s.run_and_wait(timeout=30000)
        check("vue mode: the exact pixels arrive", "exact" in info(s), info(s))
        owners = s.page.evaluate("() => [...document.querySelectorAll('img')].filter(i => /\\/view\\?/.test(i.src)).map(i => i.closest('[data-node-id]')?.dataset.nodeId + ':' + (i.closest('[data-node-id]')?.innerText || '').split('\\n')[0])")
        print("INFO /view images in vue nodes (node id:title):", owners)
        check("vue mode: no stock output image inside the FastPreview node (id 4)", not [o for o in owners if o.startswith("4:")], owners)
        s.page.screenshot(path=f"{S}/shot_vue_run.png")
    except Exception as e:
        check("vue mode: the exact pixels arrive", False, repr(e)[:200]); s.page.screenshot(path=f"{S}/shot_vue_fail.png")
    s.close()

    # ---------- 6. zoom buttons (the only zoom that works in every mode) ----------
    for vue in (False, True):
        s = Session(p, vue=vue); s.open()
        s.page.evaluate(FIXED, {"batch": 1, "bsize": 1024}); time.sleep(0.5)
        s.run_and_wait()
        t0 = time.time()
        s.page.locator(".fp-btn", has_text="1:1").first.click(force=True)
        s.page.wait_for_function("() => /Left 1024×1024 exact/.test(document.querySelector('.fp-info')?.textContent || '')", timeout=15000)
        check(f"{'vue' if vue else 'legacy'}: 1:1 at actual pixels shows the exact image", True)
        before = s.shot("e2e_z1")
        s.page.locator(".fp-btn", has_text="+").first.click(force=True); time.sleep(0.4)
        check(f"{'vue' if vue else 'legacy'}: + zooms in", np.abs(s.shot("e2e_z2") - before).mean() > 3)
        s.page.locator(".fp-btn", has_text="Fit").first.click(force=True); time.sleep(0.4)
        check(f"{'vue' if vue else 'legacy'}: Fit returns to the whole image", "Left 1024×1024" in info(s))
        s.close()

print("ALL PASS" if ok else "SOME FAILED")
