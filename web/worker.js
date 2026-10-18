// Block Video Player Web Converter (worker)
// Pyodide (ブラウザの中の Python) に、Windows 版と同じ convert.py / mvcodec / pack_builder.py を読み込み、
// 変換とアドオンの組み立てを行う。重い処理なので画面とは別のスレッド (Web Worker) で動かす。
"use strict";

const PYODIDE_VERSION = "0.26.4";
importScripts(`https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`);

const post = (type, extra, transfer = []) => self.postMessage({ type, ...extra }, transfer);
const log = (text) => post("log", { text });

async function setup() {
  log("Python を読み込み中 / Loading Python…");
  const pyodide = await loadPyodide({ stdout: log, stderr: log });
  await pyodide.loadPackage(["numpy", "pillow"]);
  // files.json は scripts/build_web.py が作る (サイトに置いた Python のソースと翻訳の一覧)
  // 公開した版を更新したときに古いソースを使わないよう、毎回サーバーに確かめる
  const files = await (await fetch("files.json", { cache: "no-cache" })).json();
  for (const path of files) {
    const dir = "/app/" + path.split("/").slice(0, -1).join("/");
    pyodide.FS.mkdirTree(dir);
    pyodide.FS.writeFile("/app/" + path, new Uint8Array(await (await fetch("app/" + path, { cache: "no-cache" })).arrayBuffer()));
  }
  pyodide.runPython(`
import sys
sys.path.insert(0, "/app")
import convert, pack_builder  # noqa: F401
`);
  log("準備完了 / Ready");
  return pyodide;
}

const ready = setup();

// MagicaVoxel の立体アニメ: 大きさは .vox のまま、色は全ブロック (自動のとき) から一番近いものを選ぶ
function voxArgs(s, voxPath, out, lang) {
  return ["--input-vox", voxPath, "--mode", "vox", "--output", out, "--fps", String(s.fps), "--palette", s.palette,
          "--device", "cpu", "--threads", "1", "--lang", lang];
}

function convertArgs(s, framesDir, out, lang) {
  return ["--frames-dir", framesDir, "--output", out, "--width", String(s.width), "--height", String(s.height),
          "--fps", String(s.fps), "--palette", s.palette, "--dither-method", s.dither, "--device", "cpu",
          "--threads", "1", "--mode", s.mode, "--depth", String(s.depth), "--lang", lang];
}

self.onmessage = async (event) => {
  const { settings: s, jobs, lang } = event.data;
  try {
    const pyodide = await ready;
    const videos = [];
    for (const [n, job] of jobs.entries()) {
      const dir = `/work/${job.id}`;
      try { pyodide.FS.mkdirTree(dir); } catch (e) { /* 既にある */ }
      job.frames.forEach((ppm, i) => pyodide.FS.writeFile(`${dir}/frame_${String(i).padStart(6, "0")}.ppm`, ppm));
      log(`[${n + 1}/${jobs.length}] ${job.title}: ブロックに変換中 / converting to blocks…`);
      post("progress", { value: (n + 0.4) / jobs.length });
      const out = `/work/frames_${job.id}.js`;
      let args = convertArgs(s, dir, out, lang);
      if (job.vox) {
        const voxPath = `${dir}/${job.id}.vox`;
        pyodide.FS.writeFile(voxPath, job.vox);
        args = voxArgs(s, voxPath, out, lang);
        const thumb = pyodide.globals.get("pack_builder").vox_thumbnail_png(voxPath, 256, s.palette);
        job.thumbnail = thumb.toJs();
        thumb.destroy();
      }
      const code = pyodide.globals.get("convert").main(pyodide.toPy(args));
      if (code !== 0) throw new Error(`convert.py exit ${code}`);
      for (const name of pyodide.FS.readdir(dir)) if (/\.(ppm|vox)$/.test(name)) pyodide.FS.unlink(`${dir}/${name}`);
      videos.push({ id: job.id, title: job.title, frames_js: pyodide.FS.readFile(out, { encoding: "utf8" }),
                    thumbnail: job.thumbnail, sounds: job.sounds, width: s.width, height: s.height });
      post("progress", { value: (n + 1) / jobs.length * 0.95 });
    }
    log("アドオンを組み立て中 / Assembling the add-on…");
    pyodide.globals.set("js_videos", pyodide.toPy(videos));
    pyodide.globals.set("pack_name", s.packName);
    pyodide.globals.set("namespace", s.namespace);
    const result = pyodide.runPython(`
from mvcodec.decode import iter_decoded_frames, load_frame_data
import json
videos = []
for v in js_videos:
    v = dict(v)
    v["thumbnail"] = bytes(v["thumbnail"])
    v["sounds"] = [(int(i), str(ext), bytes(data)) for i, ext, data in v["sounds"]]
    videos.append(v)
    # 確認: 変換したデータを Python の参照デコーダで最後まで復元できること
    text = v["frames_js"]
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    decoded = sum(1 for _ in iter_decoded_frames(data))
    print(f"  {v['id']}: {decoded} frames, {len(text) // 1024} KB, {len(v['sounds'])} audio chunks")
pack_builder.build_addon_bytes(videos, pack_name, namespace)
`);
    const addon = result.toJs();
    result.destroy();
    post("done", { addon }, [addon.buffer]);
  } catch (e) {
    post("error", { text: String(e && e.message ? e.message : e) });
  }
};
