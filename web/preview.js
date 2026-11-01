// Block Video Player Web Converter: できあがりのプレビュー
// worker.js が、変換したデータを Python の参照デコーダ (ゲームと同じ規則) で絵にしたもの (mvcodec/preview.py) を
// 受け取り、元の動画と並べて再生する。元の動画があれば、その再生位置 (音声付き・シーク可) に合わせてブロックの絵を
// 描く。.vox のように元の動画が無ければ、自分の時計で再生する。
"use strict";

const Preview = (() => {
  const $ = (id) => document.getElementById(id);
  const t = (key, params) => I18n.t(key, params);
  let items = []; // { id, title, width, height, fps, step, count, volume, rgb }
  let current = null;
  let image = null; // 描く ImageData (1 枚分を使い回す)
  let drawn = -1;
  let loopId = 0;
  let ownClock = null; // 元の動画が無いときの再生: { start (ms), offset (秒) }
  let sourceUrls = new Map(); // 動画 ID → 元の動画の URL

  /** frame 番目の絵を canvas に描く (同じ番号なら描かない)。 */
  function draw(frame) {
    if (!current || frame === drawn) return;
    const { width, height, rgb } = current;
    const size = width * height;
    const from = frame * size * 3;
    const out = image.data;
    for (let p = 0, q = from; p < size; p++, q += 3) {
      out[p * 4] = rgb[q];
      out[p * 4 + 1] = rgb[q + 1];
      out[p * 4 + 2] = rgb[q + 2];
      out[p * 4 + 3] = 255;
    }
    $("previewCanvas").getContext("2d").putImageData(image, 0, 0);
    drawn = frame;
  }

  /** 再生位置 (秒) → プレビューの番号。プレビューの fps は元の fps ÷ 間引きの幅。 */
  function frameAt(seconds) {
    return Math.max(0, Math.min(current.count - 1, Math.floor(seconds * current.fps + 1e-6)));
  }

  function tick() {
    if (current) {
      const source = $("previewSource");
      if (sourceUrls.has(current.id)) {
        draw(frameAt(source.currentTime));
      } else if (ownClock) {
        const seconds = ownClock.offset + (performance.now() - ownClock.start) / 1000;
        const length = current.count / current.fps;
        draw(frameAt(seconds % length));
      }
    }
    loopId = requestAnimationFrame(tick);
  }

  function setPlayLabel() {
    $("previewPlay").textContent = t(ownClock ? "web.preview_pause" : "web.preview_play");
  }

  function togglePlay() {
    if (ownClock) {
      ownClock = null;
    } else {
      ownClock = { start: performance.now(), offset: Math.max(0, drawn) / current.fps };
    }
    setPlayLabel();
  }

  function refreshText() {
    if (!current) return;
    let info = t("web.preview_info", { frames: current.count, fps: Number(current.fps.toFixed(2)) });
    if (current.step > 1) info += " " + t("web.preview_thinned", { step: current.step });
    $("previewInfo").textContent = info;
    setPlayLabel();
  }

  function select(id) {
    current = items.find((p) => p.id === id) || items[0];
    const canvas = $("previewCanvas");
    canvas.width = current.width;
    canvas.height = current.height;
    image = canvas.getContext("2d").createImageData(current.width, current.height);
    drawn = -1;
    ownClock = null;
    const source = $("previewSource");
    source.pause();
    const url = sourceUrls.get(current.id);
    $("previewSourceBox").hidden = !url;
    $("previewPlay").hidden = Boolean(url);
    if (url) {
      if (source.src !== url) source.src = url;
      source.currentTime = 0;
    } else {
      source.removeAttribute("src");
      source.load();
    }
    draw(0);
    refreshText();
  }

  /** プレビューを見せる。videos は app.js の動画の一覧 (元の動画の URL を引く)。 */
  function show(list, videos) {
    items = list.map((p) => ({ ...p, rgb: p.rgb instanceof Uint8Array ? p.rgb : new Uint8Array(p.rgb) }));
    sourceUrls = new Map(videos.filter((v) => v.url).map((v) => [v.id, v.url]));
    const selectBox = $("previewSelect");
    selectBox.innerHTML = "";
    for (const p of items) {
      const option = document.createElement("option");
      option.value = p.id;
      option.textContent = p.title || p.id;
      selectBox.append(option);
    }
    $("previewPick").hidden = items.length < 2;
    $("previewCard").hidden = false;
    select(items[0].id);
    if (!loopId) loopId = requestAnimationFrame(tick);
  }

  function hide() {
    $("previewCard").hidden = true;
    $("previewSource").pause();
    items = [];
    current = null;
    ownClock = null;
    if (loopId) cancelAnimationFrame(loopId);
    loopId = 0;
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", () => {
      $("previewSelect").addEventListener("change", (e) => select(e.target.value));
      $("previewPlay").addEventListener("click", togglePlay);
    });
  }

  return { show, hide, refreshText, frameAt: (seconds) => (current ? frameAt(seconds) : 0) };
})();
