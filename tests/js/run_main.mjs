// main.js をモックの Minecraft 上で動かし、結果を JSON で標準出力へ書く (tests/test_main_js.py から実行)。
// setup → start → 再生終了まで tick → list → リモコン (コンパス) を開く、の順に操作する。
import { board, fireScriptEvent, forms, messages, sounds, tick, useItem } from "@minecraft/server";

await import("./main.js");
const { VIDEOS } = await import("./videos.js");
const video = Object.values(VIDEOS)[0];

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

// スクリーンは足元の高さ、x はプレイヤーから東へ、z はプレイヤーの北側 (z - height) から
const z0 = 100 - video.height;
const finalBoard = [];
for (let y = 0; y < video.height; y++) {
  for (let x = 0; x < video.width; x++) finalBoard.push(board.get(`${x},${z0 + y}`) ?? null);
}

process.stdout.write(JSON.stringify({ ticks, finished: finished(), frameCount: video.frame_count, fps: video.fps, messages, forms, sounds, finalBoard }));
