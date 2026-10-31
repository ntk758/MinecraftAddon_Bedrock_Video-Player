// Block Video Player Web Converter: カメラで撮って、そのまま動画の一覧に加える
// getUserMedia でカメラ (とマイク) を開き、MediaRecorder で最大 MAX_SECONDS 秒録画する。録画はブラウザの中だけに置き、
// ファイルを選んだときと同じく app.js の addFiles に渡す。マイクを使えないときは映像だけを録る。
"use strict";

const Camera = (() => {
  const MAX_SECONDS = 30;
  const $ = (id) => document.getElementById(id);
  const t = (key, params) => I18n.t(key, params);
  let stream = null;
  let recorder = null;
  let chunks = [];
  let started = 0;
  let timer = 0;
  let onRecorded = null;

  function supported() {
    return Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && typeof MediaRecorder !== "undefined");
  }

  /** 録画の形式: ブラウザが録れる最初のもの (Chrome・Firefox は WebM、Safari は MP4)。 */
  function pickType() {
    const types = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"];
    return types.find((type) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(type)) || "";
  }

  function status(text) {
    $("cameraStatus").textContent = text;
  }

  async function open() {
    if (!supported()) { status(t("web.camera_unsupported")); $("cameraPanel").hidden = false; return; }
    $("cameraPanel").hidden = false;
    status(t("web.camera_hint", { max: MAX_SECONDS }));
    try {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      } catch (e) {
        stream = await navigator.mediaDevices.getUserMedia({ video: true }); // マイクが使えなければ映像だけ
      }
      const live = $("cameraLive");
      live.srcObject = stream;
      await live.play().catch(() => {});
      $("cameraStart").disabled = false;
    } catch (e) {
      status(t("web.camera_failed", { error: e.message || e.name || e }));
    }
  }

  function close() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
    if (stream) for (const track of stream.getTracks()) track.stop();
    stream = null;
    clearInterval(timer);
    $("cameraLive").srcObject = null;
    $("cameraPanel").hidden = true;
    $("cameraStart").hidden = false;
    $("cameraStop").hidden = true;
    $("cameraStart").disabled = true;
  }

  function start() {
    if (!stream) return;
    const type = pickType();
    recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      clearInterval(timer);
      const mime = (recorder.mimeType || type || "video/webm").split(";")[0];
      const blob = new Blob(chunks, { type: mime });
      const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
      const file = new File([blob], `camera_${stamp}.${mime.includes("mp4") ? "mp4" : "webm"}`, { type: mime });
      close();
      if (onRecorded && blob.size) onRecorded(file);
    };
    recorder.start(250);
    started = performance.now();
    $("cameraStart").hidden = true;
    $("cameraStop").hidden = false;
    status(t("web.camera_recording", { seconds: 0 }));
    timer = setInterval(() => {
      const seconds = Math.floor((performance.now() - started) / 1000);
      status(t("web.camera_recording", { seconds }));
      if (seconds >= MAX_SECONDS) stop();
    }, 250);
  }

  function stop() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", () => {
      $("cameraOpen").addEventListener("click", open);
      $("cameraClose").addEventListener("click", close);
      $("cameraStart").addEventListener("click", start);
      $("cameraStop").addEventListener("click", stop);
    });
  }

  return {
    MAX_SECONDS, supported,
    /** 録画を終えたときに呼ぶ関数 (File を受け取る)。 */
    onRecorded(callback) { onRecorded = callback; },
  };
})();
