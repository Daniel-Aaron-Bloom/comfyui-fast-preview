"""What the server remembers about recent results.

Each execution of the node becomes an `Entry`, keyed by a random token. It
holds the thumbnails (so a browser that reloads, or runs a cached prompt, can
be sent them again) and, once converted, the full uint8 pixels (so the browser
can fetch the lossless PNG, and "Save Image" a download of it, without the node
having written one). The whole thing is capped by bytes, oldest first.
"""

import collections
import threading

from . import pipeline

DEFAULT_MAX_BYTES = 512 << 20
DEFAULT_PNG_BYTES = 192 << 20


class Entry:
    """One execution: up to two sides (A, B), each a batch of images."""

    def __init__(self, token, node, sides, png_text=None, meta=None):
        self.token = token
        self.node = node              # ComfyUI's id for the node (a string)
        self.sides = sides            # [{"count", "w", "h", "c"}, ...]
        self.png_text = png_text      # callable -> {chunk: text} | None
        self.meta = meta or {}        # {"wf": workflow id}
        self.thumbs = {}              # (side, idx) -> Encoded
        self.nbytes = 0
        self._pending = {}            # (side, idx) -> float tensor awaiting uint8
        self._u8 = {}                 # (side, idx) -> np.ndarray [H, W, C]
        self._lock = threading.Lock()
        self._store = None

    def _grew(self, n):
        self.nbytes += n
        if self._store is not None:
            self._store._grew(self, n)

    def hold(self, side, idx, tensor):
        """Keep a reference to the float image until it is converted. It is the
        same tensor ComfyUI's own cache holds, so this costs nothing extra."""
        with self._lock:
            self._pending[(side, idx)] = tensor

    def put_thumb(self, side, idx, enc):
        if (side, idx) not in self.thumbs:
            self.thumbs[(side, idx)] = enc
            self._grew(len(enc.data))

    def has_u8(self, side, idx):
        key = (side, idx)
        return key in self._u8 or key in self._pending

    def u8(self, side, idx):
        """Full-resolution pixels as uint8 [H, W, C], converting on first use.
        None if this image was never held (or the entry has been evicted)."""
        key = (side, idx)
        with self._lock:
            arr = self._u8.get(key)
            if arr is not None:
                return arr
            tensor = self._pending.get(key)
            if tensor is None:
                return None
            arr = pipeline.to_u8(tensor).cpu().numpy()
            self._u8[key] = arr
            del self._pending[key]
        self._grew(arr.nbytes)
        return arr

    def convert_all(self):
        for key in sorted(list(self._pending), key=lambda k: (k[1], k[0])):
            self.u8(*key)

    def png(self, side, idx, level):
        arr = self.u8(side, idx)
        if arr is None:
            return None
        text = self.png_text() if self.png_text else None
        return pipeline.encode_png(arr, level=level, text=text)


class Store:
    def __init__(self, max_bytes=DEFAULT_MAX_BYTES, png_bytes=DEFAULT_PNG_BYTES):
        self.max_bytes = max_bytes
        self.png_bytes = png_bytes
        self._entries = collections.OrderedDict()
        self._png = collections.OrderedDict()
        self._png_size = 0
        self._lock = threading.Lock()

    @property
    def nbytes(self):
        with self._lock:
            return sum(e.nbytes for e in self._entries.values())

    def add(self, entry):
        entry._store = self
        with self._lock:
            self._entries[entry.token] = entry
            self._evict()

    def get(self, token):
        with self._lock:
            entry = self._entries.get(token)
            if entry is not None:
                self._entries.move_to_end(token)
            return entry

    def _grew(self, entry, n):
        with self._lock:
            self._evict(keep=entry)

    def _evict(self, keep=None):
        """Oldest first, until under the cap. The newest entry always stays,
        whatever its size: a result that is bigger than the cap is still a
        result someone is looking at."""
        total = sum(e.nbytes for e in self._entries.values())
        for token in list(self._entries):
            if total <= self.max_bytes or len(self._entries) <= 1:
                break
            if self._entries[token] is keep:
                continue
            total -= self._entries.pop(token).nbytes
        # also forget encoded PNGs of entries that are gone
        for key in [k for k in self._png if k[0] not in self._entries]:
            self._png_size -= len(self._png.pop(key))

    def png_get(self, key):
        with self._lock:
            data = self._png.get(key)
            if data is not None:
                self._png.move_to_end(key)
            return data

    def png_put(self, key, data):
        with self._lock:
            if key in self._png:
                return
            self._png[key] = data
            self._png_size += len(data)
            while self._png_size > self.png_bytes and len(self._png) > 1:
                _, old = self._png.popitem(last=False)
                self._png_size -= len(old)
