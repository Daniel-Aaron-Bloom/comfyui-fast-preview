// The name of the file Save Left / Save Right download. The template is a ComfyUI
// setting (Settings > Fast Preview); this file only knows how to expand it.
//
//   %side%                 left | right
//   %index%  %index:5%     position in the batch, from 1, zero-padded to 5 digits
//   %width%  %height%      pixels
//   %node%   %title%       this node's id and title
//   %date%   %date:FMT%    yyyy yy MM dd hh mm ss (default yyyy-MM-dd), local time
//   %random:8%             8 random lowercase letters and digits (1 to 64; 6 if no number)
//
// ".png" is added. Anything that is not a token is kept, and an unknown token is
// left as written so a typo is easy to see.

export const SETTING_ID = "FastPreview.DownloadName";
export const DEFAULT_TEMPLATE = "fast_preview_%side%_%index:5%";
export const MAX_RANDOM = 64;
export const SYNTAX_HELP =
    "Name of the file Save Left / Save Right download (.png is added). Tokens: " +
    "%side% (left or right), %index% or %index:5% (position in the batch, zero-padded), " +
    "%width% %height%, %node% %title%, %date% or %date:yyyy-MM-dd-hhmmss%, " +
    "%random:8% (8 random letters and digits). Blank uses the default, " + DEFAULT_TEMPLATE + ".";

const DEFAULT_RANDOM = 6;
const MAX_PAD = 20;
const MAX_LENGTH = 150;
const ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
const TOKEN = /%([A-Za-z]+)(?::([^%]*))?%/g;
const ILLEGAL = /[\\/:*?"<>|\u0000-\u001f]/g;
const pad = (v, n = 2) => String(v).padStart(n, "0");

/** `n` random characters from a-z0-9, every one equally likely (bytes at or over 252 are skipped,
 *  since 252 is the largest multiple of 36 that fits in a byte). `bytes(len)` is replaceable for tests. */
export function randomChars(n, bytes = (len) => crypto.getRandomValues(new Uint8Array(len))) {
    let out = "";
    while (out.length < n) {
        for (const b of bytes(Math.min(n * 2, 4096))) {    // getRandomValues refuses more than 64 KiB at once
            if (b < 252 && out.length < n) out += ALPHABET[b % 36];
        }
    }
    return out;
}

function formatDate(format, d) {
    return format.replace(/yyyy|yy|MM|dd|hh|mm|ss/g, (t) => {
        switch (t) {
            case "yyyy": return pad(d.getFullYear(), 4);
            case "yy": return pad(d.getFullYear() % 100);
            case "MM": return pad(d.getMonth() + 1);
            case "dd": return pad(d.getDate());
            case "hh": return pad(d.getHours());
            case "mm": return pad(d.getMinutes());
            default: return pad(d.getSeconds());
        }
    });
}

const count = (arg) => (/^\d+$/.test(arg ?? "") ? Number(arg) : null);

/** A file name safe on every OS, always ending in .png. */
function finish(text) {
    let name = text.replace(ILLEGAL, "_").replace(/\s+/g, " ").trim();
    name = name.replace(/\.png$/i, "");
    name = name.replace(/^\.+/, "").replace(/[. ]+$/, "");    // no hidden files; Windows drops trailing dots and spaces
    name = name.slice(0, MAX_LENGTH).replace(/[. ]+$/, "");
    return `${name || "fast_preview"}.png`;
}

/**
 * ctx: { side, index (from 1), width, height, node, title }
 * opts: { now: Date, bytes: random source (tests) }
 */
export function expandName(template, ctx, { now = new Date(), bytes } = {}) {
    const text = String(template ?? "").replace(TOKEN, (whole, name, arg) => {
        switch (name.toLowerCase()) {
            case "side": return ctx.side;
            case "index": return String(ctx.index).padStart(Math.min(count(arg) ?? 0, MAX_PAD), "0");
            case "width": return String(ctx.width);
            case "height": return String(ctx.height);
            case "node": return String(ctx.node ?? "");
            case "title": return String(ctx.title ?? "");
            case "date": return formatDate(arg || "yyyy-MM-dd", now);
            case "random": return randomChars(Math.min(Math.max(count(arg) ?? DEFAULT_RANDOM, 1), MAX_RANDOM), bytes);
            default: return whole;
        }
    });
    return finish(text);
}
