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
// だまし絵 (立体を、見ている人の目に合わせて床と壁に描く) は立体のときだけ。平面スクリーンには使わない
function viewArgs(s, threeD) {
  return s.anamorphic && threeD ? ["--view", "anamorphic"] : [];
}

function voxArgs(s, voxPath, out, lang) {
  return ["--input-vox", voxPath, "--mode", "vox", "--output", out, "--fps", String(s.fps), "--palette", s.palette,
          "--device", "cpu", "--threads", "1", "--lang", lang, ...viewArgs(s, true)];
}

// だまし絵にする、動画から作る立体の長い辺の上限 (Windows 版の STAGE_MAX_SIDE と同じ。舞台と背景の映像が立体に
// 比例して広がり、重くなる)
const STAGE_MAX_SIDE = 80;

function figureSize(s) {
  if (!s.anamorphic || s.mode === "screen") return [s.width, s.height];
  const scale = Math.min(1, STAGE_MAX_SIDE / Math.max(s.width, s.height, 1));
  return [Math.max(1, Math.round(s.width * scale)), Math.max(1, Math.round(s.height * scale))];
}

function convertArgs(s, framesDir, out, lang) {
  const [width, height] = figureSize(s);
  return ["--frames-dir", framesDir, "--output", out, "--width", String(width), "--height", String(height),
          "--fps", String(s.fps), "--palette", s.palette, "--dither-method", s.dither, "--device", "cpu",
          "--threads", "1", "--mode", s.mode, "--depth", String(s.depth), "--lang", lang,
          ...viewArgs(s, s.mode !== "screen")];
}

// だまし絵の背景の映像を、後ろの壁の大きさ (人物の大きさから mvcodec/stage.py で求める) の平面の動画に変換する
function convertBackground(pyodide, s, background, id, figureJs, lang) {
  log(`  背景の映像 / background video: ${background.title}`);
  const dims = pyodide.globals.get("pack_builder").frame_data_dims(figureJs).toJs();
  const geom = pyodide.pyimport("mvcodec.stage").stage_geometry(dims.get("width"), dims.get("height"), dims.get("depth")).toJs();
  const width = geom.get("stageWidth");
  const height = geom.get("wallHeight");
  const dir = `/work/${id}`;
  try { pyodide.FS.mkdirTree(dir); } catch (e) { /* 既にある */ }
  background.frames.forEach((ppm, i) => pyodide.FS.writeFile(`${dir}/frame_${String(i).padStart(6, "0")}.ppm`, ppm));
  const out = `/work/frames_${id}.js`;
  const args = [...convertArgs({ ...s, mode: "screen", width, height, anamorphic: false }, dir, out, lang), "--fit", "cover"];
  const code = pyodide.globals.get("convert").main(pyodide.toPy(args));
  if (code !== 0) throw new Error(`convert.py exit ${code}`);
  for (const name of pyodide.FS.readdir(dir)) if (/\.ppm$/.test(name)) pyodide.FS.unlink(`${dir}/${name}`);
  return { id, title: background.title, frames_js: pyodide.FS.readFile(out, { encoding: "utf8" }), thumbnail: null,
           sounds: background.sounds, width, height, hidden: true };
}

self.onmessage = async (event) => {
  const { settings: s, jobs, background, lang } = event.data;
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
      // だまし絵の背景の映像 (立体のときだけ。人物の動画 ID + "_bg" の、一覧に出さない平面の動画にする)
      const backgroundId = background && s.anamorphic && (job.vox || s.mode !== "screen") ? `${job.id}_bg` : null;
      if (backgroundId) args.push("--stage-background", backgroundId);
      // 変換の進み具合 (数秒ごと) に合わせてバーを動かす (この動画の 40%〜95% の区間)
      const convertModule = pyodide.globals.get("convert");
      convertModule.PROGRESS_HOOK = (done, total) => {
        post("progress", { value: (n + 0.4 + 0.55 * Math.min(1, done / total)) / jobs.length });
      };
      const code = convertModule.main(pyodide.toPy(args));
      convertModule.PROGRESS_HOOK = null;
      if (code !== 0) throw new Error(`convert.py exit ${code}`);
      for (const name of pyodide.FS.readdir(dir)) if (/\.(ppm|vox)$/.test(name)) pyodide.FS.unlink(`${dir}/${name}`);
      const framesJs = pyodide.FS.readFile(out, { encoding: "utf8" });
      videos.push({ id: job.id, title: job.title, frames_js: framesJs,
                    thumbnail: job.thumbnail, sounds: job.sounds, width: figureSize(s)[0], height: figureSize(s)[1] });
      if (backgroundId) videos.push(convertBackground(pyodide, s, background, backgroundId, framesJs, lang));
      post("progress", { value: (n + 0.95) / jobs.length });
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
    v["thumbnail"] = bytes(v["thumbnail"]) if v.get("thumbnail") is not None else None
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
