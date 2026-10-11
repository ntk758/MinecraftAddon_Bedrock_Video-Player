// main.js をモックの Minecraft 上で、手順 (JSON) どおりに操作して結果を標準出力へ書く (tests/test_playback_control.py から実行)。
// 使い方: node drive_main.mjs '<手順の JSON 配列>'
//   {"event": "badapple:setup", "message": "..."}   /scriptevent を送る
//   {"tick": 10}                                      10 tick 進める
//   {"until": "bvp.playback_finished"}                そのメッセージが出るまで tick を進める (最大 20000)
//   {"ui": [{"selection": 0}, {"formValues": [6]}]}   リモコン (コンパス) を使い、開いたフォームへ順に応答する
//   {"failZ": 92} / {"failZ": null}                   z がその値以上のマスへの設置を失敗させる / 元に戻す
//   {"y": 318}                                        プレイヤーの立つ高さを変える
//   {"snap": "名前"}                                  いま選ばれている動画の盤面 (各マスのブロック) を記録する
import {
  board, commands, fireScriptEvent, getAnchor, messages, options, player, stats, tick, uiResponses, useItem,
} from "@minecraft/server";

const steps = JSON.parse(process.argv[2]);
await import("./main.js");
const { VIDEOS } = await import("./videos.js");

const snaps = [];
function snap(label, videoId) {
  const video = VIDEOS[videoId];
  const anchor = getAnchor("badapple:anchor");
  const volume = video.layout === "volume";
  const cells = video.width * video.height * (volume ? video.depth : 1);
  const cellsOut = [];
  for (let p = 0; p < cells; p++) {
    const x = anchor.x + (p % video.width);
    const row = Math.floor(p / video.width);
    const y = volume ? anchor.y + (video.height - 1 - (row % video.height)) : anchor.y;
    const z = volume ? anchor.z + Math.floor(row / video.height) : anchor.z + row;
    cellsOut.push(board.get(`${x},${y},${z}`) ?? null);
  }
  snaps.push({ label, anchor, board: cellsOut });
}

const errors = [];
for (const step of steps) {
  try {
    if (step.event) fireScriptEvent(step.event, step.message ?? "");
    else if (step.tick) for (let i = 0; i < step.tick; i++) tick();
    else if (step.until) {
      for (let n = 0; n < 20000 && !messages.some((m) => JSON.stringify(m).includes(step.until)); n++) tick();
    } else if (step.ui) {
      uiResponses.push(...step.ui);
      useItem("minecraft:compass");
      for (let i = 0; i < 10; i++) await Promise.resolve();
    } else if ("failZ" in step) options.failZ = step.failZ ?? undefined;
    else if ("y" in step) player.location.y = step.y;
    else if (step.snap) snap(step.snap, step.video ?? Object.keys(VIDEOS)[0]);
  } catch (e) {
    errors.push(String(e?.stack ?? e));
  }
}

const allBlocks = [...board.entries()].map(([key, id]) => [...key.split(",").map(Number), id]);
process.stdout.write(JSON.stringify({
  snaps, stats, commands, errors, allBlocks, messages: messages.map((m) => JSON.stringify(m)),
}));
