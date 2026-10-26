// codec.js を使って FRAME_DATA を main.js と同じ手順で復元し、結果を JSON で標準出力へ書く。
// 使い方: node tests/js/decode_frames.mjs <frames.js>
//   frames: 各フレーム表示後の盤面 (パレット番号、未設置は -1)
//   staleGopFrames: 別 GOP (= 別パレット) のブロックが残ったまま表示されたフレーム番号
//   seekMismatches: 任意フレームへキーフレームからシークした結果が通常再生と食い違ったフレーム番号
//   motionFrames: 動きベクトルを使ったフレーム番号
//   tileFrames: タイル辞書の参照を使ったフレーム番号
//   dictionaryMismatches: 復元のたびに全タイルを比べて作った辞書 (ReferenceTiles) と、codec.js の辞書の中身・順番が
//     食い違ったフレーム番号 (seekDictionaryMismatches は任意フレームへのシークのとき)。
//     辞書が 1 つずれると、それ以後のフレームがすべて壊れる (立体は書いたタイルだけを比べるので、印の付け漏れを見つける)
import { readFileSync } from "node:fs";
import {
  createTileDictionary,
  decodeFrameInto,
  decodeUTF16BinaryToBytes,
  expandPlanes,
  findKeyframeAtOrBefore,
  isHeightfield,
  isVolume,
  parseFrameIndexV4,
  FORMAT_V6,
  readVarint,
  streamSizeOf,
  unpackGop,
} from "../../codec.js";

const text = readFileSync(process.argv[2], "utf8");
const data = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
const { width, height } = data;
const pixels = width * height * (isVolume(data) ? data.depth : 1);
const entries = parseFrameIndexV4(decodeUTF16BinaryToBytes(data.index));
// v6 は GOP ごとの入れ物 (エントロピー符号化) を開いて v5 のバイト列にする (main.js と同じ)
const gops = data.chunks.map((chunk) => {
  const bytes = decodeUTF16BinaryToBytes(chunk);
  return data.format === FORMAT_V6 ? unpackGop(bytes) : bytes;
});

/**
 * タイル辞書の正解。mvcodec/tiles.py の規則を、毎回すべてのタイルを比べるという最も素直な形で書いたもの
 * (codec.js の TileDictionary とは実装を共有しない)。
 */
class ReferenceTiles {
  constructor(video) {
    this.width = video.width;
    this.tile = video.tile_size;
    this.tilesX = Math.floor(video.width / this.tile);
    this.tilesY = Math.floor(streamSizeOf(video) / video.width / this.tile);
    this.maxEntries = video.tile_dict_max;
    this.entries = [];
  }

  update(prev, next, isKeyframe) {
    const s = this.tile;
    if (isKeyframe) this.entries.length = 0;
    for (let ty = 0; ty < this.tilesY; ty++) {
      for (let tx = 0; tx < this.tilesX; tx++) {
        const content = [];
        let changed = isKeyframe;
        for (let y = 0; y < s; y++) {
          for (let x = 0; x < s; x++) {
            const p = (ty * s + y) * this.width + tx * s + x;
            content.push(next[p]);
            if (prev[p] !== next[p]) changed = true;
          }
        }
        if (changed && this.entries.length < this.maxEntries) this.entries.push(content);
      }
    }
  }

  matches(entries) {
    return entries.length === this.entries.length
      && entries.every((entry, i) => entry.every((value, k) => value === this.entries[i][k]));
  }
}

// main.js と同じ手順: 復元した盤面と表示中の盤面を比べ、変わった画素だけ「置く」。
// 置いた画素には、そのフレームの GOP 番号を記録しておき、別パレットのブロックが残っていないか調べる。
function newState() {
  return {
    board: new Int16Array(pixels).fill(-1),
    decoded: new Int16Array(streamSizeOf(data)).fill(-1),
    scratch: new Int16Array(streamSizeOf(data)),
    // 2.5D: 前回組み立てたときの平面 (main.js と同じく、変わった列だけ作り直す)
    expanded: isHeightfield(data) ? new Int16Array(streamSizeOf(data)).fill(-32768) : null,
    boardGop: -1,
    next: new Int16Array(pixels),
    gopOf: new Int32Array(pixels).fill(-1),
    tiles: createTileDictionary(data),
    reference: createTileDictionary(data) ? new ReferenceTiles(data) : null,
    dictionaryMismatches: [],
  };
}

function applyFrame(state, frameIndex) {
  const entry = entries[frameIndex];
  if (entry.length === 0) return;
  const bytes = gops[entry.gopId];
  const isKeyframe = decodeFrameInto(bytes, entry.offset, entry.offset + entry.length, state.decoded, state.scratch, data, state.tiles);
  if (state.reference) {
    state.reference.update(state.decoded, state.scratch, isKeyframe);
    if (!state.reference.matches(state.tiles.entries)) state.dictionaryMismatches.push(frameIndex);
  }
  state.decoded.set(state.scratch);
  if (state.expanded) expandPlanes(state.decoded, state.expanded, state.next, data);
  else state.next.set(state.decoded);
  // main.js の invalidateForPaletteChange と同じ: 自動パレットで GOP が変わったときだけ、平面は全マス・立体は空気以外を置き直す
  if (data.adaptive_palette && entry.gopId !== state.boardGop) {
    for (let p = 0; p < pixels; p++) {
      if (!isVolume(data) || state.board[p] !== data.air_level) state.board[p] = -1;
    }
  }
  state.boardGop = entry.gopId;
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
  if (entry.length === 0 || !data.format.startsWith("varint_rle_v") || data.format === "varint_rle_v4") return false;
  const header = readVarint(gops[entry.gopId], { pos: entry.offset });
  return Math.floor(header / 2) % 2 === 1;
}

const state = newState();
const frames = [];
// GOP ごとにパレットが変わる自動パレットのときだけ意味がある (固定パレットは GOP が変わっても同じブロック)
const staleGopFrames = [];
for (let f = 0; f < entries.length; f++) {
  applyFrame(state, f);
  frames.push(Array.from(state.board));
  const gopId = entries[f].gopId;
  if (data.adaptive_palette && state.gopOf.some((g) => g >= 0 && g !== gopId)) staleGopFrames.push(f);
}

const seekMismatches = [];
const seekDictionaryMismatches = [];
for (let target = 0; target < entries.length; target++) {
  const seekState = newState();
  seekState.board.fill(-2); // シーク前に何が表示されていても、キーフレームから正しく描き直されるはず
  for (let f = findKeyframeAtOrBefore(entries, target); f <= target; f++) applyFrame(seekState, f);
  if (frames[target].some((v, i) => v !== seekState.board[i])) seekMismatches.push(target);
  if (seekState.dictionaryMismatches.length > 0) seekDictionaryMismatches.push(target);
}

const motionFrames = entries.flatMap((_e, i) => (hasMotion(i) ? [i] : []));
const tileFrames = entries.flatMap((entry, i) => {
  if (entry.length === 0 || !data.format.startsWith("varint_rle_v") || data.format === "varint_rle_v4") return [];
  const header = readVarint(gops[entry.gopId], { pos: entry.offset });
  return Math.floor(header / 4) % 2 === 1 ? [i] : [];
});
process.stdout.write(JSON.stringify({
  frames,
  keyframes: entries.flatMap((e, i) => (e.isKeyframe ? [i] : [])),
  staleGopFrames,
  seekMismatches,
  dictionaryMismatches: state.dictionaryMismatches,
  seekDictionaryMismatches,
  motionFrames,
  tileFrames,
}));
