// Block & Image Video Player Web Converter: 見た目と動き
// ナビ・読み込んだときの演出・最初の画面の舞台 (元の映像と本物の変換結果を同期して再生)・設定の見本・流れの図・
// ドロップの枠・ファイルのカード・切り替えの帯・スライダー・ツールチップ・知らせ・作る段階の表示・できたファイル・プレビューの操作の帯。
// アイコンは Lucide (web/vendor/lucide/icons.js)、動きは Motion の Vanilla JavaScript API (web/vendor/motion)。
// Motion が読めない・動きを減らす設定のときは、style.css の動き (または動きなし) になる。
// 変換と組み立て (app.js・worker.js) の値や順番は変えない。app.js からは UI.* を呼ぶだけ (無くても変換は動く)。
// 進み具合は app.js が入れる <progress> の値と worker のログのキーだけから出す (架空の数字は出さない)。
"use strict";

const UI = (() => {
  const $ = (id) => document.getElementById(id);
  const t = (key, params) => I18n.t(key, params);
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STAGES = ["frames", "prepare", "convert", "pack", "done"];
  // worker・app.js のログのキー → 段階 (worker.js の say() と app.js の log(t(...)) のキー)
  const STAGE_OF_KEY = {
    "web.extracting": "frames", "web.background": "frames",
    "web.converting": "convert", "build.mmd": "convert", "build.mmd_progress": "convert", "build.pair_flat": "convert",
    "web.assembling": "pack", "web.preview_making": "pack",
  };
  // ---- アイコン: <i data-lucide="名前"> を Lucide の SVG に置き換える (アイコンの形は self.LUCIDE_ICONS)
  const SVG_NS = "http://www.w3.org/2000/svg";
  function icon(name, className = "icon") {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", className);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.dataset.lucide = name;
    for (const [tag, attrs] of ((self.LUCIDE_ICONS || {})[name] || [])) {
      const child = document.createElementNS(SVG_NS, tag);
      for (const [key, value] of Object.entries(attrs)) child.setAttribute(key, String(value));
      svg.append(child);
    }
    return svg;
  }
  function renderIcons(root = document) {
    for (const el of root.querySelectorAll("i[data-lucide]")) el.replaceWith(icon(el.dataset.lucide, el.getAttribute("class") || "icon"));
  }

  // ---- 動き: Motion の animate を、ここで決めた 2 つの動き方 (ばね・減速) だけで使う
  const SPRING = { type: "spring", bounce: 0.18, visualDuration: 0.36 };
  const EASE = { duration: 0.5, ease: [0.2, 0.8, 0.2, 1] };
  const QUICK = { duration: 0.18, ease: [0.4, 0, 1, 1] };
  const hasMotion = () => Boolean(self.Motion && self.Motion.animate) && !reduced();
  /** el を動かし、終わったら解決する。Motion が使えないときは何もせずすぐ解決する (CSS の見た目のまま)。 */
  function animate(el, keyframes, options = SPRING) {
    if (!el || !hasMotion()) return Promise.resolve();
    try {
      return Promise.resolve(self.Motion.animate(el, keyframes, options)).then(() => {}, () => {});
    } catch (e) {
      return Promise.resolve();
    }
  }

  /** バイト数を読みやすく (1.2 MB など)。 */
  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "";
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB"];
    let value = bytes / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
  }

  /** 秒 → 0:00。 */
  function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  /** "High (128×128 / 10fps)" → ["High", "128×128 / 10fps"]。かっこが無ければ全体を名前に。 */
  function splitLabel(text) {
    const m = /^(.*?)\s*[（(](.*)[)）]\s*$/.exec(String(text).trim());
    return m && m[1] ? [m[1], m[2]] : [String(text).trim(), ""];
  }

  // ---- 動きをまとめて 1 フレームに 1 回だけ更新する
  let scheduled = false;
  const refreshers = [];
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; for (const fn of refreshers) fn(); });
  }

  // ---- ヘッダー: スクロールしたらすりガラスに
  function setupHeader() {
    const header = $("siteHeader");
    if (!header) return;
    let ticking = false;
    const update = () => { ticking = false; header.classList.toggle("is-scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    update();
  }

  // ---- ナビ: 今いる節に印を付ける。狭い画面ではメニューを開閉する
  function setupNav() {
    const links = $("navLinks");
    const toggle = $("navToggle");
    if (!links || !toggle) return;
    const setOpen = (open) => {
      links.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => setOpen(!links.classList.contains("is-open")));
    links.addEventListener("click", (e) => { if (e.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
    if (!("IntersectionObserver" in window)) return;
    const byId = new Map(Array.from(links.querySelectorAll("a")).map((a) => [a.getAttribute("href").slice(1), a]));
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const [id, a] of byId) { if (id === entry.target.id) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); }
      }
    }, { rootMargin: "-40% 0px -55% 0px" });
    for (const id of byId.keys()) { const el = $(id); if (el) observer.observe(el); }
    // いちばん上と変換ツールでは、どの節の印も付けない
    const clear = new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.isIntersecting) for (const a of byId.values()) a.removeAttribute("aria-current");
    }, { rootMargin: "-40% 0px -55% 0px" });
    for (const el of [document.querySelector(".hero"), $("convert")]) if (el) clear.observe(el);
  }

  // ---- 読み込んだときに 1 回だけ: 見出し → 説明 → 舞台の順に出る。流れの図の線は、見えたときに 1 回引く
  // (節ごとに下から出す動きは付けない。<head> の小さなスクリプトが has-motion を付け、ここで動かして is-ready にする)
  function setupIntro() {
    const root = document.documentElement;
    const pipeline = $("pipeline");
    if (!hasMotion()) {
      root.classList.remove("has-motion");
      root.classList.add("is-ready");
      if (pipeline) pipeline.classList.add("is-drawn");
      return;
    }
    root.classList.add("has-motion");
    const parts = [document.querySelector(".hero-title"), document.querySelector(".hero-side"), document.querySelector(".stage-figure")];
    Promise.all(parts.map((el, i) => animate(el, { opacity: [0, 1], y: [22, 0] }, { duration: 0.7, ease: [0.2, 0.8, 0.2, 1], delay: 0.05 + i * 0.09 })))
      .then(() => root.classList.add("is-ready"));
    if (pipeline && self.Motion.inView) self.Motion.inView(pipeline, () => { pipeline.classList.add("is-drawn"); }, { amount: 0.35 });
    else if (pipeline) pipeline.classList.add("is-drawn");
  }

  // ---- 版: 組み立てたサイトの app/pack_metadata.py の PACK_VERSION (無ければ出さない)
  function setupVersion() {
    fetch("app/pack_metadata.py").then((r) => (r.ok ? r.text() : "")).then((text) => {
      const m = /PACK_VERSION\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)/.exec(text);
      if (!m) return;
      const badge = $("versionBadge");
      badge.textContent = `v${m[1]}.${m[2]}.${m[3]}`;
      badge.hidden = false;
    }).catch(() => {});
  }

  // ---- 変換の準備 (ブラウザの中の Python) の状態
  let engineState = "loading";
  function engine(state) {
    engineState = state;
    const el = $("engineStatus");
    if (!el) return;
    el.dataset.state = state;
    const text = el.querySelector(".engine-text");
    const key = state === "ready" ? "web.engine_ready" : state === "error" ? "web.engine_error" : "web.engine_loading";
    text.dataset.i18n = key;
    text.textContent = t(key);
  }

  // ---- ドロップの枠: 落としたファイルは input#files に入れて change を起こす (app.js の読み込みと同じ道を通る)
  function setupDropzone() {
    const zone = $("dropzone");
    const input = $("files");
    if (!zone || !input) return;
    let depth = 0;
    const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
    zone.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; zone.classList.add("is-over"); });
    zone.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
    zone.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) zone.classList.remove("is-over"); });
    zone.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      zone.classList.remove("is-over");
      if (hasMotion()) {
        animate(zone.querySelector(".drop-icon"), { y: [-8, 0], scale: [1.1, 1] });
      } else {
        zone.classList.remove("is-dropped");
        void zone.offsetWidth; // 動きをやり直す
        zone.classList.add("is-dropped");
      }
      try {
        input.files = e.dataTransfer.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      } catch (err) { /* files を入れられない古いブラウザ: 枠をクリックして選ぶ */ }
    });
    // 枠の外に落としても、ブラウザがファイルを開いてページを離れないようにする
    for (const type of ["dragover", "drop"]) {
      window.addEventListener(type, (e) => { if (hasFiles(e) && !zone.contains(e.target)) e.preventDefault(); });
    }
  }

  // ---- ファイルのカード (app.js の renderList が呼ぶ)
  const seen = new WeakSet();
  const thumbUrls = new WeakMap();
  function fileCard(v, idInput, onRemove) {
    const li = document.createElement("li");
    li.className = "file-card";
    if (!seen.has(v)) {
      seen.add(v);
      if (hasMotion()) {
        li.style.opacity = "0";
        requestAnimationFrame(() => animate(li, { opacity: [0, 1], y: [10, 0], scale: [0.98, 1] }).then(() => { li.style.opacity = ""; }));
      } else {
        li.classList.add("is-new");
      }
    }
    const thumb = document.createElement("span");
    thumb.className = "file-thumb";
    if (v.url) {
      const video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.src = `${v.url}#t=0.5`;
      thumb.append(video);
    } else if (v.image && v.file) {
      if (!thumbUrls.has(v)) thumbUrls.set(v, URL.createObjectURL(v.file));
      const img = document.createElement("img");
      img.alt = "";
      img.src = thumbUrls.get(v);
      thumb.append(img);
    } else {
      thumb.classList.add("kind-3d");
      thumb.append(icon(v.mmd ? "person-standing" : "box"));
    }
    const main = document.createElement("div");
    main.className = "file-main";
    const title = document.createElement("span");
    title.className = "file-title";
    title.textContent = v.title;
    title.title = v.file ? v.file.name : v.title;
    const meta = document.createElement("div");
    meta.className = "file-meta-line";
    const span = (text, className = "") => { const s = document.createElement("span"); s.className = className; s.textContent = text; return s; };
    const pill = (text) => span(text, "pill");
    if (v.mmd) meta.append(pill("MMD 3D"));
    else if (v.vox) meta.append(pill("MagicaVoxel 3D"));
    else if (v.image) meta.append(pill(t("web.image_item")), span(`${v.width}×${v.height}`));
    else meta.append(pill(`${v.width}×${v.height}`), span(`${v.duration.toFixed(1)} s`));
    if (v.file && Number.isFinite(v.file.size)) meta.append(span(formatBytes(v.file.size)));
    const idBox = document.createElement("label");
    idBox.className = "file-id";
    idBox.append("ID", idInput);
    meta.append(idBox);
    main.append(title, meta);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-remove";
    remove.append(icon("x"));
    remove.setAttribute("aria-label", `${t("web.remove")}: ${v.title}`);
    remove.title = t("web.remove");
    remove.addEventListener("click", () => {
      if (hasMotion()) { animate(li, { opacity: 0, x: 16, scale: 0.98 }, QUICK).then(onRemove); return; }
      if (reduced()) { onRemove(); return; }
      li.classList.add("is-leaving");
      li.addEventListener("animationend", () => onRemove(), { once: true });
    });
    li.append(thumb, main, remove);
    return li;
  }

  // ---- 切り替えの帯: 選んだボタンの下地をばねで動かす
  function placeIndicator(group) {
    let indicator = group.querySelector(".seg-indicator");
    if (!indicator) {
      indicator = document.createElement("span");
      indicator.className = "seg-indicator";
      indicator.setAttribute("aria-hidden", "true");
      group.prepend(indicator);
    }
    const active = group.querySelector('button[aria-checked="true"]');
    if (!active || !active.offsetWidth) { indicator.style.opacity = "0"; return; }
    indicator.style.opacity = "1";
    const box = { x: active.offsetLeft, y: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight };
    if (hasMotion()) {
      // 最初は動かさずに置き、そのあとは選んだ所へばねで動く
      const first = !indicator.dataset.placed;
      indicator.dataset.placed = "1";
      self.Motion.animate(indicator, box, first ? { duration: 0 } : SPRING);
      return;
    }
    indicator.style.width = `${box.width}px`;
    indicator.style.height = `${box.height}px`;
    indicator.style.transform = `translate(${box.x}px, ${box.y}px)`;
  }

  function radioKeys(group, choose) {
    group.addEventListener("keydown", (e) => {
      const buttons = Array.from(group.querySelectorAll("button"));
      const index = buttons.indexOf(document.activeElement);
      if (index < 0) return;
      const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const next = buttons[(index + step + buttons.length) % buttons.length];
      next.focus();
      choose(next);
    });
  }

  /** 画質プリセット: <select id="preset"> の選択肢からボタンを作る (選ぶと select を変えて change を起こす)。 */
  function setupPreset() {
    const group = document.querySelector('.segmented[data-for="preset"]');
    const select = $("preset");
    if (!group || !select) return;
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    group.setAttribute("aria-label", t("settings.preset"));
    const build = () => {
      group.querySelectorAll("button").forEach((b) => b.remove());
      for (const option of select.options) {
        if (!option.value) continue;
        const [name, detail] = splitLabel(option.textContent);
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "radio");
        button.dataset.value = option.value;
        button.append(name);
        if (detail) { const small = document.createElement("small"); small.textContent = detail; button.append(small); }
        group.append(button);
      }
      sync();
    };
    const sync = () => {
      for (const button of group.querySelectorAll("button")) {
        const on = button.dataset.value === select.value;
        button.setAttribute("aria-checked", String(on));
        button.tabIndex = on || (!select.value && button === group.querySelector("button")) ? 0 : -1;
      }
      placeIndicator(group);
    };
    const choose = (button) => {
      if (select.value === button.dataset.value) return;
      select.value = button.dataset.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      sync();
    };
    group.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) choose(b); });
    radioKeys(group, choose);
    // 幅・高さ・fps を手で変えると app.js がプリセットを「—」にするので、その後に合わせる
    for (const id of ["width", "height", "fps"]) $(id).addEventListener("input", () => setTimeout(sync, 0));
    select.addEventListener("change", sync);
    refreshers.push(sync);
    I18n.onChange(build);
    build();
  }

  // ---- スライダーと数の入力欄を結ぶ (数の入力欄の input・change を起こすので、app.js の処理はそのまま動く)
  function setupRanges() {
    for (const range of document.querySelectorAll(".range[data-sync]")) {
      const number = $(range.dataset.sync);
      const fill = () => {
        const min = Number(range.min), max = Number(range.max);
        range.style.setProperty("--fill", `${((Number(range.value) - min) / (max - min || 1)) * 100}%`);
      };
      const fromNumber = () => {
        const value = Number(number.value);
        if (Number.isFinite(value)) range.value = String(Math.min(Number(range.max), Math.max(Number(range.min), value)));
        fill();
      };
      range.addEventListener("input", () => {
        number.value = range.value;
        fill();
        number.dispatchEvent(new Event("input", { bubbles: true }));
      });
      range.addEventListener("change", () => number.dispatchEvent(new Event("change", { bubbles: true })));
      number.addEventListener("input", fromNumber);
      number.addEventListener("change", fromNumber);
      refreshers.push(fromNumber); // プリセットで app.js が値を入れたとき
      fromNumber();
    }
  }

  // ---- 今の設定の要約 (入力欄の値をそのまま見せる)
  function setupSummary() {
    const box = $("settingsSummary");
    if (!box) return;
    const optionName = (id) => { const s = $(id); return s && s.selectedOptions[0] ? splitLabel(s.selectedOptions[0].textContent)[0] : ""; };
    const update = () => {
      const w = $("width").value, h = $("height").value, fps = $("fps").value, duration = $("duration").value.trim();
      const items = [
        [`${w}×${h}`, t("settings.width") + " × " + t("settings.height")],
        [`${fps} fps`, t("settings.fps")],
        [duration ? `${duration} s` : t("web.full_length"), t("web.max_length")],
        [optionName("mode"), t("settings.mode")],
        [optionName("palette"), t("settings.palette")],
        [$("dither").selectedOptions[0] ? $("dither").selectedOptions[0].textContent : "", t("settings.dither")],
      ];
      box.textContent = "";
      for (const [value, label] of items) {
        const pill = document.createElement("span");
        pill.className = "pill";
        pill.title = label;
        const b = document.createElement("b");
        b.textContent = value;
        pill.append(b);
        box.append(pill);
      }
      // 負荷の目安の色 (app.js の updateLoadHint と同じ式。文は app.js が入れる)
      const blocks = Number(w) * Number(h) * ($("mode").value === "screen" ? 1 : 2) * Number(fps);
      $("loadHint").dataset.level = blocks > 160000 ? "heavy" : blocks > 60000 ? "medium" : "light";
      // 画像モードの設定は、画像モードを選んだときだけはっきり見せる (選べるのは同じ)
      for (const el of document.querySelectorAll('[data-depends="imageMode"]')) el.classList.toggle("is-off", !$("imageMode").checked);
    };
    document.addEventListener("input", schedule, true);
    document.addEventListener("change", schedule, true);
    refreshers.push(update);
    I18n.onChange(schedule);
    update();
  }

  // ---- ツールチップ: 「i」の title の文を吹き出しで見せる (title はスクリーンリーダー向けの aria-label に移す)
  function setupTooltips() {
    const tip = $("tooltip");
    if (!tip) return;
    let current = null;
    let timer = 0;
    const adopt = () => {
      for (const el of document.querySelectorAll(".tip")) {
        if (el.title) { el.dataset.tip = el.title; el.setAttribute("aria-label", el.title); el.removeAttribute("title"); }
      }
    };
    const show = (el) => {
      adopt();
      if (!el.dataset.tip) return;
      current = el;
      tip.textContent = el.dataset.tip;
      tip.hidden = false;
      const r = el.getBoundingClientRect();
      const box = tip.getBoundingClientRect();
      const above = r.top > box.height + 16;
      const top = above ? r.top - box.height - 10 : r.bottom + 10;
      const left = Math.min(window.innerWidth - box.width - 12, Math.max(12, r.left + r.width / 2 - box.width / 2));
      tip.style.setProperty("--origin", `${r.left + r.width / 2 - left}px ${above ? "100%" : "0%"}`);
      tip.style.top = `${top}px`;
      tip.style.left = `${left}px`;
      requestAnimationFrame(() => tip.classList.add("is-shown"));
    };
    const hide = () => {
      clearTimeout(timer);
      current = null;
      tip.classList.remove("is-shown");
      setTimeout(() => { if (!current) tip.hidden = true; }, 160);
    };
    document.addEventListener("pointerover", (e) => {
      const el = e.target.closest && e.target.closest(".tip");
      if (!el || el === current) return;
      clearTimeout(timer);
      timer = setTimeout(() => show(el), 90);
    });
    document.addEventListener("pointerout", (e) => { if (e.target.closest && e.target.closest(".tip")) hide(); });
    document.addEventListener("focusin", (e) => { if (e.target.classList && e.target.classList.contains("tip")) show(e.target); });
    document.addEventListener("focusout", (e) => { if (e.target.classList && e.target.classList.contains("tip")) hide(); });
    // 「i」を押してもラベルの入力欄やチェックボックスが動かないようにし、タッチでは押すと出す
    document.addEventListener("click", (e) => {
      const el = e.target.closest && e.target.closest(".tip");
      if (!el) { if (current) hide(); return; }
      e.preventDefault();
      if (current === el) hide(); else show(el);
    });
    window.addEventListener("scroll", () => { if (current) hide(); }, { passive: true });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && current) hide(); });
    I18n.onChange(adopt);
    adopt();
  }

  // ---- 知らせ (右下に積む。3 つまで)
  function toast(text, kind = "ok") {
    const box = $("toasts");
    if (!box || !text) return;
    const item = document.createElement("div");
    item.className = `toast${kind === "error" ? " is-error" : ""}`;
    item.append(icon(kind === "error" ? "circle-x" : kind === "file" ? "file-plus" : "circle-check"));
    const span = document.createElement("span");
    span.textContent = text;
    item.append(span);
    box.append(item);
    while (box.children.length > 3) box.firstElementChild.remove();
    animate(item, { opacity: [0, 1], y: [14, 0], scale: [0.96, 1] });
    const leave = () => {
      if (hasMotion()) { animate(item, { opacity: 0, y: 6, scale: 0.96 }, QUICK).then(() => item.remove()); return; }
      if (reduced()) { item.remove(); return; }
      item.classList.add("is-leaving");
      item.addEventListener("animationend", () => item.remove(), { once: true });
    };
    setTimeout(leave, kind === "error" ? 6500 : 3800);
    item.addEventListener("click", leave);
  }

  // ---- 作る段階
  let stage = null;
  let progressLoop = 0;
  function setStage(name) {
    const card = $("buildCard");
    if (!card || card.dataset.state !== "working") return;
    const index = STAGES.indexOf(name);
    if (index < 0 || index < STAGES.indexOf(stage)) return; // 戻らない
    stage = name;
    for (const li of document.querySelectorAll("#stepper li")) {
      const i = STAGES.indexOf(li.dataset.stage);
      const done = i < index || name === "done";
      if (done && !li.classList.contains("is-done")) animate(li.querySelector(".stepper-dot"), { scale: [0.7, 1] });
      li.classList.toggle("is-done", done);
      li.classList.toggle("is-active", i === index && name !== "done");
      li.classList.remove("is-error");
      if (i === index) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    }
  }

  function showProgress() {
    const bar = $("progress");
    const text = $("progressText");
    const value = Math.max(0, Math.min(1, Number(bar.value) || 0));
    text.textContent = value > 0 ? `${Math.floor(value * 100)}%` : "";
  }

  function buildStart() {
    const card = $("buildCard");
    card.dataset.state = "working";
    stage = null;
    $("buildError").hidden = true;
    $("results").hidden = true;
    $("imageResult").hidden = true;
    $("buildActivity").textContent = "";
    for (const li of document.querySelectorAll("#stepper li")) li.classList.remove("is-done", "is-active", "is-error");
    setStage("frames");
    cancelAnimationFrame(progressLoop);
    const loop = () => { showProgress(); if (card.dataset.state === "working") progressLoop = requestAnimationFrame(loop); };
    loop();
  }

  /** 変換を worker に渡したとき: Python の読み込みを待つ段階 (読み込み済みならすぐ次へ進む)。 */
  function handedOver() { setStage("prepare"); }

  /** worker のログ (キー付き) から段階と準備の状態を読む。 */
  function workerLog(msg) {
    if (!msg || !msg.key) return;
    if (msg.key === "web.loading_python") engine("loading");
    else if (msg.key === "web.ready") engine("ready");
    const next = STAGE_OF_KEY[msg.key];
    if (next) setStage(next);
  }

  function activity(text) {
    const el = $("buildActivity");
    if (!el || $("buildCard").dataset.state !== "working") return;
    el.textContent = String(text).trim();
  }

  function buildDone({ name, size, imageName, imageSize }) {
    const card = $("buildCard");
    setStage("done");
    card.dataset.state = "done";
    cancelAnimationFrame(progressLoop);
    showProgress();
    $("buildActivity").textContent = "";
    $("downloadName").textContent = name;
    $("downloadSize").textContent = formatBytes(size);
    $("results").hidden = false;
    animate($("results"), { opacity: [0, 1], y: [12, 0] });
    $("imageResult").hidden = !imageName;
    if (imageName) {
      $("imageDownloadName").textContent = imageName;
      $("imageDownloadSize").textContent = formatBytes(imageSize);
    }
    toast(t("web.build_done_title"));
    // 成功の印 (ボタンのチェック) は少しだけ見せて、またすぐ作り直せる見た目に戻す
    setTimeout(() => { if (card.dataset.state === "done") card.dataset.state = "finished"; }, 2400);
    if (!reduced()) $("results").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function buildFailed(text) {
    const card = $("buildCard");
    if (engineState !== "ready") engine("error"); // Python を読み込めなかったとき
    // 作っている途中なら、止まった段階に印を付ける (作っていないときの失敗は、段階の表示を変えない)
    if (card.dataset.state === "working") {
      const li = document.querySelector(`#stepper li[data-stage="${stage || "frames"}"]`);
      if (li) { li.classList.remove("is-active"); li.classList.add("is-error"); }
    }
    card.dataset.state = "error";
    cancelAnimationFrame(progressLoop);
    $("results").hidden = true;
    $("buildErrorText").textContent = text;
    $("buildError").hidden = false;
    animate($("buildError"), { opacity: [0, 1], x: [-6, 4, 0] }, EASE);
    $("logBox").open = true;
    toast(t("web.build_failed_title"), "error");
  }

  function setupDownloads() {
    for (const id of ["download", "imageDownload"]) {
      const link = $(id);
      link.addEventListener("click", () => {
        if (hasMotion()) { animate(link.querySelector("svg"), { y: [0, 5, 0] }, { duration: 0.45, ease: "easeOut" }); return; }
        link.classList.remove("is-pressed");
        void link.offsetWidth;
        link.classList.add("is-pressed");
      });
    }
  }

  // ---- プレビューの操作の帯: 元の動画 (previewSource) を再生・シークする。ブロックの絵は preview.js がその位置に合わせて描く
  function setupPlayer() {
    const source = $("previewSource");
    const bar = $("glassBar");
    const play = $("glassPlay");
    const seek = $("glassSeek");
    const player = $("player");
    const view = $("previewView");
    if (!source || !bar) return;
    let seeking = false;
    let loop = 0;
    const hasSource = () => Boolean(source.getAttribute("src")) && !$("previewSourceBox").hidden;
    const update = () => {
      const duration = source.duration;
      $("glassTime").textContent = formatTime(source.currentTime);
      $("glassLength").textContent = formatTime(duration);
      if (!seeking && Number.isFinite(duration) && duration > 0) {
        seek.value = String(Math.round((source.currentTime / duration) * 1000));
        seek.style.setProperty("--fill", `${(source.currentTime / duration) * 100}%`);
      }
      play.classList.toggle("is-playing", !source.paused);
      const label = t(source.paused ? "web.preview_play" : "web.preview_pause");
      play.setAttribute("aria-label", label);
      play.title = label;
    };
    const tick = () => { update(); if (!source.paused) loop = requestAnimationFrame(tick); };
    const layout = () => {
      const show = hasSource();
      bar.hidden = !show;
      view.hidden = !show;
      player.classList.toggle("no-source", !show);
      if (!show) setView("split");
      placeIndicator(view);
      update();
    };
    const toggle = () => { if (source.paused) source.play().catch(() => {}); else source.pause(); };
    play.addEventListener("click", toggle);
    source.addEventListener("click", toggle);
    source.addEventListener("play", () => { cancelAnimationFrame(loop); tick(); });
    source.addEventListener("pause", update);
    source.addEventListener("seeked", update);
    source.addEventListener("loadedmetadata", layout);
    source.addEventListener("emptied", layout);
    seek.addEventListener("input", () => {
      seeking = true;
      if (Number.isFinite(source.duration)) source.currentTime = (Number(seek.value) / 1000) * source.duration;
      seek.style.setProperty("--fill", `${Number(seek.value) / 10}%`);
      $("glassTime").textContent = formatTime(source.currentTime);
    });
    seek.addEventListener("change", () => { seeking = false; update(); });
    // 並べる・元だけ・Minecraft だけ
    const setView = (name) => {
      const changed = player.dataset.view !== name;
      player.dataset.view = name;
      if (changed) for (const figure of player.querySelectorAll(".preview figure")) animate(figure, { opacity: [0, 1], scale: [0.985, 1] }, EASE);
      for (const b of view.querySelectorAll("button")) {
        const on = b.dataset.view === name;
        b.setAttribute("aria-checked", String(on));
        b.tabIndex = on ? 0 : -1;
      }
      placeIndicator(view);
    };
    view.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setView(b.dataset.view); });
    radioKeys(view, (b) => setView(b.dataset.view));
    // preview.js がカードを出したり、元の動画の枠を隠したりしたら合わせる
    const observer = new MutationObserver(() => requestAnimationFrame(layout));
    observer.observe($("previewSourceBox"), { attributes: true, attributeFilter: ["hidden"] });
    observer.observe($("previewCard"), { attributes: true, attributeFilter: ["hidden"] });
    window.addEventListener("resize", () => placeIndicator(view));
    I18n.onChange(() => { update(); requestAnimationFrame(() => placeIndicator(view)); });
    setView("split");
  }

  // ---- 最初の画面の舞台: 左は元の映像 (<video>)、右はこの変換ツールの出力 (web/media/hero_blocks.json) を
  // プレビューと同じ BlockPreview とゲームと同じ codec.js で復元して描く。境目はドラッグ・矢印キーで動かせる。
  // 読み込めないとき (組み立てていない web/ をそのまま開いたときなど) は、最初のコマの絵 (CSS の背景) のまま。
  function setupStage() {
    const stage = $("stage");
    const video = $("heroVideo");
    const canvas = $("heroCanvas");
    const handle = $("heroHandle");
    const play = $("stagePlay");
    if (!stage || !canvas || !canvas.getContext) return;
    let split = 0.5;
    let player = null;
    let image = null;
    let drawn = -1;
    let wanted = !reduced(); // 動きを減らす設定では、押すまで再生しない
    let visible = true;
    const setSplit = (value) => {
      split = Math.min(0.97, Math.max(0.03, value));
      stage.style.setProperty("--split", `${split * 100}%`);
      handle.setAttribute("aria-valuenow", String(Math.round(split * 100)));
    };
    const fromPointer = (e) => { const r = stage.getBoundingClientRect(); setSplit((e.clientX - r.left) / r.width); };
    stage.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".stage-play")) return;
      stage.setPointerCapture(e.pointerId);
      stage.classList.add("is-dragging");
      fromPointer(e);
    });
    stage.addEventListener("pointermove", (e) => { if (stage.classList.contains("is-dragging")) fromPointer(e); });
    const stop = () => stage.classList.remove("is-dragging");
    stage.addEventListener("pointerup", stop);
    stage.addEventListener("pointercancel", stop);
    handle.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowLeft" ? -0.04 : e.key === "ArrowRight" ? 0.04 : 0;
      if (step) { e.preventDefault(); setSplit(split + step); }
      else if (e.key === "Home") { e.preventDefault(); setSplit(0); }
      else if (e.key === "End") { e.preventDefault(); setSplit(1); }
    });
    const draw = () => {
      if (!player) return;
      const frame = Math.floor((video.currentTime || 0) * player.fps + 1e-6) % player.count;
      if (frame === drawn) return;
      player.render(frame, image);
      canvas.getContext("2d").putImageData(image, 0, 0);
      drawn = frame;
    };
    const tick = () => { draw(); if (!video.paused) requestAnimationFrame(tick); };
    const label = () => {
      const playing = !video.paused;
      play.classList.toggle("is-playing", playing);
      const text = t(playing ? "web.preview_pause" : "web.preview_play");
      play.setAttribute("aria-label", text);
      play.title = text;
    };
    const sync = () => { if (wanted && visible && player) video.play().catch(() => {}); else video.pause(); };
    video.addEventListener("play", () => { label(); tick(); });
    video.addEventListener("pause", label);
    video.addEventListener("seeked", draw);
    play.addEventListener("click", () => { wanted = video.paused; sync(); });
    play.hidden = true;
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries) => { visible = entries[0].isIntersecting; sync(); }, { threshold: 0.15 }).observe(stage);
    }
    I18n.onChange(label);
    (async () => {
      const response = await fetch("media/hero_blocks.json");
      if (!response.ok) return;
      const sample = await response.json();
      const codec = await import("./app/codec.js");
      const spec = { ...sample.spec, top: Uint8Array.from(atob(sample.spec.top), (c) => c.charCodeAt(0)) };
      const made = BlockPreview.create(codec, spec, sample.frameData);
      canvas.width = made.width;
      canvas.height = made.height;
      image = canvas.getContext("2d").createImageData(made.width, made.height);
      player = made;
      draw();
      video.src = "media/hero.mp4";
      play.hidden = false;
      label();
      sync();
    })().catch(() => {});
    // 開いたときに 1 回だけ境目がすべって、比べられることを見せる
    if (hasMotion()) {
      setSplit(0.9);
      setTimeout(() => {
        if (stage.classList.contains("is-dragging")) return;
        self.Motion.animate(0.9, 0.5, { type: "spring", bounce: 0.1, visualDuration: 1.2,
          onUpdate: (value) => { if (!stage.classList.contains("is-dragging")) setSplit(value); } });
      }, 700);
    } else {
      setSplit(0.5);
    }
  }

  // ---- 設定の見本: 大きさ・パレット・ディザを選ぶと、その設定での本物の出力の絵に替える (web/media/showcase.json)。
  // 「この設定を使う」で、下の変換ツールの入力欄に同じ値を入れる (入力欄の input・change を起こすので app.js の処理を通る)
  function setupExplorer() {
    const box = $("explorer");
    if (!box) return;
    const groups = { size: $("exSize"), palette: $("exPalette"), dither: $("exDither") };
    const state = { size: "128x72", palette: "auto", dither: "blue_noise" };
    const img = $("explorerImage");
    let items = [];
    const show = () => {
      const file = `${state.size}_${state.palette}_${state.dither}.png`;
      const [w, h] = state.size.split("x").map(Number);
      if (!img.getAttribute("src").endsWith(file)) {
        img.src = `media/showcase/${file}`;
        img.width = w;
        img.height = h;
        animate(img, { opacity: [0.35, 1] }, { duration: 0.35, ease: "easeOut" });
      }
      const item = items.find((i) => i.file === file);
      $("explorerReadout").textContent = item
        ? t("web.showcase_readout", { blocks: item.blocks.toLocaleString(I18n.language), colours: item.colours }) : "";
    };
    for (const [key, group] of Object.entries(groups)) {
      const choose = (button) => {
        state[key] = button.dataset.value;
        for (const b of group.querySelectorAll("button")) {
          const on = b === button;
          b.setAttribute("aria-checked", String(on));
          b.tabIndex = on ? 0 : -1;
        }
        placeIndicator(group);
        show();
      };
      group.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) choose(b); });
      radioKeys(group, choose);
      for (const b of group.querySelectorAll("button")) b.tabIndex = b.getAttribute("aria-checked") === "true" ? 0 : -1;
    }
    const place = () => { for (const group of Object.values(groups)) placeIndicator(group); };
    refreshers.push(place);
    I18n.onChange(() => { show(); requestAnimationFrame(place); });
    fetch("media/showcase.json").then((r) => (r.ok ? r.json() : { items: [] })).then((data) => { items = data.items || []; show(); }).catch(() => {});
    $("explorerApply").addEventListener("click", () => {
      const [w, h] = state.size.split("x");
      const set = (id, value, events) => { const el = $(id); el.value = value; for (const type of events) el.dispatchEvent(new Event(type, { bubbles: true })); };
      set("width", w, ["input", "change"]);
      set("height", h, ["input", "change"]);
      set("palette", state.palette, ["change"]);
      set("dither", state.dither, ["change"]);
      schedule();
      toast(t("web.showcase_applied"));
      $("stepSettings").scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
    });
    place();
  }

  // カードの見出しの「1. 」は、左の番号の印と重なるので見出しからは外す (翻訳の文はそのまま)
  function stripNumbers() {
    for (const h of document.querySelectorAll(".step-title[data-i18n]")) h.textContent = h.textContent.replace(/^\s*\d+\s*[.．、]\s*/, "");
  }

  function setupDisclosures() {
    for (const details of document.querySelectorAll("details.disclosure")) {
      details.addEventListener("toggle", () => {
        if (!details.open) return;
        const body = details.querySelector(":scope > .disclosure-body, :scope > pre");
        animate(body, { opacity: [0, 1], y: [-6, 0] }, EASE);
      });
    }
  }

  // ---- ボタンを押したときの小さなばね (Motion の press)
  function setupPress() {
    if (!hasMotion() || !self.Motion.press) return;
    self.Motion.press(".button, .glass-play, .file-remove, .icon-button", (el) => {
      if (el.disabled) return undefined;
      animate(el, { scale: 0.96 }, { duration: 0.1 });
      return () => animate(el, { scale: 1 }, SPRING);
    });
  }

  function init() {
    renderIcons();
    setupDisclosures();
    setupPress();
    setupHeader();
    setupNav();
    setupIntro();
    setupVersion();
    setupDropzone();
    setupPreset();
    setupRanges();
    setupSummary();
    setupTooltips();
    setupDownloads();
    setupPlayer();
    setupStage();
    setupExplorer();
    window.addEventListener("resize", schedule);
    I18n.onChange(() => { engine(engineState); stripNumbers(); schedule(); });
    stripNumbers();
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }

  return { icon, renderIcons, fileCard, toast, engine, buildStart, handedOver, workerLog, activity, buildDone, buildFailed, refresh: schedule,
           formatBytes, formatTime, splitLabel };
})();
