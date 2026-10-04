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

/** フレーム先頭のヘッダ (フレーム番号・ラン数) を読み飛ばす。 */
export function skipFrameHeader(bytes, cursor, end) {
  readVarint(bytes, cursor, end);
  readVarint(bytes, cursor, end);
}
