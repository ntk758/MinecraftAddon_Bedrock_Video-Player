// Block & Image Video Player Web Converter (worker)
// Pyodide (ブラウザの中の Python) に、Windows 版と同じ convert.py / mvcodec / pack_builder.py を読み込み、
// 変換とアドオンの組み立てを行う。重い処理なので画面とは別のスレッド (Web Worker) で動かす。
"use strict";

const PYODIDE_VERSION = "0.26.4";
// Pyodide は、公開したサイトに置いたもの (scripts/fetch_pyodide.py が SHA-256 を確かめて pyodide/ に置く) を使う。
// 以前は CDN から読み込んでいて、「動画は端末から出ない」ことが CDN の中身に頼っていた (#175)。
// サイトに無いとき (手元で scripts/build_web.py だけを実行したとき) だけ、CDN から読み込む
let PYODIDE_URL = "pyodide/";
try {
  importScripts(`${PYODIDE_URL}pyodide.js`);
} catch (e) {
  PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
  importScripts(`${PYODIDE_URL}pyodide.js`);
}

const post = (type, extra, transfer = []) => self.postMessage({ type, ...extra }, transfer);
const log = (text) => post("log", { text });
// 画面の言語で出す文は、キー (locales/*.json の web.*) を送って画面 (app.js) で翻訳する
const say = (key, params, indent = "") => post("log", { key, params, indent });

async function setup() {
  say("web.loading_python");
  if (PYODIDE_URL.startsWith("https:")) log(`Pyodide: ${PYODIDE_URL} (not hosted with this site)`);
  const pyodide = await loadPyodide({ indexURL: PYODIDE_URL, stdout: log, stderr: log });
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
  say("web.ready");
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
// 立体版と平面版をセットで入れるときの、平面版の動画 ID の末尾 (pack_builder.FLAT_PAIR_SUFFIX と同じ)
const FLAT_PAIR_SUFFIX = "_flat";
// 画像モードのアドオンのコマンドの接頭辞の末尾 (video_player_gui.py の IMAGE_NAMESPACE_SUFFIX と同じ。ブロックモードと
// 同じ接頭辞だと、ゲームに登録するコマンドの名前がぶつかる)
const IMAGE_NAMESPACE_SUFFIX = "_img";
// 画像モードの音声の区切り (web/app.js の IMAGE_AUDIO_CHUNK_SECONDS と同じ)
const IMAGE_AUDIO_CHUNK_SECONDS = 4;
// 画面の「平らな面のムラを減らす」で渡す --flat-areas の値 (video_player_gui.py の FLAT_AREAS_CHOICE と同じ)
const FLAT_AREAS_CHOICE = "smooth";

function figureSize(s) {
  if (!s.anamorphic || s.mode === "screen") return [s.width, s.height];
  const scale = Math.min(1, STAGE_MAX_SIDE / Math.max(s.width, s.height, 1));
  return [Math.max(1, Math.round(s.width * scale)), Math.max(1, Math.round(s.height * scale))];
}

function convertArgs(s, framesDir, out, lang) {
  const [width, height] = figureSize(s);
  return ["--frames-dir", framesDir, "--output", out, "--width", String(width), "--height", String(height),
          "--fps", String(s.fps), "--palette", s.palette, "--dither-method", s.dither, "--device", "cpu",
          "--keyframe-interval", String(s.keyframe), ...(s.perceptual ? [] : ["--no-perceptual"]),
          // 平らな面のムラを減らす (Windows 版の FLAT_AREAS_CHOICE と同じ値)
          ...(s.flatAreas ? ["--flat-areas", FLAT_AREAS_CHOICE] : []),
          "--threads", "1", "--mode", s.mode, "--depth", String(s.depth), "--lang", lang,
          ...viewArgs(s, s.mode !== "screen")];
}

// だまし絵の背景の映像を、後ろの壁の大きさ (人物の大きさから mvcodec/stage.py で求める) の平面の動画に変換する
function convertBackground(pyodide, s, background, id, figureJs, lang) {
  say("web.background", { name: background.title }, "  ");
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

// MMD: モデルのフォルダのファイルと踊りを書き、Windows 版と同じ mvcodec/mmd.py で .vox の立体アニメにする
function mmdToVox(pyodide, s, job, dir) {
  const base = `${dir}/mmd`;
  for (const [path, bytes] of job.mmd.files) {
    const target = `${base}/${path}`;
    pyodide.FS.mkdirTree(target.split("/").slice(0, -1).join("/"));
    pyodide.FS.writeFile(target, bytes);
  }
  const motion = `${dir}/motion.vmd`;
  pyodide.FS.writeFile(motion, job.mmd.motion);
  const voxPath = `${dir}/${job.id}.vox`;
  say("build.mmd", { height: job.mmd.height }, "");
  pyodide.globals.set("mmd_args", pyodide.toPy({
    pmx_path: `${base}/${job.mmd.pmx}`, vmd_path: motion, output: voxPath, height: job.mmd.height, fps: s.fps,
    duration: s.duration > 0 ? s.duration : null,
  }));
  pyodide.globals.set("mmd_progress", (done, total) => {
    if (done % 50 === 0 || done === total) say("build.mmd_progress", { done, total }, "");
  });
  pyodide.runPython(`
from mvcodec.mmd import mmd_to_vox
mmd_to_vox(**dict(mmd_args), progress=mmd_progress)
`);
  return voxPath;
}

// 画像モード (convert_image_mode.py の build_files) のアドオンを、ブロックモードと同じコマ (PPM) と音声から作る。
// ブラウザには ffmpeg が無いので、コマは Pillow で読む。.vox と MMD は元の映像のコマが無いので使わない。
// 画像モードは Minecraft 1.26.10 以降だけ (古い版向けを選んでいるときは作らない)。作れなければ null
function buildImageAddon(pyodide, s, jobs) {
  if (s.target === "legacy") {
    say("web.image_failed", { error: "Minecraft 1.26.10+" });
    return null;
  }
  const usable = [];
  for (const job of jobs) {
    if (job.vox || job.mmd || !job.frames.length) say("web.image_skipped", { id: job.id });
    else usable.push(job);
  }
  // 画像モード用の 4 秒ごとの音声を、音声のある動画すべてで作れたときだけ使う (区切りの長さはパックで 1 つ。
  // 1 本でも作れなければ、全部ブロックモードと同じ 10 秒ごと)
  const fine = usable.every((job) => !job.sounds.length || job.imageSounds?.length);
  const chunkSeconds = fine ? IMAGE_AUDIO_CHUNK_SECONDS : 10;
  for (let i = 0; i < usable.length; i++) {
    const job = usable[i];
    usable[i] = { id: job.id, title: job.title, frames: job.frames, sounds: fine ? (job.imageSounds ?? []) : job.sounds };
  }
  if (!usable.length) return null;
  try {
    pyodide.globals.set("img_jobs", usable);
    pyodide.globals.set("img_chunk_seconds", chunkSeconds);
    pyodide.globals.set("img_settings", pyodide.toPy({ fps: s.fps, packName: s.packName,
                                                      namespace: s.namespace + IMAGE_NAMESPACE_SUFFIX,
                                                      lighting: s.imageLighting || "bright",
                                                      pngOptimize: !!s.imagePngOptimize }));
    const proxy = pyodide.runPython(`
import io
import numpy as np
from PIL import Image
import convert_image_mode as cim

def _frames(job):
    for ppm in job.frames:
        yield np.asarray(Image.open(io.BytesIO(ppm.to_bytes())).convert("RGB"))

_s = img_settings.to_py() if hasattr(img_settings, "to_py") else dict(img_settings)
_first = np.asarray(Image.open(io.BytesIO(img_jobs[0].frames[0].to_bytes())))
_sources = []
for _job in img_jobs:
    _sounds = [(int(i), str(ext), data.to_bytes()) for i, ext, data in _job.sounds]
    _sources.append({"id": str(_job.id), "title": str(_job.title), "fps": float(_s["fps"]), "frames": _frames(_job),
                     "audio": (lambda seconds, _sounds=_sounds: _sounds)})
_files, _report = cim.build_files(_sources, width=int(_first.shape[1]), height=int(_first.shape[0]), methods=["a"],
                                  namespace=str(_s["namespace"]), pack_name=str(_s["packName"]) + " (Image)",
                                  audio_chunk_seconds=float(img_chunk_seconds),
                                  log=lambda _text: None, lighting=str(_s["lighting"]),
                                  png_optimize=bool(_s["pngOptimize"]))
cim.zip_bytes(_files)
`);
    const bytes = proxy.toJs();
    proxy.destroy();
    return bytes;
  } catch (e) {
    say("web.image_failed", { error: String(e && e.message ? e.message : e) });
    return null;
  }
}

/** 作業フォルダのコマ (.ppm) と立体 (.vox) を消す。失敗した前の作成のコマが残り、次の作成に混ざっていた (#171・#203)。 */
function clearWorkDir(pyodide, dir) {
  let names = [];
  try { names = pyodide.FS.readdir(dir); } catch (e) { return; }
  for (const name of names) if (/\.(ppm|vox)$/.test(name)) pyodide.FS.unlink(`${dir}/${name}`);
}

/** Python のトレースバックは最後の 1 行にする (全文はログへ。#173)。 */
function errorText(e) {
  const text = String(e && e.message ? e.message : e);
  if (!/Traceback \(most recent call last\)/.test(text)) return text;
  log(text);
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  return (lines[lines.length - 1] || text).replace(/^[A-Za-z_.]*(Error|Exception): /, "");
}

self.onmessage = async (event) => {
  const { settings: s, jobs, background, lang } = event.data;
  // 画面の側で読めずに飛ばした動画 (コマを取り出せなかった) と、ここで作れなかった動画 (#204)
  const skipped = [...(event.data.skipped ?? [])];
  try {
    const pyodide = await ready;
    const videos = [];
    for (const [n, job] of jobs.entries()) {
      const dir = `/work/${job.id}`;
      try { pyodide.FS.mkdirTree(dir); } catch (e) { /* 既にある */ }
      clearWorkDir(pyodide, dir);
      const before = videos.length;
      try {
      job.frames.forEach((ppm, i) => {
        pyodide.FS.writeFile(`${dir}/frame_${String(i).padStart(6, "0")}.ppm`, ppm);
        job.frames[i] = null; // 写したコマは手放す (メモリに 2 つ持たない。#174)
      });
      say("web.converting", { index: n + 1, total: jobs.length, title: job.title });
      post("progress", { value: (n + 0.4) / jobs.length });
      const out = `/work/frames_${job.id}.js`;
      let args = convertArgs(s, dir, out, lang);
      if (job.mmd) job.voxPath = mmdToVox(pyodide, s, job, dir);
      if (job.vox || job.voxPath) {
        const voxPath = job.voxPath || `${dir}/${job.id}.vox`;
        if (!job.voxPath) pyodide.FS.writeFile(voxPath, job.vox);
        args = voxArgs(s, voxPath, out, lang);
        const thumb = pyodide.globals.get("pack_builder").vox_thumbnail_png(voxPath, 256, s.palette);
        job.thumbnail = thumb.toJs();
        thumb.destroy();
      }
      // だまし絵の背景の映像 (立体のときだけ。人物の動画 ID + "_bg" の、一覧に出さない平面の動画にする)
      const backgroundId = background && s.anamorphic && (job.vox || job.mmd || s.mode !== "screen") ? `${job.id}_bg` : null;
      if (backgroundId) args.push("--stage-background", backgroundId);
      // 変換の進み具合 (数秒ごと) に合わせてバーを動かす (この動画の 40%〜95% の区間)
      const convertModule = pyodide.globals.get("convert");
      convertModule.PROGRESS_HOOK = (done, total) => {
        post("progress", { value: (n + 0.4 + 0.55 * Math.min(1, done / total)) / jobs.length });
      };
      const code = convertModule.main(pyodide.toPy(args));
      convertModule.PROGRESS_HOOK = null;
      if (code !== 0) throw new Error(`convert.py exit ${code}`);
      // 立体版と平面版をセットにする (動画・画像を立体にするときだけ。.vox と MMD には元の平面の映像が無い)。
      // 平面版は同じコマから変換し、音声は立体版 (だまし絵なら背景の映像) のものを鳴らす (pack_builder の audio_from)
      const pair = s.pairFlat && s.mode !== "screen" && !job.vox && !job.mmd;
      let flat = null;
      if (pair) {
        const flatId = `${job.id}${FLAT_PAIR_SUFFIX}`;
        say("build.pair_flat", { id: flatId }, "");
        const flatOut = `/work/frames_${flatId}.js`;
        const flatSettings = { ...s, mode: "screen", anamorphic: false, dither: s.dither === "none" ? "blue_noise" : s.dither };
        const flatCode = convertModule.main(pyodide.toPy(convertArgs(flatSettings, dir, flatOut, lang)));
        if (flatCode !== 0) throw new Error(`convert.py exit ${flatCode}`);
        flat = { id: flatId, title: `${job.title} (2D)`, frames_js: pyodide.FS.readFile(flatOut, { encoding: "utf8" }),
                 thumbnail: job.thumbnail, sounds: [], audio_from: backgroundId || job.id, width: s.width, height: s.height };
      }
      clearWorkDir(pyodide, dir);
      const framesJs = pyodide.FS.readFile(out, { encoding: "utf8" });
      videos.push({ id: job.id, title: pair ? `${job.title} (3D)` : job.title, frames_js: framesJs,
                    thumbnail: job.thumbnail, sounds: job.sounds, width: figureSize(s)[0], height: figureSize(s)[1] });
      if (flat) videos.push(flat);
      if (backgroundId) videos.push(convertBackground(pyodide, s, background, backgroundId, framesJs, lang));
      } catch (e) {
        // 1 本が作れなくても、ほかの動画は作る (1 本の失敗で全部を捨てていた。#204)
        videos.length = before;
        const error = errorText(e);
        skipped.push({ id: job.id, title: job.title, error });
        say("web.skipped_file", { title: job.title, error }, "  ");
      } finally {
        clearWorkDir(pyodide, dir);
      }
      post("progress", { value: (n + 0.95) / jobs.length });
    }
    if (videos.length === 0) throw new Error(skipped.length ? skipped[0].error : "no videos");
    say("web.assembling");
    pyodide.globals.set("js_videos", pyodide.toPy(videos));
    pyodide.globals.set("pack_name", s.packName);
    pyodide.globals.set("namespace", s.namespace);
    pyodide.globals.set("build_target", s.target || "latest");
    // できあがりのプレビュー: 絵はここで作らず、画面の側で再生しながらゲームと同じ codec.js で 1 コマずつ描く
    // (web/block_preview.js)。ここでは参照デコーダで最後まで復元できることを確かめ、色の表などを渡す
    pyodide.globals.set("preview_progress", (done, total) => post("progress", { value: 0.95 + 0.05 * Math.min(1, done / total) }));
    pyodide.globals.set("say_preview", (id) => say("web.preview_making", { id }));
    const result = pyodide.runPython(`
from mvcodec.decode import iter_decoded_frames
from mvcodec.preview import preview_spec
import json
videos = []
previews = []
for n, v in enumerate(js_videos):
    v = dict(v)
    v["thumbnail"] = bytes(v["thumbnail"]) if v.get("thumbnail") is not None else None
    v["sounds"] = [(int(i), str(ext), bytes(data)) for i, ext, data in v["sounds"]]
    videos.append(v)
    text = v["frames_js"]
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    if not v.get("hidden"):
        say_preview(v["id"])
    # Python の参照デコーダで最後まで復元できること (だまし絵の背景の映像も)
    decoded = 0
    for decoded, _ in enumerate(iter_decoded_frames(data), 1):
        if decoded % 50 == 0:
            preview_progress(decoded, data.get("frame_count") or decoded)
    if not v.get("hidden"):
        spec = preview_spec(data)
        spec.update({"index": n, "id": v["id"], "title": v["title"]})
        previews.append(spec)
    print(f"  {v['id']}: {decoded} frames, {len(text) // 1024} KB, {len(v['sounds'])} audio chunks")
# 旧版の Minecraft 向けは main.js・manifest・翻訳を Script API 1.x に合わせて書き換える (legacy_target.py)
build_options = {}
if build_target != "latest":
    import legacy_target
    build_options = {
        "main_text": legacy_target.main_text(pack_builder.MAIN_SCRIPT.read_text(encoding="utf-8"), build_target),
        "manifest_text": legacy_target.manifest_text(pack_builder.MANIFEST.read_text(encoding="utf-8"), build_target),
        "lang_transform": lambda text: legacy_target.lang_text(text, build_target),
    }
(pack_builder.build_addon_bytes(videos, pack_name, namespace, **build_options), previews)
`);
    const [addonProxy, previewProxy] = result.toJs({ depth: 1 });
    const addon = addonProxy.toJs ? addonProxy.toJs() : addonProxy;
    const previews = [];
    for (const item of previewProxy) {
      const p = item.toJs({ dict_converter: Object.fromEntries });
      // 画面の側で codec.js が復元するデータ (変換した .js の FRAME_DATA の JSON)
      const text = videos[p.index].frames_js;
      p.frameData = text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1);
      previews.push(p);
      item.destroy();
    }
    if (addonProxy.destroy) addonProxy.destroy();
    previewProxy.destroy();
    result.destroy();
    // 画像モードのアドオンは別ファイルなので、失敗してもブロックモードのアドオンはそのまま出す
    const imageAddon = s.imageMode ? buildImageAddon(pyodide, s, jobs) : null;
    const transfer = imageAddon ? [addon.buffer, imageAddon.buffer] : [addon.buffer];
    post("done", { addon, previews, imageAddon, skipped }, transfer);
  } catch (e) {
    post("error", { text: errorText(e) });
  }
};
