// Block Video Player Web Converter: MMD のモデル (.pmx) と踊り (.vmd) を立体にする
// モデルのフォルダ (.pmx とテクスチャ) と踊りを選ぶと、動画の一覧に「MMD の立体」として加える。変換は worker.js が
// Windows 版と同じ mvcodec/mmd.py (Pyodide) で .vox の立体アニメにし、あとは .vox と同じに扱う。
"use strict";

const Mmd = (() => {
  // 人物の高さ (ブロック) の上限: Windows 版の MMD_MAX_HEIGHT など (tests/test_web_parity.py で確かめる)
  const LIMITS = { normal: 64, stage: 40, extreme: 128, extremeStage: 80 };
  // モデルのフォルダから読むファイル (モデルとテクスチャ)。ほかのファイル (.psd・説明書など) は読まない
  const MODEL_FILES = /\.(pmx|png|jpe?g|bmp|tga|dds|gif|webp)$/i;
  const $ = (id) => document.getElementById(id);
  const t = (key, params) => I18n.t(key, params);
  let folderFiles = []; // [{ path, file }] (path はフォルダからの相対パス)
  let onAdd = null;

  /** 人物の高さの上限 (だまし絵・エクストリームで変わる)。 */
  function heightLimit(anamorphic, extreme) {
    if (anamorphic) return extreme ? LIMITS.extremeStage : LIMITS.stage;
    return extreme ? LIMITS.extreme : LIMITS.normal;
  }

  /** 選んだフォルダのファイルから、モデルとテクスチャだけを、フォルダからの相対パスで取り出す。 */
  function modelFiles(fileList) {
    return [...fileList]
      .map((file) => ({ file, path: (file.webkitRelativePath || file.name).split("/").slice(1).join("/") || file.name }))
      .filter((item) => MODEL_FILES.test(item.path));
  }

  function refresh() {
    const pmx = folderFiles.filter((item) => /\.pmx$/i.test(item.path));
    const select = $("mmdModel");
    select.innerHTML = "";
    for (const item of pmx) {
      const option = document.createElement("option");
      option.value = item.path;
      option.textContent = item.path;
      select.append(option);
    }
    $("mmdPick").hidden = pmx.length < 2;
    $("mmdStatus").textContent = folderFiles.length && !pmx.length ? t("web.mmd_no_pmx") : "";
    $("mmdAdd").disabled = !(pmx.length && $("mmdMotion").files.length);
  }

  function refreshText() {
    $("mmdExtremeLabel").textContent = t("settings.mmd_extreme", { height: LIMITS.extreme, stage: LIMITS.extremeStage });
    $("mmdHint").textContent = t("web.mmd_hint", { height: LIMITS.normal, stage: LIMITS.stage });
    if ($("mmdStatus").textContent) refresh();
  }

  function add() {
    const pmx = $("mmdModel").value;
    const motion = $("mmdMotion").files[0];
    if (!pmx || !motion || !onAdd) return;
    const title = pmx.split("/").pop().replace(/\.pmx$/i, "");
    onAdd({ title, files: folderFiles, pmx, motion });
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", () => {
      $("mmdFolder").addEventListener("change", (e) => { folderFiles = modelFiles(e.target.files); refresh(); });
      $("mmdMotion").addEventListener("change", refresh);
      $("mmdAdd").addEventListener("click", add);
    });
  }

  return {
    LIMITS, heightLimit, refreshText,
    /** 一覧に加えるときに呼ぶ関数 ({ title, files, pmx, motion } を受け取る)。 */
    onAdd(callback) { onAdd = callback; },
    get extreme() { return $("mmdExtreme").checked; },
  };
})();

if (typeof module !== "undefined") module.exports = Mmd; // テスト (Node) 用
