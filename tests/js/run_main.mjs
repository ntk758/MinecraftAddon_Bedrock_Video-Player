// main.js をモックの Minecraft 上で動かし、結果を JSON で標準出力へ書く (tests/test_main_js.py から実行)。
// setup → start → 再生終了まで tick → list → リモコン (コンパス) を開く、の順に操作する。
import { board, commands, fireScriptEvent, forms, getAnchor, messages, options, player, sounds, stats, tick, useItem } from "@minecraft/server";

// 引数 "nofill" で fillBlocks が無い環境をまねる
if (process.argv.includes("nofill")) options.fillBlocks = false;
// 引数 "costs=<JSON>" で、置き方ごとの費用 (マイクロ秒) をまねる (ベンチマークと auto 方式の確認用)
const costsArg = process.argv.find((arg) => arg.startsWith("costs="));
if (costsArg) options.costs = JSON.parse(costsArg.slice("costs=".length));
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
// 再生中のスクリプトの処理時間 (モックの API はほぼ 0 なので、復元・立体の組み立て・差分の走査の時間)
const playStart = performance.now();
// 引数 "holes" で、立体の各列 (幅 × 高さの各マス) に 1 個もブロックが無い瞬間 (穴) を tick ごとに数える。
// 全部の列が一度埋まった後だけ数える (最初の描画の途中は除く)
const checkHoles = process.argv.includes("holes") && (video.layout === "volume" || video.layout === "heightfield");
let holeTicks = 0;
let maxHoles = 0;
let filledOnce = false;
function countEmptyColumns() {
  const anchor = getAnchor("badapple:anchor");
  if (!anchor) return -1;
  let empty = 0;
  for (let y = 0; y < video.height; y++) {
    for (let x = 0; x < video.width; x++) {
      let solid = false;
      for (let z = 0; z < video.depth && !solid; z++) {
        const id = board.get(`${anchor.x + x},${anchor.y + y},${anchor.z + z}`);
        solid = id !== undefined && id !== "minecraft:air";
      }
      if (!solid) empty++;
    }
  }
  return empty;
}
while (!finished() && ticks < 20000) {
  tick();
  ticks++;
  if (checkHoles) {
    const empty = countEmptyColumns();
    if (empty === 0) filledOnce = true;
    else if (filledOnce && empty > 0) {
      holeTicks++;
      maxHoles = Math.max(maxHoles, empty);
    }
  }
}
const playMs = performance.now() - playStart;

// 引数 "stats" で再生後に計測結果を表示させる
let statsMessages = [];
if (process.argv.includes("stats")) {
  const before = messages.length;
  fireScriptEvent("badapple:stats");
  statsMessages = messages.slice(before);
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
const volume = video.layout === "volume" || video.layout === "heightfield";
const cells = video.width * video.height * (volume ? video.depth : 1);
const finalBoard = [];
for (let p = 0; p < cells; p++) {
  const x = anchor.x + (p % video.width);
  const row = Math.floor(p / video.width);
  const y = volume ? anchor.y + (video.height - 1 - (row % video.height)) : anchor.y;
  const z = volume ? anchor.z + Math.floor(row / video.height) : anchor.z + row;
  finalBoard.push(board.get(`${x},${y},${z}`) ?? null);
}

const costModel = getAnchor("badapple:cost_model") ?? null;
process.stdout.write(JSON.stringify({ ticks, playMs, statsMessages, holeTicks, maxHoles, filledOnce, finished: finished(), frameCount: video.frame_count, fps: video.fps, anchor, stats, commands, benchMessages, messages, forms, sounds, finalBoard, costModel }));
