import concurrent.futures
import json
import logging
import os
import secrets
import threading
import time

import torch

import folder_paths
from server import PromptServer

from . import pipeline, session, wire
from .state import hub, pool, store
from .store import Entry

try:
    from comfy.cli_args import args as _comfy_args
except Exception:  # not running inside ComfyUI
    _comfy_args = None

log = logging.getLogger(__name__)

FEED_CHOICES = ["preview", "full", "off"]


class _PngText:
    """The workflow, as PNG text chunks, built once and then forgotten about.

    `prompt` and `extra_pnginfo` can be megabytes; holding the originals in
    every remembered result would be a leak with extra steps. This renders
    them (the same way SaveImage does, so a saved image drags back in as the
    workflow) and lets go.
    """

    def __init__(self, prompt, extra_pnginfo):
        self._src = (prompt, extra_pnginfo)
        self._text = None
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            if self._src is not None:
                prompt, extra = self._src
                self._src = None
                self._text = None
                if _comfy_args is None or not _comfy_args.disable_metadata:
                    text = {}
                    if prompt is not None:
                        text["prompt"] = json.dumps(prompt)
                    for key in (extra or {}):
                        text[key] = json.dumps(extra[key])
                    self._text = text or None
            return self._text


def _workflow_id(extra_pnginfo):
    try:
        return extra_pnginfo["workflow"]["id"]
    except (TypeError, KeyError):
        return None


class FastPreview:
    CATEGORY = "image"
    FUNCTION = "preview"
    OUTPUT_NODE = True
    RETURN_TYPES = ()
    DESCRIPTION = (
        "Shows images the moment they exist, and compares two of them. The "
        "image reaches the browser as a tiny thumbnail over a binary websocket "
        "first, then as the full lossless PNG. Connect one image to preview "
        "it, two to compare."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "left": ("IMAGE", {"tooltip": "The image to preview. In a comparison it is drawn on the left of the slider."}),
            },
            "optional": {
                "right": ("IMAGE", {"tooltip": "A second image to compare against, drawn on the right of the slider. Leave empty to just preview."}),
                "feed": (FEED_CHOICES, {
                    "default": "preview",
                    "advanced": True,
                    "tooltip": "What goes in the queue/history panel. 'preview' writes a small "
                               "JPEG, which costs next to nothing. 'full' writes a lossless "
                               "PNG with the workflow embedded, like Preview Image does (slower). "
                               "'off' writes nothing. The viewer on the node is not affected.",
                }),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID", "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def preview(self, left, right=None, feed="preview",
                unique_id=None, prompt=None, extra_pnginfo=None):
        client_id = PromptServer.instance.client_id
        if client_id is None and feed == "off":
            return {"ui": {}}    # run from the API with nothing to write: nobody to show it to

        started = time.perf_counter()
        if not (isinstance(left, torch.Tensor) and left.ndim == 4 and left.shape[0] > 0):
            return {"ui": {}}    # an empty batch has nothing to show
        batches = [pipeline.as_image_batch(left)]
        if isinstance(right, torch.Tensor) and right.ndim == 4 and right.shape[0] > 0:
            batches.append(pipeline.as_image_batch(right))
        sides = [{"count": b.shape[0], "w": b.shape[2], "h": b.shape[1], "c": b.shape[3]}
                 for b in batches]
        order = [(s, i) for i in range(max(b.shape[0] for b in batches))
                 for s in range(len(batches)) if i < batches[s].shape[0]]

        token = secrets.token_hex(8)
        node_id = None if unique_id is None else str(unique_id)
        png_text = _PngText(prompt, extra_pnginfo)
        entry = Entry(token, node_id, sides, png_text=png_text,
                      meta={"wf": _workflow_id(extra_pnginfo)})
        retained = client_id is not None    # only a browser can ask for the pixels again
        live = retained and hub.has_client(client_id)
        if retained:
            for s, i in order:
                entry.hold(s, i, batches[s][i])
            store.add(entry)

        def publish(key, item, supersede=False):
            if live:
                hub.publish(client_id, key, item, node=node_id, token=token, supersede=supersede)

        def elapsed_ms():
            return round((time.perf_counter() - started) * 1000, 1)

        # 1. announce, and put a thumbnail on screen before anything else is computed
        publish(("begin", token), session.begin(entry), supersede=True)
        for s, i in order:
            try:
                enc = pipeline.make_thumb(batches[s][i])
            except Exception:
                log.warning("fast preview: thumbnail failed", exc_info=True)
                continue
            entry.put_thumb(s, i, enc)
            publish(*session.thumb(entry, s, i, enc))
        publish(("end", token), session.end(entry, ms=elapsed_ms()))

        # 2. the exact pixels are what the browser asks for next (over HTTP, as
        #    soon as it has the `begin`): get them to uint8 now, in the background
        if retained:
            pool.submit(self._remember, entry, png_text)

        ui = {"fast_preview": [json.dumps(
            {"v": 1, "t": token, "n": node_id, "sides": sides, "feed": feed},
            separators=(",", ":"))]}

        # 3. the feed needs real files before the node returns; nothing else does
        if feed != "off":
            ui["images"] = self._write_feed(token, feed, order, batches, entry, png_text, retained)

        log.debug("fast preview %s: node returned after %s ms", token, elapsed_ms())
        return {"ui": ui}

    @staticmethod
    def _remember(entry, png_text):
        """Background: keep the exact pixels (so the browser can get a lossless
        PNG for the viewer, and Save Image can download one) and the workflow text."""
        try:
            text = png_text()
            if text:
                entry._grew(sum(len(v) for v in text.values()))
            entry.convert_all()
        except Exception:
            log.warning("fast preview: could not retain full-resolution pixels", exc_info=True)

    @staticmethod
    def _write_feed(token, feed, order, batches, entry, png_text, retained):
        out_dir = folder_paths.get_temp_directory()
        os.makedirs(out_dir, exist_ok=True)

        def write_png(s, i):
            with torch.inference_mode():
                arr = entry.u8(s, i) if retained else None
                if arr is None:
                    arr = pipeline.to_u8(batches[s][i]).cpu().numpy()
                data = pipeline.encode_png(arr, level=1, text=png_text())
            return data, "png"

        def write_preview(s, i):
            with torch.inference_mode():
                enc = pipeline.make_preview(batches[s][i])
            return enc.data, wire.CODEC_EXT[enc.codec]

        job = write_png if feed == "full" else write_preview
        produced = {k: pool.submit(job, *k) for k in order}
        concurrent.futures.wait(produced.values())

        images = []
        for s, i in sorted(order):    # all of left, then all of right: `order` interleaves them
            data, ext = produced[(s, i)].result()    # re-raises if the encode failed
            name = "fastpreview_%s_%s%d.%s" % (token, "lr"[s], i, ext)
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(data)
            images.append({"filename": name, "subfolder": "", "type": "temp"})
        return images


NODE_CLASS_MAPPINGS = {"FastPreview": FastPreview}
NODE_DISPLAY_NAME_MAPPINGS = {"FastPreview": "Fast Preview / Compare"}
