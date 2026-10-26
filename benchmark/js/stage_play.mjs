// だまし絵を main.js でモックの Minecraft の上で最後まで再生し、1 tick ごとの時間とゲーム内の統計を測る
// (benchmark/stage_bench.py から、main.js・codec.js・videos.js・frames_*.js とモックを置いたフォルダで実行する)。
// 使い方: node --jitless stage_play.mjs '<JSON>'
//   path: 目の動かし方。"still" = 舞台の正面の決まった位置、"walk" = 1 tick に 0.2 ブロックずつ舞台の中を往復
//   frames: 再生するフレーム数の上限 (省略すると全部)、ns: アドオンの接頭辞 (既定 badapple)
// 置くブロックの費用は、ゲーム内で測った値 (tests/js/minecraft_mock の costs、マイクロ秒) をまねて待つ。
// 結果は JSON で標準出力へ書く。最後の盤面は、再生が終わった後に目を正面の決まった位置へ戻し、描き直しが
// 落ち着いてから SHA-1 を取る (歩いていた位置によらず、最後のフレームの絵で比べられる)。
import { createHash } from "node:crypto";
import { board, dimension, fireScriptEvent, getAnchor, messages, options, player, stats, tick } from "@minecraft/server";

// ゲーム内で測った置き方の費用 (マイクロ秒、2026-10-11 の実測)
const COSTS = { setBlock: 2.3, fillCall: 15, fillBlock: 0.55, listCall: 15, listBlock: 1.42 };
const WALK_SPEED = 0.2; // 1 tick あたりの目の動き (ブロック)
const EYE_HEIGHT = 1.62;
const SETTLE_QUIET_TICKS = 3;
const SETTLE_MAX_TICKS = 600;
const MAX_TICKS = 400000;
const STEADY_FROM_TICK = 40; // 最初の描画 (舞台全体を置く) を除いた tick の集計に使う

const args = JSON.parse(process.argv[2] ?? "{}");
const pathKind = args.path ?? "still";
const ns = args.ns ?? "badapple";
options.costs = COSTS;

// 置いた回数と数を数える (費用の見積りに使う。時間は測らない)
const api = { setBlocks: 0, fillCalls: 0, fillBlocks: 0, listCalls: 0, listBlocks: 0 };
{
  const original = dimension.setBlockPermutation;
  dimension.setBlockPermutation = function (location, permutation) {
    api.setBlocks++;
    return original.call(this, location, permutation);
  };
  const descriptor = Object.getOwnPropertyDescriptor(dimension, "fillBlocks");
  Object.defineProperty(dimension, "fillBlocks", {
    get() {
      const fill = descriptor.get.call(dimension);
      if (!fill) return fill;
      return (volume, permutation) => {
        if (volume.locations) {
          api.listCalls++;
          api.listBlocks += volume.locations.length;
        } else {
          api.fillCalls++;
          const { from, to } = volume;
          api.fillBlocks += (Math.abs(to.x - from.x) + 1) * (Math.abs(to.y - from.y) + 1) * (Math.abs(to.z - from.z) + 1);
        }
        return fill(volume, permutation);
      };
    },
    configurable: true,
  });
}

await import("./main.js");
const { VIDEOS, VIDEO_LIST } = await import("./videos.js");
const { stageGeometry } = await import("./codec.js");
const listed = VIDEO_LIST.find((entry) => !entry.hidden);
const video = VIDEOS[listed.id];
const geom = stageGeometry(video);
if (args.frames) {
  // 再生を途中で切る (背景の映像も同じ時間で切る)
  video.frame_count = Math.min(video.frame_count, args.frames);
  const background = video.stage_background ? VIDEOS[video.stage_background] : null;
  if (background) {
    background.frame_count = Math.min(background.frame_count, Math.ceil((video.frame_count * background.fps) / video.fps));
  }
}

fireScriptEvent(`${ns}:setup`);
const anchor = getAnchor(`${ns}:anchor`);
// 舞台の内側の座標の目の位置 → プレイヤーの足元 (main.js の viewerEye の逆)
function placeEye(eye) {
  player.location.x = anchor.x + geom.originX + eye.x;
  player.location.y = anchor.y + eye.y - EYE_HEIGHT;
  player.location.z = anchor.z + eye.z;
}

const H = video.height;
const base = { x: geom.stageWidth / 2, y: 1 + 0.75 * H, z: 1 + geom.floorRows + 0.2 * H };
const centre = { x: geom.stageWidth / 2, z: 1 + geom.gap + video.depth / 2 };
// 人物の中心を回る円弧の上を、一定の速さで往復する (三角波)
const radius = Math.min(base.z - centre.z, geom.stageWidth / 2 - 1);
const half = Math.asin(Math.min(0.95, (geom.stageWidth / 2 - 2) / radius)) * radius;
function eyeAt(t) {
  if (pathKind !== "walk") return base;
  const period = (4 * half) / WALK_SPEED;
  const u = (((t % period) + period) % period) / period;
  const s = u < 0.25 ? 4 * u * half : u < 0.75 ? (2 - 4 * u) * half : (4 * u - 4) * half;
  const angle = s / radius;
  return { x: centre.x + radius * Math.sin(angle), y: base.y, z: centre.z + radius * Math.cos(angle) };
}

placeEye(eyeAt(0));
fireScriptEvent(`${ns}:start`);
const tickMs = [];
let seen = 0;
let finished = false;
while (!finished && tickMs.length < MAX_TICKS) {
  placeEye(eyeAt(tickMs.length));
  const started = performance.now();
  tick();
  tickMs.push(performance.now() - started);
  for (; seen < messages.length; seen++) {
    if (JSON.stringify(messages[seen]).includes("bvp.playback_finished")) finished = true;
  }
}

// 再生の計測 (stats_play・stats_load の数字)
const before = messages.length;
fireScriptEvent(`${ns}:stats`);
const reports = {};
for (const message of messages.slice(before)) {
  const parts = message.rawtext ?? [];
  const body = parts.find((part) => typeof part.translate === "string");
  if (!body) continue;
  reports[body.translate.replace(/^bvp\./, "")] = (body.with?.rawtext ?? []).map((part) => Number(part.text));
}
const [late = 0, maxLate = 0, avgFrameMs = 0, maxFrameMs = 0] = reports.stats_load ?? [];
const [frames = 0, seconds = 0, fps = 0, targetFps = 0, tps = 0, drift = 0] = reports.stats_play ?? [];

// 目を正面へ戻して、描き直しが落ち着くまで進めてから、最後の盤面を取る
placeEye(base);
const placedBefore = () => stats.setBlockCalls + stats.fillCalls;
let quiet = 0;
let settleTicks = 0;
while (quiet < SETTLE_QUIET_TICKS && settleTicks < SETTLE_MAX_TICKS) {
  const calls = placedBefore();
  tick();
  settleTicks++;
  quiet = placedBefore() === calls ? quiet + 1 : 0;
}
const hash = createHash("sha1");
let blocks = 0;
for (const key of [...board.keys()].sort()) {
  const id = board.get(key);
  // 空気は「置いていない」のと同じ (置いたことがあるかどうかで、版の間の比べ方が変わらないように)
  if (id === "minecraft:air") continue;
  hash.update(`${key}=${id};`);
  blocks++;
}

const sum = (values) => values.reduce((s, v) => s + v, 0);
function tickSummary(values) {
  const sorted = [...values].sort((p, q) => p - q);
  const n = sorted.length;
  if (n === 0) return { n: 0, mean: 0, p95: 0, max: 0, sum: 0 };
  return { n, mean: sum(sorted) / n, p95: sorted[Math.min(n - 1, Math.floor(n * 0.95))], max: sorted[n - 1], sum: sum(sorted) };
}
const modeledApiMs = (api.fillCalls * COSTS.fillCall + api.fillBlocks * COSTS.fillBlock + api.setBlocks * COSTS.setBlock
  + api.listCalls * COSTS.listCall + api.listBlocks * COSTS.listBlock) / 1000;
const all = tickSummary(tickMs);
process.stdout.write(JSON.stringify({
  path: pathKind,
  video: { id: listed.id, width: video.width, height: video.height, depth: video.depth, fps: video.fps, frameCount: video.frame_count },
  geom: { stageWidth: geom.stageWidth, wallHeight: geom.wallHeight, floorRows: geom.floorRows, cells: geom.cells },
  finished,
  ticks: tickMs.length,
  tickMs: all,
  tickMsSteady: tickSummary(tickMs.slice(STEADY_FROM_TICK)),
  modeledApiMs,
  jsMs: all.sum - modeledApiMs,
  api,
  stats: { frames, seconds, fps, targetFps, tps, driftSeconds: drift },
  load: { lateFrames: late, maxLateTicks: maxLate, avgFrameMs, maxFrameMs },
  settleTicks,
  board: { sha1: hash.digest("hex"), blocks },
}));
