"""Who is listening, and how frames get to them without anyone waiting.

ComfyUI's own websocket sends every message through one sequential loop, with
permessage-deflate on: a slow browser stalls progress for everyone, and JPEGs
get run through zlib for nothing. This socket is separate, uncompressed, and
each client has its own queue and sender task.

A queue is also where frames go stale. If a browser is slower than the node
is producing, only the newest result for a node is worth sending: starting a
new result drops whatever is still queued for the node's previous one.
"""

import asyncio
import collections
import logging

MAX_QUEUED_BYTES = 64 << 20


class Channel:
    """One connected browser tab."""

    def __init__(self, ws):
        self.ws = ws
        self._queue = collections.OrderedDict()   # key -> (item, node, token)
        self._bytes = 0
        self._wake = asyncio.Event()
        self.closed = False

    def push(self, key, item, node=None, token=None, supersede=False):
        """Queue `item` (str for a text frame, bytes for a binary one). With
        `supersede`, first drop queued frames of `node`'s earlier results."""
        if self.closed:
            return
        if supersede and node is not None:
            for k in [k for k, (_, n, t) in self._queue.items() if n == node and t != token]:
                self._drop(k)
        if key in self._queue:
            self._bytes -= len(self._queue[key][0])   # replaced in place: it keeps its turn
        self._queue[key] = (item, node, token)
        self._bytes += len(item)
        # a stalled client must not grow memory without bound: shed the oldest images
        while self._bytes > MAX_QUEUED_BYTES and len(self._queue) > 1:
            victim = next((k for k, (i, _, _) in self._queue.items() if isinstance(i, bytes)), None)
            if victim is None or victim == key:
                break
            self._drop(victim)
        self._wake.set()

    def _drop(self, key):
        item, _, _ = self._queue.pop(key)
        self._bytes -= len(item)

    async def run(self):
        try:
            while not self.closed:
                await self._wake.wait()
                self._wake.clear()
                while self._queue and not self.closed:
                    _, (item, _, _) = self._queue.popitem(last=False)
                    self._bytes -= len(item)
                    if isinstance(item, str):
                        await self.ws.send_str(item)
                    else:
                        await self.ws.send_bytes(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:   # the peer went away mid-send
            logging.debug("fast preview: send failed: %s", exc)
        finally:
            self.closed = True

    def close(self):
        self.closed = True
        self._wake.set()


class Hub:
    """Maps ComfyUI client ids to their open preview sockets."""

    def __init__(self):
        self.loop = None
        self._channels = {}   # client_id -> {Channel}

    def bind(self, loop):
        self.loop = loop

    # -- event-loop side -------------------------------------------------

    def attach(self, client_id, ws):
        channel = Channel(ws)
        self._channels.setdefault(client_id, set()).add(channel)
        channel.task = asyncio.get_running_loop().create_task(channel.run())
        return channel

    def detach(self, client_id, channel):
        channel.close()
        channel.task.cancel()
        group = self._channels.get(client_id)
        if group is not None:
            group.discard(channel)
            if not group:
                del self._channels[client_id]

    def _deliver(self, client_id, key, item, node, token, supersede):
        for channel in list(self._channels.get(client_id, ())):
            channel.push(key, item, node, token, supersede)

    # -- any thread ------------------------------------------------------

    def has_client(self, client_id):
        return bool(client_id) and bool(self._channels.get(client_id))

    def publish(self, client_id, key, item, node=None, token=None, supersede=False):
        """Queue a frame for every socket this client has open. Safe to call
        from the node's worker thread, from the encoder pool, or the loop."""
        loop = self.loop
        if loop is None or loop.is_closed() or not self.has_client(client_id):
            return
        loop.call_soon_threadsafe(self._deliver, client_id, key, item, node, token, supersede)
