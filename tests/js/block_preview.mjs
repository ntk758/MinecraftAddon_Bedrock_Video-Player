// web/block_preview.js でプレビューの絵を描き、RGB を標準出力へ書く (tests/test_preview.py が Python の
// render_preview と画素ごとに比べる)。
// 使い方: node tests/js/block_preview.mjs <frames.js> <spec.json> <順番 (カンマ区切りのフレーム番号)>
//   spec.json は mvcodec/preview.py の preview_spec (top / side は base64)。
//   出力: 1 行目に JSON {width, height, count, fps}、続けて指定した順に 1 コマずつ RGB を base64 で 1 行ずつ
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import * as codec from "../../codec.js";

const require = createRequire(import.meta.url);
const BlockPreview = require("../../web/block_preview.js");

const [framesPath, specPath, order] = process.argv.slice(2);
const text = readFileSync(framesPath, "utf8");
const data = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
const spec = JSON.parse(readFileSync(specPath, "utf8"));
spec.top = Buffer.from(spec.top, "base64");
if (spec.side) spec.side = Buffer.from(spec.side, "base64");

const player = BlockPreview.create(codec, spec, data);
const rgba = new Uint8ClampedArray(player.width * player.height * 4);
const lines = [JSON.stringify({ width: player.width, height: player.height, count: player.count, fps: player.fps })];
for (const frame of order.split(",").map(Number)) {
  player.render(frame, rgba);
  const rgb = Buffer.alloc(player.width * player.height * 3);
  for (let p = 0; p < player.width * player.height; p++) {
    rgb[p * 3] = rgba[p * 4];
    rgb[p * 3 + 1] = rgba[p * 4 + 1];
    rgb[p * 3 + 2] = rgba[p * 4 + 2];
  }
  lines.push(rgb.toString("base64"));
}
process.stdout.write(lines.join("\n") + "\n");
