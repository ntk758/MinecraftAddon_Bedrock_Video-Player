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
 * だまし絵の舞台 (床と、後ろ・左・右の 3 枚の壁でできた、手前と上の開いた箱) の大きさ。
 * 写すときの座標 (内側の座標) は、床の北西の角を原点とするブロック単位 (x は東、y は上、z は南):
 *  - 床: 上の面が y = 1、x は 0〜stageWidth、z は 1〜1 + floorRows (北の端で後ろの壁に接する)
 *  - 後ろの壁: 南の面が z = 1、x は 0〜stageWidth、y は 1〜1 + wallHeight
 *  - 左 (西) の壁: 東の面が x = 0、右 (東) の壁: 西の面が x = stageWidth。どちらも z は床と同じ、y は後ろの壁と同じ
 *  - 人物 (幅 × 高さ × 奥行きの立体): 床の上 (y = 1 から)、左右中央、後ろの壁から gap ブロック離れた所に立つ
 * 壁は人物より高くする (人物の上の方を通る視線は、人物より高い所で壁に当たる)。左右の壁は、横から見たときに人物の
 * 向こう側の視線を受ける。見る人は箱の中か手前にいる必要があるので、左右は人物の高さの 0.75 倍 (幅の半分より
 * 狭ければ幅の半分) ずつ広げ、真横から人物の高さほど離れて見られるようにする。床は手前にも広げる。
 *
 * ワールドでは左の壁のブロックが x = 0 の列になるよう、内側の座標を originX (= 1) だけずらして置く。
 * 盤面は後ろの壁 (wallHeight 行、0 行目が一番上) → 床 (floorRows 行、0 行目が壁際) → 左の壁 → 右の壁 (どちらも
 * wallHeight 行、各行は z の順) の順で、どの行も rowWidth 列 (行が短い区画の余りは使わない)。
 */
export function stageGeometry(video) {
  const { width, height, depth } = video;
  const side = Math.ceil(Math.max(width / 2, height * 0.75));
  const gap = Math.max(4, Math.ceil(depth / 2));
  const front = Math.max(4, depth);
  const stageWidth = width + 2 * side;
  const wallHeight = Math.ceil(height * 1.5);
  const floorRows = gap + depth + front;
  const rowWidth = Math.max(stageWidth, floorRows);
  const rows = 3 * wallHeight + floorRows;
  return {
    width, height, depth, side, gap, front, stageWidth, wallHeight, floorRows, rowWidth, rows,
    originX: 1,
    cells: rowWidth * rows,
    // 区画の境目の行 (rect 方式の箱はまたがない)
    splits: [wallHeight, wallHeight + floorRows, 2 * wallHeight + floorRows],
    // ワールドで占める範囲の x・z 方向の大きさ
    sizeX: stageWidth + 2,
    sizeZ: 1 + floorRows,
  };
}

/** 盤面の p 番目のマスが、区画の余り (ワールドに置かないマス) か。 */
export function isStagePadding(geom, p) {
  const row = Math.floor(p / geom.rowWidth);
  const column = p % geom.rowWidth;
  return column >= (row < geom.wallHeight + geom.floorRows ? geom.stageWidth : geom.floorRows);
}

/**
 * 盤面の p 番目のマスを、舞台の原点 (左の壁の北の端の床の高さ) からのブロックの位置にする。
 * 後ろの壁は z = 0 の列、床は y = 0 の段、左の壁は x = 0 の列、右の壁は x = stageWidth + 1 の列。
 */
export function stageCellOffset(geom, p, out) {
  const { rowWidth, wallHeight, floorRows } = geom;
  const row = Math.floor(p / rowWidth);
  const column = p % rowWidth;
  if (row < wallHeight) {
    out.x = geom.originX + column;
    out.y = wallHeight - row;
    out.z = 0;
  } else if (row < wallHeight + floorRows) {
    out.x = geom.originX + column;
    out.y = 0;
    out.z = 1 + row - wallHeight;
  } else {
    const r = (row - wallHeight - floorRows) % wallHeight;
    out.x = row < 2 * wallHeight + floorRows ? 0 : geom.originX + geom.stageWidth;
    out.y = wallHeight - r;
    out.z = 1 + column;
  }
  return out;
}

/**
 * projectToStage の作業領域。figure (立体の人物) を書き換えたら dirty = true にする (外側のブロックの一覧を作り直す)。
 *  - background: 人物の写っていない盤面 (後ろの壁は wallLevel、床は floorLevel、左右の壁は sideLevel (main.js は空気に
 *    して、人物の写るマスだけブロックを置く)、区画の余りは -1 = 置かない)
 *  - surface / faces: 外から見える面を持つブロックの番号と、空気に面している向き (ビット: 1 西 2 東 4 下 8 上 16 北 32 南)
 *  - stamp: そのマスを何回目の写しで塗ったか (塗ったマスは後ろのブロックで調べ直さない)
 *  - rowMin / rowMax・colMin / colMax: 直前の写しで人物を塗った盤面の行と列の範囲 (塗らなければ rowMin > rowMax)
 */
export function createStageWork(geom, wallLevel, floorLevel, sideLevel = wallLevel) {
  const voxels = geom.width * geom.height * geom.depth;
  const background = new Int16Array(geom.cells);
  for (let p = 0; p < geom.cells; p++) {
    const row = Math.floor(p / geom.rowWidth);
    background[p] = isStagePadding(geom, p) ? -1
      : row < geom.wallHeight ? wallLevel
        : row < geom.wallHeight + geom.floorRows ? floorLevel : sideLevel;
  }
  return {
    dirty: true,
    background,
    // 後ろの壁の背景の映像 (STAGE_IMAGE_OFFSET を足した値、映像の無いマスは wallLevel)。wallVersion が変わったときだけ作り直す
    wallLevel,
    wallImage: new Int16Array(geom.stageWidth * geom.wallHeight),
    wallVersion: -1,
    surface: new Int32Array(voxels),
    faces: new Uint8Array(voxels),
    surfaceCount: 0,
    // collectSurface が空気だけの区間を読み飛ばすための、figure を 4 ブロックずつ Float64 として見る配列と、空気 4 つ分の値
    chunkView: null,
    chunkAir: 0,
    airChunk: 0,
    keys: new Int32Array(voxels),
    stamp: new Int32Array(geom.cells),
    pass: 0,
    rowMin: 0,
    rowMax: -1,
    box: new Float64Array(16),
    // 1 つのブロックの 8 つの角を通る視線が最初に当たる面と、その点
    cornerHit: new Uint8Array(8),
    cornerX: new Float64Array(8), cornerY: new Float64Array(8), cornerZ: new Float64Array(8),
    colMin: 0,
    colMax: -1,
    xOf: new Int32Array(geom.width), xRank: new Int32Array(geom.width),
    rowOf: new Int32Array(geom.height), rowRank: new Int32Array(geom.height),
    dOf: new Int32Array(geom.depth), dRank: new Int32Array(geom.depth),
  };
}

/**
 * 立体の外から見える面を持つブロックの一覧を作る (work.surface・faces・surfaceCount)。p の昇順に並べる。
 * 立体の大半は空気 (MMD の人物でブロックのあるのは 2〜3%) なので、figure (Int16Array) を 4 ブロック = 8 バイトずつ
 * Float64 として見て、空気だけの 4 ブロックは 1 回の比較で読み飛ばす。違うものは、4 ブロックを 1 つずつ調べる。
 * 4 ブロック分のビット列を実数として比べるので、次のときは使わない (1 つずつ調べる)。
 *  - 空気 4 つ分が 0 になる air_level (実数の +0 と -0 は等しく、ビット列の違うものを空気と取り違えかねない)
 *  - 空気 4 つ分が NaN になる air_level (どれとも等しくならず、読み飛ばせない)
 *  - figure の先頭が 8 バイトの境目に無い (Float64Array を作れない)
 * 端数 (ブロック数が 4 の倍数でないぶん) も 1 つずつ調べる。NaN のビット列を含む 4 ブロックは、空気 4 つ分とは等しくならず
 * 1 つずつ調べに回るので、結果は 1 つずつ調べたときと同じ (tests/js/stage_surface.mjs が確かめる)。
 */
function collectSurface(figure, air, geom, work) {
  const { width, height, depth } = geom;
  const slice = width * height;
  const total = slice * depth;
  let chunks = null;
  if (figure.byteOffset % 8 === 0) {
    let view = work.chunkView;
    // figure の配列 (buffer) か air_level が変わったら、見る配列と空気 4 つ分の値を作り直す
    if (view === null || view.buffer !== figure.buffer || view.byteOffset !== figure.byteOffset || work.chunkAir !== air) {
      view = work.chunkView = new Float64Array(figure.buffer, figure.byteOffset, total >> 2);
      work.chunkAir = air;
      work.airChunk = new Float64Array(new Int16Array(4).fill(air).buffer)[0];
    }
    if (work.airChunk !== 0 && work.airChunk === work.airChunk) chunks = view;
  }
  const airChunk = work.airChunk;
  const whole = chunks === null ? 0 : total >> 2;
  const groups = (total + 3) >> 2;
  let n = 0;
  for (let g = 0; g < groups; g++) {
    if (g < whole && chunks[g] === airChunk) continue;
    const end = Math.min(total, 4 * g + 4);
    for (let p = 4 * g; p < end; p++) {
      const level = figure[p];
      if (level === air || level < 0) continue;
      // 立体の中の番号は空気か 0 以上 (負の数は使わない) なので、空気かどうかは air と比べるだけでよい
      const x = p % width;
      const row = Math.floor(p / width) % height;
      const d = Math.floor(p / slice);
      let mask = 0;
      if (x === 0 || figure[p - 1] === air) mask |= 1;
      if (x + 1 === width || figure[p + 1] === air) mask |= 2;
      if (row + 1 === height || figure[p + width] === air) mask |= 4;
      if (row === 0 || figure[p - width] === air) mask |= 8;
      if (d === 0 || figure[p - slice] === air) mask |= 16;
      if (d + 1 === depth || figure[p + slice] === air) mask |= 32;
      if (mask === 0) continue;
      work.surface[n] = p;
      work.faces[n] = mask;
      n++;
    }
  }
  work.surfaceCount = n;
  work.dirty = false;
}

/**
 * 1 つの軸のマス (中心が base + i + 0.5) を、目の座標 e に近い順に並べる。order[k] = k 番目に近いマス、rank[i] = その逆。
 */
function orderByDistance(n, base, e, order, rank) {
  let hi = Math.min(n - 1, Math.max(0, Math.floor(e - base)));
  let lo = hi - 1;
  for (let k = 0; k < n; k++) {
    let i;
    if (lo < 0) i = hi++;
    else if (hi >= n) i = lo--;
    else if (Math.abs(base + hi + 0.5 - e) <= Math.abs(base + lo + 0.5 - e)) i = hi++;
    else i = lo--;
    order[k] = i;
    rank[i] = k;
  }
}

// だまし絵の後ろの壁に流す背景の映像の、盤面の値のずらし幅 (背景のパレット番号 + この値)。人物のパレット番号
// (0〜127) とも、main.js の壁・床の値 (30000・30001) とも重ならない
export const STAGE_IMAGE_OFFSET = 20000;

// 舞台の面の番号 (projectToStage の写る範囲 box の 4 つずつの並び: u の最小・最大、v の最小・最大)。
// 床は (x, z)、後ろの壁は (x, y)、左右の壁は (z, y) を u・v とする。角を通る視線がどの面にも当たらなければ NO_HIT
const FLOOR = 0;
const BACK = 1;
const LEFT = 2;
const RIGHT = 3;
const NO_HIT = 4;

/**
 * 立体の人物 figure ((奥行き × 高さ) 行 × 幅 列、空気は video.air_level) を、目の位置 eye (舞台の内側の座標) から
 * 見たとおりに床と 3 枚の壁へ写し、盤面 out (stageGeometry の cells マス) に書く。人物の当たらないマスは
 * work.background のまま。work は createStageWork で作った作業領域。
 *
 * wallImage (stageWidth × wallHeight の平面の盤面、無ければ null) を渡すと、後ろの壁を背景の映像にする (値は
 * パレット番号 + STAGE_IMAGE_OFFSET、負の数のマスは壁のまま)。人物はその上に描く。wallVersion は背景の映像の
 * フレームが変わるたびに変える (同じなら前に作った後ろの壁を使い回す。目が動いただけの描き直しでは作り直さない)。
 *
 * 外から見える面のうち目の方を向いた面を持つブロックだけを、目に近い順に写す。各ブロックの写る範囲は 8 つの角を
 * 通る視線が最初に当たる面から求め (隣の面の範囲には、その点を境目の位置として足す)、範囲のまだ塗っていないマス
 * について、マスの中心から目への視線がそのブロックを通るかを確かめる。
 * 近い順は、軸ごとに目に近い順に並べた入れ子の順 (奥行き → 高さ → 幅)。目からの視線は各座標が単調に変わるので、
 * あるブロックを隠すブロックは、どの軸でも目に近いか同じ = この順で先に来る。
 * 目が箱の中か手前 (左右の壁の間、後ろの壁の前、床の上) に無ければ写せないので false を返す (out は変えない)。
 */
export function projectToStage(figure, video, geom, eye, out, work, wallImage = null, wallVersion = 0) {
  const { width, height, depth, side, gap, stageWidth, wallHeight, floorRows, rowWidth } = geom;
  if (!(eye.z > 1 && eye.y > 1 && eye.x > 0 && eye.x < stageWidth)) return false;
  if (work.dirty) collectSurface(figure, video.air_level, geom, work);
  out.set(work.background);
  if (wallImage) {
    const wall = work.wallImage;
    if (work.wallVersion !== wallVersion) {
      for (let q = 0; q < wall.length; q++) {
        const level = wallImage[q];
        wall[q] = level >= 0 ? level + STAGE_IMAGE_OFFSET : work.wallLevel;
      }
      work.wallVersion = wallVersion;
    }
    if (rowWidth === stageWidth) out.set(wall);
    else for (let r = 0; r < wallHeight; r++) out.set(wall.subarray(r * stageWidth, (r + 1) * stageWidth), r * rowWidth);
  }
  const stamp = work.stamp;
  const pass = ++work.pass;
  const ex = eye.x;
  const ey = eye.y;
  const ez = eye.z;
  // 人物のブロック (x, row, d) の箱は x: side + x、y: height - row、z: 1 + gap + d から 1 ずつ
  const { xOf, xRank, rowOf, rowRank, dOf, dRank, surface, faces, keys, box, cornerHit, cornerX, cornerY, cornerZ } = work;
  orderByDistance(width, side, ex, xOf, xRank);
  orderByDistance(depth, 1 + gap, ez, dOf, dRank);
  // 行は上から数えるので、y の小さい方 (下の行) から並べてから行の番号に直す
  orderByDistance(height, 1, ey, rowOf, rowRank);
  for (let k = 0; k < height; k++) rowOf[k] = height - 1 - rowOf[k];
  for (let k = 0; k < height; k++) rowRank[rowOf[k]] = k;
  const slice = width * height;
  let count = 0;
  for (let n = 0; n < work.surfaceCount; n++) {
    const p = surface[n];
    const x = p % width;
    const row = Math.floor(p / width) % height;
    const d = Math.floor(p / slice);
    const x0 = side + x;
    const y0 = height - row;
    const z0 = 1 + gap + d;
    const mask = faces[n];
    // 目の方を向いた、空気に面した面が無ければ見えない
    if (!((mask & 1 && ex < x0) || (mask & 2 && ex > x0 + 1) || (mask & 4 && ey < y0) || (mask & 8 && ey > y0 + 1)
      || (mask & 16 && ez < z0) || (mask & 32 && ez > z0 + 1))) continue;
    keys[count++] = (dRank[d] * height + rowRank[row]) * width + xRank[x];
  }
  const sorted = keys.subarray(0, count).sort();
  const leftRow0 = wallHeight + floorRows;
  const rightRow0 = 2 * wallHeight + floorRows;
  let rowMin = Infinity;
  let rowMax = -1;
  let colMin = Infinity;
  let colMax = -1;
  for (let n = 0; n < count; n++) {
    const key = sorted[n];
    const x = xOf[key % width];
    const row = rowOf[Math.floor(key / width) % height];
    const d = dOf[Math.floor(key / slice)];
    const level = figure[(d * height + row) * width + x];
    const x0 = side + x;
    const y0 = height - row;
    const z0 = 1 + gap + d;
    let hits = 0;
    // 角を通る視線が各面に当たるまでの長さ (角までを 1 とする)。当たらなければ Infinity。
    // 床までは角の y だけ、後ろの壁までは z だけ、左右の壁までは x だけで決まるので、2 つずつ求めておく
    const xLow = x0 - ex;
    const yLow = y0 - ey;
    const zNear = z0 - ez;
    const sFloor0 = yLow < 0 ? (1 - ey) / yLow : Infinity;
    const sFloor1 = yLow + 1 < 0 ? (1 - ey) / (yLow + 1) : Infinity;
    const sBack0 = zNear < 0 ? (1 - ez) / zNear : Infinity;
    const sBack1 = zNear + 1 < 0 ? (1 - ez) / (zNear + 1) : Infinity;
    const sSide0 = xLow < 0 ? -ex / xLow : xLow > 0 ? (stageWidth - ex) / xLow : Infinity;
    const sSide1 = xLow + 1 < 0 ? -ex / (xLow + 1) : xLow + 1 > 0 ? (stageWidth - ex) / (xLow + 1) : Infinity;
    // 写る範囲 box (写る面ごとの u・v の最小と最大) の求め方は 2 通りあり、結果はビット単位で同じになる。
    // どちらかを直したら、もう一方も同じように直すこと (tests/test_stage_exact.py が両方の通り道を通る目で確かめる)。
    // (1) 近道: 8 つの角の視線がすべて同じ 1 つの面に当たるブロック (MMD の人物では約 99%) は、その面の範囲を
    //     式 4 つの最小と最大で直接求める。
    //     角ごとの面の選び方は下の (2) と同じ (床を先に選び、後ろの壁は < のときだけ、左右の壁は < のときだけ置き換える)。
    //     8 つの角は床・後ろの壁・左右の壁の長さ (sFloor0/1・sBack0/1・sSide0/1) の全部の組み合わせになるので、
    //     それぞれの組の小さい方 (Lo) と大きい方 (Hi) で、全部の角が同じ面になる条件を書ける。
    //     式は (2) と同じ ex + cx * s で、s > 0 なので、同じ s の値は cx が 1 大きいほうが小さくない。
    //     そのため最小は cx の小さい方の 2 つ、最大は大きい方の 2 つから選べば、(2) の 8 つの角の最小と最大に等しい。
    const sFloorLo = sFloor0 < sFloor1 ? sFloor0 : sFloor1;
    const sFloorHi = sFloor0 < sFloor1 ? sFloor1 : sFloor0;
    const sBackLo = sBack0 < sBack1 ? sBack0 : sBack1;
    const sBackHi = sBack0 < sBack1 ? sBack1 : sBack0;
    const sSideLo = sSide0 < sSide1 ? sSide0 : sSide1;
    const sSideHi = sSide0 < sSide1 ? sSide1 : sSide0;
    let single = true;
    if (sBackHi !== Infinity && sBackHi < sFloorLo && sBackHi <= sSideLo) {
      // 後ろの壁だけ (u は x、v は y)
      const u00 = ex + xLow * sBack0;
      const u01 = ex + xLow * sBack1;
      const u10 = ex + (xLow + 1) * sBack0;
      const u11 = ex + (xLow + 1) * sBack1;
      const v00 = ey + yLow * sBack0;
      const v01 = ey + yLow * sBack1;
      const v10 = ey + (yLow + 1) * sBack0;
      const v11 = ey + (yLow + 1) * sBack1;
      box[BACK * 4] = u00 < u01 ? u00 : u01;
      box[BACK * 4 + 1] = u10 > u11 ? u10 : u11;
      box[BACK * 4 + 2] = v00 < v01 ? v00 : v01;
      box[BACK * 4 + 3] = v10 > v11 ? v10 : v11;
      hits = 1 << BACK;
    } else if (sFloorHi !== Infinity && sFloorHi <= sBackLo && sFloorHi <= sSideLo) {
      // 床だけ (u は x、v は z)
      const u00 = ex + xLow * sFloor0;
      const u01 = ex + xLow * sFloor1;
      const u10 = ex + (xLow + 1) * sFloor0;
      const u11 = ex + (xLow + 1) * sFloor1;
      const v00 = ez + zNear * sFloor0;
      const v01 = ez + zNear * sFloor1;
      const v10 = ez + (zNear + 1) * sFloor0;
      const v11 = ez + (zNear + 1) * sFloor1;
      box[FLOOR * 4] = u00 < u01 ? u00 : u01;
      box[FLOOR * 4 + 1] = u10 > u11 ? u10 : u11;
      box[FLOOR * 4 + 2] = v00 < v01 ? v00 : v01;
      box[FLOOR * 4 + 3] = v10 > v11 ? v10 : v11;
      hits = 1 << FLOOR;
    } else if (sSideHi !== Infinity && sSideHi < sFloorLo && sSideHi < sBackLo && (xLow > 0 || xLow + 1 < 0)) {
      // 右 (角の x がすべて正) か左 (すべて負) の壁だけ (u は z、v は y)
      const wall = xLow > 0 ? RIGHT : LEFT;
      const u00 = ez + zNear * sSide0;
      const u01 = ez + zNear * sSide1;
      const u10 = ez + (zNear + 1) * sSide0;
      const u11 = ez + (zNear + 1) * sSide1;
      const v00 = ey + yLow * sSide0;
      const v01 = ey + yLow * sSide1;
      const v10 = ey + (yLow + 1) * sSide0;
      const v11 = ey + (yLow + 1) * sSide1;
      box[wall * 4] = u00 < u01 ? u00 : u01;
      box[wall * 4 + 1] = u10 > u11 ? u10 : u11;
      box[wall * 4 + 2] = v00 < v01 ? v00 : v01;
      box[wall * 4 + 3] = v10 > v11 ? v10 : v11;
      hits = 1 << wall;
    } else {
      single = false;
    }
    // (2) 一般の場合: 8 つの角のそれぞれについて、視線が最初に当たる面と点を求めて範囲にする
    if (!single) {
      for (let c = 0; c < 8; c++) {
        const cx = xLow + (c & 1);
        const cy = yLow + ((c >> 1) & 1);
        const cz = zNear + ((c >> 2) & 1);
        let s = c & 2 ? sFloor1 : sFloor0;
        let hit = FLOOR;
        const sBack = c & 4 ? sBack1 : sBack0;
        if (sBack < s) {
          s = sBack;
          hit = BACK;
        }
        const sSide = c & 1 ? sSide1 : sSide0;
        if (sSide < s) {
          s = sSide;
          hit = cx < 0 ? LEFT : RIGHT;
        }
        if (s === Infinity) {
          cornerHit[c] = NO_HIT;
          continue;
        }
        cornerHit[c] = hit;
        cornerX[c] = ex + cx * s;
        cornerY[c] = ey + cy * s;
        cornerZ[c] = ez + cz * s;
        hits |= 1 << hit;
      }
      // 視線の当たった面ごとに、写る範囲を求める。当たった点のほか、隣の面 (向かい合う左右の壁どうしを除く) に
      // 当たった点も、その面との境目の位置として範囲に入れる (隣の面の点の座標は、この面の縁の上にある)
      for (let surface = FLOOR; surface <= RIGHT; surface++) {
        if (!(hits & (1 << surface))) continue;
        const opposite = surface === LEFT ? RIGHT : surface === RIGHT ? LEFT : NO_HIT;
        let u0 = Infinity;
        let u1 = -Infinity;
        let v0 = Infinity;
        let v1 = -Infinity;
        for (let c = 0; c < 8; c++) {
          const hit = cornerHit[c];
          if (hit === NO_HIT || hit === opposite) continue;
          const u = surface <= BACK ? cornerX[c] : cornerZ[c];
          const v = surface === FLOOR ? cornerZ[c] : cornerY[c];
          if (u < u0) u0 = u;
          if (u > u1) u1 = u;
          if (v < v0) v0 = v;
          if (v > v1) v1 = v;
        }
        const k = surface * 4;
        box[k] = u0;
        box[k + 1] = u1;
        box[k + 2] = v0;
        box[k + 3] = v1;
      }
    }
    if (hits & (1 << FLOOR)) {
      const i0 = Math.max(0, Math.floor(box[0]));
      const i1 = Math.min(stageWidth - 1, Math.ceil(box[1]) - 1);
      const k0 = Math.max(0, Math.floor(box[2]) - 1);
      const k1 = Math.min(floorRows - 1, Math.ceil(box[3]) - 2);
      for (let k = k0; k <= k1; k++) {
        const base = (wallHeight + k) * rowWidth;
        for (let i = i0; i <= i1; i++) {
          if (stamp[base + i] === pass) continue;
          if (rayEntersBox(ex, ey, ez, i + 0.5 - ex, 1 - ey, k + 1.5 - ez, x0, y0, z0) <= 1) {
            stamp[base + i] = pass;
            out[base + i] = level;
            if (wallHeight + k < rowMin) rowMin = wallHeight + k;
            if (wallHeight + k > rowMax) rowMax = wallHeight + k;
            if (i < colMin) colMin = i;
            if (i > colMax) colMax = i;
          }
        }
      }
    }
    // 壁の r 行目は y = wallHeight - r 〜 wallHeight - r + 1
    if (hits & (1 << BACK)) {
      const i0 = Math.max(0, Math.floor(box[4]));
      const i1 = Math.min(stageWidth - 1, Math.ceil(box[5]) - 1);
      const r0 = Math.max(0, wallHeight - Math.ceil(box[7]) + 1);
      const r1 = Math.min(wallHeight - 1, wallHeight - Math.floor(box[6]));
      for (let r = r0; r <= r1; r++) {
        const base = r * rowWidth;
        const cy = wallHeight - r + 0.5;
        for (let i = i0; i <= i1; i++) {
          if (stamp[base + i] === pass) continue;
          if (rayEntersBox(ex, ey, ez, i + 0.5 - ex, cy - ey, 1 - ez, x0, y0, z0) <= 1) {
            stamp[base + i] = pass;
            out[base + i] = level;
            if (r < rowMin) rowMin = r;
            if (r > rowMax) rowMax = r;
            if (i < colMin) colMin = i;
            if (i > colMax) colMax = i;
          }
        }
      }
    }
    for (let wall = LEFT; wall <= RIGHT; wall++) {
      if (!(hits & (1 << wall))) continue;
      const k = wall * 4;
      const wx = wall === LEFT ? 0 : stageWidth;
      const row0 = wall === LEFT ? leftRow0 : rightRow0;
      const j0 = Math.max(0, Math.floor(box[k]) - 1);
      const j1 = Math.min(floorRows - 1, Math.ceil(box[k + 1]) - 2);
      const r0 = Math.max(0, wallHeight - Math.ceil(box[k + 3]) + 1);
      const r1 = Math.min(wallHeight - 1, wallHeight - Math.floor(box[k + 2]));
      for (let r = r0; r <= r1; r++) {
        const base = (row0 + r) * rowWidth;
        const cy = wallHeight - r + 0.5;
        for (let j = j0; j <= j1; j++) {
          if (stamp[base + j] === pass) continue;
          if (rayEntersBox(ex, ey, ez, wx - ex, cy - ey, j + 1.5 - ez, x0, y0, z0) <= 1) {
            stamp[base + j] = pass;
            out[base + j] = level;
            if (row0 + r < rowMin) rowMin = row0 + r;
            if (row0 + r > rowMax) rowMax = row0 + r;
            if (j < colMin) colMin = j;
            if (j > colMax) colMax = j;
          }
        }
      }
    }
  }
  work.rowMin = rowMax < 0 ? 0 : rowMin;
  work.rowMax = rowMax;
  work.colMin = rowMax < 0 ? 0 : colMin;
  work.colMax = colMax;
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
