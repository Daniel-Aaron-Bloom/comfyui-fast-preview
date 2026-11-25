# Fast Preview / Compare

A ComfyUI node that puts an image on screen the moment it exists, and compares two of them.

Connect an image to `left` to preview it; add `right` to compare (slide, flip, or difference).
`left` and `right` are the sides of the slider each one is drawn on. It is an output node with no
outputs; use it where you would use *Preview Image*.

## Install

```
cd ComfyUI/custom_nodes
git clone <this repo> comfyui-fast-preview
```

Restart ComfyUI. The node is **Fast Preview / Compare**, under `image`. There is nothing to
`pip install`: it uses what ComfyUI already needs (torch, Pillow, aiohttp).

## Using it

| Control | Does |
| --- | --- |
| **Slide** | move the pointer: left of the line is Left, right of it is Right |
| **Flip** | click to swap between Left and Right |
| **Diff** | \|Left − Right\|; click the button again to amplify ×4, ×16 |
| **Save Left / Save Right** | download that image's exact pixels as a PNG (one image: just **Save**) |
| **−  +  1:1  Fit** | zoom. Also: ctrl/cmd + wheel (or a trackpad pinch), double-click for 1:1, drag to pan |
| **‹ n/N ›** | step through a batch |
| right-click | **Save Left / Save Right**, **Copy** and **Open** for each (one image: ComfyUI's usual *Open / Copy / Save Image*), and *Show preview timings* |

Plain scrolling still zooms and pans the graph, even over the node.

**Saving gives you the full PNG**, not what is on screen: the exact pixels, byte-for-byte what
*Preview Image* would have written, with the workflow embedded so it can be dragged back in. It is
a download from your browser: nothing is written to the output or temp folders, and nothing
reaches the queue panel.

The node has no size or quality setting: the image is shown as a thumbnail, then as the exact
pixels, always.

| Advanced widget | |
| --- | --- |
| `feed` | what goes in the queue/history panel: `preview` (default; a JPEG of at most 1280 px, quality 85, nearly free), `full` (a lossless PNG with the workflow, like Preview Image; slower), `off`. It does not change what the node shows. |
| `feed_right` | on by default. Turn it off to keep the right image out of the queue/history panel, so only the left one goes there. Does nothing without a `right` image or with `feed` off, and the viewer on the node still shows both. |

Zoom, pan, mode and the split carry over when you run again, so you can see what changed in the
same place. The mode is saved with the workflow; none of it is a widget, so changing it never
re-runs anything.

## Download file names

What **Save Left**, **Save Right** and **Save Image** name the file is a ComfyUI setting:
*Settings > Fast Preview > Downloads > Download file name*. The default is
`fast_preview_%side%_%index:5%`, which gives `fast_preview_left_00001.png`.

| Token | Gives |
| --- | --- |
| `%side%` | `left` or `right` |
| `%index%`, `%index:5%` | position in the batch, from 1; `:5` zero-pads to 5 digits |
| `%random:8%` | 8 random lowercase letters and digits (1 to 64; 6 if you give no number). Fresh on every click, and each `%random%` in a name is different |
| `%date%`, `%date:FMT%` | `yyyy yy MM dd hh mm ss`, in your local time; `%date%` is `yyyy-MM-dd` |
| `%width%`, `%height%` | pixels |
| `%node%`, `%title%` | this node's id and title |

For example `%title%-%side%-%date:yyyyMMdd-hhmmss%-%random:4%` gives
`Preview-right-20261124-091530-k3x9.png`. `.png` is added if you leave it off. Anything that is
not a token is kept, an unknown token is left as written (so a typo shows), and characters a file
name cannot hold (`/ \ : * ? " < > |`) become `_`, so a template cannot reach outside the
download folder. Leave it blank for the default. `%index%` follows the batch item on screen, per
side: one right image shown against a batch of three on the left is `right-01` throughout.

## Why it is fast

Stock *Preview Image* encodes a PNG, writes it to disk, announces it, and the browser then
fetches and decodes the whole file. This node stops doing most of that.

- **A binary websocket of its own** (`/fastpreview/ws`), not ComfyUI's. ComfyUI's goes through one
  sequential send loop with deflate on, so a slow browser stalls everyone and JPEGs get compressed
  for nothing. This one is uncompressed, with a queue per browser, and a queue is where stale
  frames get dropped: starting a new result discards whatever was still unsent for the old one.
- **Two levels, arriving in order.** A 128 px JPEG thumbnail, pushed over the socket the moment the
  node runs, then the exact pixels as a lossless PNG, which the browser fetches as soon as it knows
  about the result. There is nothing in between and nothing to tune: the thumbnail is the fast first
  pixel, the PNG is the image.
- **The thumbnail is cheap to the point of being free.** It comes from a strided slice of the float
  tensor, downscaled in torch on the tensor's own device, so it never waits for the full-size image
  to be converted to 8-bit (under half a millisecond at any size). It is encoded as a baseline JPEG, with no progressive
  scan and no Huffman optimisation, which is what makes JPEG encodes fast (progressive is ~6× slower
  and optimising ~2.7× at 4096 px).
- **The node returns early.** The conversion to 8-bit and the PNG encode happen in a thread pool (PIL
  and torch release the GIL), after the thumbnail is already on its way. Nothing is written to disk
  unless `feed` asks for it.
- **Only what is on screen stays in memory.** The PNG is encoded when asked for, once, and cached on
  the server. In the browser, only the image you are looking at holds its decoded exact pixels
  (64 MB for a 4K image); step to another image in a batch, or run again, and they are let go, and a
  fetch for them that is still on its way is cancelled.
- **Frames beat `executed`.** The first frames arrive about 10 ms before ComfyUI's own `executed`
  message, so the viewer already has the image when the node reports done.
- **Cached and reloaded runs** are answered from a replay (the server keeps the last results in
  memory): the thumbnails are pushed again and the browser fetches the exact pixels again, rather
  than anything being re-encoded.

### Numbers

**First pixels.** Time from the node starting to execute until pixels are on screen, in the browser,
median of 5 fresh executions per row, over loopback (Apple M4 Max, CPU tensors, ComfyUI 0.46.0,
frontend 1.59.10, headless Chromium). Reproduce with `e2e/timing.py`.

| image | stock *Preview Image* | this node, first pixels |
| --- | --- | --- |
| 1024² | ~40 ms | 5–15 ms |
| 2048² | ~75 ms | 5–12 ms |
| 4096² | 210–270 ms | 3–9 ms |

The first pixels do not depend on the image size, because what is sent first is a fixed-size thumbnail
(a few KB). Stock's figure grows with it (PNG encode, a 50 MB transfer at 4K, decode).

**The exact pixels** now take the same road as stock's: a PNG, transferred and decoded, so they cost
about what it does, and grow with the image. Measured on the server only (the node runs, then the
PNG is requested over HTTP the moment the thumbnails are sent, over loopback, level-0 PNG, same
machine), the time until the last byte is in hand:

| image | PNG size | after the node started |
| --- | --- | --- |
| 1024² | 3 MB | 13–19 ms |
| 2048² | 13 MB | 51–64 ms |
| 4096² | 50 MB | 205–215 ms |

The browser's decode comes on top of that, and `e2e/timing.py` reports the whole path as "final
image"; those browser-side numbers have not been re-taken since the preview level was removed.
Loopback understates the cost on a remote machine, where the transfer dominates (a request from
somewhere that is not loopback gets a compressed level-1 PNG, which is smaller but slower to make).
A comparison fetches two PNGs.

## The queue / history panel

The panel only recognises files listed under `images`, so with `feed = preview` the node writes a
small JPEG there as a temp file, and the previews show up in the Assets panel and the job's
outputs like any other. Clicking one in the panel opens that small JPEG, not the full PNG; use
`feed = full` if you want the panel to hold the lossless file. The node also returns a
`fast_preview` entry as a plain string, which the panel ignores (it skips non-file items), and which
tells the page which result the node produced.

## Configuration

| Environment variable | Default | |
| --- | --- | --- |
| `FAST_PREVIEW_CACHE_MB` | 512 | how much the server remembers (thumbnails and full pixels), oldest results first |
| `FAST_PREVIEW_PNG_CACHE_MB` | 192 | encoded full-size PNGs kept for repeat requests |

## Limits worth knowing

- Tested on macOS, CPU tensors, ComfyUI 0.46.0 with frontend 1.59.10, in Chromium, in both the
  classic and the Vue-nodes renderer. The path for tensors on a GPU is written to work and is not
  exercised by the tests; Firefox and Safari have not been tried.
- A node inside a subgraph still works but loses the head start: the viewer can only be matched to
  its node by ComfyUI's own `executed` message, a few milliseconds later.
- The head start also requires the run to have come from the workflow that is open. A run from
  another tab shows up when you return to it, from the same message.
- Ctrl/cmd + wheel is the only wheel gesture the viewer takes, and it takes it over the viewer
  only. ComfyUI's Vue-nodes renderer claims every other wheel event over a node for the graph.
- With the websocket blocked (some proxies), it falls back to HTTP, and then to the feed files.
  It is slower, not broken.

## Development

```
python -m unittest discover -s tests
```

`tests/test_filename.py` runs the file-name templates through `node`, and `tests/test_net.py` the viewer's
bookkeeping (`web/net.js`: what a result holds, and what it fetches and lets go of). `tests/helpers.py` imports the repo as a package against stand-ins for `server` and
`folder_paths`, so the node, the routes (over a real websocket) and the pipeline are tested without
ComfyUI. `tests/test_protocol_parity.py` checks `web/protocol.js` reads exactly what `wire.py`
writes (needs `node`).

`e2e/` drives a real ComfyUI frontend with Playwright (needs a running server with this node,
`pip install playwright && playwright install chromium`, and `COMFYUI_URL` if it is not on port
8188): `behaviour.py` (slide, flip, diff, zoom, right-click Save Left compared pixel for pixel with
*Preview Image*), `scenarios.py` (cached run after reload, socket down, everything down, batches,
Vue nodes), `downloads.py` (Save Left / Save Right and the file name setting), `feed.py`,
`timing.py`. They toggle the server-side `Comfy.VueNodes.Enabled` setting to
test both renderers.

## License

MIT, see [LICENSE](LICENSE).
