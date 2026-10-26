// だまし絵の描画コスト (投影・描き直し・展開) を、2 つの codec.js で同じプロセスの中で交互に測る (benchmark/stage_bench.py から実行)。
// 使い方: node --jitless stage_micro.mjs <frames.js> <codecA.js> <codecB.js> [--reps 2] [--max-frames 24]
//   frames.js: convert.py の出力 (立体のだまし絵のデータ)。A と B は同じファイルでもよい (別々のモジュールとして読み込む)
// 測るもの (A と B を 1 回ごとに入れ替えて呼ぶので、機械の揺れは両方に同じように乗る):
//   projection: 変化のあるフレーム × 目 3 か所の projectToStage (work.dirty = true) の ms
//   redraw: 目を (+0.3, +0.05, -0.1) 動かして同じ人物を描き直す ms (work.dirty = false)
//   decode: 全フレームの decodeFrameInto + decoded.set の ms (main.js の decodeOnlyJob と同じ手順、タイル辞書あり)
//   diffCells: A と B の盤面 (投影・描き直し) と復元した画素 (decode) が食い違う数。出力が同じはずの変更では 0
// 結果は JSON で標準出力へ書く。
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const WALL = 30000;
const FLOOR = 30001;
const SIDE = 30002;
const MOVE = { x: 0.3, y: 0.05, z: -0.1 };

const positional = [];
const options = { reps: 2, maxFrames: 24 };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--reps") options.reps = Number(argv[++i]);
  else if (argv[i] === "--max-frames") options.maxFrames = Number(argv[++i]);
  else positional.push(argv[i]);
}
const [framesPath, pathA, pathB] = positional;
if (!framesPath || !pathA || !pathB) {
  console.error("usage: node --jitless stage_micro.mjs <frames.js> <codecA.js> <codecB.js> [--reps N] [--max-frames N]");
  process.exit(2);
}

const text = readFileSync(framesPath, "utf8");
const video = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
const load = (path, tag) => import(pathToFileURL(resolve(path)).href + `?side=${tag}`);
const sides = [{ name: "a", codec: await load(pathA, "a") }, { name: "b", codec: await load(pathB, "b") }];
const codecA = sides[0].codec;
if (!codecA.isAnamorphic(video)) {
  console.error("frames.js is not an anamorphic (view: \"anamorphic\") volume");
  process.exit(2);
}

// 2 つの配列が同じか (同じなら C の速さで終わる)。違うときだけ食い違う数を数える
function countDiff(x, y) {
  if (Buffer.compare(Buffer.from(x.buffer, x.byteOffset, x.byteLength), Buffer.from(y.buffer, y.byteOffset, y.byteLength)) === 0) return 0;
  let n = 0;
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) n++;
  return n;
}

function summary(values) {
  const sorted = [...values].sort((p, q) => p - q);
  const n = sorted.length;
  if (n === 0) return { n: 0, mean: 0, median: 0, min: 0, max: 0 };
  return {
    n,
    mean: sorted.reduce((s, v) => s + v, 0) / n,
    median: n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2,
    min: sorted[0],
    max: sorted[n - 1],
  };
}

// --- 復元の準備 (GOP は main.js と同じく、v6 なら入れ物を開いて v5 のバイト列にする。時間には入れない) ---
const entries = codecA.parseFrameIndexV4(codecA.decodeUTF16BinaryToBytes(video.index));
for (const side of sides) {
  const { codec } = side;
  side.gops = video.chunks.map((chunk) => {
    const bytes = codec.decodeUTF16BinaryToBytes(chunk);
    return video.format === codec.FORMAT_V6 ? codec.unpackGop(bytes) : bytes;
  });
}
const heightfield = codecA.isHeightfield(video);
const voxels = video.width * video.height * video.depth;

function newDecoder(side) {
  const { codec } = side;
  const streamPixels = codec.streamSizeOf(video);
  const figure = new Int16Array(voxels).fill(video.air_level);
  return {
    side,
    decoded: new Int16Array(streamPixels).fill(-1),
    figure,
    // 立体はそのまま figure に復元する。2.5D は平面に復元してから組み立てる (main.js と同じ)
    scratch: heightfield ? new Int16Array(streamPixels) : figure,
    expanded: heightfield ? new Int16Array(streamPixels).fill(-32768) : null,
    tiles: codec.createTileDictionary(video),
    ms: 0,
    frames: 0,
  };
}

function decodeFrame(decoder, entry) {
  const { codec, gops } = decoder.side;
  const started = performance.now();
  codec.decodeFrameInto(gops[entry.gopId], entry.offset, entry.offset + entry.length, decoder.decoded, decoder.scratch, video, decoder.tiles);
  decoder.decoded.set(decoder.scratch);
  decoder.ms += performance.now() - started;
  decoder.frames++;
}

function buildFigure(decoder) {
  if (decoder.expanded) decoder.side.codec.expandPlanes(decoder.decoded, decoder.expanded, decoder.figure, video);
}

// --- 準備: 人物が変わったフレーム (空でないもの) を数え、投影を測る分を等間隔に選ぶ ---
const airFigure = new Int16Array(voxels).fill(video.air_level);
const changed = [];
{
  const decoder = newDecoder(sides[0]);
  let previous = airFigure.slice();
  for (let f = 0; f < entries.length; f++) {
    if (entries[f].length === 0) continue;
    decodeFrame(decoder, entries[f]);
    buildFigure(decoder);
    if (countDiff(decoder.figure, previous) > 0 && countDiff(decoder.figure, airFigure) > 0) changed.push(f);
    previous.set(decoder.figure);
  }
}
const picked = [];
for (let k = 0; k < Math.min(options.maxFrames, changed.length); k++) {
  picked.push(changed[Math.floor((k * changed.length) / Math.min(options.maxFrames, changed.length))]);
}
const snapshots = new Map();

// --- 復元 (decode) : フレームごとに A と B の順を入れ替えて測る ---
const decodeMs = [[], []];
let decodeDiff = 0;
for (let rep = 0; rep < options.reps; rep++) {
  const decoders = sides.map(newDecoder);
  for (let f = 0; f < entries.length; f++) {
    if (entries[f].length === 0) continue;
    const order = (rep + f) % 2 === 0 ? [0, 1] : [1, 0];
    for (const k of order) decodeFrame(decoders[k], entries[f]);
    decodeDiff += countDiff(decoders[0].decoded, decoders[1].decoded);
    if (rep === 0 && picked.includes(f)) {
      buildFigure(decoders[0]);
      snapshots.set(f, decoders[0].figure.slice());
    }
  }
  decoders.forEach((decoder, k) => decodeMs[k].push(decoder.ms / Math.max(1, decoder.frames)));
}

// --- 投影と描き直し ---
const geom = codecA.stageGeometry(video);
const H = video.height;
const cx = geom.stageWidth / 2;
const front = 1 + geom.floorRows;
// 座標が整数にならない目: 正面の上、正面の下で遠い所、横
const eyes = [
  { x: cx + 0.137, y: 1 + 0.75 * H + 0.62, z: front + 0.2 * H + 0.31 },
  { x: cx + 0.3, y: 2.62, z: front + 1.0 * H + 0.07 },
  { x: Math.max(2.3, cx - 0.4 * geom.stageWidth), y: 1 + 0.6 * H + 0.62, z: front + 0.3 * H + 0.41 },
];
const rigs = sides.map(({ codec }) => ({
  codec,
  geom: codec.stageGeometry(video),
  work: codec.createStageWork(codec.stageGeometry(video), WALL, FLOOR, SIDE),
  figure: new Int16Array(voxels),
  out: new Int16Array(geom.cells),
}));
const projectionMs = [[], []];
const redrawMs = [[], []];
let projectionDiff = 0;
let redrawDiff = 0;
let drawnMismatch = 0;
let calls = 0;
for (let rep = 0; rep < options.reps; rep++) {
  for (const f of picked) {
    for (const rig of rigs) rig.figure.set(snapshots.get(f));
    for (const eye of eyes) {
      const order = calls % 2 === 0 ? [0, 1] : [1, 0];
      const drawn = [false, false];
      for (const k of order) {
        const rig = rigs[k];
        rig.work.dirty = true;
        const started = performance.now();
        drawn[k] = rig.codec.projectToStage(rig.figure, video, rig.geom, eye, rig.out, rig.work);
        projectionMs[k].push(performance.now() - started);
      }
      if (drawn[0] !== drawn[1]) drawnMismatch++;
      projectionDiff += countDiff(rigs[0].out, rigs[1].out);
      const moved = { x: eye.x + MOVE.x, y: eye.y + MOVE.y, z: eye.z + MOVE.z };
      for (const k of order) {
        const rig = rigs[k];
        const started = performance.now();
        drawn[k] = rig.codec.projectToStage(rig.figure, video, rig.geom, moved, rig.out, rig.work);
        redrawMs[k].push(performance.now() - started);
      }
      if (drawn[0] !== drawn[1]) drawnMismatch++;
      redrawDiff += countDiff(rigs[0].out, rigs[1].out);
      calls++;
    }
  }
}

process.stdout.write(JSON.stringify({
  video: {
    width: video.width, height: video.height, depth: video.depth, layout: video.layout, frames: entries.length,
    fps: video.fps, format: video.format,
  },
  geom: { stageWidth: geom.stageWidth, wallHeight: geom.wallHeight, floorRows: geom.floorRows, cells: geom.cells },
  frames: { total: entries.length, changed: changed.length, sampled: picked.length },
  projection: { a: summary(projectionMs[0]), b: summary(projectionMs[1]), diffCells: projectionDiff, drawnMismatch },
  redraw: { a: summary(redrawMs[0]), b: summary(redrawMs[1]), diffCells: redrawDiff },
  decode: { a: summary(decodeMs[0]), b: summary(decodeMs[1]), diffCells: decodeDiff, reps: options.reps },
}));
