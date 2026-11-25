"""Slide, flip, diff, zoom, right-click Save Image (compared pixel for pixel with PreviewImage), feed files.
VUE=1 runs it in the Vue-nodes renderer."""
import io, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *

ok = True
import urllib.parse
view_url = lambda e: "/api/view?" + urllib.parse.urlencode({k: e[k] for k in ("filename", "subfolder", "type")})
def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if (not cond and str(detail)) else ""))
    ok &= bool(cond)

with sync_playwright() as p:
    import os
    s = Session(p, vue=bool(os.environ.get('VUE')))
    s.open()
    ids = s.build(size=2048, invert=True)
    pg = s.page
    t_run = time.time()
    s.run_and_wait()
    check("both sides arrive as previews", "Left 2048×2048 preview" in s.info() and "Right 2048×2048 preview" in s.info(), s.info())

    box = s.viewer_box()
    cx, cy, w, h = box["x"], box["y"], box["width"], box["height"]
    # ---- slide: left of the pointer is A, right is B (inverted) ----
    # the image is square in a wider canvas: it spans ~10%..90% of the width
    pg.mouse.move(cx + w * 0.5, cy + h * 0.5); time.sleep(0.2)
    img = s.shot("e2e_slide")
    H, W = img.shape[:2]
    a_px = img[int(H * 0.8), int(W * 0.25)]
    b_px = img[int(H * 0.8), int(W * 0.75)]
    check("slide: left of the split is the left image (grass is green)", a_px[1] > a_px[0] + 40 and a_px[1] > a_px[2] + 40, a_px)
    check("slide: right of the split is the right image (inverted: grass is magenta)", b_px[1] < b_px[0] - 40 and b_px[1] < b_px[2] - 40, b_px)
    pg.mouse.move(cx + w * 0.2, cy + h * 0.5); time.sleep(0.3)
    b_px2 = s.shot("e2e_slide2")[int(H * 0.8), int(W * 0.30)]
    check("slide: moving the pointer to 20% moves the split (B now under x=30%)", b_px2[1] < b_px2[0] - 40, b_px2)

    # ---- flip: B is exactly the inverse of A at the same place ----
    pg.mouse.move(cx + w * 0.99, cy + h * 0.5); time.sleep(0.3)      # split past the right edge: all A
    a_flip = s.shot("e2e_flipA")[int(H * 0.8), int(W * 0.5)]
    pg.click("text=Flip"); time.sleep(0.2)
    pg.mouse.click(cx + w * 0.5, cy + h * 0.5); time.sleep(0.3)
    b_flip = s.shot("e2e_flipB")[int(H * 0.8), int(W * 0.5)]
    check("flip: click shows the right image (the inverse of the left, within JPEG error)", np.abs((255 - a_flip) - b_flip).max() < 14, (a_flip, b_flip))
    pg.mouse.click(cx + w * 0.5, cy + h * 0.5); time.sleep(0.3)
    a_again = s.shot("e2e_flipA2")[int(H * 0.8), int(W * 0.5)]
    check("flip: second click shows the left again", np.abs(a_again - a_flip).max() < 6, (a_flip, a_again))

    # ---- diff pulls the exact pixels for both sides ----
    t_diff = time.time()
    pg.click("text=Diff"); time.sleep(0.2)
    pg.wait_for_function("() => /Left 2048×2048 exact.*Right 2048×2048 exact/.test(document.querySelector('.fp-info')?.textContent || '')", timeout=20000)
    full = [u for u in s.fp_requests(t_diff) if "lod=2" in u]
    check("diff: fetched lod=2 for left and right once each", len(full) == 2 and any("s=l" in u for u in full) and any("s=r" in u for u in full), full)
    time.sleep(0.4)
    d = s.shot("e2e_diff")
    check("diff: left vs inverted-left is bright", d[int(H*0.3):int(H*0.7), int(W*0.3):int(W*0.7)].mean() > 40, d.mean())

    # ---- zoom past the preview triggers exact pixels; plain wheel does not zoom the image ----
    pg.click("text=Slide"); time.sleep(0.2)
    scale0 = pg.evaluate("() => window.app.canvas.ds.scale")
    pg.mouse.move(cx + w * 0.5, cy + h * 0.5)
    pg.keyboard.down("Control"); 
    for _ in range(6): pg.mouse.wheel(0, -120); time.sleep(0.03)
    pg.keyboard.up("Control"); time.sleep(0.5)
    scale1 = pg.evaluate("() => window.app.canvas.ds.scale")
    check("ctrl+wheel zooms the image, not the graph", abs(scale1 - scale0) < 1e-6, (scale0, scale1))
    zoomed = s.shot("e2e_zoom")
    check("zoomed render differs from fit render", np.abs(zoomed - img).mean() > 5)
    pg.mouse.move(cx + w * 0.5, cy + h * 0.5)
    for _ in range(3): pg.mouse.wheel(0, 100); time.sleep(0.05)
    time.sleep(0.4)
    scale2 = pg.evaluate("() => window.app.canvas.ds.scale")
    check("plain wheel over the viewer still zooms the graph", abs(scale2 - scale1) > 1e-3, (scale1, scale2))

    # ---- right-click menu and Save Image (lossless, identical to ComfyUI's own PNG) ----
    pg.evaluate("() => { const n = window.app.graph.getNodeById(%s); window.app.canvas.ds.scale = 1; window.app.canvas.ds.offset = [-n.pos[0] + 80, -n.pos[1] + 80]; window.app.canvas.setDirty(true, true); }" % ids["prev"])
    time.sleep(0.8)
    box = s.viewer_box(); cx, cy, w, h = box["x"], box["y"], box["width"], box["height"]
    pg.mouse.move(cx + w * 0.5, cy + h * 0.5); time.sleep(0.2)
    pg.click("text=Fit"); time.sleep(0.2)
    pg.mouse.move(cx + w * 0.1, cy + h * 0.5); time.sleep(0.2)
    pg.mouse.click(cx + w * 0.1, cy + h * 0.5, button="right"); time.sleep(0.5)
    items = pg.evaluate("() => [...document.querySelectorAll('.litemenu-entry, .litecontextmenu .litemenu-entry')].map(e => e.textContent.trim())")
    print("INFO menu:", items[:10])
    check("menu offers Save Left and Save Right (and Copy/Open for each)", all(i in items for i in ("Save Left", "Save Right", "Copy Left", "Copy Right", "Open Left", "Open Right")), items)
    with pg.expect_download(timeout=20000) as dl:
        pg.locator(".litemenu-entry", has_text="Save Left").first.click()
    path = dl.value.path()
    ours = np.asarray(Image.open(path).convert("RGB"))
    pid = s.prompt_id
    outs = s.history()["outputs"]
    stock_files = outs[str(ids["stock"])]["images"]
    stock = np.asarray(Image.open(io.BytesIO(api_bytes(view_url(stock_files[0]))) ).convert("RGB"))
    check("Save Left == ComfyUI's own PNG, pixel for pixel", ours.shape == stock.shape and (ours == stock).all(), (ours.shape, stock.shape, int(np.abs(ours.astype(int)-stock.astype(int)).max()) if ours.shape == stock.shape else None))
    print("INFO download filename:", dl.value.suggested_filename)

    # ---- feed: the history entry has real image files, and the metadata is invisible to it ----
    fp_out = outs[str(ids["prev"])]
    check("feed: images are temp files", len(fp_out["images"]) == 2 and all(i["type"] == "temp" for i in fp_out["images"]), fp_out)
    jpg = api_bytes(view_url(fp_out["images"][0]))
    im = Image.open(io.BytesIO(jpg)); check("feed: file is the 1280px preview JPEG", im.format == "JPEG" and max(im.size) == 1280, (im.format, im.size))
    check("feed: metadata key is a one-element list of strings", isinstance(fp_out["fast_preview"], list) and isinstance(fp_out["fast_preview"][0], str))
    jobs = api_json("/api/jobs?limit=5")
    j = jobs["jobs"][0] if "jobs" in jobs else jobs
    print("INFO job summary:", {k: j.get(k) for k in ("status", "outputs_count", "preview_output")} if isinstance(j, dict) else j)

    errs = [l for l in s.logs if ("[error]" in l or "pageerror" in l) and "404" not in l and "huggingface" not in l and "Content Security" not in l]
    check("no console errors from the page", not errs, errs)
    s.close()
print("ALL PASS" if ok else "SOME FAILED")
