// main.js をモックの Minecraft 上で、手順 (JSON) どおりに操作して結果を標準出力へ書く (tests/test_playback_control.py から実行)。
// 使い方: node drive_main.mjs '<手順の JSON 配列>'
//   {"event": "badapple:setup", "message": "..."}   /scriptevent を送る
//   {"tick": 10}                                      10 tick 進める
//   {"until": "bvp.playback_finished"}                そのメッセージが出るまで tick を進める (最大 20000)
//   {"settle": 3}                                     ブロックを置く呼び出しが 3 tick 続けて無くなるまで tick を進める
//   {"ui": [{"selection": 0}, {"formValues": [6]}]}   リモコン (コンパス) を使い、開いたフォームへ順に応答する
//   {"failZ": 92} / {"failZ": null}                   z がその値以上のマスへの設置を失敗させる / 元に戻す
//   {"y": 318} / {"x": 5, "z": 100}                   プレイヤーの立つ位置を変える
//   {"snap": "名前"}                                  いま選ばれている動画の盤面 (各マスのブロック) を記録する
//   {"world": "名前"}                                 ワールドに置かれたブロックをすべて記録する (だまし絵の舞台は snap の範囲に収まらない)
import {
  board, commands, fireScriptEvent, getAnchor, messages, options, player, stats, tick as mockTick, uiResponses, useItem,
} from "@minecraft/server";

const steps = JSON.parse(process.argv[2]);
let ticks = 0;
function tick() {
  ticks++;
  mockTick();
}
await import("./main.js");
const { VIDEOS } = await import("./videos.js");

const snaps = [];
function snap(label, videoId) {
  const video = VIDEOS[videoId];
  const anchor = getAnchor("badapple:anchor");
  const volume = video.layout === "volume" || video.layout === "heightfield";
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

const worlds = [];
const errors = [];
for (const step of steps) {
  try {
    if (step.event) fireScriptEvent(step.event, step.message ?? "");
    else if (step.tick) for (let i = 0; i < step.tick; i++) tick();
    else if (step.sleepMs) await new Promise((resolve) => setTimeout(resolve, step.sleepMs));
    else if (step.settle) {
      // 置いた回数が step.settle tick 続けて変わらなくなるまで tick を進める (最大 2000)
      for (let quiet = 0, n = 0; quiet < step.settle && n < 2000; n++) {
        const calls = stats.setBlockCalls + stats.fillCalls;
        tick();
        quiet = stats.setBlockCalls + stats.fillCalls === calls ? quiet + 1 : 0;
      }
    }
    else if (step.until) {
      for (let n = 0; n < 20000 && !messages.some((m) => JSON.stringify(m).includes(step.until)); n++) tick();
    } else if (step.ui) {
      uiResponses.push(...step.ui);
      useItem("minecraft:compass");
      for (let i = 0; i < 10; i++) await Promise.resolve();
    } else if ("failZ" in step) options.failZ = step.failZ ?? undefined;
    else if ("y" in step || "x" in step || "z" in step) Object.assign(player.location, step);
    else if (step.snap) snap(step.snap, step.video ?? Object.keys(VIDEOS)[0]);
    else if (step.world) worlds.push({ label: step.world, blocks: [...board.entries()].map(([key, id]) => [...key.split(",").map(Number), id]) });
  } catch (e) {
    errors.push(String(e?.stack ?? e));
  }
}

const allBlocks = [...board.entries()].map(([key, id]) => [...key.split(",").map(Number), id]);
process.stdout.write(JSON.stringify({
  snaps, worlds, ticks, stats, commands, errors, allBlocks, messages: messages.map((m) => JSON.stringify(m)),
}));
