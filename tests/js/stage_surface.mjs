// codec.js の projectToStage が最初に作る「外から見える面を持つブロックの一覧」(work.surface・faces) が、ブロックを 1 つずつ
// 調べた正解と同じであることを確かめる (tests/test_stage_exact.py から実行)。
// 一覧を作る処理は、空気だけの 4 ブロックを Float64 の 1 回の比較で読み飛ばす近道を使う。近道は、図形の置き場所
// (8 バイトの境目にあるか)・空気の番号 (0 や、4 つ並べると NaN になる番号)・NaN になるビット列を含むブロックの並び・
// ブロック数が 4 の倍数でない大きさでも、1 つずつ調べたときと同じ一覧になる。
// 使い方: node stage_surface.mjs。結果は JSON で標準出力へ書く (cases = 調べた組み合わせの数、mismatches = 食い違い)。
import { createStageWork, projectToStage, stageGeometry } from "./codec.js";

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
const random = mulberry32(12345);

// 正解: ブロックを 1 つずつ、6 方向の隣が空気か立体の外かを調べる (p の昇順)
function reference(figure, air, width, height, depth) {
  const slice = width * height;
  const surface = [];
  const faces = [];
  for (let p = 0; p < width * height * depth; p++) {
    const level = figure[p];
    if (level === air || level < 0) continue;
    const x = p % width;
    const row = Math.floor(p / width) % height;
    const d = Math.floor(p / slice);
    const mask = (x === 0 || figure[p - 1] === air ? 1 : 0) | (x + 1 === width || figure[p + 1] === air ? 2 : 0)
      | (row + 1 === height || figure[p + width] === air ? 4 : 0) | (row === 0 || figure[p - width] === air ? 8 : 0)
      | (d === 0 || figure[p - slice] === air ? 16 : 0) | (d + 1 === depth || figure[p + slice] === air ? 32 : 0);
    if (mask === 0) continue;
    surface.push(p);
    faces.push(mask);
  }
  return { surface, faces };
}

// ブロックの番号: 色 (0〜19)、負の数 (-1 と -32768 は 4 つ並ぶと -0 や NaN のビット列になりうる)、32760 以上 (NaN のビット列)、空気
const ODD_LEVELS = [-1, -32768, 32760, 32767, 20000, 3, 0];

const sizes = [[5, 7, 3], [8, 9, 4], [6, 6, 6], [7, 5, 9], [13, 11, 5]];
// 0 は空気 4 つ分が +0、32760 と -1 は NaN のビット列になる空気の番号
const airs = [127, 0, 5, 20000, 32760, -1];
const densities = [0, 0.02, 0.3, 1];
let cases = 0;
const mismatches = [];
for (const [width, height, depth] of sizes) {
  const total = width * height * depth;
  const geom = stageGeometry({ width, height, depth, air_level: 0 });
  const eye = { x: geom.stageWidth / 2 + 0.31, y: 1 + height * 0.6 + 0.37, z: 1 + geom.floorRows + 5.21 };
  const board = new Int16Array(geom.cells);
  // 同じ作業領域と同じ配列 (buffer) を、空気の番号・置き場所 (offset)・立体を変えて使い回す。見る配列と空気 4 つ分の値を
  // 作り直すのは、buffer・置き場所・空気の番号が変わったときだけでよいことも確かめる
  const work = createStageWork(geom, 30000, 30001, 30002);
  const backing = new Int16Array(total + 8);
  for (const [airIndex, air] of airs.entries()) {
    const video = { width, height, depth, air_level: air };
    // 空気の番号が変わった直後は、前の番号の最後と同じ置き場所から始める (置き場所が同じでも、空気の番号で作り直すこと)
    const offsets = airIndex % 2 ? [4, 3, 2, 1, 0] : [0, 1, 2, 3, 4];
    for (const offset of offsets) {
      for (const density of densities) {
        for (let repeat = 0; repeat < 2; repeat++) {
          backing.fill(air);
          const figure = backing.subarray(offset, offset + total);
          for (let p = 0; p < total; p++) {
            if (random() >= density) continue;
            const r = random();
            figure[p] = r < 0.5 ? Math.floor(random() * 20) : r < 0.8 ? ODD_LEVELS[Math.floor(random() * ODD_LEVELS.length)] : air;
          }
          // 4 つ全部が同じ値のブロックの並びと、空気の中に 1 つだけ変わった値が最後にある並び (NaN や -0 のビット列) を、
          // 4 ブロックの区切りに合わせて混ぜる
          for (let g = 0; g < 3; g++) {
            const start = 4 * Math.floor(random() * Math.floor(total / 4));
            const level = ODD_LEVELS[Math.floor(random() * ODD_LEVELS.length)];
            figure.fill(level, start, start + 4);
          }
          for (const last of [-32768, -1, 32767, 32760, 1]) {
            const start = 4 * Math.floor(random() * Math.floor(total / 4));
            figure.fill(air, start, start + 3);
            figure[start + 3] = last;
          }
          work.dirty = true;
          if (!projectToStage(figure, video, geom, eye, board, work)) throw new Error("eye refused");
          const expected = reference(figure, air, width, height, depth);
          const got = { surface: Array.from(work.surface.subarray(0, work.surfaceCount)), faces: Array.from(work.faces.subarray(0, work.surfaceCount)) };
          cases++;
          if (JSON.stringify(got) !== JSON.stringify(expected)) {
            mismatches.push({ size: [width, height, depth], air, offset, density, repeat, got: got.surface.length, expected: expected.surface.length });
          }
        }
      }
    }
  }
}
process.stdout.write(JSON.stringify({ cases, mismatchCount: mismatches.length, mismatches: mismatches.slice(0, 8) }));
