// 立体のタイル辞書の「書いたタイルだけを比べる」(codec.js の TileDictionary.dirty) の検査。結果を JSON で標準出力へ書く。
// 使い方: node tests/js/tile_dirty.mjs [乱数の種の数 (既定 40)]
//   marks: markRect / markRange が、重なるタイルにだけ (多くも少なくもなく) 印を付けたか。食い違った呼び出しの数
//   streams: 乱数で作った立体のフレーム列 (動きのブリック・タイル参照・ラン。ランは行をまたぐものを含む) を decodeFrameInto で
//     復元し、フレームごとに次の 2 つを、仕様から直接書いた素直な実装 (バイト列を読まず、全タイルを比べる) と照らす。
//     食い違った (種, フレーム) の一覧と、検査したフレーム・辞書への登録・タイル参照・動きのブリック・複数の行にまたがるランの数
//       - 復元した盤面
//       - タイル辞書の中身と順番 (1 つずれると以後のフレームがすべて壊れる)
import {
  FORMAT_V5,
  createTileDictionary,
  decodeFrameInto,
  streamSizeOf,
} from "../../codec.js";

const seedCount = Number(process.argv[2] ?? 40);

function makeRandom(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const integer = (random, low, high) => low + Math.floor(random() * (high - low + 1)); // low 以上 high 以下

// --- markRect / markRange ---
function checkMarks(random) {
  let bad = 0;
  let calls = 0;
  for (let k = 0; k < 400; k++) {
    const tile = [4, 8, 16][integer(random, 0, 2)];
    const video = {
      format: FORMAT_V5, layout: "volume", width: integer(random, 1, 45), height: integer(random, 1, 14), depth: integer(random, 1, 9),
      tile_size: tile, tile_dict_max: 100, air_level: 0, mv_block: 8, mv_range: 2,
    };
    const tiles = createTileDictionary(video);
    const rows = video.height * video.depth;
    const expected = new Uint8Array(tiles.tilesX * tiles.tilesY);
    const markCell = (r, c) => {
      if (r < tiles.tilesY * tile && c < tiles.tilesX * tile) expected[Math.floor(r / tile) * tiles.tilesX + Math.floor(c / tile)] = 1;
    };
    for (let n = 0; n < 6; n++) {
      expected.fill(0);
      tiles.dirty.fill(0);
      if (n % 2 === 0) {
        const r0 = integer(random, 0, rows);
        const r1 = integer(random, r0, rows + 3);
        const c0 = integer(random, 0, video.width);
        const c1 = integer(random, c0, video.width);
        tiles.markRect(r0, r1, c0, c1);
        for (let r = r0; r < r1; r++) for (let c = c0; c < c1; c++) markCell(r, c);
      } else {
        const start = integer(random, 0, rows * video.width);
        const end = integer(random, start, Math.min(rows * video.width + 5, start + (n === 5 ? 1000 : 3 * video.width)));
        tiles.markRange(start, end);
        for (let i = start; i < end; i++) markCell(Math.floor(i / video.width), i % video.width);
      }
      calls++;
      if (expected.some((v, i) => v !== tiles.dirty[i])) bad++;
    }
  }
  return { calls, bad };
}

// --- 乱数のフレーム列 ---
function varint(value, bytes) {
  while (value >= 0x80) {
    bytes.push((value % 128) | 0x80);
    value = Math.floor(value / 128);
  }
  bytes.push(value);
}

/** フレームの内容 (frame) を v5 のバイト列にする (mvcodec/encode.py の encode_frame_v5 と同じ形)。 */
function serialize(frame, frameNumber) {
  const bytes = [];
  const flags = (frame.tileOps.length > 0 ? 4 : 0) | (frame.bricks.length > 0 ? 2 : 0) | (frame.isKeyframe ? 1 : 0);
  varint(frameNumber * 8 + flags, bytes);
  if (frame.bricks.length > 0) {
    varint(frame.bricks.length, bytes);
    let last = -1;
    for (const { brick, packed } of frame.bricks) {
      varint(brick - last - 1, bytes);
      varint(packed, bytes);
      last = brick;
    }
  }
  if (frame.tileOps.length > 0) {
    varint(frame.tileOps.length, bytes);
    let last = -1;
    for (const { tile, recency } of frame.tileOps) {
      varint(tile - last - 1, bytes);
      varint(recency, bytes);
      last = tile;
    }
  }
  varint(frame.runs.length, bytes);
  let previousEnd = 0;
  for (const { start, length, level } of frame.runs) {
    varint((start - previousEnd) * 8192 + (length - 1) * 128 + level, bytes);
    previousEnd = start + length;
  }
  return Uint8Array.from(bytes);
}

/** タイル辞書の正解: 毎回すべてのタイルを比べる。 */
class ReferenceTiles {
  constructor(video) {
    this.video = video;
    this.tile = video.tile_size;
    this.tilesX = Math.floor(video.width / this.tile);
    this.tilesY = Math.floor((video.height * video.depth) / this.tile);
    this.entries = [];
  }

  update(prev, next, isKeyframe) {
    const s = this.tile;
    const width = this.video.width;
    if (isKeyframe) this.entries.length = 0;
    for (let ty = 0; ty < this.tilesY; ty++) {
      for (let tx = 0; tx < this.tilesX; tx++) {
        const content = [];
        let changed = isKeyframe;
        for (let y = 0; y < s; y++) {
          for (let x = 0; x < s; x++) {
            const p = (ty * s + y) * width + tx * s + x;
            content.push(next[p]);
            if (prev[p] !== next[p]) changed = true;
          }
        }
        if (changed && this.entries.length < this.video.tile_dict_max) this.entries.push(content);
      }
    }
  }

  sameAs(entries) {
    return entries.length === this.entries.length
      && entries.every((entry, i) => entry.every((value, k) => value === this.entries[i][k]));
  }
}

/** 仕様 (mvcodec/decode.py の decode_frame) どおりに、フレームの内容から盤面を作る。 */
function referenceDecode(frame, board, reference, video) {
  const { width, height, depth } = video;
  const out = frame.isKeyframe ? new Int16Array(board.length).fill(video.air_level) : board.slice();
  const clamp = (v, high) => Math.min(Math.max(v, 0), high);
  const range = video.mv_range;
  const span = 2 * range + 1;
  const block = video.mv_block;
  const bricksX = Math.ceil(width / block);
  const bricksY = Math.ceil(height / block);
  for (const { brick, packed } of frame.bricks) {
    const dx = (packed % span) - range;
    const dy = (Math.floor(packed / span) % span) - range;
    const dz = Math.floor(packed / (span * span)) - range;
    const bx = brick % bricksX;
    const by = Math.floor(brick / bricksX) % bricksY;
    const bz = Math.floor(brick / (bricksX * bricksY));
    for (let z = bz * block; z < Math.min(bz * block + block, depth); z++) {
      for (let y = by * block; y < Math.min(by * block + block, height); y++) {
        for (let x = bx * block; x < Math.min(bx * block + block, width); x++) {
          out[(z * height + y) * width + x] = board[(clamp(z + dz, depth - 1) * height + clamp(y + dy, height - 1)) * width + clamp(x + dx, width - 1)];
        }
      }
    }
  }
  const s = reference.tile;
  for (const { tile, recency } of frame.tileOps) {
    const content = reference.entries[reference.entries.length - 1 - recency];
    const ty = Math.floor(tile / reference.tilesX);
    const tx = tile % reference.tilesX;
    for (let y = 0; y < s; y++) for (let x = 0; x < s; x++) out[(ty * s + y) * width + tx * s + x] = content[y * s + x];
  }
  for (const { start, length, level } of frame.runs) for (let i = start; i < start + length; i++) out[i] = level;
  return out;
}

/** 乱数で 1 フレーム分の内容を作る。delta のときは動き・タイル参照・ランを組み合わせる。 */
function randomFrame(random, video, reference, isKeyframe, mode) {
  const cells = video.width * video.height * video.depth;
  const frame = { isKeyframe, bricks: [], tileOps: [], runs: [] };
  const levels = integer(random, 2, 12);
  if (!isKeyframe) {
    if (mode.motion) {
      const span = 2 * video.mv_range + 1;
      const bricksX = Math.ceil(video.width / video.mv_block);
      const bricksY = Math.ceil(video.height / video.mv_block);
      const bricksZ = Math.ceil(video.depth / video.mv_block);
      const total = bricksX * bricksY * bricksZ;
      const chance = random();
      for (let brick = 0; brick < total; brick++) {
        if (random() < chance) frame.bricks.push({ brick, packed: integer(random, 0, span * span * span - 1) });
      }
    }
    if (mode.tiles && reference.entries.length > 0) {
      const total = reference.tilesX * reference.tilesY;
      const chance = random() * 0.5;
      for (let tile = 0; tile < total; tile++) {
        if (random() < chance) frame.tileOps.push({ tile, recency: integer(random, 0, reference.entries.length - 1) });
      }
    }
  }
  if (mode.runs) {
    // ランは行をまたぐものも含める (mvcodec のエンコーダは行の先頭で区切るが、フォーマットは禁じていない)
    let position = 0;
    const count = integer(random, 0, Math.min(40, Math.ceil(cells / 8)));
    for (let k = 0; k < count; k++) {
      const gap = integer(random, 0, Math.max(0, Math.floor((2 * cells) / Math.max(1, count))));
      const length = random() < 0.3 ? integer(random, 1, 64) : integer(random, 1, 6);
      const start = position + gap;
      if (start + length > cells) break;
      frame.runs.push({ start, length, level: integer(random, 0, levels) });
      position = start + length;
    }
  }
  return frame;
}

function checkStreams() {
  const result = { frames: 0, additions: 0, tileRefs: 0, bricks: 0, multiRowRuns: 0, boardMismatches: [], dictionaryMismatches: [] };
  for (let seed = 1; seed <= seedCount; seed++) {
    const random = makeRandom(seed * 7919);
    const tile = [4, 8, 16][integer(random, 0, 2)];
    const video = {
      format: FORMAT_V5, layout: "volume", width: integer(random, 4, 44), height: integer(random, 3, 19), depth: integer(random, 2, 19),
      tile_size: tile, tile_dict_max: random() < 0.3 ? integer(random, 3, 60) : 32768, air_level: 0, mv_block: 8, mv_range: integer(random, 1, 2),
    };
    const tiles = createTileDictionary(video);
    const reference = new ReferenceTiles(video);
    const size = streamSizeOf(video);
    let board = new Int16Array(size).fill(-1);
    const out = new Int16Array(size);
    const mode = { motion: random() < 0.7, tiles: random() < 0.7, runs: random() < 0.9 };
    for (let f = 0; f < 14; f++) {
      const isKeyframe = f === 0 || f === 8 || random() < 0.05;
      const frame = randomFrame(random, video, reference, isKeyframe, mode);
      const expected = referenceDecode(frame, board, reference, video);
      reference.update(board, expected, isKeyframe);
      const bytes = serialize(frame, f);
      const before = tiles.entries.length;
      try {
        decodeFrameInto(bytes, 0, bytes.length, board, out, video, tiles);
      } catch (error) {
        // 辞書がずれると、存在しない辞書の番号を貼ろうとして例外になる
        result.boardMismatches.push([seed, f, String(error)]);
        break;
      }
      result.frames++;
      result.additions += Math.max(0, tiles.entries.length - (isKeyframe ? 0 : before));
      result.tileRefs += frame.tileOps.length;
      result.bricks += frame.bricks.length;
      result.multiRowRuns += frame.runs.filter((r) => Math.floor(r.start / video.width) !== Math.floor((r.start + r.length - 1) / video.width)).length;
      if (expected.some((v, i) => v !== out[i])) result.boardMismatches.push([seed, f]);
      if (!reference.sameAs(tiles.entries)) result.dictionaryMismatches.push([seed, f]);
      board = out.slice();
    }
  }
  return result;
}

const marks = checkMarks(makeRandom(12345));
const streams = checkStreams();
process.stdout.write(JSON.stringify({ marks, streams }));
