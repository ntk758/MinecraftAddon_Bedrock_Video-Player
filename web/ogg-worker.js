// Block & Image Video Player Web Converter: 音声を Ogg Vorbis にする (画面とは別のスレッド)
// エンコーダーは web/vendor/wasm-media-encoders (MIT、npm の 0.7.0 をそのまま写したもの)。その UMD は大域に
// `var e, t` を宣言するので、画面のスクリプト (app.js の t など) とぶつからないよう、この Worker の中だけで読む。
// 受け取る: { left, right (Float32Array), rate, quality, chunkSeconds }
// 返す: { chunks: [[番号, "ogg", Uint8Array], ...] } か { error }
"use strict";

importScripts("vendor/wasm-media-encoders/WasmMediaEncoder.min.js");

let encoderPromise = null;

function encoder() {
  if (!encoderPromise) {
    encoderPromise = (async () => {
      const response = await fetch("vendor/wasm-media-encoders/ogg.wasm");
      if (!response.ok) throw new Error(`ogg.wasm: HTTP ${response.status}`);
      return WasmMediaEncoder.createEncoder("audio/ogg", await response.arrayBuffer());
    })();
    encoderPromise.catch(() => { encoderPromise = null; });
  }
  return encoderPromise;
}

/** 左右の音を chunkSeconds ごとの OGG にする。区切りごとに別の OGG のファイル (Windows 版と同じ)。 */
function encodeChunks(enc, left, right, rate, quality, chunkSeconds) {
  const chunks = [];
  const step = chunkSeconds * rate;
  const slice = 4096;
  for (let start = 0, index = 0; start < left.length; start += step, index++) {
    enc.configure({ channels: 2, sampleRate: rate, vbrQuality: quality });
    const end = Math.min(left.length, start + step);
    const parts = [];
    // encode が返す配列はエンコーダーのメモリなので、すぐに写す
    for (let p = start; p < end; p += slice) {
      parts.push(enc.encode([left.subarray(p, Math.min(end, p + slice)), right.subarray(p, Math.min(end, p + slice))]).slice());
    }
    parts.push(enc.finalize().slice());
    const bytes = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
    let offset = 0;
    for (const part of parts) { bytes.set(part, offset); offset += part.length; }
    chunks.push([index, "ogg", bytes]);
  }
  return chunks;
}

self.onmessage = async (event) => {
  const { left, right, rate, quality, chunkSeconds } = event.data;
  try {
    const chunks = encodeChunks(await encoder(), left, right, rate, quality, chunkSeconds);
    self.postMessage({ chunks }, chunks.map(([, , bytes]) => bytes.buffer));
  } catch (e) {
    self.postMessage({ error: String(e && e.message ? e.message : e) });
  }
};
