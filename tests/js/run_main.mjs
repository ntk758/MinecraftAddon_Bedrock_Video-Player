// main.js をモックの Minecraft 上で動かし、結果を JSON で標準出力へ書く (tests/test_main_js.py から実行)。
// setup → start → 再生終了まで tick → list → リモコン (コンパス) を開く、の順に操作する。
import { board, commands, fireScriptEvent, forms, getAnchor, messages, options, player, sounds, stats, tick, useItem } from "@minecraft/server";

// 引数 "nofill" で fillBlocks が無い環境をまねる
if (process.argv.includes("nofill")) options.fillBlocks = false;
// 引数 "y=<高さ>" でプレイヤーの立つ高さを変える (建築限界での位置調整の確認用)
const yArg = process.argv.find((arg) => arg.startsWith("y="));
if (yArg) player.location.y = Number(yArg.slice(2));

await import("./main.js");
const { VIDEOS } = await import("./videos.js");
const video = Object.values(VIDEOS)[0];

// 引数 "render=<rect|list|block>" で描画方式を切り替えてから再生する
const renderArg = process.argv.find((arg) => arg.startsWith("render="));
if (renderArg) fireScriptEvent("badapple:render", renderArg.slice("render=".length));

fireScriptEvent("badapple:setup");
fireScriptEvent("badapple:start");

let ticks = 0;
const finished = () => messages.some((m) => JSON.stringify(m).includes("bvp.playback_finished"));
while (!finished() && ticks < 20000) {
  tick();
  ticks++;
}

fireScriptEvent("badapple:list");
fireScriptEvent("badapple:play", "no_such_video");
useItem("minecraft:compass");
await Promise.resolve();

// 引数 "bench" で再生後にベンチマークを実行する
let benchMessages = [];
if (process.argv.includes("bench")) {
  const before = messages.length;
  fireScriptEvent("badapple:bench");
  for (let i = 0; i < 2000 && !messages.slice(before).some((m) => JSON.stringify(m).includes("bvp.bench_done")); i++) tick();
  benchMessages = messages.slice(before);
}

// 盤面の各マスに置かれたブロック (main.js の cellToWorld と同じ配置規則で読む)
const anchor = getAnchor("badapple:anchor");
const volume = video.layout === "volume";
const cells = video.width * video.height * (volume ? video.depth : 1);
const finalBoard = [];
for (let p = 0; p < cells; p++) {
  const x = anchor.x + (p % video.width);
  const row = Math.floor(p / video.width);
  const y = volume ? anchor.y + (video.height - 1 - (row % video.height)) : anchor.y;
  const z = volume ? anchor.z + Math.floor(row / video.height) : anchor.z + row;
  finalBoard.push(board.get(`${x},${y},${z}`) ?? null);
}

process.stdout.write(JSON.stringify({ ticks, finished: finished(), frameCount: video.frame_count, fps: video.fps, anchor, stats, commands, benchMessages, messages, forms, sounds, finalBoard }));
