/**
 * MVCodec デコーダ (varint_rle_v4)。Minecraft に依存しない純粋関数だけを置き、Node でもテストできるようにする。
 * エンコード側は mvcodec/encode.py / convert.py。
 */

export const UTF16_CHAR_OFFSET = 0x1000;
export const RUN_POSITION_DIVISOR = 8192; // 2^13: [位置 | (長さ-1):6bit | 色:7bit]

/** 15bit/文字 で詰めた文字列をバイト列に戻す (pad 情報は先頭文字)。 */
export function decodeUTF16BinaryToBytes(utf16Str) {
  const job = decodeUTF16BinaryToBytesJob(utf16Str, Infinity);
  let result = job.next();
  while (!result.done) result = job.next();
  return result.value;
}

/**
 * decodeUTF16BinaryToBytes のジェネレータ版。charsPerYield 文字ごとに yield するので
 * system.runJob に渡せば大きな GOP も tick を跨いで少しずつ展開できる。
 */
export function* decodeUTF16BinaryToBytesJob(utf16Str, charsPerYield = 4096) {
  if (!utf16Str) return new Uint8Array(0);
  if (utf16Str.startsWith("K:")) {
    utf16Str = utf16Str.slice(2);
  }
  const len = utf16Str.length;
  if (len === 0) return new Uint8Array(0);

  const padLen = utf16Str.charCodeAt(0) - UTF16_CHAR_OFFSET;
  const bitCount = (len - 1) * 15 - padLen;
  if (bitCount <= 0) return new Uint8Array(0);

  const byteLen = Math.floor(bitCount / 8);
  const bytes = new Uint8Array(byteLen);

  let byteIdx = 0;
  let bitBuffer = 0;
  let bitsInBuffer = 0;

  for (let i = 1; i < len; i++) {
    const val15 = utf16Str.charCodeAt(i) - UTF16_CHAR_OFFSET;
    bitBuffer = ((bitBuffer << 15) | val15) & 0x3fffff; // 保持は最大 22bit で足りる
    bitsInBuffer += 15;

    while (bitsInBuffer >= 8) {
      bitsInBuffer -= 8;
      if (byteIdx < byteLen) {
        bytes[byteIdx++] = (bitBuffer >>> bitsInBuffer) & 0xff;
      }
    }
    if (i % charsPerYield === 0) yield;
  }
  return bytes;
}

/**
 * varint を 1 個読む。cursor.pos を進める (呼び出し側でオブジェクトを使い回して GC を避ける)。
 * ビット演算は 32bit に切り詰められるため、桁の大きい値も扱えるよう乗算で組み立てる。
 */
export function readVarint(bytes, cursor, end = bytes.length) {
  let val = 0;
  let scale = 1;
  while (cursor.pos < end) {
    const b = bytes[cursor.pos++];
    val += (b & 0x7f) * scale;
    if ((b & 0x80) === 0) break;
    scale *= 128;
  }
  return val;
}

/** v4 フレームインデックスを [{gopId, offset, length, isKeyframe}] に展開する。 */
export function parseFrameIndexV4(idxBytes) {
  const entries = [];
  const cursor = { pos: 0 };
  const len = idxBytes.length;
  while (cursor.pos < len) {
    const gopId = readVarint(idxBytes, cursor);
    const val1 = readVarint(idxBytes, cursor);
    const length = readVarint(idxBytes, cursor);
    entries.push({ gopId, offset: Math.floor(val1 / 2), length, isKeyframe: val1 % 2 === 1 });
  }
  return entries;
}

/** v3 フレームインデックス (gopId なし) を展開する。 */
export function parseFrameIndexV3(idxBytes) {
  const entries = [];
  const cursor = { pos: 0 };
  const len = idxBytes.length;
  while (cursor.pos < len) {
    const val1 = readVarint(idxBytes, cursor);
    const length = readVarint(idxBytes, cursor);
    entries.push({ gopId: -1, offset: Math.floor(val1 / 2), length, isKeyframe: val1 % 2 === 1 });
  }
  return entries;
}

/** targetFrame 以前で最も近いキーフレーム番号を返す (見つからなければ 0)。 */
export function findKeyframeAtOrBefore(entries, targetFrame) {
  for (let f = Math.min(targetFrame, entries.length - 1); f >= 0; f--) {
    if (entries[f].isKeyframe) return f;
  }
  return 0;
}

/** varint で読んだランの値を分解する。out オブジェクトは使い回す。 */
export function unpackRun(val, out) {
  out.position = Math.floor(val / RUN_POSITION_DIVISOR);
  const low = val % RUN_POSITION_DIVISOR;
  out.length = (low >>> 7) + 1;
  out.level = low & 0x7f;
  return out;
}

export const FORMAT_V4 = "varint_rle_v4";

/** 立体 (3D ホログラム) のデータか。盤面は (奥行き × 高さ) 行 × 幅 列で、空気は air_level 番。 */
export function isVolume(video) {
  return video.layout === "volume";
}

/** 盤面の行数 (立体は奥行き × 高さ)。 */
export function boardRows(video) {
  return isVolume(video) ? video.height * video.depth : video.height;
}

/**
 * タイル辞書 (mvcodec/tiles.py の TileDictionary と同じ規則)。
 *  - キーフレーム: 辞書を空にして、そのフレームの全タイルを登録
 *  - それ以外: 直前の盤面から内容が変わったタイルを行優先の順に登録
 *  - 端からはみ出すタイルは対象外、tile_dict_max 個に達したら次のキーフレームまで登録しない
 * 参照は「新しい方から何番目か」(0 が最新)。
 */
export class TileDictionary {
  constructor(video) {
    this.width = video.width;
    this.tile = video.tile_size;
    this.tilesX = Math.floor(video.width / this.tile);
    this.tilesY = Math.floor(boardRows(video) / this.tile);
    this.maxEntries = video.tile_dict_max;
    this.entries = [];
  }

  tileOrigin(t) {
    const ty = Math.floor(t / this.tilesX);
    const tx = t % this.tilesX;
    return ty * this.tile * this.width + tx * this.tile;
  }

  add(board, t) {
    if (this.entries.length >= this.maxEntries) return;
    const s = this.tile;
    const origin = this.tileOrigin(t);
    const content = new Int16Array(s * s);
    for (let y = 0; y < s; y++) {
      const row = origin + y * this.width;
      for (let x = 0; x < s; x++) content[y * s + x] = board[row + x];
    }
    this.entries.push(content);
  }

  differs(prev, next, t) {
    const s = this.tile;
    const origin = this.tileOrigin(t);
    for (let y = 0; y < s; y++) {
      const row = origin + y * this.width;
      for (let x = 0; x < s; x++) if (prev[row + x] !== next[row + x]) return true;
    }
    return false;
  }

  /** フレームを復元した後に呼ぶ。prev は直前の盤面。 */
  update(prev, next, isKeyframe) {
    const count = this.tilesX * this.tilesY;
    if (isKeyframe) this.entries.length = 0;
    for (let t = 0; t < count; t++) {
      if (isKeyframe || this.differs(prev, next, t)) this.add(next, t);
    }
  }

  /** out のタイル t に、辞書の新しい方から recency 番目を貼る。 */
  apply(out, t, recency) {
    const content = this.entries[this.entries.length - 1 - recency];
    const s = this.tile;
    const origin = this.tileOrigin(t);
    for (let y = 0; y < s; y++) {
      const row = origin + y * this.width;
      for (let x = 0; x < s; x++) out[row + x] = content[y * s + x];
    }
  }
}

/** データがタイル辞書を使っていれば辞書を作る (使っていなければ null)。 */
export function createTileDictionary(video) {
  return video.format === FORMAT_V5 && video.tile_size > 0 ? new TileDictionary(video) : null;
}

/** キーフレームの描き始めの状態: 立体は空気、平面スクリーンは未設置 (-1)。 */
export function emptyLevel(video) {
  return isVolume(video) ? video.air_level : -1;
}
export const FORMAT_V5 = "varint_rle_v5";

/**
 * 1 フレームを復元し、表示後の盤面 (パレット番号、未設置は -1) を out に書く。キーフレームなら true を返す。
 *
 * board は直前の盤面 (書き換えない)。規則は mvcodec/decode.py の decode_frame と同じ:
 *  - キーフレームは全画素を未設置から描き直す
 *  - v5 の動きベクトルは、ブロック内の各画素を board[clamp(y+dy)][clamp(x+dx)] から予測する
 *    (立体は mv_block³ のブリックごとに 3 成分のベクトル。decodeMotion3d)
 *  - 続けてタイル辞書の参照を貼り、最後に差分ランを上書きする (v5 のランの位置は直前のランの終わりからの距離)
 *  - tiles (createTileDictionary の戻り値) を渡すと、復元後に辞書も更新する
 * out と board は width*height の Int16Array などを使い回す (GC を避ける)。
 */
export function decodeFrameInto(bytes, start, end, board, out, video, tiles = null) {
  const width = video.width;
  const height = boardRows(video);
  const cursor = { pos: start };
  const header = readVarint(bytes, cursor, end);
  const isV5 = video.format === FORMAT_V5;
  const isKeyframe = header % 2 === 1;
  const hasMotion = isV5 && Math.floor(header / 2) % 2 === 1;
  const hasTiles = isV5 && Math.floor(header / 4) % 2 === 1;

  if (isKeyframe) out.fill(emptyLevel(video));
  else out.set(board);

  if (hasMotion && isVolume(video)) {
    decodeMotion3d(bytes, cursor, end, board, out, video);
  } else if (hasMotion) {
    const block = video.mv_block;
    const span = 2 * video.mv_range + 1;
    const mbCols = Math.ceil(width / block);
    const count = readVarint(bytes, cursor, end);
    let mb = -1;
    for (let k = 0; k < count; k++) {
      mb += readVarint(bytes, cursor, end) + 1;
      const packed = readVarint(bytes, cursor, end);
      const dx = (packed % span) - video.mv_range;
      const dy = Math.floor(packed / span) - video.mv_range;
      const x0 = (mb % mbCols) * block;
      const y0 = Math.floor(mb / mbCols) * block;
      const x1 = Math.min(x0 + block, width);
      const y1 = Math.min(y0 + block, height);
      for (let y = y0; y < y1; y++) {
        const sy = Math.min(Math.max(y + dy, 0), height - 1);
        for (let x = x0; x < x1; x++) {
          const sx = Math.min(Math.max(x + dx, 0), width - 1);
          out[y * width + x] = board[sy * width + sx];
        }
      }
    }
  }

  if (hasTiles) {
    const count = readVarint(bytes, cursor, end);
    let tileIndex = -1;
    for (let k = 0; k < count; k++) {
      tileIndex += readVarint(bytes, cursor, end) + 1;
      tiles.apply(out, tileIndex, readVarint(bytes, cursor, end));
    }
  }

  readVarint(bytes, cursor, end); // ラン数
  const run = { position: 0, length: 0, level: 0 };
  let previousEnd = 0;
  while (cursor.pos < end) {
    unpackRun(readVarint(bytes, cursor, end), run);
    // v5 の位置は直前のランの終わりからの距離、v4 は絶対位置
    const start = isV5 ? previousEnd + run.position : run.position;
    out.fill(run.level, start, start + run.length);
    previousEnd = start + run.length;
  }
  if (tiles) tiles.update(board, out, isKeyframe);
  return isKeyframe;
}

/**
 * 立体の動きベクトル (mvcodec/motion.py の apply_motion_3d と同じ規則)。
 * mv_block³ のブリックごとに、board[clamp(z+dz)][clamp(y+dy)][clamp(x+dx)] を out にコピーする。
 * ブリック番号は (bz * ブリックの行数 + by) * ブリックの列数 + bx、
 * ベクトルは ((dz + r) * span + (dy + r)) * span + (dx + r) (r = mv_range)。
 */
function decodeMotion3d(bytes, cursor, end, board, out, video) {
  const { width, height, depth } = video;
  const block = video.mv_block;
  const range = video.mv_range;
  const span = 2 * range + 1;
  const bricksX = Math.ceil(width / block);
  const bricksY = Math.ceil(height / block);
  const count = readVarint(bytes, cursor, end);
  let brick = -1;
  for (let k = 0; k < count; k++) {
    brick += readVarint(bytes, cursor, end) + 1;
    const packed = readVarint(bytes, cursor, end);
    const dx = (packed % span) - range;
    const dy = (Math.floor(packed / span) % span) - range;
    const dz = Math.floor(packed / (span * span)) - range;
    const x0 = (brick % bricksX) * block;
    const y0 = (Math.floor(brick / bricksX) % bricksY) * block;
    const z0 = Math.floor(brick / (bricksX * bricksY)) * block;
    const x1 = Math.min(x0 + block, width);
    const y1 = Math.min(y0 + block, height);
    const z1 = Math.min(z0 + block, depth);
    for (let z = z0; z < z1; z++) {
      const sz = Math.min(Math.max(z + dz, 0), depth - 1);
      for (let y = y0; y < y1; y++) {
        const sy = Math.min(Math.max(y + dy, 0), height - 1);
        const dst = (z * height + y) * width;
        const src = (sz * height + sy) * width;
        for (let x = x0; x < x1; x++) {
          out[dst + x] = board[src + Math.min(Math.max(x + dx, 0), width - 1)];
        }
      }
    }
  }
}

/** フレーム先頭のヘッダ (フレーム番号・ラン数) を読み飛ばす。 */
export function skipFrameHeader(bytes, cursor, end) {
  readVarint(bytes, cursor, end);
  readVarint(bytes, cursor, end);
}
