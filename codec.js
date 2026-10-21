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
 * 視点に合わせた立体のだまし絵 (view: "anamorphic") で表示するデータか。立体の人物はワールドに置かず、
 * 見ている人の目から人物を見たときの色を、後ろの壁と足元の床に塗る (正しい位置から見ると立体に見える)。
 */
export function isAnamorphic(video) {
  return video.view === "anamorphic" && isVolume(video);
}

/**
 * だまし絵の舞台 (壁と床) の大きさ。座標は舞台の北西の角を原点とするブロック単位 (x は東、y は上、z は南)。
 *  - 床: 上の面が y = 1、z は 1〜1 + floorRows (北の端で壁に接する)
 *  - 壁: 南の面が z = 1、y は 1〜1 + wallHeight
 *  - 人物 (幅 × 高さ × 奥行きの立体): 床の上 (y = 1 から)、左右中央、壁から gap ブロック離れた所に立つ
 * 壁は人物より高くする (人物の上の方を通る視線は、人物より高い所で壁に当たる)。壁と床は左右に人物の幅ずつ広げ、
 * 床は手前にも広げる (斜めから見ると、人物は横へずれた所に写る)。
 * 盤面は壁の行 (0 行目が一番上) の後に床の行 (0 行目が壁際) が続き、どちらも幅 stageWidth 列。
 */
export function stageGeometry(video) {
  const { width, height, depth } = video;
  const side = width;
  const gap = Math.max(4, Math.ceil(depth / 2));
  const front = Math.max(4, depth);
  const stageWidth = width + 2 * side;
  const wallHeight = Math.ceil(height * 1.5);
  const floorRows = gap + depth + front;
  return {
    width, height, depth, side, gap, front, stageWidth, wallHeight, floorRows,
    rows: wallHeight + floorRows,
    cells: stageWidth * (wallHeight + floorRows),
  };
}

/**
 * 盤面の p 番目のマスを、舞台の原点からのブロックの位置にする (壁は z = 0 の列、床は y = 0 の段)。
 */
export function stageCellOffset(geom, p, out) {
  const row = Math.floor(p / geom.stageWidth);
  out.x = p % geom.stageWidth;
  if (row < geom.wallHeight) {
    out.y = geom.wallHeight - row;
    out.z = 0;
  } else {
    out.y = 0;
    out.z = 1 + row - geom.wallHeight;
  }
  return out;
}

/**
 * 立体の人物 figure ((奥行き × 高さ) 行 × 幅 列、空気は video.air_level) を、目の位置 eye (舞台の原点からの
 * 座標) から見たとおりに壁と床へ写し、盤面 out (stageGeometry の cells マス) に書く。人物の当たらないマスは
 * 壁なら wallLevel、床なら floorLevel。
 *
 * 外から見える面を持つブロック (6 方向のどれかが空気か立体の外) ごとに、そのブロックが写る範囲を
 * 8 つの角を壁と床へ投げて求め、範囲の各マスの中心から目への視線がそのブロックを通るかを確かめる。
 * 同じマスに複数のブロックが写るときは、目に近い方を残す (zbuf: 視線の上の位置。0 が目、1 がマス)。
 * 目が壁の前・床の上に無ければ写せないので false を返す (out は変えない)。
 */
export function projectToStage(figure, video, geom, eye, out, zbuf, wallLevel, floorLevel) {
  if (!(eye.z > 1 && eye.y > 1)) return false;
  const { width, height, depth, side, gap, stageWidth, wallHeight, floorRows } = geom;
  const air = video.air_level;
  const wallCells = stageWidth * wallHeight;
  out.fill(wallLevel, 0, wallCells);
  out.fill(floorLevel, wallCells);
  zbuf.fill(2);
  const ex = eye.x;
  const ey = eye.y;
  const ez = eye.z;
  const slice = width * height;
  // 角を投げた先の範囲 (床は x と z、壁は x と y)
  let fx0, fx1, fz0, fz1, wx0, wx1, wy0, wy1;
  for (let d = 0; d < depth; d++) {
    const z0 = 1 + gap + d;
    for (let row = 0; row < height; row++) {
      const y0 = height - row;
      for (let x = 0; x < width; x++) {
        const p = (d * height + row) * width + x;
        const level = figure[p];
        if (level === air || level < 0) continue;
        // 周りがすべて埋まっているブロックは外から見えない
        if (x > 0 && x + 1 < width && row > 0 && row + 1 < height && d > 0 && d + 1 < depth
          && figure[p - 1] !== air && figure[p + 1] !== air && figure[p - width] !== air
          && figure[p + width] !== air && figure[p - slice] !== air && figure[p + slice] !== air) continue;
        const x0 = side + x;
        fx0 = wx0 = Infinity;
        fx1 = wx1 = -Infinity;
        fz0 = wy0 = Infinity;
        fz1 = wy1 = -Infinity;
        let hitsFloor = false;
        let hitsWall = false;
        for (let c = 0; c < 8; c++) {
          const cx = x0 + (c & 1) - ex;
          const cy = y0 + ((c >> 1) & 1) - ey;
          const cz = z0 + ((c >> 2) & 1) - ez;
          // 角を通る視線が床 (y = 1) と壁 (z = 1) に当たるまでの長さ (角までを 1 とする)。当たらなければ Infinity
          const sFloor = cy < 0 ? (1 - ey) / cy : Infinity;
          const sWall = cz < 0 ? (1 - ez) / cz : Infinity;
          if (sFloor <= sWall && sFloor !== Infinity) {
            // 先に床に当たる。壁の範囲には、壁の下の縁 (y = 1) の同じ x を入れる
            const hx = ex + cx * sFloor;
            const hz = ez + cz * sFloor;
            hitsFloor = true;
            if (hx < fx0) fx0 = hx;
            if (hx > fx1) fx1 = hx;
            if (hz < fz0) fz0 = hz;
            if (hz > fz1) fz1 = hz;
            if (hx < wx0) wx0 = hx;
            if (hx > wx1) wx1 = hx;
            if (1 < wy0) wy0 = 1;
          } else if (sWall !== Infinity) {
            const hx = ex + cx * sWall;
            const hy = ey + cy * sWall;
            hitsWall = true;
            if (hx < wx0) wx0 = hx;
            if (hx > wx1) wx1 = hx;
            if (hy < wy0) wy0 = hy;
            if (hy > wy1) wy1 = hy;
            if (hx < fx0) fx0 = hx;
            if (hx > fx1) fx1 = hx;
            if (1 < fz0) fz0 = 1;
          }
        }
        if (hitsFloor) {
          const i0 = Math.max(0, Math.floor(fx0));
          const i1 = Math.min(stageWidth - 1, Math.ceil(fx1) - 1);
          const k0 = Math.max(0, Math.floor(fz0) - 1);
          const k1 = Math.min(floorRows - 1, Math.ceil(fz1) - 2);
          for (let k = k0; k <= k1; k++) {
            const base = wallCells + k * stageWidth;
            for (let i = i0; i <= i1; i++) {
              const t = rayEntersBox(ex, ey, ez, i + 0.5 - ex, 1 - ey, k + 1.5 - ez, x0, y0, z0);
              if (t < zbuf[base + i]) {
                zbuf[base + i] = t;
                out[base + i] = level;
              }
            }
          }
        }
        if (hitsWall) {
          const i0 = Math.max(0, Math.floor(wx0));
          const i1 = Math.min(stageWidth - 1, Math.ceil(wx1) - 1);
          // 壁の r 行目は y = wallHeight - r 〜 wallHeight - r + 1
          const r0 = Math.max(0, wallHeight - Math.ceil(wy1) + 1);
          const r1 = Math.min(wallHeight - 1, wallHeight - Math.floor(wy0));
          for (let r = r0; r <= r1; r++) {
            const base = r * stageWidth;
            const cy = wallHeight - r + 0.5;
            for (let i = i0; i <= i1; i++) {
              const t = rayEntersBox(ex, ey, ez, i + 0.5 - ex, cy - ey, 1 - ez, x0, y0, z0);
              if (t < zbuf[base + i]) {
                zbuf[base + i] = t;
                out[base + i] = level;
              }
            }
          }
        }
      }
    }
  }
  return true;
}

/**
 * 目 (ox, oy, oz) からマスの中心まで (向き dx, dy, dz、長さ 1 まで) の視線が、1 辺 1 の箱 (x0, y0, z0 の角) に
 * 入る位置 (0〜1)。通らなければ Infinity。
 */
function rayEntersBox(ox, oy, oz, dx, dy, dz, x0, y0, z0) {
  let tmin = 0;
  let tmax = 1;
  let a = (x0 - ox) / dx;
  let b = (x0 + 1 - ox) / dx;
  if (a > b) { const s = a; a = b; b = s; }
  if (a > tmin) tmin = a;
  if (b < tmax) tmax = b;
  a = (y0 - oy) / dy;
  b = (y0 + 1 - oy) / dy;
  if (a > b) { const s = a; a = b; b = s; }
  if (a > tmin) tmin = a;
  if (b < tmax) tmax = b;
  a = (z0 - oz) / dz;
  b = (z0 + 1 - oz) / dz;
  if (a > b) { const s = a; a = b; b = s; }
  if (a > tmin) tmin = a;
  if (b < tmax) tmax = b;
  // 向きが 0 の軸は割り算が ±Infinity か NaN になる。NaN は比べると false なので、箱の外なら下で外れる
  return tmin <= tmax && tmin === tmin ? tmin : Infinity;
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
 * progress ({ bytes, produced }) を渡すと、出力の配列と、yield の時点で前から何バイトできているかを書く
 * (できた所までのフレームは、GOP を開き終わる前に再生できる)。
 * QuickJS (ゲーム内のスクリプト) では関数呼び出しが重いので、1 バイトごとの処理は関数にせずに書いている。
 */
export function* unpackGopJob(container, symbolsPerYield = 8192, progress = null) {
  if (container.length === 0) return container;
  if (container[0] === 0) return container.subarray(1); // 生のまま入れた GOP
  if (container[0] === 2) return yield* unpackRangeGopJob(container, progress);
  if (container[0] !== 1) throw new Error(`Unknown GOP container mode ${container[0]}`);
  const cursor = { pos: 1 };
  const frameCount = readVarint(container, cursor);
  const total = readVarint(container, cursor);
  const streams = [];
  for (let s = 0; s < STREAM_COUNT; s++) streams.push(yield* decodeHuffmanStreamJob(container, cursor, symbolsPerYield));

  const out = new Uint8Array(total);
  let o = 0;
  if (progress) progress.bytes = out;
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
        if (progress) progress.produced = o;
        yield;
      }
    }
    if (o >= nextYield) {
      nextYield = o + OUTPUT_BYTES_PER_YIELD;
      if (progress) progress.produced = o;
      yield;
    }
  }
  if (o !== total) throw new Error(`GOP container size mismatch: ${o} != ${total}`);
  return out;
}

// --- Phase 10.2: 入れ物の種類 2 (適応的な算術符号。mvcodec/rangecode.py と同じ規則) ---
const PROB_BITS = 11;
const PROB_ONE = 1 << PROB_BITS;
const RC_TOP = 1 << 24;
// 確率の表 (Uint16Array 1 本) の中の位置: 8 ビットの木 × 5 種類、間隔の区分、区分ごとの残りの上位 2 ビット、長さ、色 (直前の色ごと)
const RC_BYTES = 0;
const RC_SLOT = RC_BYTES + 5 * 256;
const RC_EXTRA = RC_SLOT + 64;
const RC_LENGTH = RC_EXTRA + 64 * 4;
const RC_LEVEL = RC_LENGTH + 64;
const RC_PROB_COUNT = RC_LEVEL + 128 * 128;
// 区間符号は 1 バイト作るのにハフマンより手間がかかるので、yield の間隔を短くする
const RANGE_OUTPUT_BYTES_PER_YIELD = 4096;

function* unpackRangeGopJob(buf, progress) {
  const cursor = { pos: 1 };
  const frameCount = readVarint(buf, cursor);
  const total = readVarint(buf, cursor);
  const probs = new Uint16Array(RC_PROB_COUNT).fill(PROB_ONE >> 1);
  const end = buf.length;
  let p = cursor.pos + 1; // 先頭の 1 バイトは常に 0
  let code = 0;
  for (let i = 0; i < 4; i++) code = code * 256 + (p < end ? buf[p++] : 0);
  let range = 0xffffffff;

  // probs[base + 1 .. base + 2^nbits - 1] のビット木で nbits ビットを読む
  const tree = (base, nbits, move) => {
    let node = 1;
    for (let k = 0; k < nbits; k++) {
      const prob = probs[base + node];
      const bound = (range >>> PROB_BITS) * prob;
      if (code < bound) {
        range = bound;
        probs[base + node] = prob + ((PROB_ONE - prob) >> move);
        node <<= 1;
      } else {
        code -= bound;
        range -= bound;
        probs[base + node] = prob - (prob >> move);
        node = (node << 1) | 1;
      }
      if (range < RC_TOP) {
        range = (range * 256) >>> 0;
        code = (code * 256 + (p < end ? buf[p++] : 0)) >>> 0;
      }
    }
    return node - (1 << nbits);
  };

  const out = new Uint8Array(total);
  let o = 0;
  if (progress) progress.bytes = out;
  // v5 のバイト (varint) を 8 ビットの木で読んで書き写し、値を返す
  const copyVarint = (model) => {
    const base = RC_BYTES + model * 256;
    let value = 0;
    let scale = 1;
    let b;
    do {
      b = tree(base, 8, 5);
      out[o++] = b;
      value += (b & 0x7f) * scale;
      scale *= 128;
    } while (b >= 0x80);
    return value;
  };

  let previousLevel = 0;
  let nextYield = RANGE_OUTPUT_BYTES_PER_YIELD;
  for (let f = 0; f < frameCount; f++) {
    const flags = copyVarint(0);
    if (Math.floor(flags / 2) % 2 === 1) {
      for (let k = copyVarint(0); k > 0; k--) {
        copyVarint(1);
        copyVarint(2);
      }
    }
    if (Math.floor(flags / 4) % 2 === 1) {
      for (let k = copyVarint(0); k > 0; k--) {
        copyVarint(3);
        copyVarint(4);
      }
    }
    for (let k = copyVarint(0); k > 0; k--) {
      // 間隔: 区分 (6 ビット) と残りのビット (上位 2 ビットは区分ごとの確率、それより下は確率 1/2)
      // ラン 1 個あたりの木 (区分・長さ・色) は、関数を呼ばずにその場で読む (QuickJS では関数呼び出しが重い)
      let slotNode = 1;
      for (let bitNo = 0; bitNo < 6; bitNo++) {
        const prob = probs[RC_SLOT + slotNode];
        const bound = (range >>> PROB_BITS) * prob;
        if (code < bound) {
          range = bound;
          probs[RC_SLOT + slotNode] = prob + ((PROB_ONE - prob) >> 5);
          slotNode <<= 1;
        } else {
          code -= bound;
          range -= bound;
          probs[RC_SLOT + slotNode] = prob - (prob >> 5);
          slotNode = (slotNode << 1) | 1;
        }
        if (range < RC_TOP) {
          range = (range * 256) >>> 0;
          code = (code * 256 + (p < end ? buf[p++] : 0)) >>> 0;
        }
      }
      const slot = slotNode - 64;
      let gap = slot;
      if (slot >= 4) {
        const extraBits = (slot >> 1) - 1;
        const modeled = extraBits < 2 ? extraBits : 2;
        let rest = tree(RC_EXTRA + slot * 4, modeled, 5);
        for (let d = extraBits - modeled; d > 0; d--) {
          range = Math.floor(range / 2);
          let bit = 0;
          if (code >= range) {
            code -= range;
            bit = 1;
          }
          rest = rest * 2 + bit;
          if (range < RC_TOP) {
            range = (range * 256) >>> 0;
            code = (code * 256 + (p < end ? buf[p++] : 0)) >>> 0;
          }
        }
        gap = (2 + (slot & 1)) * 2 ** extraBits + rest;
      }
      let lengthNode = 1;
      for (let bitNo = 0; bitNo < 6; bitNo++) {
        const prob = probs[RC_LENGTH + lengthNode];
        const bound = (range >>> PROB_BITS) * prob;
        if (code < bound) {
          range = bound;
          probs[RC_LENGTH + lengthNode] = prob + ((PROB_ONE - prob) >> 5);
          lengthNode <<= 1;
        } else {
          code -= bound;
          range -= bound;
          probs[RC_LENGTH + lengthNode] = prob - (prob >> 5);
          lengthNode = (lengthNode << 1) | 1;
        }
        if (range < RC_TOP) {
          range = (range * 256) >>> 0;
          code = (code * 256 + (p < end ? buf[p++] : 0)) >>> 0;
        }
      }
      const length = lengthNode - 64;
      const levelBase = RC_LEVEL + previousLevel * 128;
      let levelNode = 1;
      for (let bitNo = 0; bitNo < 7; bitNo++) {
        const prob = probs[levelBase + levelNode];
        const bound = (range >>> PROB_BITS) * prob;
        if (code < bound) {
          range = bound;
          probs[levelBase + levelNode] = prob + ((PROB_ONE - prob) >> 4);
          levelNode <<= 1;
        } else {
          code -= bound;
          range -= bound;
          probs[levelBase + levelNode] = prob - (prob >> 4);
          levelNode = (levelNode << 1) | 1;
        }
        if (range < RC_TOP) {
          range = (range * 256) >>> 0;
          code = (code * 256 + (p < end ? buf[p++] : 0)) >>> 0;
        }
      }
      const level = levelNode - 128;
      previousLevel = level;
      let v = gap * RUN_POSITION_DIVISOR + length * 128 + level;
      if (v < 0x80000000) {
        while (v >= 0x80) {
          out[o++] = (v & 0x7f) | 0x80;
          v >>>= 7;
        }
      } else {
        while (v >= 0x80) {
          out[o++] = (v % 128) | 0x80;
          v = Math.floor(v / 128);
        }
      }
      out[o++] = v;
      if (o >= nextYield) {
        nextYield = o + RANGE_OUTPUT_BYTES_PER_YIELD;
        if (progress) progress.produced = o;
        yield;
      }
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
