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
  createStageWork,
  createTileDictionary,
  decodeFrameInto,
  decodeUTF16BinaryToBytes,
  isAnamorphic,
  isStagePadding,
  isVolume,
  decodeUTF16BinaryToBytesJob,
  expandPlanes,
  findKeyframeAtOrBefore,
  isHeightfield,
  parseFrameIndexV3,
  parseFrameIndexV4,
  projectToStage,
  readVarint,
  skipFrameHeader,
  STAGE_IMAGE_OFFSET,
  stageCellOffset,
  stageGeometry,
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
// setup で決めたスクリーンの向き (JSON の { facing, wall })。動画を切り替えても同じ向きで置き直す
const LAYOUT_KEY = `${EVENT_NAMESPACE}:layout`;
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
// GOP の展開と、変わったマスを探す盤面の走査の途中で出す yield の値。ブロックを置いた後の yield と違い、1 tick の
// yield の回数の上限 (ブロック数の上限) には数えず、時間の予算だけで区切る (展開は何百回も yield する。走査は
// 2.5D の 128×72×16 で 1 フレーム 5〜14 回 yield し、回数に数えると上限 8 回の大半を使って、置くブロックが少なくても
// 1 フレームが 3 tick 以上にまたがり、実機の録画で 10 fps の映像が 8 fps に落ちていた)
const DECODE_YIELD = 1;
// だまし絵 (view: "anamorphic") の壁と床の盤面の値。パレット番号と重ならない大きな番号にし、activePalette に足す
const STAGE_WALL_LEVEL = 30000;
const STAGE_FLOOR_LEVEL = 30001;
// だまし絵の左右の壁の、人物の写らないマス (空気。見本のように、横には人物の写る所だけブロックを置く)
const STAGE_AIR_LEVEL = 30002;
const DEFAULT_STAGE_WALL = "minecraft:light_gray_concrete";
const DEFAULT_STAGE_FLOOR = "minecraft:black_concrete";
// だまし絵: 見ている人の目がこれだけ (ブロック) 動いたら、フレームの合間にも描き直す
const VIEW_REDRAW_DISTANCE = 0.25;
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

// 一覧・リモコンの「次の動画」に出す動画。だまし絵の背景の映像 (hidden) は、人物の動画と一緒に再生するので出さない
const LISTED_VIDEOS = VIDEO_LIST.filter((v) => !v.hidden);
let globalSelectedVideoId = LISTED_VIDEOS.length > 0 ? LISTED_VIDEOS[0].id : null;

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

// スクリーンの向き (facing): setup を実行したプレイヤーが見ていた方向。0 北・1 東・2 南・3 西。
// 盤面は向きに関係なく「ローカル座標」(x は見ている人の右、z は見ている人の側 (手前)) で並べ、置くときに回す。
// 北向き (setup の既定) ではローカル座標とワールド座標が同じ
const FACING_NAMES = ["north", "east", "south", "west"];
const DEFAULT_LAYOUT = { facing: 0, wall: false };

/** ローカルのマス (lx, lz) の anchor からのずれを out.x / out.z に書く。マスの中心で回すので並びは崩れない。 */
function rotateCell(facing, lx, lz, out) {
  if (facing === 1) {
    out.x = -lz - 1;
    out.z = lx;
  } else if (facing === 2) {
    out.x = -lx - 1;
    out.z = -lz - 1;
  } else if (facing === 3) {
    out.x = lz;
    out.z = -lx - 1;
  } else {
    out.x = lx;
    out.z = lz;
  }
  return out;
}

/** ワールドでの anchor の角からのずれ (dx, dz。小数でよい) をローカル座標へ戻す (だまし絵の目の位置用)。 */
function unrotatePoint(facing, dx, dz, out) {
  if (facing === 1) {
    out.x = dz;
    out.z = -dx;
  } else if (facing === 2) {
    out.x = -dx;
    out.z = -dz;
  } else if (facing === 3) {
    out.x = -dz;
    out.z = dx;
  } else {
    out.x = dx;
    out.z = dz;
  }
  return out;
}

/** 平面の動画を壁のように立てて置くか (setup wall。立体・だまし絵は元から立っているので関係しない)。 */
function standsAsWall(videoData, anchor) {
  return !!anchor.wall && !isVolume(videoData) && !isAnamorphic(videoData);
}

// 盤面の 1 マス (p 番目) をワールド座標にする (以下はローカル座標。最後に向きに合わせて回す)。
//  平面スクリーン: 足元の高さに、x は右、行は手前向き (z+)。setup wall のときは立てる (行 0 が一番上)
//  立体: x は右、高さは上向き (行 0 が一番上)、奥行き 0 が一番奥 (プレイヤーから遠い側)
//  だまし絵: 壁と床 (stageCellOffset。anchor は舞台の左奥の角の床の高さ)
function cellToWorld(videoData, anchor, p, loc) {
  let lx;
  let lz;
  if (isAnamorphic(videoData)) {
    stageCellOffset(stageOf(videoData), p, loc);
    lx = loc.x;
    lz = loc.z;
    loc.y += anchor.y;
  } else {
    const width = videoData.width;
    const height = videoData.height;
    const row = Math.floor(p / width);
    lx = p % width;
    if (isVolume(videoData)) {
      loc.y = anchor.y + (height - 1 - (row % height));
      lz = Math.floor(row / height);
    } else if (anchor.wall) {
      loc.y = anchor.y + (height - 1 - row);
      lz = 0;
    } else {
      loc.y = anchor.y;
      lz = row;
    }
  }
  rotateCell(anchor.facing ?? 0, lx, lz, loc);
  loc.x += anchor.x;
  loc.z += anchor.z;
  return loc;
}

// 動画一覧に出す大きさ (立体は 幅x高さx奥行き)
function videoSizeLabel(v) {
  return v.depth ? `${v.width}x${v.height}x${v.depth}` : `${v.width}x${v.height}`;
}

const stageGeometryCache = new WeakMap();
/** だまし絵の舞台の大きさ (動画ごとに 1 回だけ計算する)。 */
function stageOf(videoData) {
  let geom = stageGeometryCache.get(videoData);
  if (!geom) {
    geom = stageGeometry(videoData);
    stageGeometryCache.set(videoData, geom);
  }
  return geom;
}

/**
 * だまし絵の後ろの壁に流す背景の映像 (同じアドオンの平面の動画) の ID。大きさが後ろの壁と同じ (幅 stageWidth ×
 * 高さ wallHeight) でなければ使わない。無ければ null。
 */
function stageBackgroundOf(videoData) {
  const id = isAnamorphic(videoData) ? videoData.stage_background : null;
  const background = id ? VIDEOS[id] : null;
  if (!background) return null;
  const geom = stageOf(videoData);
  if (isVolume(background) || background.width !== geom.stageWidth || background.height !== geom.wallHeight) {
    warnOnce(`background:${id}`, `Background video ${id} must be a ${geom.stageWidth}x${geom.wallHeight} flat screen; ignored`);
    return null;
  }
  return id;
}

/** 盤面を立体 (空気を含む箱) としてワールドに置くか。だまし絵は立体のデータでも、置くのは壁と床だけ。 */
function placesVolume(videoData) {
  return isVolume(videoData) && !isAnamorphic(videoData);
}

function boardSizeOf(videoData) {
  if (isAnamorphic(videoData)) return stageOf(videoData).cells;
  return videoData.width * videoData.height * (isVolume(videoData) ? videoData.depth : 1);
}

/** 盤面の 1 行のマスの数 (だまし絵は舞台の幅)。 */
function boardWidthOf(videoData) {
  return isAnamorphic(videoData) ? stageOf(videoData).rowWidth : videoData.width;
}

// 占める範囲のローカル座標での x 方向 (左右)・z 方向 (奥行き) の大きさ。layout は setup の向き (wall なら平面も厚さ 1)
function footprintOf(videoData, layout = DEFAULT_LAYOUT) {
  if (isAnamorphic(videoData)) {
    const geom = stageOf(videoData);
    return { sizeX: geom.sizeX, sizeZ: geom.sizeZ };
  }
  if (isVolume(videoData)) return { sizeX: videoData.width, sizeZ: videoData.depth };
  return { sizeX: videoData.width, sizeZ: layout.wall ? 1 : videoData.height };
}

/** 占める範囲のワールドでの x・z の最小と最大 (両端を含む)。向きに合わせて回した後の範囲。 */
function worldBoxOf(videoData, anchor) {
  const { sizeX, sizeZ } = footprintOf(videoData, anchor);
  const a = rotateCell(anchor.facing ?? 0, 0, 0, { x: 0, z: 0 });
  const b = rotateCell(anchor.facing ?? 0, sizeX - 1, sizeZ - 1, { x: 0, z: 0 });
  return {
    x0: anchor.x + Math.min(a.x, b.x), x1: anchor.x + Math.max(a.x, b.x),
    z0: anchor.z + Math.min(a.z, b.z), z1: anchor.z + Math.max(a.z, b.z),
  };
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
    // 保存した anchor (x・y・z だけ) にも、setup で決めた向きを付ける
    const layout = anchor.facing === undefined ? storedLayout() : anchor;
    this.anchor = { x: anchor.x, y: anchor.y, z: anchor.z, facing: layout.facing, wall: layout.wall };
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
    // だまし絵 (view: "anamorphic") では、復元した立体 (人物) を figure に組み立て、nextBoard には人物を壁と床へ
    //   写した盤面を作る (stageWork は写すときの作業領域)。lastEye は最後に描いた目の位置 (舞台の原点から)、
    //   viewIterator はフレームの合間に目の動きに合わせて描き直す処理
    this.figure = null;
    this.stageWork = null;
    // だまし絵の背景の映像を復元する役 (ブロックは置かない VideoPlayer)。無ければ null
    this.background = null;
    this.stagePalettes = new Map();
    this.lastEye = null;
    this.viewDrawn = false;
    this.viewIterator = null;
    // だまし絵で、変わったマスを探す盤面の範囲 (行 [lo, hi)・列 [left, right))。人物を塗った所と、まだ置き終えて
    //   いない前の描画の所。盤面に「不明」のマスがあるときは全部
    this.stageRows = { lo: 0, hi: Infinity, left: 0, right: Infinity };
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

  /**
   * 再生するフレームの数。だまし絵で背景の映像の方が長ければ、背景の映像の長さ (人物のアニメは繰り返す)。
   */
  get frameCount() {
    const video = this.videoData;
    if (!video) return 0;
    const backgroundId = stageBackgroundOf(video);
    if (!backgroundId) return video.frame_count;
    const background = VIDEOS[backgroundId];
    return Math.max(video.frame_count, secondsToFrame(video, frameToSeconds(background, background.frame_count)));
  }

  /** 鳴らす音声の動画 ID。だまし絵に背景の映像があれば、その音声 (人物の .vox などには音声が無い)。 */
  get audioVideoId() {
    return (this.videoData && stageBackgroundOf(this.videoData)) ?? this.videoId;
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
      // 停止・クリアで再生位置が 0 に戻っても、止めた時点の位置で映像の遅れを計算する
      st.frameEnd = this.currentFrame;
    }
  }

  /** 計測結果 (再生していなければ null)。driftSeconds が正なら映像が実時間 (= 音声) より遅れている。 */
  playStatsSummary() {
    const st = this.playStats;
    if (!st || st.wallStart === null || st.frames === 0) return null;
    const seconds = Math.max(0.001, ((st.wallEnd ?? Date.now()) - st.wallStart) / 1000);
    const videoSeconds = frameToSeconds(this.videoData, st.frameEnd ?? this.currentFrame) - frameToSeconds(this.videoData, st.startFrame);
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
      const anamorphic = isAnamorphic(this.videoData);
      const { width, height, depth } = this.videoData;
      this.figure = anamorphic ? new Int16Array(width * height * depth).fill(this.videoData.air_level) : null;
      this.stageWork = anamorphic
        ? createStageWork(stageOf(this.videoData), STAGE_WALL_LEVEL, STAGE_FLOOR_LEVEL, STAGE_AIR_LEVEL) : null;
      const backgroundId = stageBackgroundOf(this.videoData);
      this.background = backgroundId ? new VideoPlayer(this.anchor, this.dimension, backgroundId) : null;
      this.viewDrawn = false;
      this.stageRows = { lo: 0, hi: Infinity, left: 0, right: Infinity };
      if (isHeightfield(this.videoData)) {
        this.decodeScratch = new Int16Array(streamPixels);
        // 平面の値には現れない値で埋めて、最初の組み立てで全部の列を作らせる
        this.expandedPlanes = new Int16Array(streamPixels).fill(-32768);
      } else {
        this.decodeScratch = this.figure ?? this.nextBoard;
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

      cellToWorld(this.videoData, this.anchor, run.position, loc);
      const bx = loc.x;
      const by = loc.y;
      const bz = loc.z;
      // ランは右へ続く (向きに合わせて回した右方向へ 1 マスずつ進む)
      const facing = this.anchor.facing ?? 0;
      const stepX = facing === 0 ? 1 : facing === 2 ? -1 : 0;
      const stepZ = facing === 1 ? 1 : facing === 3 ? -1 : 0;

      for (let i = 0; i < run.length; i++) {
        // yield を挟むので座標は毎回すべて書き直す
        loc.x = bx + i * stepX;
        loc.y = by;
        loc.z = bz + i * stepZ;
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
    this.stageRows = { lo: 0, hi: Infinity, left: 0, right: Infinity };
  }

  /** 変わったマスを探す盤面の範囲 [行の始め, 終わり, 列の始め, 終わり)。平面と立体は全部、だまし絵は this.stageRows。 */
  scanRange(rows, width) {
    if (!this.figure) return [0, rows, 0, width];
    const range = this.stageRows;
    return [Math.min(rows, range.lo), Math.min(rows, range.hi), Math.min(width, range.left), Math.min(width, range.right)];
  }

  /**
   * パレット番号の意味が変わるとき (自動パレットで GOP が切り替わるとき) に呼ぶ。平面は全マスを置き直す。
   * 立体は空気のマスは空気のままとみなし、ブロックのあるマスだけ置き直す (空気で全体を塗り直さずに済む)。
   * それ以外のキーフレームでは、表示中の盤面と比べて本当に変わったマスだけを置く。
   */
  invalidateForPaletteChange() {
    if (!this.board) return;
    if (isAnamorphic(this.videoData)) {
      // だまし絵の壁と床は GOP が変わっても同じブロックなので、人物が写っているマスだけ置き直す
      for (let p = 0; p < this.board.length; p++) {
        const level = this.board[p];
        if (level !== STAGE_WALL_LEVEL && level !== STAGE_FLOOR_LEVEL && level !== STAGE_AIR_LEVEL) this.board[p] = -1;
      }
      this.stageRows = { lo: 0, hi: Infinity, left: 0, right: Infinity };
      return;
    }
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
    const airLevel = placesVolume(video) ? video.air_level : -2;
    const rows = next.length / width;
    // 縦に伸ばしてよい行のまとまり (平面は全行、立体は奥行き 1 段ぶんの高さ)
    const contiguousRows = placesVolume(video) ? video.height : rows;
    const from = { x: 0, y: 0, z: 0 };
    const to = { x: 0, y: 0, z: 0 };
    let operations = 0;
    let scanned = 0;
    let failures = 0;
    let lastError = null;
    // 立体では、新しいブロックを先に置き、空気にするのは最後にする。1 フレームが何 tick にもまたがったとき、
    // 途中の状態が「古い面が消えて新しい面がまだ無い穴」ではなく「一瞬厚い」に見える (実機の録画で穴が見えたため)
    const phases = airLevel >= 0 ? 2 : 1;
    const [scanRow0, scanRow1, scanCol0, scanCol1] = this.scanRange(rows, width);

    for (let phase = 0; phase < phases; phase++) {
    for (let r = scanRow0; r < scanRow1; r++) {
      const rowStart = r * width;
      let x = scanCol0;
      while (x < scanCol1) {
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
        yield DECODE_YIELD;
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
    // だまし絵の壁と床の区画はワールドでつながっていないので、箱は区画の境目をまたがない
    const splits = isAnamorphic(this.videoData) ? stageOf(this.videoData).splits : null;
    const p = r * width + x;
    let x1 = x;
    while (x1 + 1 < width && free(p + x1 + 1 - x)) x1++;
    if (useRects && x1 - x + 1 > MAX_FILL_BLOCKS) x1 = x + MAX_FILL_BLOCKS - 1;
    let r1 = r;
    if (useRects) {
      while (r1 + 1 < rows && (r1 + 1) % contiguousRows !== 0 && !(splits && splits.includes(r1 + 1))
        && (r1 + 2 - r) * (x1 - x + 1) <= MAX_FILL_BLOCKS) {
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
    if (useRects && placesVolume(this.videoData)) {
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
    const contiguousRows = placesVolume(video) ? video.height : rows;
    if (!this.claimed || this.claimed.length !== next.length) this.claimed = new Uint8Array(next.length);
    const claimed = this.claimed;
    claimed.fill(0);
    let changed = 0;
    let boxes = 0;
    let boxBlocks = 0;
    let singles = 0;
    const levels = new Set();
    let scanned = 0;
    const [scanRow0, scanRow1, scanCol0, scanCol1] = this.scanRange(rows, width);
    for (let r = scanRow0; r < scanRow1; r++) {
      let x = scanCol0;
      while (x < scanCol1) {
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
        yield DECODE_YIELD;
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
    const airLevel = placesVolume(video) ? video.air_level : -2;
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
    const [scanRow0, scanRow1, scanCol0, scanCol1] = this.scanRange(next.length / width, width);
    const scanWidth = scanCol1 - scanCol0;
    let scanned = 0;
    for (let q = 0; q < (scanRow1 - scanRow0) * scanWidth; q++) {
      const p = (scanRow0 + Math.floor(q / scanWidth)) * width + scanCol0 + (q % scanWidth);
      if (++scanned === PIXELS_PER_SCAN_YIELD) {
        scanned = 0;
        yield DECODE_YIELD;
      }
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
    const width = boardWidthOf(this.videoData);

    if (this.board && this.decodedIndex) {
      // だまし絵で背景の映像の方が長ければ、人物のアニメを繰り返す
      const figureFrame = this.background ? frameIndex % this.decodedIndex.length : frameIndex;
      const synced = yield* this.decodeTo(figureFrame);
      if (!synced) return;
      const entry = synced.entry;
      // 背景の映像の、同じ時刻のフレーム
      let backgroundSynced = null;
      if (this.background) {
        const background = this.background;
        const seconds = frameToSeconds(this.videoData, frameIndex);
        const backgroundFrame = Math.min(background.videoData.frame_count - 1, secondsToFrame(background.videoData, seconds));
        backgroundSynced = yield* background.decodeTo(backgroundFrame);
      }
      // 省略フレームは盤面が変わらないので、順番どおりに来たときは何も置かない
      if (!synced.changed && !backgroundSynced?.changed) return;

      this.activePalette = this.videoData.adaptive_palette ? this.paletteCache[this.currentGopId] : this.paletteCache;
      const target = this.figure ?? this.nextBoard;
      if (this.expandedPlanes) expandPlanes(this.decoded, this.expandedPlanes, target, this.videoData);
      else target.set(this.decoded);
      if (this.figure) {
        this.stageWork.dirty = true;
        this.activePalette = this.stagePalette(this.activePalette, backgroundSynced ? this.background.framePalette() : null);
        this.drawStage();
        // 背景の映像が変わったら、後ろの壁の全体から変わったマスを探す
        if (backgroundSynced?.changed) this.extendStageRows(0, stageOf(this.videoData).wallHeight, 0, stageOf(this.videoData).stageWidth);
      }
      // 自動パレットで GOP が変わると同じ番号でも別のブロックになるので、表示中の盤面を「不明」にする
      if (this.videoData.adaptive_palette && entry.gopId !== this.boardGopId) this.invalidateForPaletteChange();
      this.boardGopId = entry.gopId;
      if (backgroundSynced && this.background.videoData.adaptive_palette && this.background.currentGopId !== this.backgroundGopId) {
        this.invalidateStageImage();
      }
      this.backgroundGopId = this.background?.currentGopId ?? -1;
      yield* this.placeChangedBlocks(width);
      if (this.figure) this.settleStageRows();
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

  /**
   * だまし絵のパレット: 人物のパレットに、背景の映像のパレット (STAGE_IMAGE_OFFSET から) と壁と床のブロックを
   * 足したもの (元のパレットの組ごとに 1 回だけ作る)。
   */
  stagePalette(base, backgroundBase = null) {
    let byBackground = this.stagePalettes.get(base);
    if (!byBackground) {
      byBackground = new Map();
      this.stagePalettes.set(base, byBackground);
    }
    let palette = byBackground.get(backgroundBase);
    if (!palette) {
      palette = base.slice();
      if (backgroundBase) backgroundBase.forEach((permutation, level) => { palette[STAGE_IMAGE_OFFSET + level] = permutation; });
      palette[STAGE_WALL_LEVEL] = resolvePermutation({ block: this.videoData.stage_wall ?? DEFAULT_STAGE_WALL });
      palette[STAGE_FLOOR_LEVEL] = resolvePermutation({ block: this.videoData.stage_floor ?? DEFAULT_STAGE_FLOOR });
      palette[STAGE_AIR_LEVEL] = this.airPermutation;
      byBackground.set(backgroundBase, palette);
    }
    return palette;
  }

  /** だまし絵: 変わったマスを探す範囲を広げる (行 [lo, hi)・列 [left, right))。 */
  extendStageRows(lo, hi, left, right) {
    const range = this.stageRows;
    range.lo = Math.min(range.lo, lo);
    range.hi = Math.max(range.hi, hi);
    range.left = Math.min(range.left, left);
    range.right = Math.max(range.right, right);
  }

  /** だまし絵: 背景の映像の自動パレットの GOP が変わったとき、背景の映像のマスを「不明」にして置き直させる。 */
  invalidateStageImage() {
    const board = this.board;
    for (let p = 0; p < board.length; p++) {
      if (board[p] >= STAGE_IMAGE_OFFSET && board[p] < STAGE_WALL_LEVEL) board[p] = -1;
    }
    const geom = stageOf(this.videoData);
    this.extendStageRows(0, geom.wallHeight, 0, geom.stageWidth);
  }

  /**
   * だまし絵を見る人の目の位置 (舞台の内側の座標)。同じディメンションで、箱の中か手前 (左右の壁の間、後ろの壁の前、
   * 床の上) にいるプレイヤーのうち、舞台の中央に一番近い人。いなければ null。
   */
  viewerEye() {
    const geom = stageOf(this.videoData);
    let best = null;
    let bestDistance = Infinity;
    for (const player of world.getAllPlayers()) {
      if (player.dimension?.id !== this.dimension.id) continue;
      let head;
      try {
        head = player.getHeadLocation();
      } catch (e) {
        continue;
      }
      // 舞台のローカル座標へ戻す (setup の向きに合わせて回して置いているため)
      const local = unrotatePoint(this.anchor.facing ?? 0, head.x - this.anchor.x, head.z - this.anchor.z, { x: 0, z: 0 });
      const eye = { x: local.x - geom.originX, y: head.y - this.anchor.y, z: local.z };
      if (!(eye.z > 1 && eye.y > 1 && eye.x > 0 && eye.x < geom.stageWidth)) continue;
      const dx = eye.x - geom.stageWidth / 2;
      const dz = eye.z - (1 + geom.floorRows / 2);
      if (dx * dx + dz * dz < bestDistance) {
        best = eye;
        bestDistance = dx * dx + dz * dz;
      }
    }
    return best;
  }

  /**
   * だまし絵: 人物 (figure) を、見ている人の目から見たとおりに壁と床へ写して nextBoard に書く。見ている人が
   * いなければ前回の目の位置 (最初は舞台の正面、人物の高さぶん離れた所) から描く。
   */
  drawStage() {
    const geom = stageOf(this.videoData);
    const eye = this.viewerEye() ?? this.lastEye
      ?? { x: geom.stageWidth / 2, y: 1 + this.videoData.height / 2, z: 1 + geom.floorRows + this.videoData.height };
    // 背景の映像のフレームが変わったときだけ後ろの壁を作り直す (decodedFrame を版にする)
    const background = this.background;
    projectToStage(this.figure, this.videoData, geom, eye, this.nextBoard, this.stageWork, background?.decoded ?? null,
      background ? background.decodedFrame : 0);
    const work = this.stageWork;
    if (work.rowMin <= work.rowMax) this.extendStageRows(work.rowMin, work.rowMax + 1, work.colMin, work.colMax + 1);
    this.lastEye = eye;
    this.viewDrawn = true;
  }

  /** だまし絵: 描いた盤面を置き終えたら、次に探す範囲を今の人物の所だけにする。 */
  settleStageRows() {
    const work = this.stageWork;
    this.stageRows = work.rowMin <= work.rowMax
      ? { lo: work.rowMin, hi: work.rowMax + 1, left: work.colMin, right: work.colMax + 1 }
      : { lo: 0, hi: 0, left: 0, right: 0 };
  }

  /** だまし絵: 見ている人の目が動いていれば、フレームの合間に描き直しを始める。 */
  maybeStartViewRedraw() {
    if (!this.figure || !this.viewDrawn || this.viewIterator || this.frameIterator || this.currentJobId !== null) return;
    const eye = this.viewerEye();
    const last = this.lastEye;
    if (!eye || (last && Math.hypot(eye.x - last.x, eye.y - last.y, eye.z - last.z) < VIEW_REDRAW_DISTANCE)) return;
    const self = this;
    this.viewIterator = (function* () {
      self.drawStage();
      yield* self.placeChangedBlocks(boardWidthOf(self.videoData));
      self.settleStageRows();
    })();
  }

  /** 目が動いていれば描き直しを始め、この tick のうちに進める (次の tick に回すと、次のフレームが遅れる)。 */
  startViewRedraw() {
    this.maybeStartViewRedraw();
    if (this.viewIterator && this.advanceJob(this.viewIterator)) this.viewIterator = null;
  }

  /** ジェネレータを 1 tick の予算 (ブロック数と時間) まで進める。終われば true。 */
  advanceJob(iterator) {
    const tickStart = Date.now();
    let yields = 0;
    while (yields < MAX_YIELDS_PER_TICK) {
      const { done, value } = iterator.next();
      if (done) return true;
      if (value !== DECODE_YIELD) yields++;
      if (Date.now() - tickStart > TICK_TIME_BUDGET_MS) break;
    }
    return false;
  }

  /**
   * decoded を frameIndex の状態にする (ブロックは置かない)。直前のフレームの続きでなければ (シークの中断・
   * ベンチマーク・描画途中での一時停止・だまし絵の人物のアニメの繰り返しの後など)、直近のキーフレームから復元し直す。
   * 戻り値は { entry, changed } (changed: 置き直すべき変化があるか)。復元できなければ null。
   */
  *decodeTo(frameIndex) {
    if (!this.ensureDecodedData() || !this.board || !this.decodedIndex) return null;
    this.initPaletteCache();
    if (frameIndex < 0 || frameIndex >= this.decodedIndex.length) return null;
    const entry = this.decodedIndex[frameIndex];
    // 先の GOP を先読みする。40 フレーム先までにある GOP をすべて (短い GOP が続いても取りこぼさない)。
    // キャッシュは MAX_CACHED_GOPS 個なので、今の GOP の次から 2 個まで
    const lookaheadFrame = Math.min(frameIndex + GOP_PREFETCH_FRAMES, this.decodedIndex.length - 1);
    const lastGopId = Math.min(this.decodedIndex[lookaheadFrame].gopId, entry.gopId + 2);
    for (let gopId = entry.gopId + 1; gopId <= lastGopId; gopId++) this.prefetchGop(gopId);
    let resynced = false;
    if (this.decodedFrame !== frameIndex) {
      if (this.decodedFrame !== frameIndex - 1) {
        resynced = true;
        const start = findKeyframeAtOrBefore(this.decodedIndex, frameIndex);
        for (let f = start; f < frameIndex; f++) {
          if (!(yield* this.decodeOnlyJob(f))) return null;
          yield DECODE_YIELD;
        }
      }
      if (!(yield* this.decodeOnlyJob(frameIndex))) return null;
    }
    this.currentGopId = entry.gopId;
    return { entry, changed: resynced || entry.length > 0 };
  }

  /** 今復元しているフレームのパレット (ブロックの一覧)。 */
  framePalette() {
    return this.videoData.adaptive_palette ? this.paletteCache[this.currentGopId] : this.paletteCache;
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
      const trackId = `${EVENT_NAMESPACE}.${this.audioVideoId}.chunk_${targetChunk}`;
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
    // だまし絵の描き直しの途中なら、それを先に進める (再生中は時計も進める)
    if (this.viewIterator) {
      if (this.running && this.startDelayTicks === 0) this.elapsedTicks++;
      if (this.advanceJob(this.viewIterator)) this.viewIterator = null;
      return;
    }
    if (!this.running) {
      this.startViewRedraw();
      return;
    }

    if (this.startDelayTicks > 0) {
      this.startDelayTicks--;
      return;
    }
    this.elapsedTicks++;

    if (!this.frameIterator) {
      if (this.currentFrame >= this.frameCount) {
        this.stopPlayback();
        notify("playback_finished", this.anchor.x, this.anchor.y, this.anchor.z);
        return;
      }
      // 動画の fps に合わせて、表示時刻が来るまで次のフレームを始めない
      if (this.currentFrame * this.ticksPerFrame >= this.elapsedTicks) {
        this.startViewRedraw();
        return;
      }
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
    this.viewIterator = null;
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
    if (!this.videoData || this.currentFrame >= this.frameCount) {
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
    return !this.running && this.currentFrame > 0 && this.videoData && this.currentFrame < this.frameCount;
  }

  stopAndClear() {
    this.stopPlayback();
    this.frameIterator = null;
    this.currentFrame = 0;
    this.invalidateBoard();
    this.viewDrawn = false;
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
      // だまし絵の舞台は空気にして片付ける (盤面の値は壁・床・人物の色なので、不明にする)
      const clearedLevel = isAnamorphic(self.videoData) ? -1
        : volume ? self.videoData.air_level : self.videoData.adaptive_palette ? -1 : 0;
      let operations = 0;
      const stage = isAnamorphic(self.videoData) ? stageOf(self.videoData) : null;
      for (let p = 0; p < cells; p++) {
        // だまし絵の盤面の、区画の余りのマスはワールドに無い
        if (stage && isStagePadding(stage, p)) continue;
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
    targetFrame = Math.max(0, Math.min(targetFrame, this.frameCount - 1));

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
  // スクリーンの範囲 (向きに合わせて回した後) の北西の角から、範囲に収まる正方形を使う (壁に立てたスクリーンは 1 マス)
  const box = worldBoxOf(vp.videoData, vp.anchor);
  const side = Math.min(64, box.x1 - box.x0 + 1, box.z1 - box.z0 + 1);
  const base = { x: box.x0, y: vp.anchor.y, z: box.z0 };
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

  const box = worldBoxOf(videoData, anchor);
  const chunk0X = Math.floor(box.x0 / 16);
  const chunk0Z = Math.floor(box.z0 / 16);
  const chunk1X = Math.floor(box.x1 / 16);
  const chunk1Z = Math.floor(box.z1 / 16);
  const step = TICKING_AREA_CHUNKS_PER_SIDE;
  const areas = [];
  for (let cz = chunk0Z; cz <= chunk1Z; cz += step) {
    for (let cx = chunk0X; cx <= chunk1X; cx += step) {
      const fromX = Math.max(box.x0, cx * 16);
      const fromZ = Math.max(box.z0, cz * 16);
      const toX = Math.min(box.x1, (cx + step) * 16 - 1);
      const toZ = Math.min(box.z1, (cz + step) * 16 - 1);
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
 * プレイヤーの足元 origin から、動画を置く位置 (盤面の 0 番のマスの座標) を決める。layout は setup で決めた向き。
 * 平面は足元から前と右へ広がる (北向きなら北東)。立体と壁に立てた平面はプレイヤーの 2 ブロック前に、左右中央を
 * そろえて立てる。どれも建築できる高さに収める。返す anchor は向き (facing・wall) も持つ。
 */
function anchorForVideo(videoData, origin, dimension, layout = DEFAULT_LAYOUT) {
  const { sizeX, sizeZ } = footprintOf(videoData, layout);
  // 盤面のローカル座標での anchor の、足元からのずれ (北向きのときのワールドのずれと同じ)
  let offsetX;
  let offsetZ;
  let sizeY;
  if (isAnamorphic(videoData)) {
    // だまし絵の舞台は、プレイヤーが床の手前から人物の高さの半分ほど離れて見る位置に、左右中央をそろえて置く
    offsetX = -Math.floor(sizeX / 2);
    offsetZ = -sizeZ - Math.ceil(videoData.height / 2);
    sizeY = stageOf(videoData).wallHeight + 1;
  } else if (isVolume(videoData) || layout.wall) {
    offsetX = -Math.floor(videoData.width / 2);
    offsetZ = -sizeZ - 2;
    sizeY = videoData.height;
  } else {
    offsetX = 0;
    offsetZ = -sizeZ;
    sizeY = 1;
  }
  // 足元のマスはローカル座標で (-offsetX, -offsetZ)。向きに合わせて回したとき、そのマスが足元に来るよう anchor を決める
  const feet = rotateCell(layout.facing, -offsetX, -offsetZ, { x: 0, z: 0 });
  const anchor = { x: origin.x - feet.x, y: origin.y, z: origin.z - feet.z, facing: layout.facing, wall: layout.wall };
  const fittedY = fitToBuildHeight(dimension, anchor.y, sizeY);
  const shifted = fittedY !== anchor.y;
  anchor.y = fittedY;
  return { anchor, shifted };
}

/** setup で保存した向き。無い・壊れていれば北向きの床置き (以前の版と同じ)。 */
function storedLayout() {
  try {
    const value = JSON.parse(world.getDynamicProperty(LAYOUT_KEY) ?? "null");
    if (value && FACING_NAMES[value.facing] !== undefined) return { facing: value.facing, wall: !!value.wall };
  } catch (e) { /* 壊れた値は無視する */ }
  return DEFAULT_LAYOUT;
}

/** プレイヤーが見ている方向を 4 方向 (0 北・1 東・2 南・3 西) にまるめる。 */
function facingOf(player) {
  let view;
  try {
    view = player.getViewDirection();
  } catch (e) {
    return 0;
  }
  if (Math.abs(view.x) > Math.abs(view.z)) return view.x > 0 ? 1 : 3;
  return view.z > 0 ? 2 : 0;
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
function alignOriginToChunks(videoData, origin, dimension, layout = DEFAULT_LAYOUT) {
  const { anchor } = anchorForVideo(videoData, origin, dimension, layout);
  const box = worldBoxOf(videoData, anchor);
  const sizeX = box.x1 - box.x0 + 1;
  const sizeZ = box.z1 - box.z0 + 1;
  const dx = chunkAlignShift(box.x0, sizeX);
  const dz = chunkAlignShift(box.z0, sizeZ);
  return {
    origin: { x: origin.x + dx, y: origin.y, z: origin.z + dz },
    dx,
    dz,
    before: chunksSpanned(box.x0, sizeX) * chunksSpanned(box.z0, sizeZ),
    after: chunksSpanned(box.x0 + dx, sizeX) * chunksSpanned(box.z0 + dz, sizeZ),
  };
}

/**
 * setup のオプション: align (チャンクにそろえる)、face (見ている方向に向けて置く)、wall (平面の動画を壁のように
 * 立てて、見ている方向に向けて置く)。face も wall も無ければ以前の版と同じ北向き。
 */
function setup(player, options = "") {
  if (!globalSelectedVideoId) {
    notify("no_video_selected");
    return;
  }
  const videoData = VIDEOS[globalSelectedVideoId];
  const words = options.toLowerCase().split(/\s+/);
  const wall = words.includes("wall");
  const layout = { facing: wall || words.includes("face") ? facingOf(player) : 0, wall };
  world.setDynamicProperty(LAYOUT_KEY, JSON.stringify(layout));
  if (layout.facing !== 0 || wall) notify(wall ? "setup_wall" : "setup_facing", tr(`facing_${FACING_NAMES[layout.facing]}`));
  const loc = player.location;
  let origin = { x: Math.floor(loc.x), y: Math.floor(loc.y), z: Math.floor(loc.z) };
  if (words.includes("align")) {
    const aligned = alignOriginToChunks(videoData, origin, player.dimension, layout);
    origin = aligned.origin;
    notify("setup_aligned", aligned.dx, aligned.dz, aligned.before, aligned.after);
  }
  // 建築できる高さ (オーバーワールドは Y=-64〜319) からはみ出すと置けないので、収まる高さへずらす
  const { anchor, shifted } = anchorForVideo(videoData, origin, player.dimension, layout);
  if (shifted) notify("setup_shifted", anchor.y);
  world.setDynamicProperty(ORIGIN_KEY, origin);

  // 常時読み込みが一部しか登録できなくても、プレイヤーが近くにいれば再生できるので設置は続ける
  const ticking = ensureTickingArea(player.dimension, anchor, videoData);
  if (ticking.added < ticking.needed) notify("tickingarea_partial", ticking.added, ticking.needed);
  
  const keyStr = getAnchorKeyStr(anchor);
  const vp = new VideoPlayer(anchor, player.dimension, globalSelectedVideoId);
  activePlayers.set(keyStr, vp);
  // 保存するのは位置 (x・y・z) だけ。向きは LAYOUT_KEY に別に保存してある
  world.setDynamicProperty(ANCHOR_KEY, { x: anchor.x, y: anchor.y, z: anchor.z }); // For single remote control backwards compatibility
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
      const fitted = anchorForVideo(videoData, origin, playbackDimension, storedLayout());
      anchor = fitted.anchor;
      if (fitted.shifted) notify("setup_shifted", anchor.y);
      const ticking = ensureTickingArea(playbackDimension, anchor, videoData);
      if (ticking.added < ticking.needed) notify("tickingarea_partial", ticking.added, ticking.needed);
      activePlayers.delete(keyStr);
      world.setDynamicProperty(ANCHOR_KEY, { x: anchor.x, y: anchor.y, z: anchor.z });
    }
    vp = new VideoPlayer(anchor, playbackDimension, globalSelectedVideoId);
    activePlayers.set(getAnchorKeyStr(anchor), vp);
    startMainLoop();
  }

  vp.restart();
  notify("starting");
  // 音声が入っていない動画なら、音が出ない理由が分かるよう知らせる (古い版で作ったアドオンには数が無いので何も出さない)
  const audioEntry = VIDEO_LIST.find((v) => v.id === vp.audioVideoId);
  if (audioEntry?.audio_chunks === 0) notify("no_audio");
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
  const totalSec = videoData ? Math.floor(frameToSeconds(videoData, vp && vp.videoId === currentVideoId ? vp.frameCount : videoData.frame_count)) : 0;

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
  if (LISTED_VIDEOS.length === 0) return;
  const vp = getActivePlayer();
  const cvId = vp ? vp.videoId : globalSelectedVideoId;
  let currentIdx = LISTED_VIDEOS.findIndex((v) => v.id === cvId);
  if (currentIdx === -1) currentIdx = 0;
  let newIdx = (currentIdx + direction + LISTED_VIDEOS.length) % LISTED_VIDEOS.length;
  globalSelectedVideoId = LISTED_VIDEOS[newIdx].id;
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
  const maxSec = Math.floor(frameToSeconds(vp.videoData, vp.frameCount));
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
  for (const v of LISTED_VIDEOS) {
    const rawtext = [{ text: v.title }];
    if (v.id === cvId) rawtext.push({ text: " " }, tr("selected_tag"));
    rawtext.push({ text: "\n" }, tr("library.item", v.frame_count, videoSizeLabel(v)));
    form.button({ rawtext });
  }

  form.show(player).then((response) => {
    if (response.canceled) return;
    const selectedVideo = LISTED_VIDEOS[response.selection];
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
      if (LISTED_VIDEOS.length === 0) {
        notify("no_videos");
      } else {
        notify("video_list");
        for (const v of LISTED_VIDEOS) {
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
