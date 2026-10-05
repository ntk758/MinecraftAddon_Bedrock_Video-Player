// main.js をモックの Minecraft 上で動かし、結果を JSON で標準出力へ書く (tests/test_main_js.py から実行)。
// setup → start → 再生終了まで tick → list → リモコン (コンパス) を開く、の順に操作する。
import { board, fireScriptEvent, forms, getAnchor, messages, sounds, tick, useItem } from "@minecraft/server";

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

process.stdout.write(JSON.stringify({ ticks, finished: finished(), frameCount: video.frame_count, fps: video.fps, anchor, messages, forms, sounds, finalBoard }));
