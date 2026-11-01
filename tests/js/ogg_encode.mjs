// web/vendor/wasm-media-encoders を node で動かし、web/app.js と同じ設定 (44.1 kHz ステレオ、品質 3、10 秒ごとに
// 別の OGG) で正弦波を OGG にする (tests/test_web_ogg.py から実行)。
// 使い方: node ogg_encode.mjs <vendor フォルダ> <出力フォルダ> <秒数>   区切りごとに chunk_<番号>.ogg を書く
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const [vendor, out, secondsText] = process.argv.slice(2);
const require = createRequire(import.meta.url);
const { createEncoder } = require(path.resolve(vendor, "WasmMediaEncoder.min.js"));
const encoder = await createEncoder("audio/ogg", readFileSync(path.join(vendor, "ogg.wasm")));
const rate = 44100;
const total = Math.round(Number(secondsText) * rate);
const left = new Float32Array(total), right = new Float32Array(total);
for (let i = 0; i < total; i++) {
  left[i] = 0.3 * Math.sin((2 * Math.PI * 440 * i) / rate);
  right[i] = 0.3 * Math.sin((2 * Math.PI * 660 * i) / rate);
}
const step = 10 * rate;
const sizes = [];
for (let start = 0, index = 0; start < total; start += step, index++) {
  encoder.configure({ channels: 2, sampleRate: rate, vbrQuality: 3 });
  const end = Math.min(total, start + step);
  const parts = [];
  for (let p = start; p < end; p += 4096) parts.push(encoder.encode([left.subarray(p, Math.min(end, p + 4096)), right.subarray(p, Math.min(end, p + 4096))]).slice());
  parts.push(encoder.finalize().slice());
  const bytes = Buffer.concat(parts.map((part) => Buffer.from(part)));
  writeFileSync(path.join(out, `chunk_${index}.ogg`), bytes);
  sizes.push(bytes.length);
}
process.stdout.write(JSON.stringify(sizes));
