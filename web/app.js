// Block Video Player Web Converter (main thread)
// 動画のコマ (<video> + canvas) と音声 (WebAudio → WAV) をブラウザで取り出し、変換とアドオンの組み立ては
// worker.js の Pyodide (ブラウザの中の Python) で、Windows 版と同じ convert.py / pack_builder.py を動かして行う。
"use strict";

const AUDIO_CHUNK_SECONDS = 10; // pack_builder.AUDIO_CHUNK_SECONDS と同じ
const AUDIO_RATE = 22050; // WAV は大きいので 22.05 kHz モノラルにする
const THUMB_SIZE = 256;
// コマのデータがこれより大きくなりそうなら注意を出す (MB)。ブラウザのタブが使えるメモリと、Pyodide (32bit の
// WebAssembly) に同じデータを写すことを考えて決めた目安
const MEMORY_WARNING_MB = 1000;

const $ = (id) => document.getElementById(id);
const t = (key, params) => I18n.t(key, params); // 翻訳 (web/i18n.js、locales/*.json)
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

function loadVideoElement(url, name = "") {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.playsInline = true;
    video.src = url;
    video.onloadedmetadata = () => resolve(video);
    video.onerror = () => reject(new Error(t("web.cannot_open", { name })));
  });
}

async function addFiles(fileList) {
  const used = new Set(videos.map((v) => v.id));
  for (const file of fileList) {
    if (isVox(file)) {
      // MagicaVoxel の立体アニメ: 大きさ・コマ数は .vox のまま (設定は FPS とパレットだけを使う)
      const header = new Uint8Array(await file.slice(0, 4).arrayBuffer());
      if (String.fromCharCode(...header) !== "VOX ") { log(t("web.not_vox", { name: file.name })); continue; }
      videos.push({ file, id: safeId(file.name, used), title: file.name.replace(/\.[^.]+$/, ""), vox: true });
      continue;
    }
    const url = URL.createObjectURL(file);
    try {
      const el = await loadVideoElement(url, file.name);
      videos.push({ file, url, id: safeId(file.name, used), title: file.name.replace(/\.[^.]+$/, ""),
                    width: el.videoWidth, height: el.videoHeight, duration: el.duration });
    } catch (e) {
      log(e.message);
    }
  }
  renderList();
  fillDuration();
}

// 変換する長さ: 動画を追加したら、一番長い動画の終わりの秒数を入れる (利用者が書き換えた値はそのまま)。
// 空なら最後まで変換する (Windows 版の GUI と同じ)
let durationEdited = false;

function longestVideo() {
  return Math.max(0, ...videos.filter((v) => !v.vox && Number.isFinite(v.duration)).map((v) => v.duration));
}

function fillDuration() {
  if (durationEdited) return;
  const longest = longestVideo();
  $("duration").value = longest > 0 ? String(Math.ceil(longest * 10) / 10) : "";
  updateLoadHint();
}

/** 変換する秒数の上限。空・0 以下なら上限なし (最後まで)。 */
function durationLimit(s) {
  return s.duration > 0 ? s.duration : Infinity;
}

function renderList() {
  const list = $("videoList");
  list.innerHTML = "";
  for (const v of videos) {
    const li = document.createElement("li");
    const input = document.createElement("input");
    input.value = v.id;
    input.title = t("web.video_id");
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
    duration: $("duration").value.trim() === "" ? 0 : Number($("duration").value), mode: $("mode").value, depth: Number($("depth").value),
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
  const level = t(blocks > 160000 ? "load.heavy" : blocks > 60000 ? "load.medium" : "load.light");
  let hint = t("web.load_estimate", { blocks: blocks.toLocaleString(I18n.language), level });
  // ブラウザ版は取り出したコマ (1 コマ 幅×高さ×3 バイト) をすべてメモリに持つので、長い動画では注意を出す
  const seconds = videos.filter((v) => !v.vox && Number.isFinite(v.duration))
    .reduce((sum, v) => sum + Math.min(v.duration, durationLimit(s)), 0);
  const mb = Math.round((seconds * s.fps * s.width * s.height * 3) / 1e6);
  if (mb >= MEMORY_WARNING_MB) hint += "\n" + t("web.memory_warning", { mb: mb.toLocaleString(I18n.language) });
  $("loadHint").textContent = hint;
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
  const length = Math.min(video.duration, durationLimit(s));
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

/**
 * 動画ファイルの音声をデコードする。まず動画ファイルを丸ごと渡し、失敗したら (iPhone の Safari / Chrome は MP4・MOV
 * の動画ファイルを読めない) AAC の音声トラックだけを取り出して (mp4audio.js) もう一度デコードする。
 */
async function decodeVideoAudio(file) {
  const context = new AudioContext();
  try {
    try {
      return await context.decodeAudioData(await file.arrayBuffer());
    } catch (e) {
      const adts = await Mp4Audio.extractAdts(file).catch(() => null);
      if (!adts || adts.length === 0) throw e;
      log("  " + t("web.audio_aac"));
      return await context.decodeAudioData(adts.buffer.slice(adts.byteOffset, adts.byteOffset + adts.length));
    }
  } finally {
    // iPhone は同時に作れる AudioContext の数に限りがあるので、動画ごとに閉じる
    context.close().catch(() => {});
  }
}

/** decoded を AUDIO_RATE のモノラルにする。OfflineAudioContext が使えない・失敗したときは、平均を取って間引く。 */
async function toMono(decoded, seconds) {
  const frames = Math.ceil(seconds * AUDIO_RATE);
  try {
    const offline = new OfflineAudioContext(1, frames, AUDIO_RATE);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    return (await offline.startRendering()).getChannelData(0);
  } catch (e) {
    const channels = [];
    for (let c = 0; c < decoded.numberOfChannels; c++) channels.push(decoded.getChannelData(c));
    const ratio = decoded.sampleRate / AUDIO_RATE;
    const mono = new Float32Array(frames);
    for (let i = 0; i < frames; i++) {
      const from = Math.floor(i * ratio);
      const to = Math.min(decoded.length, Math.max(from + 1, Math.floor((i + 1) * ratio)));
      let sum = 0;
      for (const data of channels) for (let k = from; k < to; k++) sum += data[k];
      mono[i] = to > from ? sum / ((to - from) * channels.length) : 0;
    }
    return mono;
  }
}

// 音声を 22.05 kHz モノラルにして AUDIO_CHUNK_SECONDS ごとの WAV にする (音声が無ければ [])
async function extractAudio(v, length) {
  try {
    const decoded = await decodeVideoAudio(v.file);
    const mono = await toMono(decoded, Math.min(decoded.duration, length));
    const chunks = [];
    const step = AUDIO_CHUNK_SECONDS * AUDIO_RATE;
    for (let start = 0, index = 0; start < mono.length; start += step, index++) {
      chunks.push([index, "wav", wavBytes(mono.subarray(start, Math.min(mono.length, start + step)), AUDIO_RATE)]);
    }
    return chunks;
  } catch (e) {
    log("  " + t("web.no_audio", { error: e.message || e }));
    return [];
  }
}

function startWorker() {
  if (worker) return worker;
  worker = new Worker("worker.js");
  worker.onmessage = (event) => {
    const msg = event.data;
    // worker の文はキーで受け取ってここで翻訳する (Python の出力はそのまま)
    if (msg.type === "log") log(msg.key ? (msg.indent || "") + t(msg.key, msg.params) : msg.text);
    else if (msg.type === "progress") $("progress").value = msg.value;
    else if (msg.type === "done") finish(msg);
    else if (msg.type === "error") failed(msg.text);
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
  log(t("web.done", { size: (blob.size / 1024).toFixed(0) }));
  window.lastResult = msg; // テスト用
  if (lastBuild) {
    Usage.event("build-done");
    Usage.event(`build-kind-${lastBuild.kind}`);
    Usage.event(`build-lang-${lastBuild.lang}`);
    lastBuild = null;
  }
}

function failed(text) {
  log(t("web.error", { error: text }));
  $("build").disabled = false;
  if (lastBuild) Usage.event("build-error");
  lastBuild = null;
}

// 利用数で数える、作成中のアドオンの種類と画面の言語 (完成・失敗したときに送る)
let lastBuild = null;

// だまし絵の背景の映像のコマ: 後ろの壁の大きさは人物を変換するまで分からないので、長い辺 256 で取り出し、
// Python (convert.py --fit cover) で後ろの壁の大きさに切り取る
const BACKGROUND_LONG_SIDE = 256;

async function extractBackground(s) {
  const file = $("stageBackground").files[0];
  if (!s.anamorphic || !file || !videos.some((v) => v.vox || s.mode !== "screen")) return null;
  log(t("web.background", { name: file.name }));
  const url = URL.createObjectURL(file);
  const el = await loadVideoElement(url, file.name);
  const scale = BACKGROUND_LONG_SIDE / Math.max(el.videoWidth, el.videoHeight);
  const size = { ...s, width: Math.max(2, Math.round(el.videoWidth * scale)), height: Math.max(2, Math.round(el.videoHeight * scale)) };
  const v = { file, url };
  const { frames, length } = await extractFrames(v, size, () => {});
  log("  " + t("web.frames", { count: frames.length }));
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
  lastBuild = { kind: Usage.kindOf(s, videos.some((v) => v.vox), videos.some((v) => !v.vox)), lang: I18n.language };
  Usage.event("build-start");
  const background = await extractBackground(s);
  const jobs = [];
  for (const [n, v] of videos.entries()) {
    if (v.vox) {
      // .vox はそのまま渡し、変換とサムネイル (立体を斜め上から見た絵) は Python で作る。音声は無し
      jobs.push({ id: v.id, title: v.title, vox: new Uint8Array(await v.file.arrayBuffer()), frames: [], thumbnail: null, sounds: [] });
      continue;
    }
    log(t("web.extracting", { index: n + 1, total: videos.length, title: v.title }));
    const { frames, thumbnail, length } = await extractFrames(v, s, (f) => { $("progress").value = (n + f * 0.4) / videos.length; });
    log("  " + t("web.frames", { count: frames.length }));
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
  w.postMessage({ type: "build", settings: s, jobs, background, lang: I18n.language },
    transfer);
}

$("files").addEventListener("change", (e) => addFiles(e.target.files));
$("fitAspect").addEventListener("click", fitAspect);
$("build").addEventListener("click", () => build().catch((e) => failed(e.message)));
for (const id of ["width", "height", "fps", "mode", "duration"]) $(id).addEventListener("change", updateLoadHint);
// 利用者が長さを書き換えたら、動画を追加しても上書きしない。空にしたら、また動画の長さを入れる
$("duration").addEventListener("change", () => { durationEdited = $("duration").value.trim() !== ""; if (!durationEdited) fillDuration(); });
$("namespace").addEventListener("input", renderHowto);
$("mode").addEventListener("change", () => { if ($("mode").value !== "screen" && $("dither").value === "blue_noise") $("dither").value = "none"; });

// 使い方の setup・start のコマンド (コマンドの接頭辞に合わせる)。翻訳の {setup}・{start} の所に <code> を入れる
function renderHowto() {
  const ns = settings().namespace;
  const codes = { setup: `/scriptevent ${ns}:setup`, start: `/scriptevent ${ns}:start` };
  const item = $("howtoSetup");
  item.textContent = "";
  for (const part of t("web.howto_setup").split(/(\{setup\}|\{start\})/)) {
    const name = part.slice(1, -1);
    if (part.startsWith("{") && name in codes) {
      const code = document.createElement("code");
      code.textContent = codes[name];
      item.append(code);
    } else if (part) {
      item.append(part);
    }
  }
}

// 利用数: 計測先が設定されているときだけ、説明と送信を止めるチェックを見せる
function setupUsage() {
  if (!Usage.configured) return;
  $("usage").hidden = false;
  $("usageOptOut").checked = Usage.optedOut;
  $("usageOptOut").addEventListener("change", (e) => Usage.setOptOut(e.target.checked));
}

I18n.onChange(() => { updateLoadHint(); renderHowto(); renderList(); });
I18n.init().then(() => {
  I18n.fillSelect($("language"));
  setupUsage();
  Usage.pageview(I18n.language);
});
updateLoadHint();
startWorker(); // Python の読み込みを先に始める

// テスト用: ?src=<同じサイトの動画> で動画を読み込む
const src = new URLSearchParams(location.search).get("src");
if (src) {
  fetch(src).then((r) => r.blob()).then((blob) => addFiles([new File([blob], src.split("/").pop(), { type: blob.type })]));
}
