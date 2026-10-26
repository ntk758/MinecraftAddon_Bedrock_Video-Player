// codec.js の projectToStage (立体のだまし絵の投影) が、視線を 3D DDA でたどった正解と一致することを確かめる
// (tests/test_stage_exact.py から実行)。
// 使い方: node stage_exact.mjs <幅>x<高さ>x<奥行き> <図形> [目の数] [--inside] [--path 面] [--dump ファイル] [--project ファイル]
//   図形: shell (中が空洞の殻) / plates (薄い板) / points (ばらばらの点) / solid (中身の詰まった塊) / rods (細い棒)
// 乱数の種は図形と大きさで決まる。目は座標が整数でない乱数で、舞台の箱の中のブロックの無い所 (正面・横・高い位置・ブロックの面のすぐ外)。
// 盤面の各マスの中心から目への線分を DDA で人物の格子に沿って進め、最初に当たったブロックの値 (当たらなければ背景)
// を正解とする。食い違うマスの数 (mismatchCount) が 0 であるべき。
//   --path floor / back / left / right / general: 写る範囲の求め方 (projectToStage の近道) のうち、その面にしか写らない
//     ブロック (general は 1 つの面に決まらないブロック) を多く含む目だけを使う。paths に、目に向いた面を持つブロックを
//     8 つの角を通る視線が最初に当たる面で分けた数 (codec.js を使わず、定義どおりに数える) を出す
//   --dump ファイル: 作った立体を Int16 の生のバイト列で書く
//   --project ファイル: 投影にはこのファイルの立体 (閉じた空洞を埋めたものなど) を使い、正解はもとの立体で求める
import { readFileSync, writeFileSync } from "node:fs";
import { createStageWork, isStagePadding, projectToStage, stageGeometry } from "./codec.js";

const AIR = 127;
const WALL = 30000;
const FLOOR = 30001;
const SIDE = 30002;

let inside = false;
const options = { "--path": null, "--dump": null, "--project": null };
const positional = [];
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--inside") inside = true;
  else if (argv[i] in options) options[argv[i]] = argv[++i];
  else positional.push(argv[i]);
}
const wantedPath = options["--path"];
const [dims, shape, eyeCountArg] = positional;
const [width, height, depth] = dims.split("x").map(Number);
const eyeCount = Number(eyeCountArg ?? 20);
const video = { width, height, depth, air_level: AIR };

// 整数だけの擬似乱数 (どの環境でも同じ並びになる)
function mulberry32(seed) {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const seedOf = (text) => [...text].reduce((h, c) => (Math.imul(h, 31) + c.charCodeAt(0)) | 0, 7);
const random = mulberry32(seedOf(`${dims}/${shape}`));
const between = (lo, hi) => lo + (hi - lo) * random();

// --- 立体 (幅 × 高さ × 奥行き。index = (d * height + row) * width + x、row は上から数える) ---
const figure = new Int16Array(width * height * depth).fill(AIR);
const at = (x, row, d) => (d * height + row) * width + x;
// 色は位置から決める (手前のブロックを取り違えたときに、値の違いで分かるように)
const colour = (x, row, d) => (x * 7 + row * 3 + d * 5) % 20;
const put = (x, row, d) => {
  if (x >= 0 && x < width && row >= 0 && row < height && d >= 0 && d < depth) figure[at(x, row, d)] = colour(x, row, d);
};

function ellipsoid(cx, cy, cz, rx, ry, rz, inner = 0) {
  for (let d = 0; d < depth; d++) {
    for (let row = 0; row < height; row++) {
      for (let x = 0; x < width; x++) {
        const q = ((x + 0.5 - cx) / rx) ** 2 + ((height - row - 0.5 - cy) / ry) ** 2 + ((d + 0.5 - cz) / rz) ** 2;
        if (q <= 1 && q >= inner) put(x, row, d);
      }
    }
  }
}

// 太さ 1 の棒: 2 点の間を細かくたどって、通るブロックを立てる
function rod(a, b) {
  const steps = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) * 3);
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    put(Math.floor(a[0] + (b[0] - a[0]) * t), Math.floor(a[1] + (b[1] - a[1]) * t), Math.floor(a[2] + (b[2] - a[2]) * t));
  }
}

if (shape === "shell") {
  // 表面だけの殻 (MMD のモデルと同じ。内側の面も空気に面している): 体と頭の殻 + 手足の細い殻
  const shell = 1.6;
  ellipsoid(width / 2, height * 0.45, depth / 2, width * 0.3, height * 0.3, depth * 0.4, (1 - shell / (width * 0.3)) ** 2);
  ellipsoid(width / 2, height * 0.85, depth / 2, width * 0.16, height * 0.12, depth * 0.35, 0.55);
  ellipsoid(width * 0.2, height * 0.5, depth / 2, width * 0.1, height * 0.22, depth * 0.3, 0.4);
  ellipsoid(width * 0.8, height * 0.5, depth / 2, width * 0.1, height * 0.22, depth * 0.3, 0.4);
} else if (shape === "plates") {
  // 厚さ 1 の板を、向きと位置と広さを変えて重ねる
  for (let k = 0; k < 5; k++) {
    const axis = k % 3;
    const size = [width, height, depth];
    const lo = size.map((n) => Math.floor(between(0, n * 0.4)));
    const hi = size.map((n, a) => Math.min(n - 1, lo[a] + Math.floor(between(n * 0.3, n * 0.8))));
    const level = Math.floor(between(1, size[axis] - 1));
    lo[axis] = hi[axis] = level;
    for (let d = lo[2]; d <= hi[2]; d++) {
      for (let row = lo[1]; row <= hi[1]; row++) for (let x = lo[0]; x <= hi[0]; x++) put(x, row, d);
    }
  }
} else if (shape === "points") {
  // ばらばらの点 (隣り合わない所が多い)
  for (let i = 0; i < figure.length; i++) if (random() < 0.04) figure[i] = Math.floor(random() * 20);
} else if (shape === "solid") {
  // 中身の詰まった体・頭・腕・脚
  ellipsoid(width / 2, height * 0.5, depth / 2, width * 0.22, height * 0.2, depth * 0.35);
  ellipsoid(width / 2, height * 0.82, depth / 2, width * 0.12, height * 0.1, depth * 0.3);
  ellipsoid(width * 0.22, height * 0.5, depth / 2, width * 0.08, height * 0.2, depth * 0.25);
  ellipsoid(width * 0.78, height * 0.5, depth / 2, width * 0.08, height * 0.2, depth * 0.25);
  ellipsoid(width * 0.4, height * 0.17, depth / 2, width * 0.07, height * 0.17, depth * 0.25);
  ellipsoid(width * 0.6, height * 0.17, depth / 2, width * 0.07, height * 0.17, depth * 0.25);
} else if (shape === "rods") {
  // 髪やスカートの縁のような細い棒
  for (let k = 0; k < 14; k++) {
    rod([between(0, width), between(0, height), between(0, depth)], [between(0, width), between(0, height), between(0, depth)]);
  }
} else {
  console.error(`unknown shape: ${shape}`);
  process.exit(2);
}

if (options["--dump"]) writeFileSync(options["--dump"], Buffer.from(figure.buffer, figure.byteOffset, figure.byteLength));
// 投影する立体: --project があればそのファイルの立体 (閉じた空洞を埋めたもの)、無ければ作った立体。正解 (DDA) は作った立体で求める
let projected = figure;
if (options["--project"]) {
  const raw = readFileSync(options["--project"]);
  projected = new Int16Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.length));
  if (projected.length !== figure.length) {
    console.error(`--project: ${projected.length} voxels, expected ${figure.length}`);
    process.exit(2);
  }
}

const geom = stageGeometry(video);
const { side, gap, stageWidth, wallHeight, floorRows, rowWidth } = geom;
const box = { x0: side, x1: side + width, y0: 1, y1: 1 + height, z0: 1 + gap, z1: 1 + gap + depth };

// --- 写る範囲の求め方の分類 (定義どおりに数える。codec.js の近道の条件式は使わない) ---
// 投影される立体の、空気に面した面 (6 方向の隣が空気か立体の外) を持つブロック
const surfaceBlocks = [];
for (let p = 0; p < projected.length; p++) {
  if (projected[p] === AIR) continue;
  const x = p % width;
  const row = Math.floor(p / width) % height;
  const d = Math.floor(p / (width * height));
  const air = (dx, dy, dz) => {
    const nx = x + dx;
    const nr = row + dy;
    const nd = d + dz;
    return nx < 0 || nx >= width || nr < 0 || nr >= height || nd < 0 || nd >= depth || projected[at(nx, nr, nd)] === AIR;
  };
  // 1 西 2 東 4 下 8 上 16 北 32 南 (codec.js の faces と同じ向き)
  const mask = (air(-1, 0, 0) ? 1 : 0) | (air(1, 0, 0) ? 2 : 0) | (air(0, 1, 0) ? 4 : 0) | (air(0, -1, 0) ? 8 : 0)
    | (air(0, 0, -1) ? 16 : 0) | (air(0, 0, 1) ? 32 : 0);
  if (mask) surfaceBlocks.push({ x0: side + x, y0: height - row, z0: 1 + gap + d, mask });
}
const PATH_NAMES = ["floor", "back", "left", "right", "general"];
// 目から角 (目との差 cx, cy, cz) を通る視線が、舞台のどの面に最初に当たるか (0 床 1 後ろの壁 2 左の壁 3 右の壁、当たらなければ -1)。
// 同じ長さのときは床、後ろの壁、左右の壁の順
function firstFace(e, cx, cy, cz) {
  let nearest = Infinity;
  let face = -1;
  if (cy < 0 && (1 - e.y) / cy < nearest) { nearest = (1 - e.y) / cy; face = 0; }
  if (cz < 0 && (1 - e.z) / cz < nearest) { nearest = (1 - e.z) / cz; face = 1; }
  if (cx < 0 && -e.x / cx < nearest) { nearest = -e.x / cx; face = 2; }
  if (cx > 0 && (stageWidth - e.x) / cx < nearest) { nearest = (stageWidth - e.x) / cx; face = 3; }
  return face;
}
// 目に向いた面を持つブロックを、8 つの角の視線が当たる面で分けた数 (全部の角が同じ 1 つの面なら、その面。それ以外は general)
function countPaths(e) {
  const counts = [0, 0, 0, 0, 0];
  for (const b of surfaceBlocks) {
    const facing = (b.mask & 1 && e.x < b.x0) || (b.mask & 2 && e.x > b.x0 + 1) || (b.mask & 4 && e.y < b.y0) || (b.mask & 8 && e.y > b.y0 + 1)
      || (b.mask & 16 && e.z < b.z0) || (b.mask & 32 && e.z > b.z0 + 1);
    if (!facing) continue;
    const first = firstFace(e, b.x0 - e.x, b.y0 - e.y, b.z0 - e.z);
    let same = first >= 0;
    for (let c = 1; c < 8 && same; c++) same = firstFace(e, b.x0 - e.x + (c & 1), b.y0 - e.y + ((c >> 1) & 1), b.z0 - e.z + ((c >> 2) & 1)) === first;
    counts[same ? first : 4]++;
  }
  return counts;
}

// --- 目: 舞台の箱の中。座標は整数でない乱数。立てたブロックの外 (正面・横・高い位置・ブロックの面のすぐ外)。
// inside を付けると、ブロックの並びの中の空気の所 (空洞の殻の中や、板・棒のすぐ横) に立つ目にする ---
function makeEyes() {
  const eyes = [];
  const centre = stageWidth / 2;
  const front = 1 + floorRows;
  // 立てたブロック全体を囲む最小の箱 (ブロックの番号の範囲)
  const lo = [width, height, depth];
  const hi = [-1, -1, -1];
  const solids = [];
  for (let p = 0; p < figure.length; p++) {
    if (figure[p] === AIR) continue;
    const c = [p % width, Math.floor(p / width) % height, Math.floor(p / (width * height))];
    solids.push(c);
    for (let a = 0; a < 3; a++) {
      lo[a] = Math.min(lo[a], c[a]);
      hi[a] = Math.max(hi[a], c[a]);
    }
  }
  // ワールドの座標 (舞台の内側) で、立てたブロックを囲む箱
  const tight = { x0: side + lo[0], x1: side + hi[0] + 1, y0: 1 + height - 1 - hi[1], y1: 1 + height - lo[1], z0: 1 + gap + lo[2], z1: 1 + gap + hi[2] + 1 };
  // projectToStage が正確な範囲の目: ブロックの外で、全部のブロックより手前 (z が大きい) にいるか、全部のブロックの左右どちらかの外
  // (目と同じ高さ・左右の位置のブロックが目より奥行き側にあると、写る範囲が前方へ果てしなく延びるのに、角の視線では
  // 求められず、すれすれの数マスが抜ける。人物の後ろや中に入った目では起きる = inside で確かめる)
  const outsideTight = (e) => !(e.x > tight.x0 && e.x < tight.x1 && e.y > tight.y0 && e.y < tight.y1 && e.z > tight.z0 && e.z < tight.z1)
    && (e.z >= tight.z1 || e.x <= tight.x0 || e.x >= tight.x1);
  const inAir = (e) => {
    const x = Math.floor(e.x) - box.x0;
    const row = height - Math.floor(e.y);
    const d = Math.floor(e.z) - box.z0;
    if (x < 0 || x >= width || row < 0 || row >= height || d < 0 || d >= depth) return true;
    return figure[at(x, row, d)] === AIR;
  };
  // 立てたブロック 1 つの面のすぐ外 (0.02〜0.95 ブロック離れた所)
  const beside = (c, axis, sign) => {
    const near = between(0.02, 0.95);
    const eye = { x: side + c[0] + between(0.05, 0.95), y: height - c[1] + between(0.05, 0.95), z: 1 + gap + c[2] + between(0.05, 0.95) };
    if (axis === 0) eye.x = side + c[0] + (sign > 0 ? 1 + near : -near);
    else if (axis === 1) eye.y = height - c[1] + (sign > 0 ? 1 + near : -near);
    else eye.z = 1 + gap + c[2] + (sign > 0 ? 1 + near : -near);
    return eye;
  };
  const pick = (list) => list[Math.floor(random() * list.length)];
  // --path: 舞台の箱の中のどこでも (人物の外の) 目を選び、目に向いた面のあるブロックのうち、指定した通り道のものが
  // 3 割以上 (general は 1 割以上) ある目だけにする
  const wide = () => ({ x: between(0.5, stageWidth - 0.5), y: between(1.3, 1 + 1.6 * height), z: between(1.3, front + height) });
  const classes = wantedPath ? [wide] : inside ? [() => beside(pick(solids), Math.floor(random() * 3), random() < 0.5 ? -1 : 1)] : [
    // 正面: 床の手前の方、人物の背の高さの中ほど
    () => ({ x: between(centre - 0.25 * stageWidth, centre + 0.25 * stageWidth), y: between(1.3, 1 + 1.1 * height), z: between(front - 2, front + height) }),
    // 横: 人物の横 (左右の壁の近く) から人物の方を見る
    () => ({ x: random() < 0.5 ? between(0.4, side - 0.4) : between(side + width + 0.4, stageWidth - 0.4), y: between(1.3, 1 + height),
      z: between(1.3, 1 + gap + depth + 0.5 * geom.front) }),
    // 高い位置: 人物の頭より高い所から見下ろす
    () => ({ x: between(centre - 0.2 * stageWidth, centre + 0.2 * stageWidth), y: between(1 + 1.1 * height, 1 + 1.5 * height),
      z: between(1.3, front + 0.5 * height) }),
    // すれすれ: いちばん上・手前・左右にあるブロックの面のすぐ外 (目に向いた面かどうかの判定が分かれる)
    () => {
      const axis = Math.floor(random() * 3);
      const sign = random() < 0.5 ? -1 : 1;
      // y は行が下向きに増えるので、「上」は行が最小のブロック
      const extreme = sign > 0 ? (axis === 1 ? lo[1] : hi[axis]) : (axis === 1 ? hi[1] : lo[axis]);
      return beside(pick(solids.filter((c) => c[axis] === extreme)), axis, axis === 1 ? -sign : sign);
    },
  ];
  const wanted = PATH_NAMES.indexOf(wantedPath);
  if (wantedPath && wanted < 0) {
    console.error(`unknown --path: ${wantedPath}`);
    process.exit(2);
  }
  for (let i = 0; eyes.length < eyeCount && i < (wantedPath ? 20000 : 100000); i++) {
    const eye = classes[i % classes.length]();
    // 座標が整数にならない値にする (整数だと視線がブロックの角をかすめて、同点の扱いで食い違う)
    if ([eye.x, eye.y, eye.z].some((v) => Number.isInteger(v * 2)) || !inAir(eye) || (!inside && !outsideTight(eye))) continue;
    // 舞台の箱の中 (projectToStage が受け付ける所)
    if (!(eye.x > 0 && eye.x < stageWidth && eye.y > 1 && eye.z > 1)) continue;
    if (wantedPath) {
      const counts = countPaths(eye);
      const total = counts.reduce((sum, n) => sum + n, 0);
      if (total === 0 || counts[wanted] < total * (wanted === 4 ? 0.1 : 0.3)) continue;
    }
    eyes.push(eye);
  }
  return eyes;
}

// --- 正解: 目からマスの中心までの線分を、人物の格子に沿って進んで最初に当たるブロックの値 (無ければ -1) ---
function castRay(ex, ey, ez, tx, ty, tz) {
  const dx = tx - ex;
  const dy = ty - ey;
  const dz = tz - ez;
  let t0 = 0;
  let t1 = 1;
  for (const [o, d, lo, hi] of [[ex, dx, box.x0, box.x1], [ey, dy, box.y0, box.y1], [ez, dz, box.z0, box.z1]]) {
    if (d === 0) {
      if (o < lo || o >= hi) return -1;
      continue;
    }
    let a = (lo - o) / d;
    let b = (hi - o) / d;
    if (a > b) [a, b] = [b, a];
    if (a > t0) t0 = a;
    if (b < t1) t1 = b;
  }
  if (t0 > t1) return -1;
  const eps = 1e-9;
  const px = ex + dx * (t0 + eps);
  const py = ey + dy * (t0 + eps);
  const pz = ez + dz * (t0 + eps);
  let ix = Math.min(box.x1 - 1, Math.max(box.x0, Math.floor(px)));
  let iy = Math.min(box.y1 - 1, Math.max(box.y0, Math.floor(py)));
  let iz = Math.min(box.z1 - 1, Math.max(box.z0, Math.floor(pz)));
  const sx = Math.sign(dx);
  const sy = Math.sign(dy);
  const sz = Math.sign(dz);
  const stepX = sx ? Math.abs(1 / dx) : Infinity;
  const stepY = sy ? Math.abs(1 / dy) : Infinity;
  const stepZ = sz ? Math.abs(1 / dz) : Infinity;
  let nextX = sx > 0 ? (ix + 1 - ex) / dx : sx < 0 ? (ix - ex) / dx : Infinity;
  let nextY = sy > 0 ? (iy + 1 - ey) / dy : sy < 0 ? (iy - ey) / dy : Infinity;
  let nextZ = sz > 0 ? (iz + 1 - ez) / dz : sz < 0 ? (iz - ez) / dz : Infinity;
  for (;;) {
    const level = figure[at(ix - side, height - iy, iz - 1 - gap)];
    if (level !== AIR) return level;
    let reached;
    if (nextX <= nextY && nextX <= nextZ) {
      reached = nextX;
      ix += sx;
      nextX += stepX;
    } else if (nextY <= nextZ) {
      reached = nextY;
      iy += sy;
      nextY += stepY;
    } else {
      reached = nextZ;
      iz += sz;
      nextZ += stepZ;
    }
    if (reached > t1) return -1;
    if (ix < box.x0 || ix >= box.x1 || iy < box.y0 || iy >= box.y1 || iz < box.z0 || iz >= box.z1) return -1;
  }
}

// 盤面の p 番目のマスの中心 (舞台の内側の座標。projectToStage が視線を通すのと同じ点)
function cellCentre(p, out) {
  const row = Math.floor(p / rowWidth);
  const column = p % rowWidth;
  if (row < wallHeight) {
    out[0] = column + 0.5;
    out[1] = wallHeight - row + 0.5;
    out[2] = 1;
  } else if (row < wallHeight + floorRows) {
    out[0] = column + 0.5;
    out[1] = 1;
    out[2] = 1.5 + (row - wallHeight);
  } else {
    const r = (row - wallHeight - floorRows) % wallHeight;
    out[0] = row < 2 * wallHeight + floorRows ? 0 : stageWidth;
    out[1] = wallHeight - r + 0.5;
    out[2] = 1.5 + column;
  }
}

const work = createStageWork(geom, WALL, FLOOR, SIDE);
const board = new Int16Array(geom.cells);
const point = [0, 0, 0];
const eyes = makeEyes();
let checked = 0;
let painted = 0;
let mismatchCount = 0;
let surfaces = 0;
const paths = Object.fromEntries(PATH_NAMES.map((name) => [name, 0]));
const mismatches = [];
for (const eye of eyes) {
  work.dirty = true;
  countPaths(eye).forEach((n, k) => { paths[PATH_NAMES[k]] += n; });
  if (!projectToStage(projected, video, geom, eye, board, work)) {
    console.error(`eye refused: ${JSON.stringify(eye)}`);
    process.exit(3);
  }
  for (let p = 0; p < geom.cells; p++) {
    if (isStagePadding(geom, p)) continue;
    cellCentre(p, point);
    const hit = castRay(eye.x, eye.y, eye.z, point[0], point[1], point[2]);
    const expected = hit >= 0 ? hit : work.background[p];
    checked++;
    if (hit >= 0) painted++;
    if (board[p] !== expected) {
      mismatchCount++;
      if (mismatches.length < 8) mismatches.push({ eye, p, row: Math.floor(p / rowWidth), column: p % rowWidth, got: board[p], expected });
    }
  }
  surfaces += work.surfaceCount;
}
process.stdout.write(JSON.stringify({
  shape, dims, inside, voxels: figure.reduce((n, v) => n + (v !== AIR ? 1 : 0), 0), eyes: eyes.length, checked, painted, mismatchCount, mismatches,
  surfaceBlocks: surfaceBlocks.length, surfaces, paths,
}));
