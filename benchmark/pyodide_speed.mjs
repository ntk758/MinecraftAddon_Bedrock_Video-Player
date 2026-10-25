// ブラウザ版 (web/) と同じ Pyodide で、変換にかかる時間と、時間のかかる関数を測る (Phase 11.2)。
// 同じコマを手元の CPython で変換した時間 (benchmark/pyodide_speed_native.py) と比べる。
//
// 使い方:
//   python scripts/build_web.py --out web_dist
//   ffmpeg -i clip.mp4 -vf "fps=10,scale=128:72" frames/f%04d.ppm
//   npm install --no-save pyodide@0.26.4
//   node benchmark/pyodide_speed.mjs web_dist frames [--mode screen] [--dither blue_noise] [--profile]
import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const [site, framesDir, ...rest] = process.argv.slice(2);
const option = (name, fallback) => {
  const i = rest.indexOf(name);
  return i >= 0 ? rest[i + 1] : fallback;
};
const mode = option("--mode", "screen");
const dither = option("--dither", "blue_noise");
const profile = rest.includes("--profile");

let t0 = performance.now();
const pyodide = await loadPyodide();
await pyodide.loadPackage(["numpy", "pillow"]);
for (const path of JSON.parse(readFileSync(join(site, "files.json"), "utf8"))) {
  pyodide.FS.mkdirTree("/app/" + path.split("/").slice(0, -1).join("/"));
  pyodide.FS.writeFile("/app/" + path, readFileSync(join(site, "app", path)));
}
pyodide.runPython(`
import sys
sys.path.insert(0, "/app")
import convert, pack_builder  # noqa: F401
`);
const setup = (performance.now() - t0) / 1000;

const names = readdirSync(framesDir).filter((n) => n.endsWith(".ppm")).sort();
pyodide.FS.mkdirTree("/work/frames");
for (const name of names) pyodide.FS.writeFile(`/work/frames/${name}`, readFileSync(join(framesDir, name)));
const first = readFileSync(join(framesDir, names[0]), "latin1").split(/\s+/);
const [width, height] = [Number(first[1]), Number(first[2])];

const args = ["--frames-dir", "/work/frames", "--output", "/work/out.js", "--width", String(width), "--height",
              String(height), "--fps", "10", "--palette", "auto", "--dither-method", dither, "--device", "cpu",
              "--threads", "1", "--mode", mode, "--depth", "16"];
pyodide.globals.set("ARGS", pyodide.toPy(args));
t0 = performance.now();
const report = pyodide.runPython(`
import cProfile, io, pstats
import convert
profiler = cProfile.Profile() if ${profile ? "True" : "False"} else None
if profiler:
    profiler.enable()
code = convert.main(list(ARGS))
if profiler:
    profiler.disable()
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats("tottime").print_stats(25)
    text = out.getvalue()
else:
    text = ""
assert code == 0
text
`);
const seconds = (performance.now() - t0) / 1000;
console.log(JSON.stringify({ setup_s: +setup.toFixed(1), frames: names.length, width, height, mode, dither,
                             convert_s: +seconds.toFixed(2), ms_per_frame: +(1000 * seconds / names.length).toFixed(1) }));
if (profile) console.log(report);
