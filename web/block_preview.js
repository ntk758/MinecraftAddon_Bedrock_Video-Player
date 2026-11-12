// Block & Image Video Player Web Converter: できあがりのプレビューの 1 コマを描く
// 変換したデータ (FRAME_DATA) を、ゲームと同じ codec.js で再生しながら 1 コマずつ復元して描く。絵を先に全部作って
// メモリに置かないので、長い動画でも全部のコマを元の fps のまま見られる (使うメモリは盤面 1 枚ぶん)。
// 描く規則は mvcodec/preview.py の render_preview (Windows 版の GIF) とそろえてある:
//  - 盤面は main.js と同じ手順で復元する (キーフレームからシーク、2.5D は expandPlanes で立体に組み立てる)
//  - 色は mvcodec/decode.py の iter_decoded_frames と同じく、番号が変わったマス (キーフレームは全部) だけを
//    そのフレームの GOP のパレットで塗り直す (ゲームでも置き替えないブロックはそのまま残る)
//  - 立体は mvcodec/volume.py の iso_view と同じ等角投影 (立方体の形は Python から受け取る)
// codec.js (ES モジュール) は使う側が読み込んで渡す (ブラウザは app/codec.js、テストはリポジトリの codec.js)。
"use strict";

const BlockPreview = (() => {
  const BACKGROUND = [24, 26, 32];
  const SHADE = [0, 1.0, 0.82, 0.62]; // 面: 1 上面・2 正面 (南)・3 側面 (東)
  const PALETTE_ROWS = 128;
  const MAX_CACHED_GOPS = 3;

  /** 明るさを掛けた色の表 [面][元の値] (numpy の float32 の掛け算と切り捨てに合わせる)。 */
  function shadeTable() {
    const table = SHADE.map((s) => {
      const f = Math.fround(s);
      const row = new Uint8Array(256);
      for (let c = 0; c < 256; c++) row[c] = Math.min(255, Math.floor(Math.fround(c * f)));
      return row;
    });
    return table;
  }

  const asBytes = (value) => (value instanceof Uint8Array ? value : new Uint8Array(value));

  /**
   * spec は mvcodec/preview.py の preview_spec、data は FRAME_DATA (JSON を読んだもの)。
   * 返す物の render(frame, imageData) が frame 番目の絵を imageData (width × height) に描く。
   */
  function create(codec, spec, data) {
    const entries = codec.parseFrameIndexV4(codec.decodeUTF16BinaryToBytes(data.index));
    const volume = Boolean(spec.volume);
    const heightfield = codec.isHeightfield(data);
    const { width: w, height: h } = data;
    const depth = volume ? data.depth : 1;
    const cells = w * h * depth;
    const streamSize = codec.streamSizeOf(data);
    const top = asBytes(spec.top);
    const side = volume ? asBytes(spec.side) : null;

    // 復元の状態 (main.js の VideoPlayer と同じ配列)
    const decoded = new Int16Array(streamSize).fill(-1);
    const scratch = new Int16Array(streamSize);
    const expanded = heightfield ? new Int16Array(streamSize) : null;
    const board = heightfield ? new Int16Array(cells) : null;
    let tiles = null;
    // 表示中の盤面 (番号) と、塗った色 (iter_decoded_frames の index_board / rgb_board)
    const shown = new Int16Array(cells);
    const rgbTop = new Uint8Array(cells * 3);
    const rgbSide = volume ? new Uint8Array(cells * 3) : null;
    let decodedFrame = -1;
    const gopCache = new Map();

    function reset() {
      decoded.fill(-1);
      if (expanded) expanded.fill(-32768); // 平面の値に現れない値: 最初の組み立てで全部の列を作る
      tiles = codec.createTileDictionary(data);
      shown.fill(-1);
      rgbTop.fill(0);
      if (rgbSide) rgbSide.fill(0);
      decodedFrame = -1;
    }

    function gopBytes(gopId) {
      let bytes = gopCache.get(gopId);
      if (bytes) return bytes;
      bytes = codec.decodeUTF16BinaryToBytes(data.chunks[gopId]);
      if (data.format === codec.FORMAT_V6) bytes = codec.unpackGop(bytes);
      if (gopCache.size >= MAX_CACHED_GOPS) gopCache.delete(gopCache.keys().next().value);
      gopCache.set(gopId, bytes);
      return bytes;
    }

    /** 1 フレーム進める (decoded は frame - 1 の状態)。 */
    function step(frame) {
      const entry = entries[frame];
      decodedFrame = frame;
      if (entry.length <= 0) return; // 省略フレームは盤面が変わらない
      const bytes = gopBytes(entry.gopId);
      const keyframe = codec.decodeFrameInto(bytes, entry.offset, entry.offset + entry.length, decoded, scratch, data, tiles);
      decoded.set(scratch);
      let next = decoded;
      if (heightfield) {
        codec.expandPlanes(decoded, expanded, board, data);
        next = board;
      }
      const base = entry.gopId * PALETTE_ROWS * 3;
      for (let p = 0; p < cells; p++) {
        const level = next[p];
        if (level >= 0 && (keyframe || level !== shown[p])) {
          const c = base + level * 3;
          const q = p * 3;
          rgbTop[q] = top[c]; rgbTop[q + 1] = top[c + 1]; rgbTop[q + 2] = top[c + 2];
          if (rgbSide) { rgbSide[q] = side[c]; rgbSide[q + 1] = side[c + 1]; rgbSide[q + 2] = side[c + 2]; }
        }
        shown[p] = level;
      }
    }

    /** frame 番目まで復元する。続きなら前から進め、そうでなければ手前のキーフレームから (main.js の decodeTo と同じ)。 */
    function decodeTo(frame) {
      if (frame === decodedFrame) return;
      const start = codec.findKeyframeAtOrBefore(entries, frame);
      if (!(decodedFrame >= start && decodedFrame < frame)) {
        reset();
        for (let f = start; f <= frame; f++) step(f);
        return;
      }
      for (let f = decodedFrame + 1; f <= frame; f++) step(f);
    }

    // ---- 描く ----
    let canvasW = w;
    let canvasH = h;
    let iso = null;
    if (volume) {
      const { hw, hh, vh } = spec;
      canvasW = (w + depth) * hw + 2 * hw + 2;
      canvasH = (w + depth) * hh + h * vh + vh + 2;
      iso = {
        hw, hh, vh,
        sx: Int32Array.from(spec.sprite_x), sy: Int32Array.from(spec.sprite_y), face: Uint8Array.from(spec.sprite_face),
        shiftX: (depth - 1) * hw + hw, shiftY: (h - 1) * vh + vh + 1,
        zbuf: new Int32Array(canvasW * canvasH), shade: shadeTable(), air: spec.air ?? 127,
      };
    }

    function drawFlat(out) {
      for (let p = 0, q = 0; p < cells; p++, q += 3) {
        out[p * 4] = rgbTop[q];
        out[p * 4 + 1] = rgbTop[q + 1];
        out[p * 4 + 2] = rgbTop[q + 2];
        out[p * 4 + 3] = 255;
      }
    }

    /** iso_view と同じ: 東・上・南のどれかが空いているボクセルを、手前 (x + y + z が大きい) ほど後から描く。 */
    function drawVolume(out) {
      const { hw, hh, vh, sx, sy, face, shiftX, shiftY, zbuf, shade, air } = iso;
      for (let i = 0; i < canvasW * canvasH; i++) {
        out[i * 4] = BACKGROUND[0]; out[i * 4 + 1] = BACKGROUND[1]; out[i * 4 + 2] = BACKGROUND[2]; out[i * 4 + 3] = 255;
      }
      zbuf.fill(-1);
      const plane = w * h;
      const n = sx.length;
      for (let d = 0; d < depth; d++) {
        for (let row = 0; row < h; row++) {
          for (let col = 0; col < w; col++) {
            const p = d * plane + row * w + col;
            if (shown[p] === air) continue;
            const east = col + 1 < w && shown[p + 1] !== air;
            const up = row > 0 && shown[p - w] !== air;
            const south = d + 1 < depth && shown[p + plane] !== air;
            if (east && up && south) continue;
            const wy = h - 1 - row;
            const near = col + wy + d;
            const ox = (col - d) * hw + shiftX;
            const oy = (col + d) * hh - wy * vh + shiftY;
            const q = p * 3;
            for (let k = 0; k < n; k++) {
              const x = ox + sx[k];
              const y = oy + sy[k];
              if (x < 0 || x >= canvasW || y < 0 || y >= canvasH) continue;
              const pixel = y * canvasW + x;
              // 同じ近さなら後から描いたものが勝つ (numpy の安定ソートの後に書いたものと同じ)
              if (near < zbuf[pixel]) continue;
              zbuf[pixel] = near;
              const f = face[k];
              const rgb = f === 1 ? rgbTop : rgbSide;
              const table = shade[f];
              out[pixel * 4] = table[rgb[q]];
              out[pixel * 4 + 1] = table[rgb[q + 1]];
              out[pixel * 4 + 2] = table[rgb[q + 2]];
            }
          }
        }
      }
    }

    reset();
    return {
      width: canvasW,
      height: canvasH,
      count: entries.length,
      fps: spec.fps,
      volume,
      /** frame 番目の絵を imageData (または RGBA の配列) に描く。 */
      render(frame, image) {
        decodeTo(Math.max(0, Math.min(entries.length - 1, frame)));
        const out = image.data ?? image;
        if (volume) drawVolume(out);
        else drawFlat(out);
      },
    };
  }

  return { create };
})();

if (typeof module !== "undefined" && module.exports) module.exports = BlockPreview;
