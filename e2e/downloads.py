"""Save Left / Save Right (toolbar and right-click menu) and the download file name setting."""
import io, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

ok = True
def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if (not cond and str(detail)) else "")); ok &= bool(cond)

SETTING = "FastPreview.DownloadName"
set_name = lambda pg, v: pg.evaluate("async ([id, v]) => { await window.app.extensionManager.setting.set(id, v) }", [SETTING, v])
png = lambda dl: np.asarray(Image.open(dl.value.path()).convert("RGB")).astype(int)

def download(pg, locator):
    with pg.expect_download(timeout=20000) as dl:
        locator.click()
    return dl

# a left-only graph, and a batch of 3 on the left: for the single-image and %index% cases
GRAPH = """
async ({batch, both}) => {
  const L = window.LiteGraph, app = window.app;
  app.graph.clear(); app.graph.last_node_id = window.__ctr = (window.__ctr || 0) + 100;
  const mk = (type, x, y) => { const n = L.createNode(type); n.pos = [x, y]; app.graph.add(n); return n; };
  const w = (n, name) => n.widgets.find(x => x.name === name);
  const load = mk("LoadImage", 0, 0), sc = mk("ImageScale", 360, 0);
  w(sc, "upscale_method").value = "bilinear"; w(sc, "width").value = 600 + Math.floor(Math.random() * 100); w(sc, "height").value = 400;
  load.connect(0, sc, 0);
  let a = sc;
  if (batch > 1) { a = mk("RepeatImageBatch", 360, 200); w(a, "amount").value = batch; sc.connect(0, a, 0); }
  const prev = mk("FastPreview", 720, 0); prev.title = "Look / at: this";
  a.connect(0, prev, 0);
  if (both) { const inv = mk("ImageInvert", 360, 400); a.connect(0, inv, 0); inv.connect(0, prev, 1); }    // right is a batch too
  if (both === "single") { const one = mk("ImageInvert", 360, 600); sc.connect(0, one, 0); one.connect(0, prev, 1); }   // ... or one image for the whole batch
  app.canvas.ds.scale = 1; app.canvas.ds.offset = [-prev.pos[0] + 80, -prev.pos[1] + 80]; app.canvas.setDirty(true, true);
  return prev.id;
}
"""

with sync_playwright() as p:
    s = Session(p, vue=bool(os.environ.get("VUE"))); s.open(); pg = s.page
    ids = s.build(size=1024, invert=True, jitter=True); s.run_and_wait()
    set_name(pg, "")     # whatever an earlier run left behind
    box = s.viewer_box(); pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2); time.sleep(0.3)

    # ---- the toolbar ----
    labels = pg.evaluate("() => [...document.querySelectorAll('.fp-top .fp-btn')].map(b => b.textContent)")
    check("toolbar has Save Left and Save Right", "Save Left" in labels and "Save Right" in labels, labels)
    left_dl = download(pg, pg.locator(".fp-btn", has_text="Save Left").first)
    right_dl = download(pg, pg.locator(".fp-btn", has_text="Save Right").first)
    check("default name: fast_preview_left_00001.png", left_dl.value.suggested_filename == "fast_preview_left_00001.png", left_dl.value.suggested_filename)
    check("default name: fast_preview_right_00001.png", right_dl.value.suggested_filename == "fast_preview_right_00001.png", right_dl.value.suggested_filename)

    # Save Left is the stock PNG; Save Right is the (inverted) right image: trunc(a) + trunc(255 - a) is 254 or 255
    pid = s.prompt_id; outs = s.history()["outputs"]
    import urllib.parse
    stock_files = outs[str(ids["stock"])]["images"]
    stock = np.asarray(Image.open(io.BytesIO(api_bytes("/api/view?" + urllib.parse.urlencode({k: stock_files[0][k] for k in ("filename", "subfolder", "type")})))).convert("RGB")).astype(int)
    left, right = png(left_dl), png(right_dl)
    check("Save Left is ComfyUI's own PNG of the left image, pixel for pixel", left.shape == stock.shape and (left == stock).all())
    gap = 255 - left - right
    check("Save Right is the other image (inverted): 255 - left - right is 0 or 1 everywhere", set(np.unique(gap)) <= {0, 1}, np.unique(gap))

    # ---- the right-click menu ----
    pg.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5, button="right"); time.sleep(0.5)
    items = pg.evaluate("() => [...document.querySelectorAll('.litemenu-entry')].map(e => e.textContent.trim())")
    check("menu: Save Left, Save Right come first", items[:2] == ["Save Left", "Save Right"], items[:8])
    menu_dl = download(pg, pg.locator(".litemenu-entry", has_text="Save Right").first)
    check("menu: Save Right downloads the same image as the toolbar button", (png(menu_dl) == right).all())

    # ---- the setting ----
    time.sleep(0.2)
    set_name(pg, "x-%side%-%random:8%-%index:3%-%width%x%height%-%date:yyyy%")
    a = download(pg, pg.locator(".fp-btn", has_text="Save Left").first).value.suggested_filename
    b = download(pg, pg.locator(".fp-btn", has_text="Save Left").first).value.suggested_filename
    # the build jitter makes the size 1024..4216 square; the point is the shape of the name
    pattern = r"^x-left-([a-z0-9]{8})-001-(\d+)x(\d+)-(\d{4})\.png$"
    ma, mb = re.match(pattern, a), re.match(pattern, b)
    check("custom template expands (side, 8 random chars, padded index, size, year)", bool(ma and mb), (a, b))
    if ma and mb:
        check("the random part is new on every click", ma.group(1) != mb.group(1), (a, b))
        check("... and the size in the name is the real one", int(ma.group(2)) == int(ma.group(3)) >= 1024, a)
    set_name(pg, "   ")
    check("a blank setting falls back to the default", download(pg, pg.locator(".fp-btn", has_text="Save Right").first).value.suggested_filename == "fast_preview_right_00001.png")
    set_name(pg, "../../%side%:evil?*")
    n = download(pg, pg.locator(".fp-btn", has_text="Save Left").first).value.suggested_filename
    check("path separators and reserved characters can't escape the name", "/" not in n and "\\" not in n and ":" not in n and "?" not in n and n.endswith(".png"), n)

    # ---- the setting is in the real Settings dialog ----
    set_name(pg, "")
    pg.evaluate("() => window.app.extensionManager.command.execute('Comfy.ShowSettingsDialog')"); time.sleep(1.5)
    try:
        pg.get_by_placeholder(re.compile("search", re.I)).first.fill("Download file name"); time.sleep(1.2)
        shown = pg.get_by_text("Download file name").count() > 0
        pg.screenshot(path=f"{S}/shot_settings.png")
        check("the setting is listed in Settings (search: Download file name)", shown)
        print("INFO settings screenshot:", f"{S}/shot_settings.png")
    except Exception as e:
        check("the setting is listed in Settings", False, repr(e)[:150])
    pg.keyboard.press("Escape")

    # ---- one image: a plain Save, a plain menu ----
    pg.evaluate(GRAPH, {"batch": 1, "both": False}); time.sleep(0.6); s.run_and_wait()
    box = s.viewer_box(); pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2); time.sleep(0.3)
    labels = pg.evaluate("() => [...document.querySelectorAll('.fp-top .fp-btn')].filter(b => b.offsetParent).map(b => b.textContent)")
    check("one image: the toolbar says Save, not Save Left/Right", "Save" in labels and "Save Right" not in labels and "Save Left" not in labels, labels)
    pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, button="right"); time.sleep(0.5)
    items = pg.evaluate("() => [...document.querySelectorAll('.litemenu-entry')].map(e => e.textContent.trim())")
    check("one image: the menu keeps ComfyUI's Open / Copy / Save Image", items[:3] == ["Open Image", "Copy Image", "Save Image"], items[:5])
    pg.keyboard.press("Escape"); pg.mouse.click(5, 500); time.sleep(0.2)
    set_name(pg, "%title%_%index%")
    n = download(pg, pg.locator(".fp-btn", has_text="Save").first).value.suggested_filename
    check("%title% is the node's title, made safe", n == "Look _ at_ this_1.png", n)

    # ---- a batch: %index% follows the viewer ----
    set_name(pg, "%side%-%index:2%")
    pg.evaluate(GRAPH, {"batch": 3, "both": True}); time.sleep(0.6); s.run_and_wait()
    pg.locator(".fp-nav .fp-btn").nth(1).click(); time.sleep(0.3)
    n = download(pg, pg.locator(".fp-btn", has_text="Save Right").first).value.suggested_filename
    check("batch: on 2/3 the right file is item 2 (index follows the viewer)", n == "right-02.png", n)
    n = download(pg, pg.locator(".fp-btn", has_text="Save Left").first).value.suggested_filename
    check("batch: ... and so is the left one", n == "left-02.png", n)
    # one right image against a batch of three on the left: it is shown (and so saved) as its own item 1
    pg.evaluate(GRAPH, {"batch": 3, "both": "single"}); time.sleep(0.6); s.run_and_wait()
    pg.locator(".fp-nav .fp-btn").nth(1).click(); time.sleep(0.3)
    names = (download(pg, pg.locator(".fp-btn", has_text="Save Left").first).value.suggested_filename,
             download(pg, pg.locator(".fp-btn", has_text="Save Right").first).value.suggested_filename)
    check("batch of 3 vs one right image: left-02 and right-01", names == ("left-02.png", "right-01.png"), names)
    set_name(pg, "")
    errs = [l for l in s.logs if ("[error]" in l or "pageerror" in l) and "404" not in l and "huggingface" not in l and "Content Security" not in l]
    check("no console errors from the page", not errs, errs)
    s.close()
print("ALL PASS" if ok else "SOME FAILED")
