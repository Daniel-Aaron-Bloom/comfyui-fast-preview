// stdin: JSON list of { template, ctx, now?, bytes? }. stdout: expandName() of each.
// filename.js is a browser module with no imports, so load it as text: no package.json "type" needed.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const src = readFileSync(fileURLToPath(new URL("../web/filename.js", import.meta.url)), "utf8");
const { expandName, randomChars } = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

let input = "";
for await (const chunk of process.stdin) input += chunk;
const out = JSON.parse(input).map((c) => {
    const opts = {};
    if (c.now) opts.now = new Date(...c.now);                         // local-time components
    if (c.bytes) opts.bytes = (len) => Uint8Array.from({ length: len }, (_, i) => c.bytes[i % c.bytes.length]);
    if (c.randomChars !== undefined) return randomChars(c.randomChars, opts.bytes);
    return expandName(c.template, c.ctx ?? {}, opts);
});
console.log(JSON.stringify(out));
