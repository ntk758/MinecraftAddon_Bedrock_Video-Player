// codec.js の projectToStage で、立体 (人物) を目の位置から壁と床へ写した盤面を求める (tests/test_anamorphic.py から実行)。
// 標準入力: {"video": {width, height, depth, air_level}, "figure": [...], "eye": {x, y, z}, "wall": n, "floor": n}
import { readFileSync } from "node:fs";
import { projectToStage, stageGeometry } from "./codec.js";

const input = JSON.parse(readFileSync(0, "utf-8"));
const geom = stageGeometry(input.video);
const board = new Int16Array(geom.cells);
const drawn = projectToStage(Int16Array.from(input.figure), input.video, geom, input.eye, board,
  new Float32Array(geom.cells), input.wall, input.floor);
process.stdout.write(JSON.stringify({ drawn, geom, board: [...board] }));
