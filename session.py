"""Building the frames one result is sent as. Shared by the node (live) and
the socket's `want` handler (replay), so both say exactly the same thing."""

from . import wire

PREVIEW = "preview"
THUMB = "thumb"
_LOD = {THUMB: wire.LOD_THUMB, PREVIEW: wire.LOD_PREVIEW}


def begin(entry, replay=False):
    return wire.control(
        "begin", t=entry.token, n=entry.node, wf=entry.meta.get("wf"),
        sides=entry.sides, edge=entry.meta.get("edge"), replay=replay,
    )


def end(entry, ms=None):
    return wire.control("end", t=entry.token, ms=ms)


def image(entry, kind, side, idx, enc):
    """(queue key, binary frame) for one encoded image."""
    meta = {"t": entry.token, "s": side, "i": idx, "l": _LOD[kind],
            "c": enc.codec, "w": enc.w, "h": enc.h}
    return ("img", entry.token, side, idx, _LOD[kind]), wire.pack_image(meta, enc.data)


def order(entry):
    """(side, idx) in the order they should be sent: item 0 of every side first,
    so the image the viewer opens on is complete before the rest of a batch."""
    longest = max(s["count"] for s in entry.sides)
    return [(s, i) for i in range(longest) for s in range(len(entry.sides))
            if i < entry.sides[s]["count"]]


def replay(entry):
    """Everything a browser needs to show `entry` again, as (key, item, supersede)."""
    out = [(("begin", entry.token), begin(entry, replay=True), True)]
    for side, idx in order(entry):
        enc = entry.previews.get((side, idx)) or entry.thumbs.get((side, idx))
        if enc is not None:
            kind = PREVIEW if (side, idx) in entry.previews else THUMB
            key, frame = image(entry, kind, side, idx, enc)
            out.append((key, frame, False))
    out.append((("end", entry.token), end(entry), False))
    return out
