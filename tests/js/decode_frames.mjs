// codec.js を使って FRAME_DATA を main.js と同じ手順で復元し、結果を JSON で標準出力へ書く。
// 使い方: node tests/js/decode_frames.mjs <frames.js>
//   frames: 各フレーム表示後の盤面 (パレット番号、未設置は -1)
//   staleGopFrames: 別 GOP (= 別パレット) のブロックが残ったまま表示されたフレーム番号
//   seekMismatches: 任意フレームへキーフレームからシークした結果が通常再生と食い違ったフレーム番号
//   motionFrames: 動きベクトルを使ったフレーム番号
import { readFileSync } from "node:fs";
import { decodeFrameInto, decodeUTF16BinaryToBytes, findKeyframeAtOrBefore, isVolume, parseFrameIndexV4, readVarint } from "../../codec.js";

const text = readFileSync(process.argv[2], "utf8");
const data = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
const { width, height } = data;
const pixels = width * height * (isVolume(data) ? data.depth : 1);
const entries = parseFrameIndexV4(decodeUTF16BinaryToBytes(data.index));
const gops = data.chunks.map((chunk) => decodeUTF16BinaryToBytes(chunk));

// main.js と同じ手順: 復元した盤面と表示中の盤面を比べ、変わった画素だけ「置く」。
// 置いた画素には、そのフレームの GOP 番号を記録しておき、別パレットのブロックが残っていないか調べる。
function newState() {
  return { board: new Int16Array(pixels).fill(-1), next: new Int16Array(pixels), gopOf: new Int32Array(pixels).fill(-1) };
}

function applyFrame(state, frameIndex) {
  const entry = entries[frameIndex];
  if (entry.length === 0) return;
  const bytes = gops[entry.gopId];
  const isKeyframe = decodeFrameInto(bytes, entry.offset, entry.offset + entry.length, state.board, state.next, data);
  // main.js の prepareKeyframe と同じ: 平面は全マス、立体は空気以外のマスを置き直す
  if (isKeyframe) {
    for (let p = 0; p < pixels; p++) {
      if (!isVolume(data) || state.board[p] !== data.air_level) state.board[p] = -1;
    }
  }
  for (let p = 0; p < pixels; p++) {
    if (state.next[p] !== state.board[p] && state.next[p] >= 0) {
      state.board[p] = state.next[p];
      // 空気はどのパレットでも同じなので、GOP の取り違えの検査から外す
      state.gopOf[p] = isVolume(data) && state.next[p] === data.air_level ? -1 : entry.gopId;
    }
  }
}

function hasMotion(frameIndex) {
  const entry = entries[frameIndex];
  if (entry.length === 0 || data.format !== "varint_rle_v5") return false;
  const header = readVarint(gops[entry.gopId], { pos: entry.offset });
  return Math.floor(header / 2) % 2 === 1;
}

const state = newState();
const frames = [];
const staleGopFrames = [];
for (let f = 0; f < entries.length; f++) {
  applyFrame(state, f);
  frames.push(Array.from(state.board));
  const gopId = entries[f].gopId;
  if (state.gopOf.some((g) => g >= 0 && g !== gopId)) staleGopFrames.push(f);
}

const seekMismatches = [];
for (let target = 0; target < entries.length; target++) {
  const seekState = newState();
  seekState.board.fill(-2); // シーク前に何が表示されていても、キーフレームから正しく描き直されるはず
  for (let f = findKeyframeAtOrBefore(entries, target); f <= target; f++) applyFrame(seekState, f);
  if (frames[target].some((v, i) => v !== seekState.board[i])) seekMismatches.push(target);
}

const motionFrames = entries.flatMap((_e, i) => (hasMotion(i) ? [i] : []));
process.stdout.write(JSON.stringify({
  frames,
  keyframes: entries.flatMap((e, i) => (e.isKeyframe ? [i] : [])),
  staleGopFrames,
  seekMismatches,
  motionFrames,
}));
