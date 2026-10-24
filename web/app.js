// Block Video Player Web Converter (main thread)
// 動画のコマ (<video> + canvas) と音声 (WebAudio → WAV) をブラウザで取り出し、変換とアドオンの組み立ては
// worker.js の Pyodide (ブラウザの中の Python) で、Windows 版と同じ convert.py / pack_builder.py を動かして行う。
"use strict";

const AUDIO_CHUNK_SECONDS = 10; // pack_builder.AUDIO_CHUNK_SECONDS と同じ
const AUDIO_RATE = 22050; // WAV は大きいので 22.05 kHz モノラルにする
const THUMB_SIZE = 256;

const $ = (id) => document.getElementById(id);
const videos = []; // { file, url, id, title, width, height, duration, vox }
const isVox = (file) => /\.vox$/i.test(file.name);
let worker = null;
let lastDownloadUrl = null;

function log(text) {
  const el = $("log");
  el.textContent += text + "\n";
  el.scrollTop = el.scrollHeight;
}

function safeId(name, used) {
  let base = name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "video";
  if (/^[0-9]/.test(base)) base = "v_" + base;
  let id = base;
  for (let n = 2; used.has(id); n++) id = `${base}_${n}`;
  used.add(id);
  return id;
}

function loadVideoElement(url) {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.playsInline = true;
    video.src = url;
    video.onloadedmetadata = () => resolve(video);
    video.onerror = () => reject(new Error("この動画はブラウザで開けません / The browser cannot open this video"));
  });
}

async function addFiles(fileList) {
  const used = new Set(videos.map((v) => v.id));
  for (const file of fileList) {
    if (isVox(file)) {
      // MagicaVoxel の立体アニメ: 大きさ・コマ数は .vox のまま (設定は FPS とパレットだけを使う)
      const header = new Uint8Array(await file.slice(0, 4).arrayBuffer());
      if (String.fromCharCode(...header) !== "VOX ") { log(`${file.name}: MagicaVoxel の .vox ではありません / not a MagicaVoxel .vox file`); continue; }
      videos.push({ file, id: safeId(file.name, used), title: file.name.replace(/\.[^.]+$/, ""), vox: true });
      continue;
    }
    const url = URL.createObjectURL(file);
    try {
      const el = await loadVideoElement(url);
      videos.push({ file, url, id: safeId(file.name, used), title: file.name.replace(/\.[^.]+$/, ""),
                    width: el.videoWidth, height: el.videoHeight, duration: el.duration });
    } catch (e) {
      log(`${file.name}: ${e.message}`);
    }
  }
  renderList();
}

function renderList() {
  const list = $("videoList");
  list.innerHTML = "";
  for (const v of videos) {
    const li = document.createElement("li");
    const input = document.createElement("input");
    input.value = v.id;
    input.title = "動画 ID / Video ID";
    input.onchange = () => { v.id = safeId(input.value, new Set(videos.filter((o) => o !== v).map((o) => o.id))); input.value = v.id; };
    li.append(input, v.vox ? ` ${v.title} (MagicaVoxel 3D)` : ` ${v.title} (${v.width}×${v.height}, ${v.duration.toFixed(1)} s)`);
    list.append(li);
  }
  $("build").disabled = videos.length === 0;
  updateLoadHint();
}

// GUI の「動画の縦横比に合わせる」と同じ: 長い辺をそのままに、短い辺を縦横比に合わせる (偶数にそろえる)
function fitAspect() {
  const v = videos.find((o) => !o.vox);
  if (!v) return;
  const longSide = Math.max(Number($("width").value), Number($("height").value));
  let w, h;
  if (v.width >= v.height) { w = longSide; h = Math.max(2, Math.round((longSide * v.height) / v.width / 2) * 2); }
  else { h = longSide; w = Math.max(2, Math.round((longSide * v.width) / v.height / 2) * 2); }
  $("width").value = w;
  $("height").value = h;
  updateLoadHint();
}

function settings() {
  return {
    width: Number($("width").value), height: Number($("height").value), fps: Number($("fps").value),
    duration: Number($("duration").value), mode: $("mode").value, depth: Number($("depth").value),
    anamorphic: $("anamorphic").checked,
    palette: $("palette").value, dither: $("dither").value,
    packName: $("packName").value.trim() || "Block Video Player",
    namespace: ($("namespace").value.trim().toLowerCase().replace(/[^a-z0-9_]/g, "") || "badapple"),
  };
}

// GUI の負荷の目安と同じ考え方: 画面全体が変わったとき 1 秒に置くブロック数
function updateLoadHint() {
  const s = settings();
  const blocks = s.width * s.height * (s.mode === "screen" ? 1 : 2) * s.fps;
  const level = blocks > 160000 ? "重い / heavy" : blocks > 60000 ? "やや重い / moderate" : "軽い / light";
  $("loadHint").textContent = `再生負荷の目安 / Load estimate: ${blocks.toLocaleString()} blocks/s (${level})`;
}

function drawLetterboxed(ctx, source, srcW, srcH, w, h) {
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, w, h);
  const scale = Math.min(w / srcW, h / srcH);
  const dw = Math.round(srcW * scale), dh = Math.round(srcH * scale);
  ctx.drawImage(source, Math.floor((w - dw) / 2), Math.floor((h - dh) / 2), dw, dh);
}

function seek(video, time) {
  return new Promise((resolve) => {
    video.onseeked = () => resolve();
    video.currentTime = time;
  });
}

// 動画のコマを PPM (P6) にして返す (convert.py が Pillow で読める一番簡単な形式)
async function extractFrames(v, s, onProgress) {
  const video = await loadVideoElement(v.url);
  const canvas = document.createElement("canvas");
  canvas.width = s.width;
  canvas.height = s.height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const length = Math.min(video.duration, s.duration);
  const count = Math.max(1, Math.floor(length * s.fps));
  const header = new TextEncoder().encode(`P6\n${s.width} ${s.height}\n255\n`);
  const frames = [];
  for (let i = 0; i < count; i++) {
    await seek(video, Math.min(video.duration - 0.001, i / s.fps + 0.001));
    drawLetterboxed(ctx, video, video.videoWidth, video.videoHeight, s.width, s.height);
    const rgba = ctx.getImageData(0, 0, s.width, s.height).data;
    const ppm = new Uint8Array(header.length + s.width * s.height * 3);
    ppm.set(header);
    for (let p = 0, q = header.length; p < rgba.length; p += 4) {
      ppm[q++] = rgba[p];
      ppm[q++] = rgba[p + 1];
      ppm[q++] = rgba[p + 2];
    }
    frames.push(ppm);
    if (i % 10 === 0) onProgress(i / count);
  }
  // サムネイル (最初の動画のものがパックのアイコンになる): 1 秒目か中ほど
  await seek(video, Math.min(1, video.duration / 2));
  const thumbCanvas = document.createElement("canvas");
  thumbCanvas.width = thumbCanvas.height = THUMB_SIZE;
  drawLetterboxed(thumbCanvas.getContext("2d"), video, video.videoWidth, video.videoHeight, THUMB_SIZE, THUMB_SIZE);
  const thumbnail = new Uint8Array(await (await new Promise((r) => thumbCanvas.toBlob(r, "image/png"))).arrayBuffer());
  return { frames, thumbnail, length };
}

function wavBytes(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

// 音声を 22.05 kHz モノラルにして AUDIO_CHUNK_SECONDS ごとの WAV にする (音声が無ければ [])
async function extractAudio(v, length) {
  try {
    const data = await v.file.arrayBuffer();
    const decoded = await new AudioContext().decodeAudioData(data);
    const frames = Math.ceil(Math.min(decoded.duration, length) * AUDIO_RATE);
    const offline = new OfflineAudioContext(1, frames, AUDIO_RATE);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const mono = (await offline.startRendering()).getChannelData(0);
    const chunks = [];
    const step = AUDIO_CHUNK_SECONDS * AUDIO_RATE;
    for (let start = 0, index = 0; start < mono.length; start += step, index++) {
      chunks.push([index, "wav", wavBytes(mono.subarray(start, Math.min(mono.length, start + step)), AUDIO_RATE)]);
    }
    return chunks;
  } catch (e) {
    log(`  音声なし / no audio (${e.message || e})`);
    return [];
  }
}

function startWorker() {
  if (worker) return worker;
  worker = new Worker("worker.js");
  worker.onmessage = (event) => {
    const msg = event.data;
    if (msg.type === "log") log(msg.text);
    else if (msg.type === "progress") $("progress").value = msg.value;
    else if (msg.type === "done") finish(msg);
    else if (msg.type === "error") { log("エラー / Error: " + msg.text); $("build").disabled = false; }
  };
  return worker;
}

function finish(msg) {
  const blob = new Blob([msg.addon], { type: "application/octet-stream" });
  if (lastDownloadUrl) URL.revokeObjectURL(lastDownloadUrl);
  lastDownloadUrl = URL.createObjectURL(blob);
  const link = $("download");
  link.href = lastDownloadUrl;
  link.download = `${settings().packName.replace(/[^\w.-]+/g, "_")}.mcaddon`;
  link.hidden = false;
  $("progress").value = 1;
  $("build").disabled = false;
  log(`完成 / Done: ${(blob.size / 1024).toFixed(0)} KB`);
  window.lastResult = msg; // テスト用
}

// だまし絵の背景の映像のコマ: 後ろの壁の大きさは人物を変換するまで分からないので、長い辺 256 で取り出し、
// Python (convert.py --fit cover) で後ろの壁の大きさに切り取る
const BACKGROUND_LONG_SIDE = 256;

async function extractBackground(s) {
  const file = $("stageBackground").files[0];
  if (!s.anamorphic || !file || !videos.some((v) => v.vox || s.mode !== "screen")) return null;
  log(`背景の映像 / background video: ${file.name}`);
  const url = URL.createObjectURL(file);
  const el = await loadVideoElement(url);
  const scale = BACKGROUND_LONG_SIDE / Math.max(el.videoWidth, el.videoHeight);
  const size = { ...s, width: Math.max(2, Math.round(el.videoWidth * scale)), height: Math.max(2, Math.round(el.videoHeight * scale)) };
  const v = { file, url };
  const { frames, length } = await extractFrames(v, size, () => {});
  log(`  ${frames.length} frames`);
  const sounds = await extractAudio(v, length);
  URL.revokeObjectURL(url);
  return { title: file.name.replace(/\.[^.]+$/, ""), frames, sounds };
}

async function build() {
  const s = settings();
  $("build").disabled = true;
  $("download").hidden = true;
  $("log").textContent = "";
  const w = startWorker();
  const background = await extractBackground(s);
  const jobs = [];
  for (const [n, v] of videos.entries()) {
    if (v.vox) {
      // .vox はそのまま渡し、変換とサムネイル (立体を斜め上から見た絵) は Python で作る。音声は無し
      jobs.push({ id: v.id, title: v.title, vox: new Uint8Array(await v.file.arrayBuffer()), frames: [], thumbnail: null, sounds: [] });
      continue;
    }
    log(`[${n + 1}/${videos.length}] ${v.title}: コマを取り出し中 / extracting frames…`);
    const { frames, thumbnail, length } = await extractFrames(v, s, (f) => { $("progress").value = (n + f * 0.4) / videos.length; });
    log(`  ${frames.length} frames`);
    const sounds = await extractAudio(v, length);
    jobs.push({ id: v.id, title: v.title, frames, thumbnail, sounds });
  }
  const transfer = [];
  for (const job of jobs) {
    for (const f of job.frames) transfer.push(f.buffer);
    if (job.thumbnail) transfer.push(job.thumbnail.buffer);
    if (job.vox) transfer.push(job.vox.buffer);
    for (const [, , bytes] of job.sounds) transfer.push(bytes.buffer);
  }
  if (background) {
    for (const f of background.frames) transfer.push(f.buffer);
    for (const [, , bytes] of background.sounds) transfer.push(bytes.buffer);
  }
  w.postMessage({ type: "build", settings: s, jobs, background, lang: navigator.language.startsWith("ja") ? "ja" : "en" },
    transfer);
}

$("files").addEventListener("change", (e) => addFiles(e.target.files));
$("fitAspect").addEventListener("click", fitAspect);
$("build").addEventListener("click", () => build().catch((e) => { log("エラー / Error: " + e.message); $("build").disabled = false; }));
for (const id of ["width", "height", "fps", "mode"]) $(id).addEventListener("change", updateLoadHint);
$("mode").addEventListener("change", () => { if ($("mode").value !== "screen" && $("dither").value === "blue_noise") $("dither").value = "none"; });
updateLoadHint();
startWorker(); // Python の読み込みを先に始める

// テスト用: ?src=<同じサイトの動画> で動画を読み込む
const src = new URLSearchParams(location.search).get("src");
if (src) {
  fetch(src).then((r) => r.blob()).then((blob) => addFiles([new File([blob], src.split("/").pop(), { type: blob.type })]));
}
