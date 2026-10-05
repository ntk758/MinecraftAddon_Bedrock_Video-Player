/**
 * Block Video Player - Bedrock Script API 再生スクリプト
 * (v3: Object-Oriented Multi-Screen & Adaptive Palette Support)
 */

import { world, system, BlockPermutation, BlockVolume } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { VIDEOS, VIDEO_LIST } from "./videos.js";
import {
  FORMAT_V4,
  FORMAT_V5,
  createTileDictionary,
  decodeFrameInto,
  decodeUTF16BinaryToBytes,
  isVolume,
  decodeUTF16BinaryToBytesJob,
  findKeyframeAtOrBefore,
  parseFrameIndexV3,
  parseFrameIndexV4,
  readVarint,
  skipFrameHeader,
  unpackRun,
} from "./codec.js";

const EVENT_NAMESPACE = "badapple";
const TICKS_PER_SECOND = 20;
// 動画データに fps が無い旧形式の場合だけ使う 1 フレームあたりの tick 数
const FRAME_INTERVAL_TICKS = 1;
const EVENT_PREFIX = `${EVENT_NAMESPACE}:`;
const ANCHOR_KEY = `${EVENT_NAMESPACE}:anchor`;
const ANCHOR_DIMENSION_KEY = `${EVENT_NAMESPACE}:dimension`;
const MESSAGE_PREFIX = `§b[${EVENT_NAMESPACE}]`;
const START_LOAD_DELAY_TICKS = 5;
const TICKING_AREA_NAME = "badapple_area";

const REMOTE_CONTROL_ITEM = "minecraft:compass";
// video_player_gui.py が音声を切り出す長さ (-segment_time) と一致させること
const AUDIO_CHUNK_SECONDS = 10;

// 1 tick あたりのブロック設置上限。超えた分は次の tick へ持ち越す
const MAX_BLOCKS_PER_TICK = 4000;
const BLOCKS_PER_YIELD = 500;
const MAX_YIELDS_PER_TICK = MAX_BLOCKS_PER_TICK / BLOCKS_PER_YIELD;
// 1 tick の描画にかける時間の上限 (ミリ秒)。ブロック数の上限より先に達したら次の tick へ持ち越す
const TICK_TIME_BUDGET_MS = 30;
// 盤面の比較 (変わったブロックを探す走査) をこの画素数ごとに区切って tick を跨ぐ
const PIXELS_PER_SCAN_YIELD = 32768;
// 何フレーム先の GOP を先読みするか
const GOP_PREFETCH_FRAMES = 10;

const BLOCK_ID_ALIASES = {
  "minecraft:terracotta": "minecraft:hardened_clay",
};

let mainIntervalId = null;
const activePlayers = new Map(); // key: "x,y,z"

let globalSelectedVideoId = VIDEO_LIST.length > 0 ? VIDEO_LIST[0].id : null;

// プレイヤーに見せる文字列はリソースパックの texts/*.lang にあり、各プレイヤーのゲーム言語で表示される。
// 翻訳の元は locales/*.json の "addon.*" キー (GUI がビルド時に .lang を生成し、キー名は "bvp.*" になる)。
function tr(key, ...args) {
  const message = { translate: `bvp.${key}` };
  if (args.length > 0) {
    message.with = { rawtext: args.map((arg) => (typeof arg === "object" ? arg : { text: String(arg) })) };
  }
  return message;
}

function textLines(...parts) {
  const rawtext = [];
  parts.forEach((part, index) => {
    if (index > 0) rawtext.push({ text: "\n" });
    rawtext.push(part);
  });
  return { rawtext };
}

function notify(key, ...args) {
  world.sendMessage({ rawtext: [{ text: `${MESSAGE_PREFIX}§r ` }, tr(key, ...args)] });
}

const warnedMessages = new Set();
function warnOnce(key, message) {
  if (warnedMessages.has(key)) return;
  warnedMessages.add(key);
  console.warn(`[${EVENT_NAMESPACE}] ${message}`);
}

function resolvePermutation(spec) {
  const blockId = BLOCK_ID_ALIASES[spec.block] ?? spec.block;
  try {
    return BlockPermutation.resolve(blockId, spec.states);
  } catch (e) {
    warnOnce(`resolve:${blockId}`, `Cannot resolve block ${blockId}; using dirt instead: ${e}`);
    return BlockPermutation.resolve("minecraft:dirt");
  }
}

// 盤面の 1 マス (p 番目) をワールド座標にする。
//  平面スクリーン: 足元の高さに、x は東、行は南向き (z+)
//  立体: x は東、高さは上向き (行 0 が一番上)、奥行き 0 が一番北 (プレイヤーから遠い側)
function cellToWorld(videoData, anchor, p, loc) {
  const width = videoData.width;
  const row = Math.floor(p / width);
  loc.x = anchor.x + (p % width);
  if (isVolume(videoData)) {
    const height = videoData.height;
    loc.y = anchor.y + (height - 1 - (row % height));
    loc.z = anchor.z + Math.floor(row / height);
  } else {
    loc.y = anchor.y;
    loc.z = anchor.z + row;
  }
  return loc;
}

function boardSizeOf(videoData) {
  return videoData.width * videoData.height * (isVolume(videoData) ? videoData.depth : 1);
}

// ワールド上で占める範囲の x 方向・z 方向の大きさ
function footprintOf(videoData) {
  return { sizeX: videoData.width, sizeZ: isVolume(videoData) ? videoData.depth : videoData.height };
}

function ticksPerFrameOf(videoData) {
  if (videoData && videoData.fps > 0) return TICKS_PER_SECOND / videoData.fps;
  return FRAME_INTERVAL_TICKS;
}

function frameToSeconds(videoData, frame) {
  return (frame * ticksPerFrameOf(videoData)) / TICKS_PER_SECOND;
}

function secondsToFrame(videoData, seconds) {
  return Math.floor((seconds * TICKS_PER_SECOND) / ticksPerFrameOf(videoData));
}

class VideoPlayer {
  constructor(anchor, dimension, videoId) {
    this.anchor = anchor;
    this.dimension = dimension;
    this.videoId = videoId;
    this.videoData = VIDEOS[videoId];
    this.ticksPerFrame = ticksPerFrameOf(this.videoData);

    this.currentFrame = 0;
    this.elapsedTicks = 0;
    this.running = false;
    this.currentAudioChunk = -1;
    this.masterVolume = 1.0;

    this.decodedIndex = null;
    this.decodedVideoId = null;
    this.gopCache = new Map();
    this.pendingGops = new Set();
    this.MAX_CACHED_GOPS = 4;
    this.decodedBinary = null;

    this.paletteCache = null;
    this.currentGopId = -1;
    this.activePalette = null;

    this.frameIterator = null;
    this.startDelayTicks = 0;
    this.currentJobId = null;

    this.tempBlockLoc = { x: 0, y: 0, z: 0 };
    this.cursor = { pos: 0 };
    this.run = { position: 0, length: 0, level: 0 };

    // 現在スクリーンに表示しているパレット番号 (-1 は不明 = 次のフレームで必ず置き直す) と作業用の盤面
    this.board = null;
    this.nextBoard = null;
  }

  ensureDecodedData() {
    if (!this.videoData) return false;
    if (this.decodedVideoId === this.videoId && (this.decodedIndex || this.videoData.frames)) return true;

    const format = this.videoData.format;
    if ((format === FORMAT_V4 || format === FORMAT_V5) && this.videoData.chunks && this.videoData.index) {
      this.decodedIndex = parseFrameIndexV4(decodeUTF16BinaryToBytes(this.videoData.index));
      const pixels = boardSizeOf(this.videoData);
      this.board = new Int16Array(pixels).fill(-1);
      this.nextBoard = new Int16Array(pixels);
      this.tiles = createTileDictionary(this.videoData);
      this.decodedBinary = null;
      this.gopCache.clear();
      this.decodedVideoId = this.videoId;
      return true;
    }

    if (this.videoData.format === "varint_rle_v3" && this.videoData.binary && this.videoData.index) {
      this.decodedBinary = decodeUTF16BinaryToBytes(this.videoData.binary);
      this.decodedIndex = parseFrameIndexV3(decodeUTF16BinaryToBytes(this.videoData.index));
      this.decodedVideoId = this.videoId;
      return true;
    }

    if (this.videoData.frames) {
      this.decodedVideoId = this.videoId;
      this.decodedBinary = null;
      this.decodedIndex = null;
      return true;
    }
    return false;
  }

  cacheGop(gopId, decoded) {
    if (this.gopCache.size >= this.MAX_CACHED_GOPS) {
      const oldestKey = this.gopCache.keys().next().value;
      this.gopCache.delete(oldestKey);
    }
    this.gopCache.set(gopId, decoded);
  }

  /** GOP を同期的に展開して返す (LRU キャッシュ)。 */
  ensureGopDecoded(gopId) {
    const cached = this.gopCache.get(gopId);
    if (cached) {
      // 最近使ったものを末尾へ移して LRU にする
      this.gopCache.delete(gopId);
      this.gopCache.set(gopId, cached);
      return cached;
    }
    if (!this.videoData || !this.videoData.chunks) return null;
    if (gopId < 0 || gopId >= this.videoData.chunks.length) return null;

    const decoded = decodeUTF16BinaryToBytes(this.videoData.chunks[gopId]);
    this.cacheGop(gopId, decoded);
    return decoded;
  }

  /** 次の GOP を runJob で tick を跨いで展開しておく (再生中のスパイク防止)。 */
  prefetchGop(gopId) {
    if (this.gopCache.has(gopId) || this.pendingGops.has(gopId)) return;
    if (!this.videoData || !this.videoData.chunks || gopId >= this.videoData.chunks.length) return;
    this.pendingGops.add(gopId);
    const self = this;
    const videoId = this.videoId;
    system.runJob((function* () {
      const decoded = yield* decodeUTF16BinaryToBytesJob(self.videoData.chunks[gopId]);
      self.pendingGops.delete(gopId);
      if (self.videoId === videoId && !self.gopCache.has(gopId)) {
        self.cacheGop(gopId, decoded);
      }
    })());
  }

  initPaletteCache() {
    if (!this.videoData || this.paletteCache) return;
    this.airPermutation = BlockPermutation.resolve("minecraft:air");
    if (this.videoData.adaptive_palette) {
      this.paletteCache = this.videoData.level_blocks.map((gopPalette) => gopPalette.map(resolvePermutation));
    } else {
      this.paletteCache = this.videoData.level_blocks.map(resolvePermutation);
    }
  }

  *applyBinarySlice(width, bytes, startOff, endOff) {
    const cursor = this.cursor;
    const run = this.run;
    const loc = this.tempBlockLoc;
    let operations = 0;
    let failures = 0;
    cursor.pos = startOff;

    skipFrameHeader(bytes, cursor, endOff);

    while (cursor.pos < endOff) {
      unpackRun(readVarint(bytes, cursor, endOff), run);

      const permutation = this.activePalette ? this.activePalette[run.level] : null;
      if (!permutation) continue;

      const bx = this.anchor.x + (run.position % width);
      const bz = this.anchor.z + Math.floor(run.position / width);

      for (let i = 0; i < run.length; i++) {
        // yield を挟むので座標は毎回すべて書き直す
        loc.x = bx + i;
        loc.y = this.anchor.y;
        loc.z = bz;
        try {
          this.dimension.setBlockPermutation(loc, permutation);
        } catch (e) {
          failures++;
        }
        operations++;
        if (operations >= BLOCKS_PER_YIELD) {
          // yield 中に他の処理が cursor を使っても壊れないよう位置を退避する
          const savedPos = cursor.pos;
          yield;
          cursor.pos = savedPos;
          operations = 0;
        }
      }
    }
    if (failures > 0) {
      warnOnce("setBlock", `Failed to place ${failures} block(s). The screen may be outside the loaded area`);
    }
  }

  /** 盤面を「不明」にして、次に描くフレームで全ブロックを置き直させる (盤面クリア後など)。 */
  invalidateBoard() {
    if (this.board) this.board.fill(-1);
  }

  /**
   * キーフレームの前に呼ぶ。平面は全マスを置き直す。
   * 立体は空気のマスは空気のままとみなし、ブロックのあるマスだけ置き直す (空気で全体を塗り直さずに済む)。
   */
  prepareKeyframe() {
    if (!this.board) return;
    if (!isVolume(this.videoData)) {
      this.board.fill(-1);
      return;
    }
    const air = this.videoData.air_level;
    for (let p = 0; p < this.board.length; p++) {
      if (this.board[p] !== air) this.board[p] = -1;
    }
  }

  /** nextBoard と board を比べ、変わったブロックだけを置く。tick 予算に合わせて yield する。 */
  /**
   * nextBoard と board を比べ、変わったブロックだけを置く。tick 予算に合わせて yield する。
   *
   * 同じ色の変更ブロックを長方形にまとめ、dimension.fillBlocks 1 回で置く (API 呼び出しを大きく減らす)。
   * 横に続く同じ色を伸ばし、下の行でも同じ幅が全部同じ色の変更なら下へも伸ばす。
   * 立体では、ワールド上で隣り合う同じ奥行きの段の中だけで縦に伸ばす。
   * fillBlocks が使えない環境では 1 個ずつ setBlockPermutation で置く。
   */
  *placeChangedBlocks(width) {
    const video = this.videoData;
    const board = this.board;
    const next = this.nextBoard;
    const palette = this.activePalette;
    const airLevel = isVolume(video) ? video.air_level : -2;
    const rows = next.length / width;
    // 縦に伸ばしてよい行のまとまり (平面は全行、立体は奥行き 1 段ぶんの高さ)
    const contiguousRows = isVolume(video) ? video.height : rows;
    if (this.useFill === undefined) this.useFill = typeof this.dimension.fillBlocks === "function";
    const from = { x: 0, y: 0, z: 0 };
    const to = { x: 0, y: 0, z: 0 };
    let operations = 0;
    let scanned = 0;
    let failures = 0;

    for (let r = 0; r < rows; r++) {
      const rowStart = r * width;
      let x = 0;
      while (x < width) {
        const p = rowStart + x;
        const level = next[p];
        if (level === board[p] || level < 0) {
          x++;
          continue;
        }
        let x1 = x;
        while (x1 + 1 < width && next[p + x1 + 1 - x] === level && board[p + x1 + 1 - x] !== level) x1++;
        let r1 = r;
        if (this.useFill) {
          while (r1 + 1 < rows && (r1 + 1) % contiguousRows !== 0) {
            const base = (r1 + 1) * width + x;
            let sameRun = true;
            for (let k = 0; k <= x1 - x; k++) {
              if (next[base + k] !== level || board[base + k] === level) {
                sameRun = false;
                break;
              }
            }
            if (!sameRun) break;
            r1++;
          }
        }
        const permutation = level === airLevel ? this.airPermutation : palette ? palette[level] : null;
        const area = (x1 - x + 1) * (r1 - r + 1);

        if (area > 1 && this.useFill && permutation) {
          for (let rr = r; rr <= r1; rr++) board.fill(level, rr * width + x, rr * width + x1 + 1);
          cellToWorld(video, this.anchor, p, from);
          cellToWorld(video, this.anchor, r1 * width + x1, to);
          try {
            this.dimension.fillBlocks(new BlockVolume(from, to), permutation);
          } catch (e) {
            failures += area;
            warnOnce("fill", `fillBlocks failed: ${e}`);
          }
          operations += area;
        } else {
          // 1 個ずつ置く (途中で yield しても盤面と実際の表示がずれないよう、置くたびに盤面を更新する)
          for (let q = p; q <= rowStart + x1; q++) {
            board[q] = level;
            if (!permutation) continue;
            try {
              this.dimension.setBlockPermutation(cellToWorld(video, this.anchor, q, this.tempBlockLoc), permutation);
            } catch (e) {
              failures++;
            }
            if (++operations >= BLOCKS_PER_YIELD) {
              operations = 0;
              yield;
            }
          }
        }
        if (operations >= BLOCKS_PER_YIELD) {
          operations = 0;
          yield;
        }
        x = x1 + 1;
      }
      scanned += width;
      if (scanned >= PIXELS_PER_SCAN_YIELD) {
        scanned = 0;
        yield;
      }
    }
    if (failures > 0) {
      warnOnce("setBlock", `Failed to place ${failures} block(s). The screen may be outside the loaded area`);
    }
  }

  *applyFrameJob(frameIndex) {
    if (!this.videoData) return;
    if (!this.ensureDecodedData()) return;
    this.initPaletteCache();
    const width = this.videoData.width;

    if (this.board && this.decodedIndex) {
      if (frameIndex < 0 || frameIndex >= this.decodedIndex.length) return;
      const entry = this.decodedIndex[frameIndex];

      const lookaheadFrame = frameIndex + GOP_PREFETCH_FRAMES;
      if (lookaheadFrame < this.decodedIndex.length) {
        const nextGopId = this.decodedIndex[lookaheadFrame].gopId;
        if (nextGopId > entry.gopId) this.prefetchGop(nextGopId);
      }
      if (entry.length === 0) return;

      this.currentGopId = entry.gopId;
      this.activePalette = this.videoData.adaptive_palette ? this.paletteCache[this.currentGopId] : this.paletteCache;

      const gopBytes = this.ensureGopDecoded(entry.gopId);
      if (!gopBytes) return;
      const isKeyframe = decodeFrameInto(
        gopBytes, entry.offset, entry.offset + entry.length, this.board, this.nextBoard, this.videoData, this.tiles
      );
      // キーフレームはワールド上の状態に関係なく全ブロックを置き直す (パレット切替・クリア後・シーク時)
      if (isKeyframe) this.prepareKeyframe();
      yield* this.placeChangedBlocks(width);
      return;
    }

    this.currentGopId = -1;
    this.activePalette = this.videoData.adaptive_palette ? this.paletteCache[0] : this.paletteCache;

    if (this.decodedBinary && this.decodedIndex) {
      if (frameIndex < 0 || frameIndex >= this.decodedIndex.length) return;
      const entry = this.decodedIndex[frameIndex];
      if (entry.length === 0) return;
      yield* this.applyBinarySlice(width, this.decodedBinary, entry.offset, entry.offset + entry.length);
      return;
    }

    const diffData = this.videoData.frames?.[frameIndex];
    if (typeof diffData !== "string" || diffData.length === 0) return;
    if (diffData.charCodeAt(diffData.startsWith("K:") ? 2 : 0) < 0x1000) return;
    const bytes = decodeUTF16BinaryToBytes(diffData);
    yield* this.applyBinarySlice(width, bytes, 0, bytes.length);
  }

  syncAudioForFrame(frameIndex) {
    if (!this.videoData || this.masterVolume <= 0) return;
    const targetChunk = Math.floor(frameToSeconds(this.videoData, frameIndex) / AUDIO_CHUNK_SECONDS);
    if (targetChunk !== this.currentAudioChunk) {
      this.currentAudioChunk = targetChunk;
      const trackId = `${EVENT_NAMESPACE}.${this.videoId}.chunk_${targetChunk}`;
      for (const p of world.getAllPlayers()) {
        try {
          p.stopMusic();
          p.playSound(trackId, { location: p.location, volume: this.masterVolume, pitch: 1.0 });
        } catch (e) {
          try {
            p.playMusic(trackId, { volume: this.masterVolume, loop: false });
          } catch (e2) {
            warnOnce(`audio:${trackId}`, `Cannot play sound ${trackId}: ${e2}`);
          }
        }
      }
    }
  }

  tick() {
    if (!this.running) return;

    if (this.startDelayTicks > 0) {
      this.startDelayTicks--;
      return;
    }
    this.elapsedTicks++;

    if (!this.frameIterator) {
      if (this.currentFrame >= this.videoData.frame_count) {
        this.stopPlayback();
        notify("playback_finished", this.anchor.x, this.anchor.y, this.anchor.z);
        return;
      }
      // 動画の fps に合わせて、表示時刻が来るまで次のフレームを始めない
      if (this.currentFrame * this.ticksPerFrame >= this.elapsedTicks) return;
      this.frameIterator = this.applyFrameJob(this.currentFrame);
    }

    const tickStart = Date.now();
    for (let yc = 0; yc < MAX_YIELDS_PER_TICK; yc++) {
      const { done } = this.frameIterator.next();
      if (done) {
        this.syncAudioForFrame(this.currentFrame);
        this.currentFrame++;
        this.frameIterator = null;
        break;
      }
      if (Date.now() - tickStart > TICK_TIME_BUDGET_MS) break;
    }
  }

  cancelJob() {
    if (this.currentJobId !== null) {
      system.clearJob(this.currentJobId);
      this.currentJobId = null;
    }
  }

  stopPlayback() {
    this.running = false;
    this.currentAudioChunk = -1;
    this.cancelJob();
    for (const p of world.getAllPlayers()) {
      try { p.stopMusic(); } catch (e) { /* 既に停止している */ }
    }
  }

  /** 先頭から再生する。 */
  restart() {
    this.stopPlayback();
    this.frameIterator = null;
    this.currentFrame = 0;
    this.elapsedTicks = 0;
    this.running = true;
    this.startDelayTicks = START_LOAD_DELAY_TICKS;
  }

  /** 一時停止・シーク位置から再生を続ける。 */
  resume() {
    if (!this.videoData || this.currentFrame >= this.videoData.frame_count) {
      this.restart();
      return;
    }
    this.cancelJob();
    this.elapsedTicks = Math.floor(this.currentFrame * this.ticksPerFrame);
    this.currentAudioChunk = -1;
    this.syncAudioForFrame(this.currentFrame);
    this.running = true;
  }

  canResume() {
    return !this.running && this.currentFrame > 0 && this.videoData && this.currentFrame < this.videoData.frame_count;
  }

  stopAndClear() {
    this.stopPlayback();
    this.frameIterator = null;
    this.currentFrame = 0;
    this.invalidateBoard();
    if (!this.videoData) return;

    this.initPaletteCache();
    // 平面はパレットの先頭のブロックで塗りつぶし、立体は空気にする
    const volume = isVolume(this.videoData);
    const clearPermutation = volume
      ? this.airPermutation
      : this.videoData.adaptive_palette ? this.paletteCache?.[0]?.[0] : this.paletteCache?.[0];
    if (!clearPermutation) return;

    // 大きなスクリーンを 1 tick で塗るとウォッチドッグに止められるため runJob で分割する
    const self = this;
    const cells = boardSizeOf(this.videoData);
    this.currentJobId = system.runJob((function* () {
      const loc = { x: 0, y: 0, z: 0 };
      let operations = 0;
      for (let p = 0; p < cells; p++) {
        cellToWorld(self.videoData, self.anchor, p, loc);
        try {
          self.dimension.setBlockPermutation(loc, clearPermutation);
        } catch (e) {
          warnOnce("clear", `Failed to place blocks while clearing the screen: ${e}`);
        }
        if (++operations >= BLOCKS_PER_YIELD) {
          operations = 0;
          yield;
        }
      }
      // 立体は全マスが空気になったことが分かっているので、次のキーフレームで空気を置き直さずに済む
      if (volume && self.board) self.board.fill(self.videoData.air_level);
      self.currentJobId = null;
      notify("clear_done");
    })());
  }

  seekToFrame(targetFrame) {
    if (!this.videoData || !this.ensureDecodedData()) return;
    targetFrame = Math.max(0, Math.min(targetFrame, this.videoData.frame_count - 1));

    // 再生中のフレーム描画とシーク描画が混ざらないよう一旦止める
    this.stopPlayback();
    this.frameIterator = null;
    this.currentFrame = targetFrame;

    let startFrame;
    if (this.decodedIndex) {
      startFrame = findKeyframeAtOrBefore(this.decodedIndex, targetFrame);
    } else {
      const gop = this.videoData.keyframe_interval || 30;
      startFrame = Math.floor(targetFrame / gop) * gop;
    }

    notify("seeking", targetFrame);

    const self = this;
    this.currentJobId = system.runJob((function* () {
      for (let f = startFrame; f <= targetFrame; f++) {
        yield* self.applyFrameJob(f);
      }
      // targetFrame まで描画済みなので、再開時は次のフレームから
      self.currentFrame = targetFrame + 1;
      self.currentJobId = null;
      notify("seek_done");
    })());
  }
}

function startMainLoop() {
  if (mainIntervalId !== null) return;
  // 各プレイヤーが自分の fps で進むよう毎 tick 呼ぶ
  mainIntervalId = system.runInterval(() => {
    for (const player of activePlayers.values()) {
      player.tick();
    }
  }, 1);
}

function getAnchorKeyStr(anchor) {
  return `${anchor.x},${anchor.y},${anchor.z}`;
}

function ensureTickingArea(dimension, anchor, videoData) {
  if (!videoData) return false;
  const areaName = `${TICKING_AREA_NAME}_${anchor.x}_${anchor.y}_${anchor.z}`;
  try {
    dimension.runCommand(`tickingarea remove ${areaName}`);
  } catch (e) { /* 未登録なら削除失敗は想定内 */ }

  const { sizeX, sizeZ } = footprintOf(videoData);
  const toX = anchor.x + sizeX - 1;
  const toZ = anchor.z + sizeZ - 1;
  try {
    dimension.runCommand(
      `tickingarea add ${anchor.x} ${anchor.y} ${anchor.z} ${toX} ${anchor.y} ${toZ} ${areaName}`
    );
    return true;
  } catch (e) {
    console.warn(`[${EVENT_NAMESPACE}] tickingarea add failed: ${e}`);
    return false;
  }
}

function setup(player) {
  if (!globalSelectedVideoId) {
    notify("no_video_selected");
    return;
  }
  const videoData = VIDEOS[globalSelectedVideoId];
  const loc = player.location;
  // 平面は足元から北東へ広がる。立体はプレイヤーの 2 ブロック北に、左右中央をそろえて立てる
  const volume = isVolume(videoData);
  const anchor = {
    x: Math.floor(loc.x) - (volume ? Math.floor(videoData.width / 2) : 0),
    y: Math.floor(loc.y),
    z: Math.floor(loc.z) - footprintOf(videoData).sizeZ - (volume ? 2 : 0),
  };
  
  if (!ensureTickingArea(player.dimension, anchor, videoData)) {
    notify("tickingarea_failed");
    return;
  }
  
  const keyStr = getAnchorKeyStr(anchor);
  const vp = new VideoPlayer(anchor, player.dimension, globalSelectedVideoId);
  activePlayers.set(keyStr, vp);
  world.setDynamicProperty(ANCHOR_KEY, anchor); // For single remote control backwards compatibility
  world.setDynamicProperty(ANCHOR_DIMENSION_KEY, player.dimension.id);
  startMainLoop();

  notify("setup_done", EVENT_PREFIX);
}

function getPlaybackDimension(fallbackDimension) {
  const dimensionId = world.getDynamicProperty(ANCHOR_DIMENSION_KEY);
  if (typeof dimensionId === "string") {
    try {
      return world.getDimension(dimensionId);
    } catch (e) {
      warnOnce(`dimension:${dimensionId}`, `Cannot get dimension ${dimensionId}: ${e}`);
    }
  }
  return fallbackDimension;
}

function startPlayback(dimension) {
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  if (!anchorLoc) {
    notify("no_anchor", EVENT_PREFIX);
    return;
  }
  const keyStr = getAnchorKeyStr(anchorLoc);
  let vp = activePlayers.get(keyStr);
  // 別の動画が選ばれていたらプレイヤーを作り直す (以前は古い動画のまま再生されていた)
  if (!vp || (globalSelectedVideoId && vp.videoId !== globalSelectedVideoId)) {
    if (!globalSelectedVideoId) return;
    if (vp) vp.stopPlayback();
    vp = new VideoPlayer(anchorLoc, getPlaybackDimension(dimension), globalSelectedVideoId);
    activePlayers.set(keyStr, vp);
    startMainLoop();
  }

  vp.restart();
  notify("starting");
}

function resumeOrStartPlayback(dimension) {
  const vp = getActivePlayer();
  if (vp && vp.canResume() && (!globalSelectedVideoId || vp.videoId === globalSelectedVideoId)) {
    vp.resume();
    notify("resumed");
  } else {
    startPlayback(dimension);
  }
}

function stopAndClearAll(dimension) {
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  if (anchorLoc) {
    const keyStr = getAnchorKeyStr(anchorLoc);
    let vp = activePlayers.get(keyStr);
    if (vp) {
      vp.stopAndClear();
      notify("clearing");
    }
  }
}

function getActivePlayer() {
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  if (anchorLoc) {
    return activePlayers.get(getAnchorKeyStr(anchorLoc));
  }
  return null;
}

function showRemoteControlGUI(player) {
  const vp = getActivePlayer();
  const running = vp ? vp.running : false;
  const currentVideoId = vp ? vp.videoId : globalSelectedVideoId;
  const videoData = VIDEOS[currentVideoId];
  
  const statusText = tr(running ? "remote.playing" : "remote.stopped");
  const titleText = currentVideoId ? { text: currentVideoId } : tr("remote.none");
  const currentFrame = vp ? vp.currentFrame : 0;
  const masterVolume = vp ? vp.masterVolume : 1.0;
  
  const currentSec = videoData ? Math.floor(frameToSeconds(videoData, currentFrame)) : 0;
  const totalSec = videoData ? Math.floor(frameToSeconds(videoData, videoData.frame_count)) : 0;

  const form = new ActionFormData()
    .title(tr("remote.title"))
    .body(textLines(
      tr("remote.status", statusText),
      tr("remote.selected", titleText),
      tr("remote.position", currentSec, totalSec, currentFrame),
      tr("remote.volume", `${Math.round(masterVolume * 100)}%`),
    ))
    .button(tr(running ? "remote.pause" : "remote.play"), "textures/items/emerald")
    .button(tr("remote.stop"), "textures/blocks/redstone_block")
    .button(tr("remote.next"), "textures/items/paper")
    .button(tr("remote.prev"), "textures/items/paper")
    .button(tr("remote.volume_button"), "textures/items/repeater")
    .button(tr("remote.seek"), "textures/items/clock")
    .button(tr("remote.library"), "textures/items/book_portfolio");

  form.show(player).then((response) => {
    if (response.canceled) return;
    const selection = response.selection;
    const dimension = player.dimension;

    switch (selection) {
      case 0:
        if (vp && vp.running) {
          vp.stopPlayback();
          notify("paused");
        } else {
          resumeOrStartPlayback(dimension);
        }
        break;
      case 1:
        stopAndClearAll(dimension);
        break;
      case 2:
        switchVideoIndex(1, player);
        break;
      case 3:
        switchVideoIndex(-1, player);
        break;
      case 4:
        showVolumeGUI(player);
        break;
      case 5:
        showSeekGUI(player);
        break;
      case 6:
        showVideoSelectGUI(player);
        break;
    }
  });
}

function switchVideoIndex(direction, player) {
  if (VIDEO_LIST.length === 0) return;
  const vp = getActivePlayer();
  const cvId = vp ? vp.videoId : globalSelectedVideoId;
  let currentIdx = VIDEO_LIST.findIndex((v) => v.id === cvId);
  if (currentIdx === -1) currentIdx = 0;
  let newIdx = (currentIdx + direction + VIDEO_LIST.length) % VIDEO_LIST.length;
  globalSelectedVideoId = VIDEO_LIST[newIdx].id;
  notify("next_video", globalSelectedVideoId);
  showRemoteControlGUI(player);
}

function showVolumeGUI(player) {
  const vp = getActivePlayer();
  const mv = vp ? vp.masterVolume : 1.0;
  const form = new ModalFormData()
    .title(tr("volume.title"))
    .slider(tr("volume.slider"), 0, 100, 10, Math.round(mv * 100));

  form.show(player).then((response) => {
    if (response.canceled) return;
    if (vp) {
      vp.masterVolume = response.formValues[0] / 100.0;
      vp.currentAudioChunk = -1;
      if (vp.running) vp.syncAudioForFrame(vp.currentFrame);
      notify("volume_set", `${Math.round(vp.masterVolume * 100)}%`);
    }
  });
}

function showSeekGUI(player) {
  const vp = getActivePlayer();
  if (!vp || !vp.videoData) return;
  const maxSec = Math.floor(frameToSeconds(vp.videoData, vp.videoData.frame_count));
  const currentSec = Math.floor(frameToSeconds(vp.videoData, vp.currentFrame));

  const form = new ModalFormData()
    .title(tr("seek.title"))
    .slider(tr("seek.slider"), 0, Math.max(1, maxSec), 1, currentSec);

  form.show(player).then((response) => {
    if (response.canceled) return;
    const targetSec = response.formValues[0];
    const targetFrame = secondsToFrame(vp.videoData, targetSec);
    vp.seekToFrame(targetFrame);
  });
}

function showVideoSelectGUI(player) {
  const vp = getActivePlayer();
  const cvId = vp ? vp.videoId : globalSelectedVideoId;
  const form = new ActionFormData().title(tr("library.title")).body(tr("library.body"));
  for (const v of VIDEO_LIST) {
    const rawtext = [{ text: v.title }];
    if (v.id === cvId) rawtext.push({ text: " " }, tr("selected_tag"));
    rawtext.push({ text: "\n" }, tr("library.item", v.frame_count, `${v.width}x${v.height}`));
    form.button({ rawtext });
  }

  form.show(player).then((response) => {
    if (response.canceled) return;
    const selectedVideo = VIDEO_LIST[response.selection];
    if (selectedVideo) {
      globalSelectedVideoId = selectedVideo.id;
      notify("video_selected_hint", selectedVideo.id);
      showRemoteControlGUI(player);
    }
  });
}

world.afterEvents.itemUse.subscribe((event) => {
  if (event.itemStack.typeId === REMOTE_CONTROL_ITEM) {
    showRemoteControlGUI(event.source);
  }
});

system.afterEvents.scriptEventReceive.subscribe((event) => {
  if (!event.id.startsWith(EVENT_PREFIX)) return;

  const player = event.sourceEntity;
  const dimension = player ? player.dimension : world.getDimension("overworld");
  const action = event.id.slice(EVENT_PREFIX.length);

  switch (action) {
    case "setup":
      if (!player) {
        notify("setup_needs_player");
        break;
      }
      setup(player);
      break;
    case "start":
      startPlayback(dimension);
      break;
    case "stop":
      stopAndClearAll(dimension);
      break;
    case "gui":
    case "remote":
      if (player) showRemoteControlGUI(player);
      break;
    case "list":
      if (VIDEO_LIST.length === 0) {
        notify("no_videos");
      } else {
        notify("video_list");
        for (const v of VIDEO_LIST) {
          const rawtext = [{ text: "  " }, tr("video_list_item", v.id, v.title, v.frame_count, `${v.width}x${v.height}`)];
          if (v.id === globalSelectedVideoId) rawtext.push({ text: " " }, tr("selected_tag"));
          world.sendMessage({ rawtext });
        }
        notify("list_play_hint", EVENT_PREFIX);
      }
      break;
    case "play":
      const videoId = event.message?.trim();
      if (!videoId) {
        startPlayback(dimension);
      } else {
        if (VIDEOS[videoId]) {
          globalSelectedVideoId = videoId;
          notify("video_selected", videoId);
          startPlayback(dimension);
        } else {
          notify("video_not_found", videoId, EVENT_PREFIX);
        }
      }
      break;
    default:
      break;
  }
});
