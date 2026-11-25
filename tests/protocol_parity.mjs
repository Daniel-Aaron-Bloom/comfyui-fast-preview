// stdin: JSON list of hex-encoded frames. stdout: what web/protocol.js makes of each.
// protocol.js is a browser module with no imports, so load it as text: no package.json "type" needed.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const src = readFileSync(fileURLToPath(new URL("../web/protocol.js", import.meta.url)), "utf8");
const { parseFrame } = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

let input = "";
for await (const chunk of process.stdin) input += chunk;
const out = JSON.parse(input).map((hex) => {
    const bytes = Uint8Array.from(hex.match(/../g) ?? [], (h) => parseInt(h, 16));
    const frame = parseFrame(bytes.buffer);
    return frame && { kind: frame.kind, meta: frame.meta, payload: Buffer.from(frame.payload).toString("hex") };
});
console.log(JSON.stringify(out));
