"""The preview socket, and the one HTTP endpoint behind it.

  GET /fastpreview/ws?clientId=...   frames pushed as results are produced
  GET /fastpreview/img?t=&s=&i=&lod= one image of a remembered result (s is l or r)

Both are mirrored under /api by ComfyUI, like every route registered here.
"""

import asyncio
import ipaddress
import json
import logging

from aiohttp import web
from server import PromptServer

from . import session, wire
from .state import hub, pool, store

log = logging.getLogger(__name__)

routes = PromptServer.instance.routes
hub.bind(PromptServer.instance.loop)

# Tokens are random and never reused, so what they name never changes.
IMMUTABLE = {"Cache-Control": "private, max-age=31536000, immutable"}
_FORWARDED = ("X-Forwarded-For", "Forwarded", "X-Real-IP")

_inflight = {}   # (token, side, idx, level) -> Future: a burst of requests encodes once


@routes.get("/fastpreview/ws")
async def preview_socket(request):
    client_id = request.rel_url.query.get("clientId", "")
    # compress=False: these are JPEGs. Deflating them burns CPU for nothing.
    ws = web.WebSocketResponse(compress=False, heartbeat=25, max_msg_size=1 << 16)
    await ws.prepare(request)
    if not client_id:
        await ws.close(code=4400, message=b"clientId is required")
        return ws
    channel = hub.attach(client_id, ws)
    channel.push(("hello",), wire.control("hello", v=wire.VERSION))
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    _on_message(channel, msg.data)
                except Exception:    # nothing a client sends may end its own session
                    log.debug("fast preview: ignoring a bad message", exc_info=True)
            elif msg.type == web.WSMsgType.ERROR:
                log.debug("fast preview: socket error: %s", ws.exception())
    finally:
        hub.detach(client_id, channel)
    return ws


def _on_message(channel, text):
    try:
        msg = json.loads(text)
        op, token = msg["op"], msg.get("t")
    except (ValueError, KeyError, TypeError):
        return
    if op == "want" and isinstance(token, str):
        # The browser saw a result (a cached prompt, a reload) it holds no
        # frames for. Say it again if we still remember it.
        entry = store.get(token)
        if entry is None:
            channel.push(("gone", token), wire.control("gone", t=token))
            return
        for key, item, supersede in session.replay(entry):
            channel.push(key, item, node=entry.node, token=entry.token)


def _is_loopback(request):
    if any(h in request.headers for h in _FORWARDED):
        return False    # a reverse proxy on this machine does not make the client local
    try:
        return ipaddress.ip_address(request.remote or "127.0.0.1").is_loopback
    except ValueError:
        return False


async def _png(entry, side, idx, level):
    key = (entry.token, side, idx, level)
    data = store.png_get(key)
    if data is not None:
        return data
    fut = _inflight.get(key)
    if fut is None:
        fut = asyncio.get_running_loop().run_in_executor(pool, entry.png, side, idx, level)
        _inflight[key] = fut

        def done(f):
            _inflight.pop(key, None)
            if not f.cancelled() and f.exception() is None and f.result() is not None:
                store.png_put(key, f.result())
        fut.add_done_callback(done)
    # shield: one tab giving up must not cancel the encode another is waiting on
    return await asyncio.shield(fut)


@routes.get("/fastpreview/img")
async def image(request):
    q = request.rel_url.query
    entry = store.get(q.get("t", ""))
    if entry is None:
        return web.Response(status=404, text="this result is no longer remembered")
    try:
        side = {"l": 0, "r": 1}[q.get("s", "l")]
        idx = int(q.get("i", "0"))
        lod = int(q.get("lod", str(wire.LOD_FULL)))
    except (KeyError, ValueError):
        return web.Response(status=400, text="bad s, i or lod")
    if side >= len(entry.sides) or not 0 <= idx < entry.sides[side]["count"]:
        return web.Response(status=404, text="no such image")

    if lod in (wire.LOD_THUMB, wire.LOD_PREVIEW):
        enc = (entry.thumbs if lod == wire.LOD_THUMB else entry.previews).get((side, idx))
        if enc is None:
            return web.Response(status=404, text="not encoded yet")
        return web.Response(body=enc.data, content_type=wire.CODEC_MIME[enc.codec], headers=IMMUTABLE)

    download = q.get("dl") == "1"
    # Zooming wants it now, and a loopback link does not care about 13 MB:
    # store the pixels (level 0). A download is a one-off and gets kept: squeeze it.
    level = 3 if download else (0 if _is_loopback(request) else 1)
    try:
        data = await _png(entry, side, idx, level)
    except Exception as exc:
        log.warning("fast preview: PNG encode failed: %s", exc, exc_info=True)
        return web.Response(status=500, text="could not encode the image")
    if data is None:
        return web.Response(status=404, text="the full-resolution pixels were not kept")
    headers = dict(IMMUTABLE)
    if download:
        headers["Content-Disposition"] = 'attachment; filename="fast_preview_%s_%05d.png"' % (("left", "right")[side], idx + 1)
    return web.Response(body=data, content_type="image/png", headers=headers)
