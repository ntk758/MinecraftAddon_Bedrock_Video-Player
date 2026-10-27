// 動画ファイル (MP4・MOV) から AAC の音声トラックだけを取り出し、ADTS (AAC の素のストリーム) にする。
// iPhone の Safari / Chrome (どちらも WebKit) は、動画ファイルを丸ごと decodeAudioData に渡すとデコードに失敗する
// ("Decoding failed") が、ADTS にした AAC は読める。動画の本体 (mdat) は読まず、目次 (moov) と音声の部分だけを
// File.slice で読むので、大きな動画でもメモリを使わない。
"use strict";

(function () {
  // ADTS のヘッダーに書ける標本化周波数 (この表の番号を書く)
  const SAMPLE_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350];

  async function readBytes(file, start, end) {
    return new Uint8Array(await file.slice(start, end).arrayBuffer());
  }

  const typeAt = (bytes, pos) => String.fromCharCode(bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3]);
  const u32 = (bytes, pos) => ((bytes[pos] << 24) | (bytes[pos + 1] << 16) | (bytes[pos + 2] << 8) | bytes[pos + 3]) >>> 0;
  const u64 = (bytes, pos) => u32(bytes, pos) * 4294967296 + u32(bytes, pos + 4);

  /** ファイルの一番外側の box (moov など) を探して { start, size, header } を返す。無ければ null。 */
  async function findTopBox(file, type) {
    let pos = 0;
    while (pos + 8 <= file.size) {
      const head = await readBytes(file, pos, Math.min(file.size, pos + 16));
      let size = u32(head, 0);
      let header = 8;
      if (size === 1) {
        if (head.length < 16) return null;
        size = u64(head, 8);
        header = 16;
      } else if (size === 0) {
        size = file.size - pos;
      }
      if (size < header) return null;
      if (typeAt(head, 4) === type) return { start: pos, size, header };
      pos += size;
    }
    return null;
  }

  /** bytes の [start, end) に並ぶ box を { type, start (中身の先頭), end } の配列にする。 */
  function boxes(bytes, start, end) {
    const list = [];
    let pos = start;
    while (pos + 8 <= end) {
      let size = u32(bytes, pos);
      let header = 8;
      if (size === 1) {
        size = u64(bytes, pos + 8);
        header = 16;
      } else if (size === 0) {
        size = end - pos;
      }
      if (size < header || pos + size > end) break;
      list.push({ type: typeAt(bytes, pos + 4), start: pos + header, end: pos + size });
      pos += size;
    }
    return list;
  }

  const child = (bytes, box, type) => boxes(bytes, box.start, box.end).find((b) => b.type === type) ?? null;

  /** MPEG-4 の記述子の長さ (1〜4 バイト、上位ビットが続きの印) を読む。 */
  function descriptorLength(bytes, cursor) {
    let length = 0;
    for (let i = 0; i < 4; i++) {
      const b = bytes[cursor.pos++];
      length = (length << 7) | (b & 0x7f);
      if (!(b & 0x80)) break;
    }
    return length;
  }

  /** esds から AudioSpecificConfig (AAC の種類・標本化周波数・チャンネル数を書いたバイト列) を取り出す。 */
  function audioSpecificConfig(bytes, esds) {
    const cursor = { pos: esds.start + 4 }; // version と flags を飛ばす
    while (cursor.pos < esds.end) {
      const tag = bytes[cursor.pos++];
      const length = descriptorLength(bytes, cursor);
      if (tag === 0x03) {
        // ES_Descriptor: ES_ID (2)・フラグ (1) と、フラグで決まる追加の項目の後に記述子が続く
        cursor.pos += 2;
        const flags = bytes[cursor.pos++];
        if (flags & 0x80) cursor.pos += 2;
        if (flags & 0x40) cursor.pos += 1 + bytes[cursor.pos];
        if (flags & 0x20) cursor.pos += 2;
      } else if (tag === 0x04) {
        cursor.pos += 13; // objectTypeIndication・streamType・bufferSize・maxBitrate・avgBitrate
      } else if (tag === 0x05) {
        return bytes.subarray(cursor.pos, cursor.pos + length);
      } else {
        cursor.pos += length;
      }
    }
    return null;
  }

  /** AudioSpecificConfig を ADTS のヘッダーの値 { profile, rateIndex, channels } にする。書けない形なら null。 */
  function adtsParams(config) {
    let bit = 0;
    const read = (n) => {
      let value = 0;
      for (let i = 0; i < n; i++, bit++) value = (value << 1) | ((config[bit >> 3] >> (7 - (bit & 7))) & 1);
      return value;
    };
    const objectType = () => {
      const type = read(5);
      return type === 31 ? 32 + read(6) : type;
    };
    const rateIndex = () => {
      const index = read(4);
      return index === 15 ? SAMPLE_RATES.indexOf(read(24)) : index;
    };
    let type = objectType();
    let index = rateIndex();
    const channels = read(4);
    if (type === 5 || type === 29) {
      // HE-AAC (SBR・PS): ADTS には中の AAC-LC として書く (デコーダーが SBR を見つけて元の周波数に戻す)
      rateIndex();
      type = objectType();
    }
    if (type < 1 || type > 4 || index < 0 || index >= SAMPLE_RATES.length || channels < 1 || channels > 7) return null;
    return { profile: type - 1, rateIndex: index, channels };
  }

  /** 音のトラックの mp4a のサンプル記述から esds を探す (QuickTime の MOV は wave の中にあることがある)。 */
  function findEsds(bytes, entry) {
    // AudioSampleEntry の決まった部分の長さは、QuickTime の版 (0・1・2) で違う
    const version = (bytes[entry.start + 8] << 8) | bytes[entry.start + 9];
    const fixed = version === 1 ? 44 : version === 2 ? 64 : 28;
    for (const box of boxes(bytes, entry.start + fixed, entry.end)) {
      if (box.type === "esds") return box;
      if (box.type === "wave") {
        const inner = child(bytes, box, "esds");
        if (inner) return inner;
      }
    }
    return null;
  }

  /** moov の中の最初の AAC の音のトラックの { params, sizes, chunkOffsets, samplesPerChunk } 。無ければ null。 */
  function audioTrack(bytes, moov) {
    for (const trak of boxes(bytes, moov.start, moov.end).filter((b) => b.type === "trak")) {
      const mdia = child(bytes, trak, "mdia");
      const hdlr = mdia && child(bytes, mdia, "hdlr");
      if (!hdlr || typeAt(bytes, hdlr.start + 8) !== "soun") continue;
      const stbl = child(bytes, child(bytes, mdia, "minf") ?? mdia, "stbl");
      const stsd = stbl && child(bytes, stbl, "stsd");
      if (!stsd) continue;
      const entry = boxes(bytes, stsd.start + 8, stsd.end)[0];
      if (!entry || entry.type !== "mp4a") continue;
      const esds = findEsds(bytes, entry);
      const config = esds && audioSpecificConfig(bytes, esds);
      const params = config && adtsParams(config);
      if (!params) continue;

      const stsz = child(bytes, stbl, "stsz");
      const stsc = child(bytes, stbl, "stsc");
      const stco = child(bytes, stbl, "stco") ?? child(bytes, stbl, "co64");
      if (!stsz || !stsc || !stco) continue;
      const fixedSize = u32(bytes, stsz.start + 4);
      const count = u32(bytes, stsz.start + 8);
      const sizes = new Array(count);
      for (let i = 0; i < count; i++) sizes[i] = fixedSize || u32(bytes, stsz.start + 12 + 4 * i);
      const wide = stco.type === "co64";
      const chunkCount = u32(bytes, stco.start + 4);
      const chunkOffsets = new Array(chunkCount);
      for (let i = 0; i < chunkCount; i++) {
        chunkOffsets[i] = wide ? u64(bytes, stco.start + 8 + 8 * i) : u32(bytes, stco.start + 8 + 4 * i);
      }
      // stsc: 「何番目のチャンクから、1 チャンクに何サンプル」の並び (番号は 1 から)
      const runs = [];
      for (let i = 0, n = u32(bytes, stsc.start + 4); i < n; i++) {
        runs.push({ first: u32(bytes, stsc.start + 8 + 12 * i), samples: u32(bytes, stsc.start + 12 + 12 * i) });
      }
      const samplesPerChunk = new Array(chunkCount);
      for (let r = 0; r < runs.length; r++) {
        const last = r + 1 < runs.length ? runs[r + 1].first - 1 : chunkCount;
        for (let c = runs[r].first; c <= last && c <= chunkCount; c++) samplesPerChunk[c - 1] = runs[r].samples;
      }
      return { params, sizes, chunkOffsets, samplesPerChunk };
    }
    return null;
  }

  function adtsHeader(params, frameLength, out, pos) {
    const length = frameLength + 7;
    out[pos] = 0xff;
    out[pos + 1] = 0xf1; // MPEG-4、CRC なし
    out[pos + 2] = (params.profile << 6) | (params.rateIndex << 2) | (params.channels >> 2);
    out[pos + 3] = ((params.channels & 3) << 6) | (length >> 11);
    out[pos + 4] = (length >> 3) & 0xff;
    out[pos + 5] = ((length & 7) << 5) | 0x1f;
    out[pos + 6] = 0xfc;
  }

  /**
   * file (File / Blob) の最初の AAC の音のトラックを ADTS のバイト列 (Uint8Array) にする。
   * MP4・MOV でない、音のトラックが無い、AAC でない (ADTS に書けない) ときは null。
   */
  async function extractAdts(file) {
    const moovBox = await findTopBox(file, "moov");
    if (!moovBox) return null;
    const bytes = await readBytes(file, moovBox.start, moovBox.start + moovBox.size);
    const track = audioTrack(bytes, { start: moovBox.header, end: moovBox.size });
    if (!track) return null;
    const { params, sizes, chunkOffsets, samplesPerChunk } = track;
    const total = sizes.reduce((sum, size) => sum + size, 0);
    const out = new Uint8Array(total + 7 * sizes.length);
    let pos = 0;
    let sample = 0;
    for (let c = 0; c < chunkOffsets.length && sample < sizes.length; c++) {
      const n = Math.min(samplesPerChunk[c] ?? 0, sizes.length - sample);
      let length = 0;
      for (let i = 0; i < n; i++) length += sizes[sample + i];
      // 1 チャンク (音声のサンプルが続けて並ぶ所) をまとめて読む
      const chunk = await readBytes(file, chunkOffsets[c], chunkOffsets[c] + length);
      let at = 0;
      for (let i = 0; i < n; i++, sample++) {
        const size = sizes[sample];
        adtsHeader(params, size, out, pos);
        out.set(chunk.subarray(at, at + size), pos + 7);
        pos += size + 7;
        at += size;
      }
    }
    return out.subarray(0, pos);
  }

  globalThis.Mp4Audio = { extractAdts };
})();
