"""Image tensors in, encoded bytes out. No ComfyUI imports.

An image reaches the screen in two steps. The first, a thumbnail, comes from a
strided slice of the float tensor and is encoded inline, so it is on screen
before anything else has been computed. The second is the exact pixels as a
PNG, made in the background from the uint8 copy of the full image.

`make_preview` is a third, separate thing: a lossy, size-capped copy that is
only written to disk for the queue/history panel (the node's `feed` option).
"""

import io
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from PIL.PngImagePlugin import PngInfo

THUMB_EDGE = 128
THUMB_QUALITY = 60

# The file written for the queue/history panel when `feed` is "preview".
FEED_EDGE = 1280
FEED_QUALITY = 85

# Fastest JPEG encode: baseline scan, standard Huffman tables. Progressive is
# ~6x slower and optimize ~2.7x at 4096 px. Pillow's defaults already are
# these; they are spelled out so that stays true.
_JPEG_FAST = {"progressive": False, "optimize": False}


@dataclass(frozen=True)
class Encoded:
    data: bytes
    codec: str   # "jpeg" | "webp" | "png"
    w: int
    h: int


def fit(w, h, edge):
    """(w, h) scaled down so the longer side is at most `edge`. Never upscales."""
    longest = max(w, h)
    if longest <= edge:
        return w, h
    scale = edge / longest
    return max(1, round(w * scale)), max(1, round(h * scale))


def as_image_batch(t):
    """A ComfyUI IMAGE as [B, H, W, C] with C of 3 or 4."""
    if not isinstance(t, torch.Tensor) or t.ndim != 4:
        raise ValueError("expected an IMAGE tensor shaped [batch, height, width, channels]")
    c = t.shape[-1]
    if c == 1:        # grayscale
        t = t.expand(-1, -1, -1, 3)
    elif c == 2:      # grayscale + alpha
        t = torch.cat([t[..., :1].expand(-1, -1, -1, 3), t[..., 1:]], dim=-1)
    elif c > 4:
        t = t[..., :3]
    return t


def to_u8(x):
    """Float [0, 1] to uint8 the way SaveImage does it (scale, clip, truncate),
    so a PNG saved from here matches a saved one pixel for pixel."""
    return x.float().mul(255).clamp_(0, 255).to(torch.uint8)


def _resized(x, w, h):
    """[H, W, C] float -> [h, w, C] float, area-averaged (antialiased)."""
    xb = x.permute(2, 0, 1).unsqueeze(0)
    if xb.dtype != torch.float32:
        xb = xb.float()
    try:
        y = F.interpolate(xb, size=(h, w), mode="bilinear", antialias=True, align_corners=False)
    except (RuntimeError, NotImplementedError):
        # a backend without antialiased resize: plain area averaging
        y = F.interpolate(xb, size=(h, w), mode="area")
    return y[0].permute(1, 2, 0)


def _small_u8(x, w, h, decimate=1):
    if decimate > 1:
        x = x[::decimate, ::decimate]
    if (x.shape[1], x.shape[0]) != (w, h):
        x = _resized(x, w, h)
    return np.ascontiguousarray(to_u8(x).cpu().numpy())


def _encode_lossy(u8, quality):
    h, w, c = u8.shape
    buf = io.BytesIO()
    if c == 4:
        # JPEG has no alpha; WebP at its fastest setting does
        Image.fromarray(u8).save(buf, "WEBP", quality=quality, method=0)
        return Encoded(buf.getvalue(), "webp", w, h)
    Image.fromarray(u8).save(buf, "JPEG", quality=quality, **_JPEG_FAST)
    return Encoded(buf.getvalue(), "jpeg", w, h)


def make_thumb(x, edge=THUMB_EDGE, quality=THUMB_QUALITY):
    """x: one image [H, W, C], any device. Cheap enough to run inline."""
    H, W = x.shape[:2]
    w, h = fit(W, H, edge)
    # decimate first so the antialiased resize only ever sees ~4x the target
    k = max(1, min(H // h, W // w) // 4)
    return _encode_lossy(_small_u8(x, w, h, k), quality)


def make_preview(x, edge=FEED_EDGE, quality=FEED_QUALITY):
    """x: one image [H, W, C], any device. Lossy, at most `edge` on its long side."""
    H, W = x.shape[:2]
    w, h = fit(W, H, edge)
    return _encode_lossy(_small_u8(x, w, h), quality)


def encode_png(u8, level=1, text=None):
    """Lossless PNG of a uint8 [H, W, C] array. `text` becomes PNG text chunks
    (how ComfyUI embeds the workflow so a saved image can be dragged back in)."""
    info = None
    if text:
        info = PngInfo()
        for key, value in text.items():
            info.add_text(key, value)
    buf = io.BytesIO()
    Image.fromarray(u8).save(buf, "PNG", compress_level=level, pnginfo=info)
    return buf.getvalue()
