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
  if (utf16Str.startsWith("B:")) {
    return yield* decodeBase64Job(utf16Str, 2, charsPerYield);
  }
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

const BASE64_VALUES = (() => {
  const table = new Uint8Array(128).fill(255);
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  for (let i = 0; i < alphabet.length; i++) table[alphabet.charCodeAt(i)] = i;
  return table;
})();

/**
 * "B:" + base64 の文字列をバイト列に戻す (v6.15 からの書き方。以前の 15bit/文字 は UTF-8 のファイルで 1.6 倍に膨らんでいた)。
 * QuickJS は ASCII の文字列を 1 文字 1 バイトで持つ。charsPerYield 文字ごとに yield する。
 */
export function* decodeBase64Job(text, start = 0, charsPerYield = 4096) {
  let end = text.length;
  while (end > start && text.charCodeAt(end - 1) === 61) end--; // "=" の詰め物
  const count = end - start;
  const bytes = new Uint8Array(Math.floor((count * 6) / 8));
  let out = 0;
  let i = start;
  // 4 文字 → 3 バイト
  const full = start + count - (count % 4);
  while (i < full) {
    const v = (BASE64_VALUES[text.charCodeAt(i)] << 18) | (BASE64_VALUES[text.charCodeAt(i + 1)] << 12)
      | (BASE64_VALUES[text.charCodeAt(i + 2)] << 6) | BASE64_VALUES[text.charCodeAt(i + 3)];
    bytes[out++] = v >>> 16;
    bytes[out++] = (v >>> 8) & 0xff;
    bytes[out++] = v & 0xff;
    i += 4;
    if ((i - start) % charsPerYield === 0) yield;
  }
  const rest = end - i;
  if (rest >= 2) {
    const v = (BASE64_VALUES[text.charCodeAt(i)] << 18) | (BASE64_VALUES[text.charCodeAt(i + 1)] << 12)
      | (rest === 3 ? BASE64_VALUES[text.charCodeAt(i + 2)] << 6 : 0);
    bytes[out++] = v >>> 16;
    if (rest === 3) bytes[out++] = (v >>> 8) & 0xff;
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

export const LAYOUT_HEIGHTFIELD = "heightfield";

/** 2.5D の立体 (色の平面 + 値の平面で送り、expandPlanes で立体に組み立てる。mvcodec/planes.py) か。 */
export function isHeightfield(video) {
  return video.layout === LAYOUT_HEIGHTFIELD;
}

/**
 * 立体 (3D ホログラム) として表示するデータか (2.5D を含む)。
 * 表示する盤面は (奥行き × 高さ) 行 × 幅 列で、空気は air_level 番。
 */
export function isVolume(video) {
  return video.layout === "volume" || isHeightfield(video);
}

/** 符号化された 2D 画像の行数 (立体は奥行き × 高さ、2.5D は色と値の 2 枚で 2 × 高さ)。 */
export function boardRows(video) {
  if (isHeightfield(video)) return 2 * video.height;
  return isVolume(video) ? video.height * video.depth : video.height;
}

/** 復元する 2D 画像のマスの数 (decodeFrameInto の board / out の大きさ)。 */
export function streamSizeOf(video) {
  return video.width * boardRows(video);
}

/**
 * 2.5D の平面 (上 height 行が色、下 height 行が値) を、立体の盤面 volume ((奥行き × 高さ) 行 × 幅 列) に組み立てる。
 * mvcodec/planes.py の planes_to_volume と同じ規則:
 *  - solid_rule "relief": 値は表面の段。埋まる区間は [min(段, 上下左右の段の最小 + 1), 段] (画像の外は自分と同じ段)
 *  - solid_rule "span":   値は埋まる段数 n。区間は [(D-n)>>1, (D-n)>>1 + n - 1]
 * previous (前回組み立てたときの平面) を渡すと、自分か上下左右 (relief のみ) の値・色が変わった列だけを作り直し、
 * previous を planes に更新する。previous が null なら全部の列を作る。作り直した列の数を返す。
 */
export function expandPlanes(planes, previous, volume, video) {
  const { width, height, depth } = video;
  const air = video.air_level;
  const relief = video.solid_rule === "relief";
  const plane = width * height;
  let rebuilt = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const p = y * width + x;
      if (previous) {
        let dirty = planes[p] !== previous[p] || planes[plane + p] !== previous[plane + p];
        if (!dirty && relief) {
          // 隣の段が変わると、この列の側面 (段差を埋める部分) が変わる
          const v = plane + p;
          dirty = (x > 0 && planes[v - 1] !== previous[v - 1])
            || (x + 1 < width && planes[v + 1] !== previous[v + 1])
            || (y > 0 && planes[v - width] !== previous[v - width])
            || (y + 1 < height && planes[v + width] !== previous[v + width]);
        }
        if (!dirty) continue;
      }
      const colour = planes[p];
      const value = planes[plane + p];
      let low;
      let high;
      if (relief) {
        const v = plane + p;
        let neighbourMin = value;
        if (x > 0 && planes[v - 1] < neighbourMin) neighbourMin = planes[v - 1];
        if (x + 1 < width && planes[v + 1] < neighbourMin) neighbourMin = planes[v + 1];
        if (y > 0 && planes[v - width] < neighbourMin) neighbourMin = planes[v - width];
        if (y + 1 < height && planes[v + width] < neighbourMin) neighbourMin = planes[v + width];
        high = value;
        low = Math.min(value, neighbourMin + 1);
      } else {
        low = (depth - value) >> 1;
        high = low + value - 1;
      }
      for (let k = 0; k < depth; k++) {
        volume[(k * height + y) * width + x] = k >= low && k <= high ? colour : air;
      }
      rebuilt++;
    }
  }
  if (previous) previous.set(planes);
  return rebuilt;
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
  return isV5Family(video) && video.tile_size > 0 ? new TileDictionary(video) : null;
}

/** キーフレームの描き始めの状態: 立体は空気、平面スクリーンと 2.5D の平面は未設置 (-1)。 */
export function emptyLevel(video) {
  return video.layout === "volume" ? video.air_level : -1;
}
export const FORMAT_V5 = "varint_rle_v5";

export const FORMAT_V6 = "varint_rle_v6";

/** v5 のフレーム形式か (v6 は GOP ごとの入れ物を開けば v5 と同じ)。 */
export function isV5Family(video) {
  return video.format === FORMAT_V5 || video.format === FORMAT_V6;
}

// --- v6: GOP ごとのエントロピー符号化 (mvcodec/entropy.py と同じ規則) ---
const STREAM_COUNT = 8;
const S_HEADER = 0;
const S_MV_GAP = 1;
const S_MV_VEC = 2;
const S_TILE_GAP = 3;
const S_TILE_REF = 4;
const S_RUN_GAP = 5;
const S_RUN_LEN = 6;
const S_RUN_LEVEL = 7;
// 入れ物を開くとき、出力 (v5 のバイト列) をこのバイト数作るごとに yield する
const OUTPUT_BYTES_PER_YIELD = 32768;

/**
 * 1 個のストリーム (正準ハフマン符号) を記号の Uint8Array に戻す。cursor.pos を次のストリームの先頭へ進める。
 * 符号長の表から「先頭 maxLength ビット → 記号と符号長」の早見表を作り、1 記号ずつ引く。
 */
function* decodeHuffmanStreamJob(buf, cursor, symbolsPerYield) {
  const count = readVarint(buf, cursor);
  const out = new Uint8Array(count);
  if (count === 0) return out;
  const alphabet = readVarint(buf, cursor);
  const lengths = new Uint8Array(alphabet);
  for (let i = 0; i < alphabet; i++) {
    const b = buf[cursor.pos + (i >> 1)];
    lengths[i] = i % 2 === 0 ? b >> 4 : b & 0x0f;
  }
  cursor.pos += (alphabet + 1) >> 1;
  const size = readVarint(buf, cursor);
  const start = cursor.pos;
  const end = start + size;
  cursor.pos = end;

  let maxLength = 0;
  for (let i = 0; i < alphabet; i++) if (lengths[i] > maxLength) maxLength = lengths[i];
  // 早見表: 記号 | 符号長 << 8。正準符号は 符号長の短い順、同じ長さなら記号の小さい順に連番
  const table = new Uint16Array(1 << maxLength);
  let code = 0;
  let previous = 0;
  for (let length = 1; length <= maxLength; length++) {
    for (let symbol = 0; symbol < alphabet; symbol++) {
      if (lengths[symbol] !== length) continue;
      code <<= length - previous;
      previous = length;
      const first = code << (maxLength - length);
      table.fill(symbol | (length << 8), first, first + (1 << (maxLength - length)));
      code++;
    }
  }

  let bitBuffer = 0;
  let bitCount = 0;
  let p = start;
  const mask = (1 << maxLength) - 1;
  // ビットは上位から読む。bitBuffer の下位 bitCount ビットが未使用 (最大 27 ビット)
  const safeEnd = end - 3;
  let i = 0;
  while (i < count) {
    const stop = Math.min(count, i + symbolsPerYield);
    for (; i < stop; i++) {
      if (bitCount < maxLength) {
        if (p < safeEnd) {
          bitBuffer = ((bitBuffer << 16) | (buf[p] << 8) | buf[p + 1]) & 0x7ffffff;
          p += 2;
          bitCount += 16;
        } else {
          while (bitCount < maxLength) {
            bitBuffer = ((bitBuffer << 8) | (p < end ? buf[p] : 0)) & 0xffffff;
            p++;
            bitCount += 8;
          }
        }
      }
      const e = table[(bitBuffer >>> (bitCount - maxLength)) & mask];
      out[i] = e & 0xff;
      bitCount -= e >> 8;
    }
    if (i < count) yield;
  }
  return out;
}

/**
 * v6 の GOP の入れ物を開いて、v5 のバイト列に戻す (mvcodec/entropy.py の unpack_gop と同じ)。
 * system.runJob に渡せるよう、symbolsPerYield 個の記号ごと・64 フレームごとに yield する。
 * QuickJS (ゲーム内のスクリプト) では関数呼び出しが重いので、1 バイトごとの処理は関数にせずに書いている。
 */
export function* unpackGopJob(container, symbolsPerYield = 8192) {
  if (container.length === 0) return container;
  if (container[0] === 0) return container.subarray(1); // 生のまま入れた GOP
  if (container[0] !== 1) throw new Error(`Unknown GOP container mode ${container[0]}`);
  const cursor = { pos: 1 };
  const frameCount = readVarint(container, cursor);
  const total = readVarint(container, cursor);
  const streams = [];
  for (let s = 0; s < STREAM_COUNT; s++) streams.push(yield* decodeHuffmanStreamJob(container, cursor, symbolsPerYield));

  const out = new Uint8Array(total);
  let o = 0;
  const header = streams[S_HEADER];
  const runGap = streams[S_RUN_GAP];
  const runLength = streams[S_RUN_LEN];
  const runLevel = streams[S_RUN_LEVEL];
  let h = 0;
  let g = 0;
  let r = 0;
  const pairPos = [0, 0, 0, 0, 0];
  // 出力 OUTPUT_BYTES_PER_YIELD バイトごとに yield する (フレームの数で区切ると、64 フレーム以下の GOP が
  // 1 回で全部組み立てられ、1 tick の時間の予算を何倍も超えた)
  let nextYield = OUTPUT_BYTES_PER_YIELD;
  for (let f = 0; f < frameCount; f++) {
    // ヘッダ (varint) を書き写しながら値を読む
    let value = 0;
    let scale = 1;
    let b;
    do {
      b = header[h++];
      out[o++] = b;
      value += (b & 0x7f) * scale;
      scale *= 128;
    } while (b >= 0x80);
    const flags = value;
    // 動きベクトル (ビット 1)・タイル (ビット 2): 数 (ヘッダのストリーム) と、間隔・値の組を書き写す
    for (let part = 0; part < 2; part++) {
      if (Math.floor(flags / (part === 0 ? 2 : 4)) % 2 !== 1) continue;
      let count = 0;
      scale = 1;
      do {
        b = header[h++];
        out[o++] = b;
        count += (b & 0x7f) * scale;
        scale *= 128;
      } while (b >= 0x80);
      const gapStream = streams[part === 0 ? S_MV_GAP : S_TILE_GAP];
      const valueStream = streams[part === 0 ? S_MV_VEC : S_TILE_REF];
      const gi = part === 0 ? S_MV_GAP : S_TILE_GAP;
      const vi = part === 0 ? S_MV_VEC : S_TILE_REF;
      let gp = pairPos[gi];
      let vp = pairPos[vi];
      for (let k = 0; k < count; k++) {
        do {
          b = gapStream[gp++];
          out[o++] = b;
        } while (b >= 0x80);
        do {
          b = valueStream[vp++];
          out[o++] = b;
        } while (b >= 0x80);
      }
      pairPos[gi] = gp;
      pairPos[vi] = vp;
    }
    // ラン: 数を書き写し、間隔 (varint)・長さ・色から v5 のランの値を作って varint で書く
    let runs = 0;
    scale = 1;
    do {
      b = header[h++];
      out[o++] = b;
      runs += (b & 0x7f) * scale;
      scale *= 128;
    } while (b >= 0x80);
    for (let k = 0; k < runs; k++) {
      let gap = 0;
      scale = 1;
      do {
        b = runGap[g++];
        gap += (b & 0x7f) * scale;
        scale *= 128;
      } while (b >= 0x80);
      let v = gap * RUN_POSITION_DIVISOR + runLength[r] * 128 + runLevel[r];
      r++;
      if (v < 0x80000000) {
        while (v >= 0x80) {
          out[o++] = (v & 0x7f) | 0x80;
          v >>>= 7;
        }
      } else {
        // 2^31 以上 (とても大きな画面のランの位置) はビット演算が使えないので割り算で
        while (v >= 0x80) {
          out[o++] = (v % 128) | 0x80;
          v = Math.floor(v / 128);
        }
      }
      out[o++] = v;
      if (o >= nextYield) {
        nextYield = o + OUTPUT_BYTES_PER_YIELD;
        yield;
      }
    }
    if (o >= nextYield) {
      nextYield = o + OUTPUT_BYTES_PER_YIELD;
      yield;
    }
  }
  if (o !== total) throw new Error(`GOP container size mismatch: ${o} != ${total}`);
  return out;
}

/** unpackGopJob を最後まで一度に実行する。 */
export function unpackGop(container) {
  const job = unpackGopJob(container, Infinity);
  let result = job.next();
  while (!result.done) result = job.next();
  return result.value;
}

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
  const isV5 = isV5Family(video);
  const isKeyframe = header % 2 === 1;
  const hasMotion = isV5 && Math.floor(header / 2) % 2 === 1;
  const hasTiles = isV5 && Math.floor(header / 4) % 2 === 1;

  if (isKeyframe) out.fill(emptyLevel(video));
  else out.set(board);

  if (hasMotion && video.layout === "volume") {
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
