// だまし絵 (codec.js の projectToStage) を確かめる (tests/test_anamorphic.py から実行)。
// 立体の人物を壁と床へ写し、同じ目の位置のカメラで「壁と床を見た絵」と「人物を直接見た絵」を描いて比べる。
// 使い方: node stage_view.mjs '<目の位置の JSON 配列 [{x, y, z}, ...]>'  (座標は舞台の原点から)
import { projectToStage, stageCellOffset, stageGeometry } from "./codec.js";

const AIR = 127;
const WALL = 30000;
const FLOOR = 30001;
const video = { width: 24, height: 40, depth: 8, air_level: AIR };

// 人物: 胴 (円柱)、頭 (球)、右に伸ばした腕。高さごとに色を変え、写った位置のずれが色の違いで分かるようにする
function makeFigure() {
  const { width, height, depth } = video;
  const figure = new Int16Array(width * height * depth).fill(AIR);
  const cx = width / 2;
  const cz = depth / 2;
  for (let d = 0; d < depth; d++) {
    for (let row = 0; row < height; row++) {
      const y = height - row; // 下から何段目か
      for (let x = 0; x < width; x++) {
        const dx = x + 0.5 - cx;
        const dz = d + 0.5 - cz;
        let solid = false;
        if (y <= 28 && dx * dx + dz * dz <= 9) solid = true; // 胴
        const hy = y - 33;
        if (dx * dx + hy * hy + dz * dz <= 20) solid = true; // 頭
        if (y >= 22 && y <= 24 && x >= cx && Math.abs(dz) <= 1.5) solid = true; // 腕
        if (solid) figure[(d * height + row) * width + x] = (Math.floor(y / 5) * 3 + Math.floor(x / 6)) % 20;
      }
    }
  }
  return figure;
}

// 3D DDA で、目から向き dir の視線が最初に当たる人物のブロックの色 (当たらなければ -1)
function castFigure(figure, geom, eye, dir) {
  const { width, height, depth, side, gap } = geom;
  // 人物の箱: x は side〜side + width、y は 1〜1 + height、z は 1 + gap〜1 + gap + depth
  const lo = [side, 1, 1 + gap];
  const hi = [side + width, 1 + height, 1 + gap + depth];
  const o = [eye.x, eye.y, eye.z];
  let t0 = 0;
  let t1 = Infinity;
  for (let a = 0; a < 3; a++) {
    let u = (lo[a] - o[a]) / dir[a];
    let v = (hi[a] - o[a]) / dir[a];
    if (u > v) [u, v] = [v, u];
    t0 = Math.max(t0, u);
    t1 = Math.min(t1, v);
  }
  if (!(t0 <= t1)) return -1;
  const start = o.map((value, a) => value + dir[a] * (t0 + 1e-6));
  const cell = start.map((value, a) => Math.min(hi[a] - lo[a] - 1, Math.max(0, Math.floor(value - lo[a]))));
  const step = dir.map((value) => (value > 0 ? 1 : -1));
  const next = cell.map((c, a) => (lo[a] + c + (step[a] > 0 ? 1 : 0) - o[a]) / dir[a]);
  const delta = dir.map((value) => Math.abs(1 / value));
  const size = [width, height, depth];
  for (let n = 0; n < 400; n++) {
    if (cell.some((c, a) => c < 0 || c >= size[a])) return -1;
    const row = height - 1 - cell[1];
    const level = figure[(cell[2] * height + row) * width + cell[0]];
    if (level !== AIR) return level;
    const a = next[0] < next[1] ? (next[0] < next[2] ? 0 : 2) : (next[1] < next[2] ? 1 : 2);
    cell[a] += step[a];
    next[a] += delta[a];
  }
  return -1;
}

// 目から向き dir の視線が当たる壁か床のマスの値 (舞台の外なら -1)
function castStage(board, geom, eye, dir) {
  const { stageWidth, wallHeight, floorRows } = geom;
  const tFloor = dir[1] < 0 ? (1 - eye.y) / dir[1] : Infinity;
  const tWall = dir[2] < 0 ? (1 - eye.z) / dir[2] : Infinity;
  const t = Math.min(tFloor, tWall);
  if (t === Infinity) return -1;
  const x = eye.x + dir[0] * t;
  if (x < 0 || x >= stageWidth) return -1;
  if (tFloor < tWall) {
    const k = Math.floor(eye.z + dir[2] * t - 1);
    if (k < 0 || k >= floorRows) return -1;
    return board[(wallHeight + k) * stageWidth + Math.floor(x)];
  }
  const y = eye.y + dir[1] * t;
  const r = wallHeight - Math.floor(y);
  if (r < 0 || r >= wallHeight) return -1;
  return board[r * stageWidth + Math.floor(x)];
}

const eyes = JSON.parse(process.argv[2]);
const geom = stageGeometry(video);
const figure = makeFigure();
const board = new Int16Array(geom.cells);
const zbuf = new Float32Array(geom.cells);
const results = [];
for (const eye of eyes) {
  const started = performance.now();
  const drawn = projectToStage(figure, video, geom, eye, board, zbuf, WALL, FLOOR);
  const ms = performance.now() - started;
  // カメラは人物の中心を向く
  const target = [geom.side + video.width / 2, 1 + video.height / 2, 1 + geom.gap + video.depth / 2];
  const forward = target.map((value, a) => value - [eye.x, eye.y, eye.z][a]);
  const norm = (v) => {
    const l = Math.hypot(...v);
    return v.map((value) => value / l);
  };
  const f = norm(forward);
  const right = norm([f[2], 0, -f[0]].map((v) => -v));
  const up = [right[1] * f[2] - right[2] * f[1], right[2] * f[0] - right[0] * f[2], right[0] * f[1] - right[1] * f[0]];
  const size = 120;
  const fov = Math.tan((50 * Math.PI) / 360);
  let figurePixels = 0;
  let matched = 0;
  let wrongColour = 0;
  let missing = 0;
  let extra = 0;
  let offStage = 0;
  const image = process.argv.includes("images") ? { direct: [], stage: [] } : null;
  for (let j = 0; j < size; j++) {
    for (let i = 0; i < size; i++) {
      const u = ((i + 0.5) / size * 2 - 1) * fov;
      const v = (1 - (j + 0.5) / size * 2) * fov;
      const dir = norm(f.map((value, a) => value + right[a] * u + up[a] * v));
      const direct = castFigure(figure, geom, eye, dir);
      const seen = castStage(board, geom, eye, dir);
      const seenFigure = seen >= 0 && seen !== WALL && seen !== FLOOR;
      if (image) {
        image.direct.push(direct);
        image.stage.push(seen);
      }
      if (direct >= 0) {
        figurePixels++;
        if (seen < 0) offStage++;
        else if (!seenFigure) missing++;
        else if (seen === direct) matched++;
        else wrongColour++;
      } else if (seenFigure) {
        extra++;
      }
    }
  }
  results.push({ eye, drawn, ms, figurePixels, matched, wrongColour, missing, extra, offStage, image, size });
}
// 盤面の位置の規則も返す (壁の左上・床の手前の右端)
const corner = (p) => stageCellOffset(geom, p, { x: 0, y: 0, z: 0 });
const stageBoard = process.argv.includes("images") ? [...board] : null;
process.stdout.write(JSON.stringify({ geom, stageBoard, results, wallTopLeft: corner(0), floorLast: corner(geom.cells - 1) }));
