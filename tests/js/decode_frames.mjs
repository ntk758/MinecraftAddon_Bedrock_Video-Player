// codec.js を使って FRAME_DATA を main.js と同じ手順で復元し、結果を JSON で標準出力へ書く。
// 使い方: node tests/js/decode_frames.mjs <frames.js>
//   frames: 各フレーム表示後の盤面 (パレット番号、未設置は -1)
//   staleGopFrames: 別 GOP (= 別パレット) のブロックが残ったまま表示されたフレーム番号
//   seekMismatches: 任意フレームへキーフレームからシークした結果が通常再生と食い違ったフレーム番号
import { readFileSync } from "node:fs";
import {
  decodeUTF16BinaryToBytes,
  findKeyframeAtOrBefore,
  parseFrameIndexV4,
  readVarint,
  skipFrameHeader,
  unpackRun,
} from "../../codec.js";

const text = readFileSync(process.argv[2], "utf8");
const data = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
const { width, height } = data;
const entries = parseFrameIndexV4(decodeUTF16BinaryToBytes(data.index));
const gops = data.chunks.map((chunk) => decodeUTF16BinaryToBytes(chunk));
const cursor = { pos: 0 };
const run = { position: 0, length: 0, level: 0 };

// 盤面には「GOP番号 * 128 + パレット番号」を入れ、パレットの取り違えも検出できるようにする
function applyFrame(board, frameIndex) {
  const entry = entries[frameIndex];
  if (entry.length === 0) return;
  const bytes = gops[entry.gopId];
  const end = entry.offset + entry.length;
  cursor.pos = entry.offset;
  skipFrameHeader(bytes, cursor, end);
  while (cursor.pos < end) {
    unpackRun(readVarint(bytes, cursor, end), run);
    for (let i = 0; i < run.length; i++) {
      board[run.position + i] = entry.gopId * 128 + run.level;
    }
  }
}

const board = new Int32Array(width * height).fill(-1);
const frames = [];
const sequential = [];
for (let f = 0; f < entries.length; f++) {
  applyFrame(board, f);
  sequential.push(Int32Array.from(board));
  frames.push(Array.from(board, (v) => (v < 0 ? -1 : v % 128)));
}

const staleGopFrames = [];
for (let f = 0; f < entries.length; f++) {
  const gopId = entries[f].gopId;
  if (sequential[f].some((v) => v >= 0 && Math.floor(v / 128) !== gopId)) staleGopFrames.push(f);
}

const seekMismatches = [];
for (let target = 0; target < entries.length; target++) {
  const seekBoard = new Int32Array(width * height).fill(-2); // 何が残っていても上書きされるはず
  for (let f = findKeyframeAtOrBefore(entries, target); f <= target; f++) applyFrame(seekBoard, f);
  const expected = sequential[target];
  if (seekBoard.some((v, i) => v !== expected[i])) seekMismatches.push(target);
}

process.stdout.write(JSON.stringify({ frames, keyframes: entries.flatMap((e, i) => (e.isKeyframe ? [i] : [])), staleGopFrames, seekMismatches }));
