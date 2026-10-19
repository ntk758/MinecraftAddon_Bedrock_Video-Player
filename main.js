/**
 * Block Video Player - Bedrock Script API 再生スクリプト
 * (v3: Object-Oriented Multi-Screen & Adaptive Palette Support)
 */

import { world, system, BlockPermutation, BlockVolume, ListBlockVolume } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { VIDEOS, VIDEO_LIST } from "./videos.js";
import {
  FORMAT_V4,
  FORMAT_V5,
  FORMAT_V6,
  createTileDictionary,
  decodeFrameInto,
  decodeUTF16BinaryToBytes,
  isVolume,
  decodeUTF16BinaryToBytesJob,
  expandPlanes,
  findKeyframeAtOrBefore,
  isHeightfield,
  parseFrameIndexV3,
  parseFrameIndexV4,
  readVarint,
  skipFrameHeader,
  streamSizeOf,
  unpackGopJob,
  unpackRun,
} from "./codec.js";

const EVENT_NAMESPACE = "badapple";
const TICKS_PER_SECOND = 20;
// 動画データに fps が無い旧形式の場合だけ使う 1 フレームあたりの tick 数
const FRAME_INTERVAL_TICKS = 1;
const EVENT_PREFIX = `${EVENT_NAMESPACE}:`;
const ANCHOR_KEY = `${EVENT_NAMESPACE}:anchor`;
const ANCHOR_DIMENSION_KEY = `${EVENT_NAMESPACE}:dimension`;
// setup を実行したプレイヤーの足元。動画を切り替えたとき、新しい動画の形 (平面・立体) に合わせて置く位置を決め直す
const ORIGIN_KEY = `${EVENT_NAMESPACE}:origin`;
// 登録した tickingarea の名前の一覧 (JSON)。次の setup・動画の切り替えで、別の位置の分も含めて外す
const TICKING_NAMES_KEY = `${EVENT_NAMESPACE}:ticking_areas`;
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
// fillBlocks 1 回で置くブロック数の上限 (公式の上限は未公開。コミュニティでは 32768 が安全とされる)
const MAX_FILL_BLOCKS = 32768;
// list 方式で 1 回の fillBlocks(ListBlockVolume) に入れる座標の数
const LIST_BATCH = 4096;
// 描画方式: auto = フレームごとに下の 3 つから一番速いと見積もった方式 / rect = 同じ色の箱を fillBlocks /
// list = 色ごとの座標リストを fillBlocks / block = 1 個ずつ
const RENDER_MODES = ["auto", "rect", "list", "block"];
const RENDER_MODE_KEY = `${EVENT_NAMESPACE}:render_mode`;
// 置き方の費用のモデル (マイクロ秒)。/scriptevent <接頭辞>:bench で測った値をワールドに保存し、auto が使う。
//   rect  = fillCall × 箱の数 + fillBlock × 箱のブロック数 + setBlock × 1 個だけのマス
//   list  = listCall × 呼び出し数 (≒ 色の数) + listBlock × ブロック数
//   block = setBlock × ブロック数
// 既定値は Windows 版 Minecraft で測った値 (2026-10: fillBlocks 1 回 15 µs + 1 ブロック 0.55 µs、1 個ずつ 2.3 µs、
// 座標リスト 1 ブロック約 1.4 µs)。端末によって違うので、bench で測り直すとその値を使う
const COST_MODEL_KEY = `${EVENT_NAMESPACE}:cost_model`;
const DEFAULT_COST_MODEL = { setBlock: 2.3, fillCall: 15, fillBlock: 0.55, listCall: 15, listBlock: 1.42 };
// bench の結果を保存するのに必要な、1 方式あたりの合計時間 (ミリ秒)。短すぎると Date.now の 1 ms 単位の誤差が大きい
const BENCH_MIN_TOTAL_MS = 5;
// tickingarea の上限: 1 区画 100 チャンク (10x10 に分割)、ワールド全体で 10 区画 (他のパック用に残す)
const TICKING_AREA_CHUNKS_PER_SIDE = 10;
const MAX_TICKING_AREAS = 8;
// 盤面の比較 (変わったブロックを探す走査) をこの画素数ごとに区切って tick を跨ぐ
const PIXELS_PER_SCAN_YIELD = 32768;
// 何フレーム先の GOP を先読みするか
// 次の GOP を何フレーム前から展開し始めるか。v6 (エントロピー符号化) は展開に時間がかかるので早めに始める
const GOP_PREFETCH_FRAMES = 40;
// GOP の展開の途中で出す yield の値。描画の yield と違い、1 tick の yield の回数の上限 (ブロック数の上限) には数えず、
// 時間の予算だけで区切る (展開は何百回も yield するので、回数で区切ると何十 tick もかかる)
const DECODE_YIELD = 1;
const BLOCK_ID_ALIASES = {
  "minecraft:terracotta": "minecraft:hardened_clay",
};

let mainIntervalId = null;

function currentRenderMode() {
  const saved = world.getDynamicProperty(RENDER_MODE_KEY);
  return RENDER_MODES.includes(saved) ? saved : "auto";
}

let costModelCache = null;
/** 保存された費用のモデル (無ければ・壊れていれば既定値)。 */
function currentCostModel() {
  if (costModelCache) return costModelCache;
  costModelCache = DEFAULT_COST_MODEL;
  try {
    const saved = JSON.parse(world.getDynamicProperty(COST_MODEL_KEY) ?? "null");
    if (saved && Object.keys(DEFAULT_COST_MODEL).every((key) => Number.isFinite(saved[key]) && saved[key] >= 0)) {
      costModelCache = saved;
    }
  } catch (e) { /* 壊れた値は既定値を使う */ }
  return costModelCache;
}
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

// 動画一覧に出す大きさ (立体は 幅x高さx奥行き)
function videoSizeLabel(v) {
  return v.depth ? `${v.width}x${v.height}x${v.depth}` : `${v.width}x${v.height}`;
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
    // 展開中の GOP: gopId → { job, progress: { bytes, produced }, done, videoId }。再生と先読みが同じ展開を進める
    this.partialGops = new Map();
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

    // board: ワールドに実際に置いてあるパレット番号 (-1 は不明 = 次のフレームで必ず置き直す)
    // decoded: 直前に復元したフレーム (動き補償・タイル辞書の参照元。エンコーダと常に同じ内容)
    // nextBoard: 次に表示する盤面 (作業用)
    // 2.5D の立体では decoded は色と値の平面で、nextBoard はそれを組み立てた立体 (expandPlanes)。
    //   decodeScratch は平面の復元の作業用、expandedPlanes は nextBoard を組み立てたときの平面 (変わった列だけ作り直す)
    // decodedFrame: decoded が何番のフレームまで復元した状態か (-1 = まだ何も無い)。
    //   一時停止・シークの中断・ベンチマークなどで順番が飛んでも、applyFrameJob が直近のキーフレームから復元し直す
    this.board = null;
    this.decoded = null;
    this.nextBoard = null;
    this.decodeScratch = null;
    this.expandedPlanes = null;
    this.decodedFrame = -1;
    this.boardGopId = -1;
    // 再生の計測 (/scriptevent <接頭辞>:stats)。再生・再開のたびに測り直す
    this.playStats = null;
  }

  /** 再生の計測を始め直す。実時間は最初のフレームを描く tick から数える (読み込み待ちの tick を含めない)。 */
  resetPlayStats() {
    this.playStats = {
      frames: 0,
      lateFrames: 0,
      maxLateTicks: 0,
      scriptMs: 0,
      maxFrameMs: 0,
      frameMs: 0,
      frameLate: 0,
      wallStart: null,
      tickStart: 0,
      // 一時停止・終了で時計を止める (止めた後に stats を見ても時間が伸びない)
      wallEnd: null,
      tickEnd: 0,
      // 一時停止の前に描きかけだったフレームは、遅れも時間も正しく測れないので数えない
      skipPartialFrame: this.frameIterator !== null,
      startFrame: this.currentFrame,
    };
  }

  /** 計測の時計を止める (再生の終了・一時停止・停止のとき)。 */
  stopPlayStatsClock() {
    const st = this.playStats;
    if (st && st.wallStart !== null && st.wallEnd === null) {
      st.wallEnd = Date.now();
      st.tickEnd = this.elapsedTicks;
    }
  }

  /** 計測結果 (再生していなければ null)。driftSeconds が正なら映像が実時間 (= 音声) より遅れている。 */
  playStatsSummary() {
    const st = this.playStats;
    if (!st || st.wallStart === null || st.frames === 0) return null;
    const seconds = Math.max(0.001, ((st.wallEnd ?? Date.now()) - st.wallStart) / 1000);
    const videoSeconds = frameToSeconds(this.videoData, this.currentFrame) - frameToSeconds(this.videoData, st.startFrame);
    return {
      frames: st.frames,
      seconds,
      fps: st.frames / seconds,
      targetFps: this.videoData.fps || TICKS_PER_SECOND / FRAME_INTERVAL_TICKS,
      lateFrames: st.lateFrames,
      maxLateTicks: st.maxLateTicks,
      avgFrameMs: st.scriptMs / st.frames,
      maxFrameMs: st.maxFrameMs,
      tps: ((st.wallEnd !== null ? st.tickEnd : this.elapsedTicks) - st.tickStart) / seconds,
      driftSeconds: seconds - videoSeconds,
    };
  }

  ensureDecodedData() {
    if (!this.videoData) return false;
    if (this.decodedVideoId === this.videoId && (this.decodedIndex || this.videoData.frames)) return true;

    const format = this.videoData.format;
    if ((format === FORMAT_V4 || format === FORMAT_V5 || format === FORMAT_V6) && this.videoData.chunks && this.videoData.index) {
      this.decodedIndex = parseFrameIndexV4(decodeUTF16BinaryToBytes(this.videoData.index));
      const pixels = boardSizeOf(this.videoData);
      const streamPixels = streamSizeOf(this.videoData);
      this.board = new Int16Array(pixels).fill(-1);
      this.decoded = new Int16Array(streamPixels).fill(-1);
      this.nextBoard = new Int16Array(pixels);
      if (isHeightfield(this.videoData)) {
        this.decodeScratch = new Int16Array(streamPixels);
        // 平面の値には現れない値で埋めて、最初の組み立てで全部の列を作らせる
        this.expandedPlanes = new Int16Array(streamPixels).fill(-32768);
      } else {
        this.decodeScratch = this.nextBoard;
        this.expandedPlanes = null;
      }
      this.tiles = createTileDictionary(this.videoData);
      this.decodedFrame = -1;
      this.decodedBinary = null;
      this.gopCache.clear();
      this.partialGops.clear();
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
    // 知らない形式 (新しい版の変換ツールで作ったデータなど) は、黙って何も描かないのではなく知らせる
    warnOnce(`format:${this.videoData.format}`, `Unsupported video data format "${this.videoData.format}". Rebuild the add-on with the same version of the converter`);
    return false;
  }

  cacheGop(gopId, decoded) {
    if (this.gopCache.size >= this.MAX_CACHED_GOPS) {
      const oldestKey = this.gopCache.keys().next().value;
      this.gopCache.delete(oldestKey);
    }
    this.gopCache.set(gopId, decoded);
  }

  /** GOP の展開を始める (まだ進めない)。 */
  startGopDecode(gopId) {
    const chunk = this.videoData.chunks[gopId];
    const isV6 = this.videoData.format === FORMAT_V6;
    const progress = { bytes: null, produced: 0 };
    const job = (function* () {
      let decoded = yield* decodeUTF16BinaryToBytesJob(chunk);
      // v6 は GOP ごとの入れ物 (エントロピー符号化) を開いて v5 のバイト列にする
      if (isV6) decoded = yield* unpackGopJob(decoded, 8192, progress);
      return decoded;
    })();
    const entry = { job, progress, done: false, videoId: this.videoId };
    this.partialGops.set(gopId, entry);
    return entry;
  }

  /** 展開を次の yield まで進める。終わればキャッシュに入れる。失敗したら展開中の印を外して投げ直す。 */
  stepGopDecode(gopId, entry) {
    let result;
    try {
      result = entry.job.next();
    } catch (e) {
      entry.done = true;
      if (this.partialGops.get(gopId) === entry) this.partialGops.delete(gopId);
      throw e;
    }
    if (!result.done) return;
    entry.done = true;
    entry.progress.bytes = result.value;
    entry.progress.produced = result.value.length;
    if (this.partialGops.get(gopId) === entry) this.partialGops.delete(gopId);
    if (this.videoId === entry.videoId) this.cacheGop(gopId, result.value);
  }

  /**
   * GOP のバイト列を返す (LRU キャッシュ)。tick を跨いで少しずつ展開するジェネレータ (DECODE_YIELD を出す)。
   * needed バイト目まで (再生するフレームの終わりまで) できれば、GOP を開き終わる前でも返す (残りは続けて展開する)。
   * 先読み (prefetchGop) が同じ GOP を展開中なら、その続きを進める。
   */
  *gopBytesJob(gopId, needed = Infinity) {
    if (!this.videoData || !this.videoData.chunks) return null;
    if (gopId < 0 || gopId >= this.videoData.chunks.length) return null;
    const videoId = this.videoId;
    const cached = this.gopCache.get(gopId);
    if (cached) {
      // 最近使ったものを末尾へ移して LRU にする
      this.gopCache.delete(gopId);
      this.gopCache.set(gopId, cached);
      return cached;
    }
    const entry = this.partialGops.get(gopId) || this.startGopDecode(gopId);
    while (!entry.done && !(entry.progress.bytes && entry.progress.produced >= needed)) {
      this.stepGopDecode(gopId, entry);
      if (this.videoId !== videoId) return null;
      if (!entry.done) yield DECODE_YIELD;
    }
    if (this.videoId !== videoId) return null;
    return entry.progress.bytes;
  }

  /** 次の GOP を runJob で tick を跨いで展開しておく (再生中のスパイク防止)。 */
  prefetchGop(gopId) {
    if (this.gopCache.has(gopId) || this.partialGops.has(gopId)) return;
    if (!this.videoData || !this.videoData.chunks || gopId >= this.videoData.chunks.length) return;
    const self = this;
    const entry = this.startGopDecode(gopId);
    system.runJob((function* () {
      try {
        while (!entry.done && self.videoId === entry.videoId) {
          self.stepGopDecode(gopId, entry);
          if (!entry.done) yield;
        }
      } catch (e) {
        warnOnce(`prefetch:${gopId}`, `Failed to decode GOP ${gopId}: ${e}`);
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
    let lastError = null;
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
          lastError = e;
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
    this.reportPlaceFailures(failures, lastError);
  }

  /** 盤面を「不明」にして、次に描くフレームで全ブロックを置き直させる (盤面クリア後など)。 */
  invalidateBoard() {
    if (this.board) this.board.fill(-1);
  }

  /**
   * パレット番号の意味が変わるとき (自動パレットで GOP が切り替わるとき) に呼ぶ。平面は全マスを置き直す。
   * 立体は空気のマスは空気のままとみなし、ブロックのあるマスだけ置き直す (空気で全体を塗り直さずに済む)。
   * それ以外のキーフレームでは、表示中の盤面と比べて本当に変わったマスだけを置く。
   */
  invalidateForPaletteChange() {
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

  /**
   * nextBoard と board を比べ、変わったブロックだけを置く。tick 予算に合わせて yield する。
   *
   * 同じ色の変更ブロックを長方形にまとめ、dimension.fillBlocks 1 回で置く (API 呼び出しを大きく減らす)。
   * 横に続く同じ色を伸ばし、下の行でも同じ幅が全部同じ色の変更なら下へも伸ばす。
   * 立体では、同じ奥行きの段の中で縦に伸ばした後、奥の段へも伸ばして 1 個の箱にする。
   * fillBlocks が使えない環境では 1 個ずつ setBlockPermutation で置く。
   */
  *placeChangedBlocks(width) {
    if (this.useFill === undefined) this.useFill = typeof this.dimension.fillBlocks === "function";
    let mode = this.useFill ? currentRenderMode() : "block";
    if (mode === "auto") mode = yield* this.chooseRenderMode(width);
    if (mode === "none") return;
    if (mode === "list") {
      yield* this.placeChangedBlocksAsLists(width);
      return;
    }
    yield* this.placeChangedBlocksAsRects(width, mode === "rect");
  }

  /**
   * 置けなかったブロックがあれば、再生ごとに 1 回だけチャットで知らせる (理由のエラーも)。置けなかったマスは盤面を
   * 「不明」にして次のフレームで置き直すが、実機では置き直しも続けて失敗し、前の場面のブロックが残ることがあった。
   */
  reportPlaceFailures(count, error) {
    if (count <= 0) return;
    warnOnce("setBlock", `Failed to place ${count} block(s): ${error}`);
    if (this.playStats) this.playStats.placeFailures = (this.playStats.placeFailures ?? 0) + count;
    if (this.placeFailureNotified) return;
    this.placeFailureNotified = true;
    const reason = String(error?.message ?? error ?? "").replace(/\s+/g, " ").slice(0, 120);
    notify("place_failed", String(count), reason);
  }

  /** rect / block 方式 (useRects が false なら 1 個ずつ)。 */
  *placeChangedBlocksAsRects(width, useRects) {
    const video = this.videoData;
    const board = this.board;
    const next = this.nextBoard;
    const palette = this.activePalette;
    const airLevel = isVolume(video) ? video.air_level : -2;
    const rows = next.length / width;
    // 縦に伸ばしてよい行のまとまり (平面は全行、立体は奥行き 1 段ぶんの高さ)
    const contiguousRows = isVolume(video) ? video.height : rows;
    const from = { x: 0, y: 0, z: 0 };
    const to = { x: 0, y: 0, z: 0 };
    let operations = 0;
    let scanned = 0;
    let failures = 0;
    let lastError = null;
    // 立体では、新しいブロックを先に置き、空気にするのは最後にする。1 フレームが何 tick にもまたがったとき、
    // 途中の状態が「古い面が消えて新しい面がまだ無い穴」ではなく「一瞬厚い」に見える (実機の録画で穴が見えたため)
    const phases = airLevel >= 0 ? 2 : 1;

    for (let phase = 0; phase < phases; phase++) {
    for (let r = 0; r < rows; r++) {
      const rowStart = r * width;
      let x = 0;
      while (x < width) {
        const p = rowStart + x;
        const level = next[p];
        if (level === board[p] || level < 0 || (phases === 2 && (level === airLevel) !== (phase === 1))) {
          x++;
          continue;
        }
        const { x1, r1, slices } = this.growBox(width, rows, contiguousRows, r, x, level, useRects, null);
        const permutation = level === airLevel ? this.airPermutation : palette ? palette[level] : null;
        const area = (x1 - x + 1) * (r1 - r + 1) * slices;

        if (area > 1 && useRects && permutation) {
          const lastRow = r1 + (slices - 1) * contiguousRows;
          this.fillBoxCells(board, width, contiguousRows, x, x1, r, r1, slices, level);
          cellToWorld(video, this.anchor, p, from);
          cellToWorld(video, this.anchor, lastRow * width + x1, to);
          try {
            this.dimension.fillBlocks(new BlockVolume(from, to), permutation);
          } catch (e) {
            failures += area;
            lastError = e;
            this.fillBoxCells(board, width, contiguousRows, x, x1, r, r1, slices, -1);
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
              lastError = e;
              board[q] = -1;
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
    }
    this.reportPlaceFailures(failures, lastError);
  }

  /**
   * (r 行, x 列) から始まる、色 level の「変わったマス」だけでできた箱を探す (rect 方式と auto の見積もりで共通)。
   * 横に伸ばし、useRects なら下 (同じ奥行きの段の中) へ、立体なら奥の段へも伸ばす。1 回の fillBlocks は MAX_FILL_BLOCKS まで。
   * claimed (Uint8Array) を渡すと、そこで 1 のマスは使用済みとして避ける。戻り値のオブジェクトは使い回す。
   */
  growBox(width, rows, contiguousRows, r, x, level, useRects, claimed) {
    const next = this.nextBoard;
    const board = this.board;
    const free = (q) => next[q] === level && board[q] !== level && !(claimed && claimed[q]);
    const p = r * width + x;
    let x1 = x;
    while (x1 + 1 < width && free(p + x1 + 1 - x)) x1++;
    if (useRects && x1 - x + 1 > MAX_FILL_BLOCKS) x1 = x + MAX_FILL_BLOCKS - 1;
    let r1 = r;
    if (useRects) {
      while (r1 + 1 < rows && (r1 + 1) % contiguousRows !== 0 && (r1 + 2 - r) * (x1 - x + 1) <= MAX_FILL_BLOCKS) {
        const base = (r1 + 1) * width;
        let sameRun = true;
        for (let k = x; k <= x1; k++) {
          if (!free(base + k)) {
            sameRun = false;
            break;
          }
        }
        if (!sameRun) break;
        r1++;
      }
    }
    // 立体では、同じ長方形が奥の段でも全部同じ色の変更なら奥へも伸ばして 1 個の箱にする
    let slices = 1;
    if (useRects && isVolume(this.videoData)) {
      const sliceCells = contiguousRows * width;
      const faceArea = (x1 - x + 1) * (r1 - r + 1);
      while (r + slices * contiguousRows < rows && (slices + 1) * faceArea <= MAX_FILL_BLOCKS) {
        const offset = slices * sliceCells;
        let sameFace = true;
        for (let rr = r; rr <= r1 && sameFace; rr++) {
          const base = rr * width + offset;
          for (let k = x; k <= x1; k++) {
            if (!free(base + k)) {
              sameFace = false;
              break;
            }
          }
        }
        if (!sameFace) break;
        slices++;
      }
    }
    const box = this.box ?? (this.box = { x1: 0, r1: 0, slices: 1 });
    box.x1 = x1;
    box.r1 = r1;
    box.slices = slices;
    return box;
  }

  /**
   * auto 方式: このフレームで変わったマスを rect / list / block で置いたときの時間を、費用のモデルで見積もり、
   * 一番速い方式を返す (変わったマスが無ければ "none")。rect の箱の数は実際と同じ探し方で数える。
   */
  *chooseRenderMode(width) {
    const video = this.videoData;
    const board = this.board;
    const next = this.nextBoard;
    const rows = next.length / width;
    const contiguousRows = isVolume(video) ? video.height : rows;
    if (!this.claimed || this.claimed.length !== next.length) this.claimed = new Uint8Array(next.length);
    const claimed = this.claimed;
    claimed.fill(0);
    let changed = 0;
    let boxes = 0;
    let boxBlocks = 0;
    let singles = 0;
    const levels = new Set();
    let scanned = 0;
    for (let r = 0; r < rows; r++) {
      let x = 0;
      while (x < width) {
        const p = r * width + x;
        const level = next[p];
        if (claimed[p] || level === board[p] || level < 0) {
          x++;
          continue;
        }
        const { x1, r1, slices } = this.growBox(width, rows, contiguousRows, r, x, level, true, claimed);
        this.fillBoxCells(claimed, width, contiguousRows, x, x1, r, r1, slices, 1);
        const area = (x1 - x + 1) * (r1 - r + 1) * slices;
        changed += area;
        levels.add(level);
        if (area > 1) {
          boxes++;
          boxBlocks += area;
        } else {
          singles++;
        }
        x = x1 + 1;
      }
      scanned += width;
      if (scanned >= PIXELS_PER_SCAN_YIELD) {
        scanned = 0;
        yield;
      }
    }
    if (changed === 0) return "none";
    const cost = currentCostModel();
    const estimates = {
      rect: cost.fillCall * boxes + cost.fillBlock * boxBlocks + cost.setBlock * singles,
      list: cost.listCall * (levels.size + Math.floor(changed / LIST_BATCH)) + cost.listBlock * changed,
      block: cost.setBlock * changed,
    };
    let best = "rect";
    for (const mode of ["list", "block"]) if (estimates[mode] < estimates[best]) best = mode;
    this.lastRenderChoice = best;
    return best;
  }

  /** 盤面上の箱 (x0..x1 列, r0..r1 行, 奥へ slices 段) を value にする。 */
  fillBoxCells(board, width, sliceRows, x0, x1, r0, r1, slices, value) {
    for (let s = 0; s < slices; s++) {
      for (let rr = r0; rr <= r1; rr++) {
        const base = (rr + s * sliceRows) * width;
        board.fill(value, base + x0, base + x1 + 1);
      }
    }
  }

  /**
   * list 方式: 変わったマスを色ごとの座標リストにまとめ、fillBlocks(ListBlockVolume) で置く。
   * 1 フレームの呼び出し回数がおよそ「色の数」になる (形に関係なく少ない)。
   * 盤面は置いた後で更新するので、途中で一時停止・シークしても表示とずれない。
   */
  *placeChangedBlocksAsLists(width) {
    const video = this.videoData;
    const board = this.board;
    const next = this.nextBoard;
    const palette = this.activePalette;
    const airLevel = isVolume(video) ? video.air_level : -2;
    const groups = new Map();
    let failures = 0;
    let lastError = null;
    const self = this;

    let placedSinceYield = 0;
    function* flush(level, group) {
      if (group.cells.length === 0) return;
      const permutation = level === airLevel ? self.airPermutation : palette ? palette[level] : null;
      let placedLevel = level;
      if (permutation) {
        try {
          self.dimension.fillBlocks(new ListBlockVolume(group.locations), permutation);
        } catch (e) {
          failures += group.cells.length;
          lastError = e;
          placedLevel = -1;
          warnOnce("fill-list", `fillBlocks(ListBlockVolume) failed: ${e}`);
        }
      }
      for (const cell of group.cells) board[cell] = placedLevel;
      // 色ごとではなく、置いたブロックが BLOCKS_PER_YIELD 個たまるごとに区切る。1 tick に区切れる回数には上限
      // (MAX_YIELDS_PER_TICK) があるので、色ごとに区切ると色の多いフレーム (自動パレットで 40〜60 色) が
      // 何 tick にもまたがり、映像が遅れて音声が途切れていた (実機の録画: 128×128・10 fps で実効約 3 fps)
      placedSinceYield += group.cells.length;
      group.cells = [];
      group.locations = [];
      if (placedSinceYield >= BLOCKS_PER_YIELD) {
        placedSinceYield = 0;
        yield;
      }
    }

    // 立体では空気にするマスを最後に回す (rect 方式と同じく、描画の途中で穴が見えないように)
    const airCells = [];
    for (let p = 0; p < next.length; p++) {
      if (p % PIXELS_PER_SCAN_YIELD === PIXELS_PER_SCAN_YIELD - 1) yield;
      const level = next[p];
      if (level === board[p] || level < 0) continue;
      if (level === airLevel) {
        airCells.push(p);
        continue;
      }
      let group = groups.get(level);
      if (!group) {
        group = { cells: [], locations: [] };
        groups.set(level, group);
      }
      group.cells.push(p);
      group.locations.push(cellToWorld(video, this.anchor, p, { x: 0, y: 0, z: 0 }));
      if (group.cells.length >= LIST_BATCH) yield* flush(level, group);
    }
    for (const [level, group] of groups) yield* flush(level, group);
    const airGroup = { cells: [], locations: [] };
    for (const p of airCells) {
      airGroup.cells.push(p);
      airGroup.locations.push(cellToWorld(video, this.anchor, p, { x: 0, y: 0, z: 0 }));
      if (airGroup.cells.length >= LIST_BATCH) yield* flush(airLevel, airGroup);
    }
    yield* flush(airLevel, airGroup);
    this.reportPlaceFailures(failures, lastError);
  }

  *applyFrameJob(frameIndex) {
    if (!this.videoData) return;
    if (!this.ensureDecodedData()) return;
    this.initPaletteCache();
    const width = this.videoData.width;

    if (this.board && this.decodedIndex) {
      if (frameIndex < 0 || frameIndex >= this.decodedIndex.length) return;
      const entry = this.decodedIndex[frameIndex];

      // 先の GOP を先読みする。40 フレーム先までにある GOP をすべて (短い GOP が続いても取りこぼさない)。
      // キャッシュは MAX_CACHED_GOPS 個なので、今の GOP の次から 2 個まで
      const lookaheadFrame = Math.min(frameIndex + GOP_PREFETCH_FRAMES, this.decodedIndex.length - 1);
      const lastGopId = Math.min(this.decodedIndex[lookaheadFrame].gopId, entry.gopId + 2);
      for (let gopId = entry.gopId + 1; gopId <= lastGopId; gopId++) this.prefetchGop(gopId);
      // decoded を frameIndex の状態にする。直前のフレームの続きでなければ (シークの中断・ベンチマーク・
      // 描画途中での一時停止の後など)、直近のキーフレームから置かずに復元し直す
      let resynced = false;
      if (this.decodedFrame !== frameIndex) {
        if (this.decodedFrame !== frameIndex - 1) {
          resynced = true;
          const start = findKeyframeAtOrBefore(this.decodedIndex, frameIndex);
          for (let f = start; f < frameIndex; f++) {
            if (!(yield* this.decodeOnlyJob(f))) return;
            yield DECODE_YIELD;
          }
        }
        if (!(yield* this.decodeOnlyJob(frameIndex))) return;
      }
      // 省略フレームは盤面が変わらないので、順番どおりに来たときは何も置かない
      if (entry.length === 0 && !resynced) return;

      this.currentGopId = entry.gopId;
      this.activePalette = this.videoData.adaptive_palette ? this.paletteCache[this.currentGopId] : this.paletteCache;
      if (this.expandedPlanes) expandPlanes(this.decoded, this.expandedPlanes, this.nextBoard, this.videoData);
      else this.nextBoard.set(this.decoded);
      // 自動パレットで GOP が変わると同じ番号でも別のブロックになるので、表示中の盤面を「不明」にする
      if (this.videoData.adaptive_palette && entry.gopId !== this.boardGopId) this.invalidateForPaletteChange();
      this.boardGopId = entry.gopId;
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

  /** frameIndex を decoded へ復元するだけで、ブロックは置かない (decoded は frameIndex - 1 の状態であること)。 */
  *decodeOnlyJob(frameIndex) {
    const entry = this.decodedIndex[frameIndex];
    if (entry.length > 0) {
      const gopBytes = yield* this.gopBytesJob(entry.gopId, entry.offset + entry.length);
      if (!gopBytes) return false;
      decodeFrameInto(
        gopBytes, entry.offset, entry.offset + entry.length, this.decoded, this.decodeScratch, this.videoData, this.tiles
      );
      this.decoded.set(this.decodeScratch);
    }
    this.decodedFrame = frameIndex;
    return true;
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
      const st = this.playStats;
      if (st) {
        if (st.wallStart === null) {
          st.wallStart = Date.now();
          st.tickStart = this.elapsedTicks - 1;
        }
        // 予定の tick (表示時刻が来た最初の tick) から何 tick 遅れて描き始めたか
        st.frameLate = this.elapsedTicks - (Math.floor(this.currentFrame * this.ticksPerFrame) + 1);
        st.frameMs = 0;
      }
    }

    const tickStart = Date.now();
    // ブロックを置いた後の yield は MAX_YIELDS_PER_TICK 回まで (1 tick のブロック数の上限)。GOP の展開の yield は数えない
    let yields = 0;
    while (yields < MAX_YIELDS_PER_TICK) {
      const { done, value } = this.frameIterator.next();
      if (value !== DECODE_YIELD) yields++;
      if (done) {
        this.recordFrameStats(Date.now() - tickStart);
        this.syncAudioForFrame(this.currentFrame);
        this.currentFrame++;
        this.frameIterator = null;
        return;
      }
      if (Date.now() - tickStart > TICK_TIME_BUDGET_MS) break;
    }
    if (this.playStats) this.playStats.frameMs += Date.now() - tickStart;
  }

  recordFrameStats(lastTickMs) {
    const st = this.playStats;
    if (!st) return;
    if (st.skipPartialFrame) {
      st.skipPartialFrame = false;
      st.startFrame = this.currentFrame + 1;
      st.wallStart = null;
      return;
    }
    const frameMs = st.frameMs + lastTickMs;
    st.frames++;
    st.scriptMs += frameMs;
    st.maxFrameMs = Math.max(st.maxFrameMs, frameMs);
    if (st.frameLate > 0) st.lateFrames++;
    st.maxLateTicks = Math.max(st.maxLateTicks, st.frameLate);
  }

  cancelJob() {
    if (this.currentJobId !== null) {
      system.clearJob(this.currentJobId);
      this.currentJobId = null;
    }
  }

  stopPlayback() {
    this.stopPlayStatsClock();
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
    this.placeFailureNotified = false;
    this.resetPlayStats();
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
    this.placeFailureNotified = false;
    this.resetPlayStats();
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
      // 塗った内容が分かっているマスは盤面に記録する (次の再生で置き直すマスが最小になる)。
      // 立体は空気、固定パレットの平面はパレット 0 番。自動パレットの平面は GOP ごとに番号の意味が違うので不明のまま。
      // 置けなかったマスは不明 (-1) のままにして、次の再生で必ず置き直す
      const clearedLevel = volume ? self.videoData.air_level : self.videoData.adaptive_palette ? -1 : 0;
      let operations = 0;
      for (let p = 0; p < cells; p++) {
        cellToWorld(self.videoData, self.anchor, p, loc);
        let placed = true;
        try {
          self.dimension.setBlockPermutation(loc, clearPermutation);
        } catch (e) {
          placed = false;
          warnOnce("clear", `Failed to place blocks while clearing the screen: ${e}`);
        }
        if (self.board) self.board[p] = placed ? clearedLevel : -1;
        if (++operations >= BLOCKS_PER_YIELD) {
          operations = 0;
          yield;
        }
      }
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
      if (self.decodedIndex && self.board) {
        // v4 / v5: 途中のフレームは置かずに復元だけして、目的のフレームだけを置く
        yield* self.applyFrameJob(targetFrame);
      } else {
        for (let f = startFrame; f <= targetFrame; f++) {
          yield* self.applyFrameJob(f);
        }
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

/**
 * 描画 API の速さを実機で測る (/scriptevent <接頭辞>:bench)。スクリーンの範囲を使うので、先に setup が必要。
 * 同じマス (最大 64x64) を白黒交互に塗り替え、方式ごとの 1 ブロックあたりの時間を表示する。
 */
function runBenchmark(player) {
  const vp = getActivePlayer();
  if (!vp || !vp.videoData) {
    notify("no_anchor", EVENT_PREFIX);
    return;
  }
  const dimension = vp.dimension;
  const side = Math.min(64, vp.videoData.width, footprintOf(vp.videoData).sizeZ);
  const base = { x: vp.anchor.x, y: vp.anchor.y, z: vp.anchor.z };
  const colors = [BlockPermutation.resolve("minecraft:white_concrete"), BlockPermutation.resolve("minecraft:black_concrete")];
  const cells = [];
  for (let z = 0; z < side; z++) for (let x = 0; x < side; x++) cells.push({ x: base.x + x, y: base.y, z: base.z + z });
  // Date.now は 1 ms 単位なので、小さな範囲では回数を増やして合計時間を長くする
  const rounds = Math.min(60, Math.max(6, Math.ceil(20000 / cells.length)));
  const rowCells = [];
  for (let z = 0; z < side; z++) rowCells.push(cells.slice(z * side, (z + 1) * side));

  const methods = [
    ["setBlockPermutation", (perm) => {
      for (const loc of cells) dimension.setBlockPermutation(loc, perm);
      return cells.length;
    }],
    ["fillBlocks(row)", (perm) => {
      for (let z = 0; z < side; z++) {
        dimension.fillBlocks(new BlockVolume({ x: base.x, y: base.y, z: base.z + z }, { x: base.x + side - 1, y: base.y, z: base.z + z }), perm);
      }
      return side;
    }],
    ["fillBlocks(area)", (perm) => {
      dimension.fillBlocks(new BlockVolume(base, { x: base.x + side - 1, y: base.y, z: base.z + side - 1 }), perm);
      return 1;
    }],
    ["fillBlocks(list)", (perm) => {
      dimension.fillBlocks(new ListBlockVolume(cells), perm);
      return 1;
    }],
    ["fillBlocks(list rows)", (perm) => {
      for (const row of rowCells) dimension.fillBlocks(new ListBlockVolume(row), perm);
      return side;
    }],
    ["structure.place", (perm, round) => {
      const id = `${EVENT_NAMESPACE}:bench_${round % 2}`;
      world.structureManager.place(id, dimension, base);
      return 1;
    }],
    ["runCommand(fill)", (perm, round) => {
      const block = round % 2 === 0 ? "white_concrete" : "black_concrete";
      for (let z = 0; z < side; z++) {
        dimension.runCommand(`fill ${base.x} ${base.y} ${base.z + z} ${base.x + side - 1} ${base.y} ${base.z + z} ${block}`);
      }
      return side;
    }],
  ];

  notify("bench_start", side * side, rounds);
  vp.stopPlayback();
  system.runJob((function* () {
    // structure.place 用に、白と黒の板をメモリ上に作っておく (計測には含めない)
    try {
      for (let k = 0; k < 2; k++) {
        const id = `${EVENT_NAMESPACE}:bench_${k}`;
        world.structureManager.delete(id);
        const structure = world.structureManager.createEmpty(id, { x: side, y: 1, z: side });
        for (let z = 0; z < side; z++) for (let x = 0; x < side; x++) structure.setBlockPermutation({ x, y: 0, z }, colors[k]);
      }
    } catch (e) {
      warnOnce("bench-structure", `Could not prepare benchmark structures: ${e}`);
    }
    yield;
    const totals = {};
    for (const [name, method] of methods) {
      let elapsed = 0;
      let calls = 0;
      let failed = false;
      for (let round = 0; round < rounds; round++) {
        const start = Date.now();
        try {
          calls += method(colors[round % 2], round);
        } catch (e) {
          failed = true;
          warnOnce(`bench-${name}`, `${name} failed: ${e}`);
          break;
        }
        elapsed += Date.now() - start;
        yield;
      }
      if (failed) {
        notify("bench_failed", name);
      } else {
        totals[name] = elapsed;
        const microsPerBlock = Math.round((elapsed * 1000) / (rounds * side * side) * 100) / 100;
        notify("bench_result", name, microsPerBlock, Math.round(calls / rounds), elapsed);
      }
      yield;
    }
    vp.invalidateBoard();
    const model = fitCostModel(totals, rounds, side);
    if (model) {
      world.setDynamicProperty(COST_MODEL_KEY, JSON.stringify(model));
      costModelCache = null;
      notify("bench_model", model.fillCall, model.fillBlock, model.setBlock, model.listCall, model.listBlock);
    } else {
      notify("bench_unreliable", BENCH_MIN_TOTAL_MS);
    }
    notify("bench_done", currentRenderMode());
  })());
}

/**
 * bench の合計時間 (ミリ秒) から費用のモデル (マイクロ秒) を求める。測れなかった・短すぎる場合は null (既定値のまま)。
 * 全面 1 回と 1 行ずつ (side 回) の差から 1 回あたりの費用を、残りから 1 ブロックあたりの費用を求める。
 */
function fitCostModel(totals, rounds, side) {
  const needed = ["setBlockPermutation", "fillBlocks(row)", "fillBlocks(area)", "fillBlocks(list)", "fillBlocks(list rows)"];
  if (!needed.every((name) => totals[name] >= BENCH_MIN_TOTAL_MS)) return null;
  const cells = side * side;
  const perRound = (name) => (totals[name] * 1000) / rounds;
  const split = (many, one) => {
    const call = Math.max(0, (perRound(many) - perRound(one)) / Math.max(1, side - 1));
    return [call, Math.max(0, (perRound(one) - call) / cells)];
  };
  const [fillCall, fillBlock] = split("fillBlocks(row)", "fillBlocks(area)");
  const [listCall, listBlock] = split("fillBlocks(list rows)", "fillBlocks(list)");
  const round2 = (v) => Math.round(v * 100) / 100;
  return {
    setBlock: round2(perRound("setBlockPermutation") / cells),
    fillCall: round2(fillCall),
    fillBlock: round2(fillBlock),
    listCall: round2(listCall),
    listBlock: round2(listBlock),
  };
}

function setRenderMode(mode) {
  if (!RENDER_MODES.includes(mode)) {
    notify("render_mode_usage", EVENT_PREFIX, currentRenderMode());
    return;
  }
  world.setDynamicProperty(RENDER_MODE_KEY, mode);
  notify("render_mode_set", mode);
}

function getAnchorKeyStr(anchor) {
  return `${anchor.x},${anchor.y},${anchor.z}`;
}

/**
 * スクリーンの範囲が読み込まれ続けるよう tickingarea を登録する。
 * 1 区画は 100 チャンクまでなので 10x10 チャンクずつに分け、最大 MAX_TICKING_AREAS 区画まで登録する。
 * 戻り値: { added, needed }  needed > added なら一部しか常時読み込みされない (プレイヤーが近くにいれば再生はできる)
 */
function removeRegisteredTickingAreas(dimension) {
  let names = [];
  try {
    names = JSON.parse(world.getDynamicProperty(TICKING_NAMES_KEY) ?? "[]");
  } catch (e) { /* 壊れた値は無視する */ }
  for (const name of Array.isArray(names) ? names : []) {
    try {
      dimension.runCommand(`tickingarea remove ${name}`);
    } catch (e) { /* 既に無い */ }
  }
  world.setDynamicProperty(TICKING_NAMES_KEY, "[]");
}

function ensureTickingArea(dimension, anchor, videoData) {
  if (!videoData) return { added: 0, needed: 0 };
  // 以前の setup (別の位置・別の大きさ) で登録した区画を外す。外さないと 1 ワールド 10 区画の上限を使い切ってしまう
  removeRegisteredTickingAreas(dimension);
  const prefix = `${TICKING_AREA_NAME}_${anchor.x}_${anchor.y}_${anchor.z}`;
  for (let i = 0; i < 16; i++) {
    try {
      dimension.runCommand(`tickingarea remove ${prefix}_${i}`);
    } catch (e) { /* 未登録なら削除失敗は想定内 */ }
  }
  try {
    dimension.runCommand(`tickingarea remove ${prefix}`); // 以前の版の名前
  } catch (e) { /* 同上 */ }

  const { sizeX, sizeZ } = footprintOf(videoData);
  const chunk0X = Math.floor(anchor.x / 16);
  const chunk0Z = Math.floor(anchor.z / 16);
  const chunk1X = Math.floor((anchor.x + sizeX - 1) / 16);
  const chunk1Z = Math.floor((anchor.z + sizeZ - 1) / 16);
  const step = TICKING_AREA_CHUNKS_PER_SIDE;
  const areas = [];
  for (let cz = chunk0Z; cz <= chunk1Z; cz += step) {
    for (let cx = chunk0X; cx <= chunk1X; cx += step) {
      const fromX = Math.max(anchor.x, cx * 16);
      const fromZ = Math.max(anchor.z, cz * 16);
      const toX = Math.min(anchor.x + sizeX - 1, (cx + step) * 16 - 1);
      const toZ = Math.min(anchor.z + sizeZ - 1, (cz + step) * 16 - 1);
      areas.push([fromX, fromZ, toX, toZ]);
    }
  }
  let added = 0;
  const names = [];
  for (const [index, [fromX, fromZ, toX, toZ]] of areas.slice(0, MAX_TICKING_AREAS).entries()) {
    const name = `${prefix}_${index}`;
    try {
      dimension.runCommand(`tickingarea add ${fromX} ${anchor.y} ${fromZ} ${toX} ${anchor.y} ${toZ} ${name}`);
      names.push(name);
      added++;
    } catch (e) {
      console.warn(`[${EVENT_NAMESPACE}] tickingarea add failed: ${e}`);
    }
  }
  world.setDynamicProperty(TICKING_NAMES_KEY, JSON.stringify(names));
  return { added, needed: areas.length };
}

/**
 * プレイヤーの足元 origin から、動画を置く位置 (盤面の 0 番のマスの座標) を決める。
 * 平面は足元から北東へ広がる。立体はプレイヤーの 2 ブロック北に、左右中央をそろえて立て、建築できる高さに収める。
 */
function anchorForVideo(videoData, origin, dimension) {
  const volume = isVolume(videoData);
  const anchor = {
    x: origin.x - (volume ? Math.floor(videoData.width / 2) : 0),
    y: origin.y,
    z: origin.z - footprintOf(videoData).sizeZ - (volume ? 2 : 0),
  };
  const fittedY = fitToBuildHeight(dimension, anchor.y, volume ? videoData.height : 1);
  const shifted = fittedY !== anchor.y;
  anchor.y = fittedY;
  return { anchor, shifted };
}

/** 高さ sizeY の範囲が dimension.heightRange (max は含まない) に収まる一番近い y を返す。 */
function fitToBuildHeight(dimension, y, sizeY) {
  const range = dimension.heightRange;
  if (!range) return y;
  return Math.max(range.min, Math.min(y, range.max - sizeY));
}

// チャンク (16×16 の区画) の大きさ。ブロックを置き替えると、そのブロックがあるサブチャンク (16×16×16) が描き直され、
// プレイヤーへ送り直される。スクリーンが区画の境目をまたがないようにそろえると、触るチャンクの数が減る
const CHUNK_SIZE = 16;

/** start から size マスの範囲がまたぐチャンクの数。 */
function chunksSpanned(start, size) {
  return Math.floor((start + size - 1) / CHUNK_SIZE) - Math.floor(start / CHUNK_SIZE) + 1;
}

/** start を ±(CHUNK_SIZE - 1) の範囲でずらして、またぐチャンクが一番少なくなる量 (同じなら動かす量が小さい方)。 */
function chunkAlignShift(start, size) {
  let best = 0;
  for (let s = -(CHUNK_SIZE - 1); s <= CHUNK_SIZE - 1; s++) {
    const fewer = chunksSpanned(start + s, size) - chunksSpanned(start + best, size);
    if (fewer < 0 || (fewer === 0 && Math.abs(s) < Math.abs(best))) best = s;
  }
  return best;
}

/**
 * スクリーン (立体は床の範囲) がまたぐチャンクの数が一番少なくなるよう、置く位置 origin を東西・南北にずらす
 * (/scriptevent <接頭辞>:setup align)。ずらした量と、ずらす前後のチャンクの数を返す。
 */
function alignOriginToChunks(videoData, origin, dimension) {
  const { anchor } = anchorForVideo(videoData, origin, dimension);
  const { sizeX, sizeZ } = footprintOf(videoData);
  const dx = chunkAlignShift(anchor.x, sizeX);
  const dz = chunkAlignShift(anchor.z, sizeZ);
  return {
    origin: { x: origin.x + dx, y: origin.y, z: origin.z + dz },
    dx,
    dz,
    before: chunksSpanned(anchor.x, sizeX) * chunksSpanned(anchor.z, sizeZ),
    after: chunksSpanned(anchor.x + dx, sizeX) * chunksSpanned(anchor.z + dz, sizeZ),
  };
}

function setup(player, options = "") {
  if (!globalSelectedVideoId) {
    notify("no_video_selected");
    return;
  }
  const videoData = VIDEOS[globalSelectedVideoId];
  const loc = player.location;
  let origin = { x: Math.floor(loc.x), y: Math.floor(loc.y), z: Math.floor(loc.z) };
  if (options.split(/\s+/).includes("align")) {
    const aligned = alignOriginToChunks(videoData, origin, player.dimension);
    origin = aligned.origin;
    notify("setup_aligned", aligned.dx, aligned.dz, aligned.before, aligned.after);
  }
  // 建築できる高さ (オーバーワールドは Y=-64〜319) からはみ出すと置けないので、収まる高さへずらす
  const { anchor, shifted } = anchorForVideo(videoData, origin, player.dimension);
  if (shifted) notify("setup_shifted", anchor.y);
  world.setDynamicProperty(ORIGIN_KEY, origin);

  // 常時読み込みが一部しか登録できなくても、プレイヤーが近くにいれば再生できるので設置は続ける
  const ticking = ensureTickingArea(player.dimension, anchor, videoData);
  if (ticking.added < ticking.needed) notify("tickingarea_partial", ticking.added, ticking.needed);
  
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
    const playbackDimension = getPlaybackDimension(dimension);
    let anchor = anchorLoc;
    const origin = world.getDynamicProperty(ORIGIN_KEY);
    const videoData = VIDEOS[globalSelectedVideoId];
    if (origin && videoData) {
      // 平面と立体では置く位置・高さ・常時読み込みの範囲が違うので、setup の足元から決め直す
      const fitted = anchorForVideo(videoData, origin, playbackDimension);
      anchor = fitted.anchor;
      if (fitted.shifted) notify("setup_shifted", anchor.y);
      const ticking = ensureTickingArea(playbackDimension, anchor, videoData);
      if (ticking.added < ticking.needed) notify("tickingarea_partial", ticking.added, ticking.needed);
      activePlayers.delete(keyStr);
      world.setDynamicProperty(ANCHOR_KEY, anchor);
    }
    vp = new VideoPlayer(anchor, playbackDimension, globalSelectedVideoId);
    activePlayers.set(getAnchorKeyStr(anchor), vp);
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

/** 再生の計測結果を表示する (/scriptevent <接頭辞>:stats)。再生中でも、再生が終わった後でもよい。 */
function showPlayStats() {
  const vp = getActivePlayer();
  const summary = vp ? vp.playStatsSummary() : null;
  if (!summary) {
    notify("stats_none", EVENT_PREFIX);
    return;
  }
  const fixed = (value, digits) => value.toFixed(digits);
  notify("stats_play", summary.frames, fixed(summary.seconds, 1), fixed(summary.fps, 2), fixed(summary.targetFps, 2),
    fixed(summary.tps, 1), fixed(summary.driftSeconds, 2));
  notify("stats_load", summary.lateFrames, summary.maxLateTicks, fixed(summary.avgFrameMs, 1), summary.maxFrameMs);
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
    rawtext.push({ text: "\n" }, tr("library.item", v.frame_count, videoSizeLabel(v)));
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
    case "bench":
      runBenchmark(player);
      break;
    case "stats":
      showPlayStats();
      break;
    case "render":
      setRenderMode(event.message?.trim());
      break;
    case "setup":
      if (!player) {
        notify("setup_needs_player");
        break;
      }
      setup(player, event.message ?? "");
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
          const rawtext = [{ text: "  " }, tr("video_list_item", v.id, v.title, v.frame_count, videoSizeLabel(v))];
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
