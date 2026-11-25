"""Frames paint before `executed`; our files show up in the Assets panel; job detail."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *
ok = True
def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if (not cond and str(detail)) else "")); ok &= bool(cond)

with sync_playwright() as p:
    s = Session(p, dsf=1, viewport=(1500, 900)); s.open()
    pg = s.page
    ids = s.build(size=1024, invert=True, jitter=True)
    # when is the first image on screen, relative to the `executed` message?
    pg.evaluate("""() => {
      const api = window.comfyAPI.api.api, id = String(window.app.graph.nodes.find(n => n.type === 'FastPreview').id);
      window.__ev = { executed: null, painted: null };
      api.addEventListener('executed', (e) => { if (String(e.detail.node) === id && window.__ev.executed === null) window.__ev.executed = performance.now(); });
      const poll = () => { if (window.__ev.painted === null && /thumbnail|preview/.test(document.querySelector('.fp-info')?.textContent || '')) window.__ev.painted = performance.now(); if (window.__ev.painted === null) requestAnimationFrame(poll); };
      requestAnimationFrame(poll);
    }""")
    s.run_and_wait()
    ev = pg.evaluate("() => window.__ev")
    check("the first image is painted before the node's `executed` message arrives", ev["painted"] is not None and ev["executed"] is not None and ev["painted"] < ev["executed"], ev)
    print("INFO painted %.1f ms before `executed`" % (ev["executed"] - ev["painted"]))

    # the real feed: open each sidebar tab that lists generated media and look for our files
    found = {}
    for tab in ("assets",):
        try:
            pg.evaluate("(t) => window.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.' + t)", tab)
        except Exception as e:
            print("INFO could not open", tab, str(e)[:80]); continue
        time.sleep(2.0)
        srcs = pg.evaluate("() => [...document.querySelectorAll('img')].map(i => decodeURIComponent(i.src)).filter(s => /fastpreview_/.test(s))")
        found[tab] = srcs
        pg.screenshot(path=f"{S}/shot_feed_{tab}.png")
        pg.evaluate("(t) => window.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.' + t)", tab); time.sleep(0.5)
    print("INFO feed images by panel:", {k: [x.split('filename=')[1][:48] for x in v[:2]] for k, v in found.items()})
    check("our preview files appear in the feed panel", any(found.values()), found)
    s.history()
    detail = api_json(f"/api/jobs/{s.prompt_id}")
    outs = detail.get("outputs", {}).get(str(ids["prev"]), {})
    check("job detail lists our two images and the invisible metadata", len(outs.get("images", [])) == 2 and "fast_preview" in outs, list(outs))
    s.close()
print("ALL PASS" if ok else "SOME FAILED")
