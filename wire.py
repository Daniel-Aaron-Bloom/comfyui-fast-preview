"""Frame format for the preview socket.

Control messages are JSON text frames (`hello`, `begin`, `end`, `gone`).
Images are binary frames:

    offset  size  field
    0       2     magic "FP"
    2       1     version (1)
    3       1     kind (1 = image)
    4       4     meta length N, little-endian
    8       N     meta, UTF-8 JSON
    8+N     ...   payload: the encoded image, byte for byte

The JSON header costs about 100 bytes and microseconds to parse, and lets
fields be added without a version bump. web/protocol.js reads the same layout;
tests/test_protocol_parity.py keeps the two honest.
"""

import json
import struct

MAGIC = b"FP"
VERSION = 1
KIND_IMAGE = 1

_HEAD = struct.Struct("<2sBBI")
HEAD_SIZE = _HEAD.size

# Levels of detail, in the order they arrive and in order of quality.
LOD_THUMB = 0     # ~128 px: pushed over the socket, the first thing on screen
LOD_FULL = 1      # exact pixels, lossless PNG: fetched over HTTP as soon as a result is shown

CODEC_MIME = {"jpeg": "image/jpeg", "webp": "image/webp", "png": "image/png"}
CODEC_EXT = {"jpeg": "jpg", "webp": "webp", "png": "png"}


def pack_image(meta, payload):
    """One binary frame. `payload` is not copied more than once."""
    head = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    return b"".join((_HEAD.pack(MAGIC, VERSION, KIND_IMAGE, len(head)), head, payload))


def unpack(frame):
    """(kind, meta, payload) of a binary frame. Raises ValueError if malformed."""
    frame = memoryview(frame)
    if len(frame) < HEAD_SIZE:
        raise ValueError("frame shorter than its header")
    magic, version, kind, meta_len = _HEAD.unpack_from(frame)
    if magic != MAGIC:
        raise ValueError("bad magic")
    if version != VERSION:
        raise ValueError("unsupported version %d" % version)
    end = HEAD_SIZE + meta_len
    if end > len(frame):
        raise ValueError("meta runs past the end of the frame")
    meta = json.loads(bytes(frame[HEAD_SIZE:end]).decode("utf-8"))
    return kind, meta, bytes(frame[end:])


def control(op, **fields):
    """A JSON text frame."""
    fields["op"] = op
    return json.dumps(fields, separators=(",", ":"))
