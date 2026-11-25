// The binary frame layout of wire.py, read back. See wire.py for the diagram.

export const VERSION = 1;
export const KIND_IMAGE = 1;
export const LOD = { THUMB: 0, FULL: 1 };
export const SIDE_NAMES = ["left", "right"];    // side 0 and side 1: which half of the slider each is drawn on
export const MIME = { jpeg: "image/jpeg", webp: "image/webp", png: "image/png" };

const HEAD = 8;
const decoder = new TextDecoder();

/** { kind, meta, payload } for a binary frame, or null if it is not one of ours. */
export function parseFrame(buffer) {
    if (buffer.byteLength < HEAD) return null;
    const view = new DataView(buffer);
    if (view.getUint8(0) !== 0x46 || view.getUint8(1) !== 0x50 || view.getUint8(2) !== VERSION) return null;
    const metaLength = view.getUint32(4, true);
    if (HEAD + metaLength > buffer.byteLength) return null;
    let meta;
    try {
        meta = JSON.parse(decoder.decode(new Uint8Array(buffer, HEAD, metaLength)));
    } catch {
        return null;
    }
    return { kind: view.getUint8(3), meta, payload: new Uint8Array(buffer, HEAD + metaLength) };
}
