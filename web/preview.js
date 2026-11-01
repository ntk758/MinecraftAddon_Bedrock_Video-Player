// Block Video Player Web Converter: できあがりのプレビュー
// 変換したデータを、再生しながらゲームと同じ codec.js で 1 コマずつ復元して描き (web/block_preview.js)、元の動画と
// 並べて再生する。絵を先に全部作らないので、長い動画でも全部のコマを元の fps のまま見られる。元の動画があれば、
// その再生位置 (音声付き・シーク可) に合わせてブロックの絵を描く。.vox のように元の動画が無ければ、自分の時計で再生する。
"use strict";

const Preview = (() => {
  const $ = (id) => document.getElementById(id);
  const t = (key, params) => I18n.t(key, params);
  let items = []; // { id, title, spec (mvcodec/preview.py の preview_spec), frameData (FRAME_DATA の JSON), player }
  let codec = null; // ゲームと同じ codec.js (ES モジュール。組み立てのコードと一緒に app/ に置いてある)
  let current = null;
  let image = null; // 描く ImageData (1 枚分を使い回す)
  let drawn = -1;
  let loopId = 0;
  let ownClock = null; // 元の動画が無いときの再生: { start (ms), offset (秒) }
  let sourceUrls = new Map(); // 動画 ID → 元の動画の URL

  /** frame 番目の絵を canvas に描く (同じ番号なら描かない)。 */
  function draw(frame) {
    if (!current || !current.player || frame === drawn) return;
    current.player.render(frame, image);
    $("previewCanvas").getContext("2d").putImageData(image, 0, 0);
    drawn = frame;
  }

  /** 再生位置 (秒) → フレームの番号。 */
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
    $("previewInfo").textContent = t("web.preview_info", { frames: current.count, fps: Number(current.fps.toFixed(2)) });
    setPlayLabel();
  }

  function select(id) {
    const previous = current;
    current = items.find((p) => p.id === id) || items[0];
    if (previous && previous !== current) previous.player = null; // 選んでいない動画の盤面は持たない
    if (!current.player) {
      // 選んだ動画だけを復元の準備をする (FRAME_DATA を読み、盤面 1 枚ぶんの配列を用意する)
      current.player = BlockPreview.create(codec, current.spec, JSON.parse(current.frameData));
      Object.assign(current, { width: current.player.width, height: current.player.height,
                               count: current.player.count, fps: current.player.fps });
    }
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

  /** プレビューを見せる。list は worker.js の previews、videos は app.js の動画の一覧 (元の動画の URL を引く)。 */
  async function show(list, videos) {
    if (!codec) codec = await import("./app/codec.js");
    items = list.map(({ id, title, frameData, ...spec }) => ({ id, title, frameData, spec, player: null }));
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
