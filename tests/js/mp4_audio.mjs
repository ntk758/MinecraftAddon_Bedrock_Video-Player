// web/mp4audio.js の extractAdts を node で動かし、取り出した ADTS をファイルに書く (tests/test_web_audio.py から実行)。
// 使い方: node mp4_audio.mjs <mp4audio.js> <動画> <出力 .aac>   (音声を取り出せなければ "null" と出力する)
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [script, input, output] = process.argv.slice(2);
await import(pathToFileURL(script).href);
const file = new Blob([readFileSync(input)]);
const adts = await globalThis.Mp4Audio.extractAdts(file);
if (adts) writeFileSync(output, adts);
process.stdout.write(adts ? String(adts.length) : "null");
