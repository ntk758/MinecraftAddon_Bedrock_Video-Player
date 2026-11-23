/**
 * Block & Image Video Player - Bedrock Script API 再生スクリプト
 * (v3: Object-Oriented Multi-Screen & Adaptive Palette Support)
 */

// <legacy:imports> (ブラウザ版の旧版向けの出力 (legacy_target.py) はこの範囲を 1.x の書き方に置き換える)
import {
  world, system, BlockPermutation, BlockVolume, ListBlockVolume, CommandPermissionLevel, CustomCommandParamType,
  CustomCommandStatus, ItemStack,
} from "@minecraft/server";
// </legacy:imports>
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
import {
  PLAYBACK_STATES, PlaybackScheduler, ScreenManager, boxContains, boxesIntersect, sameBox,
} from "./screen_system.js";
// <legacy:screen_imports> (旧版向けの出力では消す。スクリーンブロックは Script API 2.x で動く)
import { showScreenMenu } from "./screen_ui.js";
// </legacy:screen_imports>

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
// 選んだ動画と音量 (ワールドを開き直しても同じ動画・同じ音量で続ける)
const SELECTED_KEY = `${EVENT_NAMESPACE}:selected`;
const VOLUME_KEY = `${EVENT_NAMESPACE}:volume`;
const MESSAGE_PREFIX = `§b[${EVENT_NAMESPACE}]`;
const START_LOAD_DELAY_TICKS = 5;
// 再生を始める前に、スクリーンの範囲のチャンクが読み込まれるのを待つ最大の tick 数 (10 秒)。setup の直後は常時読み込み
// (tickingarea) の登録が効くまで時間がかかり、読み込まれていないチャンクに置けずに最初の画面に穴が開いていた
const CHUNK_WAIT_TICKS = 200;
const TICKING_AREA_NAME = "badapple_area";

const REMOTE_CONTROL_ITEM = "minecraft:compass";
// video_player_gui.py が音声を切り出す長さ (-segment_time) と一致させること
const AUDIO_CHUNK_SECONDS = 10;
// 途中で入ってきた人に、今のチャンクを頭から鳴らしてよい遅れ (秒)。これより遅いと音がずれるので、次の境目を待つ
const LATE_JOIN_AUDIO_SECONDS = 1;
// ワールドに入ったときの案内を出したプレイヤーの印 (プレイヤーごとに 1 回)
const WELCOMED_KEY = `${EVENT_NAMESPACE}:welcomed`;
// 手順書の本を渡したプレイヤーの印 (最初の setup で 1 回だけ渡す。guide でいつでももう 1 冊もらえる)
const GUIDE_GIVEN_KEY = `${EVENT_NAMESPACE}:guide_given`;
// setup の確認 (置く前に枠を見せる) で、枠を描き直す間隔 (tick) と、一度に出す粒の数の上限
const PREVIEW_PARTICLE = "minecraft:villager_happy";
const PREVIEW_REDRAW_TICKS = 10;
const PREVIEW_MAX_PARTICLES = 240;
// チャットを閉じる途中などでフォームを出せなかったとき、出し直す回数 (1 秒おき)
const PREVIEW_BUSY_RETRIES = 10;
// 天井に貼るとき、頭の上で天井を探す高さと、天井が無いときに置く高さ (足元から)
const CEILING_SCAN_BLOCKS = 24;
const CEILING_DEFAULT_GAP = 4;

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
// 常時読み込みの区画を 10x10 チャンクずつに分ける (このパックの枠に入りきらないとき、入る分だけでも登録できるように)
const TICKING_AREA_CHUNKS_PER_SIDE = 10;
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
  // くり抜いたカボチャは、雪・銅・鉄のブロックの上に置かれるとゴーレムができる (ブロックが消えて、銅は銅のチェストになる)。
  // 立てた画面・立体では上下に並ぶので、PC の実機で画面からコッパーゴーレムが出て、画面が崩れた。上面と側面の色は普通の
  // カボチャと同じ (顔は 1 面だけ) なので、普通のカボチャを置く
  "minecraft:carved_pumpkin": "minecraft:pumpkin",
};

// スクリーンブロック (screen_system.js) とのつなぎ。ファイルの最後の legacy:screens の印の範囲で入れる (旧版向けの出力では null のまま)。
//   tickSources()        スケジューラーに渡す、スクリーンブロックの描画するもの
//   overlapsScreen(dimensionId, box)  コマンドで置く画面の箱と重なるスクリーンブロックの ID (無ければ null)
//   onPlayerSpawn(player)  途中で入ってきた人に、スクリーンブロックの今の音を鳴らす
let screenHooks = null;

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
// setup を呼んだ回数。前の setup の片付けが遅れて終わったとき、古い位置に常時読み込みを付けないように使う
let setupCount = 0;

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

/** 1 人だけに知らせる (ワールドに入ったときの案内など)。 */
function notifyPlayer(player, key, ...args) {
  try {
    player.sendMessage({ rawtext: [{ text: `${MESSAGE_PREFIX}§r ` }, tr(key, ...args)] });
  } catch (e) { /* 抜けた */ }
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
const DEFAULT_LAYOUT = { facing: 0, wall: false, ceiling: false };

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
  // 拡大した平面 (スクリーンブロックの「大きさ」) は、マスの正方形の若い方の角
  if (anchor.scale > 1) return scaledCellCorner(videoData, anchor, p, loc, 0);
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
      // 天井に貼った平面は下から見上げるので、行の並びを逆にする (見上げた人の頭の後ろ側が画面の上になる。左右はそのまま)
      lz = anchor.ceiling ? height - 1 - row : row;
    }
  }
  rotateCell(anchor.facing ?? 0, lx, lz, loc);
  loc.x += anchor.x;
  loc.z += anchor.z;
  return loc;
}

/**
 * 拡大して置く平面 (anchor.scale 倍。スクリーンブロックだけが使う) で、盤面の p 番目のマスが占める scale×scale の
 * 正方形の角。far = 0 は盤面の若い方 (左・上) の角、far = 1 は反対 (右・下) の角。箱の最初のマスの far = 0 と最後のマスの
 * far = 1 を BlockVolume の角にすると、箱のマスの正方形がすべて入る (90 度ずつ回しても箱は箱のまま)。
 */
function scaledCellCorner(videoData, anchor, p, loc, far) {
  const s = anchor.scale;
  const width = videoData.width;
  const row = Math.floor(p / width);
  const lx = (p % width) * s + far * (s - 1);
  let lz;
  if (anchor.wall) {
    // 行 0 が一番上。若い角はマスの上の端、反対の角は下の端
    loc.y = anchor.y + (videoData.height - 1 - row) * s + (far ? 0 : s - 1);
    lz = 0;
  } else {
    loc.y = anchor.y;
    lz = row * s + far * (s - 1);
  }
  rotateCell(anchor.facing ?? 0, lx, lz, loc);
  loc.x += anchor.x;
  loc.z += anchor.z;
  return loc;
}

/** 拡大した平面のマス p の正方形の、すべてのブロックの位置を out に足す。 */
function scaledCellBlocks(videoData, anchor, p, out) {
  const a = scaledCellCorner(videoData, anchor, p, { x: 0, y: 0, z: 0 }, 0);
  const b = scaledCellCorner(videoData, anchor, p, { x: 0, y: 0, z: 0 }, 1);
  for (let x = Math.min(a.x, b.x); x <= Math.max(a.x, b.x); x++) {
    for (let y = Math.min(a.y, b.y); y <= Math.max(a.y, b.y); y++) {
      for (let z = Math.min(a.z, b.z); z <= Math.max(a.z, b.z); z++) out.push({ x, y, z });
    }
  }
  return out;
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
  // 拡大した平面 (スクリーンブロック) は scale 倍
  const s = layout.scale > 1 ? layout.scale : 1;
  return { sizeX: videoData.width * s, sizeZ: layout.wall ? 1 : videoData.height * s };
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
  /**
   * hooks (スクリーンブロックの画面だけ。コマンドで置く画面は null で、以前と同じ動き):
   *   volume              音量 (0〜1。無ければワールド全体の音量)
   *   onFinished()        最後まで再生した (チャットで全員に知らせる代わりに呼ぶ)
   *   message(key, ...)   この画面の知らせ (シーク・置けなかったブロックなど。全員ではなく操作した人へ)
   *   playAudio(trackId, volume) / stopAudio(trackId) / playAudioFor(player, trackId, volume)
   *                       この画面の音声だけを鳴らす・止める (ほかの画面の音を止めない)
   */
  constructor(anchor, dimension, videoId, hooks = null) {
    // 保存した anchor (x・y・z だけ) にも、setup で決めた向きを付ける
    const layout = anchor.facing === undefined ? storedLayout() : anchor;
    this.anchor = { x: anchor.x, y: anchor.y, z: anchor.z, facing: layout.facing, wall: layout.wall, ceiling: !!layout.ceiling };
    // 拡大 (スクリーンブロックの平面だけ)。1 マスを scale×scale のブロックで置く
    this.scale = anchor.scale > 1 ? anchor.scale : 1;
    if (this.scale > 1) this.anchor.scale = this.scale;
    // fillBlocks 1 回に入れる盤面のマスの数 (拡大するとマス 1 つが scale² ブロック)
    this.maxFillCells = Math.max(1, Math.floor(MAX_FILL_BLOCKS / (this.scale * this.scale)));
    this.hooks = hooks;
    this.disposed = false;
    this.dimension = dimension;
    this.videoId = videoId;
    this.videoData = VIDEOS[videoId];
    this.ticksPerFrame = ticksPerFrameOf(this.videoData);

    this.currentFrame = 0;
    this.elapsedTicks = 0;
    this.running = false;
    this.currentAudioChunk = -1;
    this.masterVolume = hooks?.volume ?? savedVolume();

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
    this.chunkWaitTicks = 0;
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
    //   viewIterator はフレームの合間に目の動きに合わせて描き直す処理。viewRedrawPending は、描きかけのフレームや
    //   描き直しを一時停止などで捨てたので、目が動いていなくても描き直す印 (drawStage で下ろす)
    this.figure = null;
    this.stageWork = null;
    // だまし絵の背景の映像を復元する役 (ブロックは置かない VideoPlayer)。無ければ null
    this.background = null;
    this.stagePalettes = new Map();
    this.lastEye = null;
    this.viewDrawn = false;
    this.viewIterator = null;
    this.viewRedrawPending = false;
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

  /**
   * 鳴らす音声の動画 ID。だまし絵に背景の映像があれば、その音声 (人物の .vox などには音声が無い)。
   * 同じ動画の立体版と平面版をセットで入れたときは、片方 (平面版) は一覧の audio_from の動画の音声を鳴らす
   * (音声を 2 回入れない)。audio_from の無い古いアドオンは今までどおり。
   */
  get audioVideoId() {
    const background = this.videoData && stageBackgroundOf(this.videoData);
    if (background) return background;
    const entry = VIDEO_LIST.find((v) => v.id === this.videoId);
    return entry?.audio_from ?? this.videoId;
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
      // だまし絵: 人物を壁と床へ写した回数と時間の合計・最大 (ms)、目の動きで描き直しを始めた回数。Date.now は 1 ms 単位
      //   (写すのは 1 回が数 ms なので、1 回ごとの値は粗い) なので、合計を回数で割った平均で見る
      stageProjMs: 0,
      stageProjMax: 0,
      stageProjCount: 0,
      viewRedraws: 0,
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
      stageProjAvg: st.stageProjCount > 0 ? st.stageProjMs / st.stageProjCount : 0,
      stageProjMax: st.stageProjMax,
      stageProjCount: st.stageProjCount,
      viewRedraws: st.viewRedraws,
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
      this.viewRedrawPending = false;
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
        // 片付けた画面 (スクリーンの削除・動画の切り替え) の先読みは、そこでやめる
        while (!entry.done && self.videoId === entry.videoId && !self.disposed) {
          self.stepGopDecode(gopId, entry);
          if (!entry.done) yield;
        }
      } catch (e) {
        warnOnce(`prefetch:${gopId}`, `Failed to decode GOP ${gopId}: ${e}`);
      }
    })());
  }

  /** この画面の知らせ。コマンドで置く画面は全員へ (以前と同じ)、スクリーンブロックの画面は hooks.message へ。 */
  say(key, ...args) {
    if (this.hooks) {
      this.hooks.message?.(key, ...args);
      return;
    }
    notify(key, ...args);
  }

  /**
   * 盤面のマス q に permutation を置く (拡大していなければ 1 ブロック、拡大していれば scale×scale の正方形)。
   * 置けなければ例外を投げる (呼ぶ側がマスを「不明」にする)。
   */
  placeCell(q, permutation) {
    if (this.scale === 1) {
      this.dimension.setBlockPermutation(cellToWorld(this.videoData, this.anchor, q, this.tempBlockLoc), permutation);
      return;
    }
    const a = scaledCellCorner(this.videoData, this.anchor, q, { x: 0, y: 0, z: 0 }, 0);
    const b = scaledCellCorner(this.videoData, this.anchor, q, { x: 0, y: 0, z: 0 }, 1);
    if (this.useFill !== false && typeof this.dimension.fillBlocks === "function") {
      try {
        this.dimension.fillBlocks(new BlockVolume(a, b), permutation);
        return;
      } catch (e) { /* 一部が読み込まれていないなど。1 ブロックずつ置き直す */ }
    }
    let lastError = null;
    for (const loc of scaledCellBlocks(this.videoData, this.anchor, q, [])) {
      try {
        this.dimension.setBlockPermutation(loc, permutation);
      } catch (e) {
        lastError = e;
      }
    }
    if (lastError) throw lastError;
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
   * 古い GOP のパレット番号 → 新しい GOP で同じブロックの番号 (無ければ -1)。GOP の組ごとに作り置く。
   * 盤面の番号を付け替えれば、ブロックが本当に変わるマスだけを置き直せる (同じブロックは置かない)。
   */
  levelRemap(fromGop, toGop) {
    const key = `${fromGop}>${toGop}`;
    this.levelRemaps ??= new Map();
    let remap = this.levelRemaps.get(key);
    if (remap) return remap;
    const blockKey = (spec) => `${BLOCK_ID_ALIASES[spec.block] ?? spec.block}|${JSON.stringify(spec.states ?? {})}`;
    const target = new Map();
    this.videoData.level_blocks[toGop].forEach((spec, level) => {
      const k = blockKey(spec);
      if (!target.has(k)) target.set(k, level);
    });
    remap = Int32Array.from(this.videoData.level_blocks[fromGop], (spec) => target.get(blockKey(spec)) ?? -1);
    this.levelRemaps.set(key, remap);
    return remap;
  }

  /**
   * パレット番号の意味が変わるとき (自動パレットで GOP が切り替わるとき) に呼ぶ。
   * 平面と立体は、盤面の番号を新しい GOP の同じブロックの番号に付け替え、同じブロックが無いマスだけ置き直す
   * (立体の空気は空気のまま)。fromGop が分からないときは平面は全マスを置き直す。
   * それ以外のキーフレームでは、表示中の盤面と比べて本当に変わったマスだけを置く。
   */
  invalidateForPaletteChange(fromGop = -1, toGop = -1) {
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
    const levels = this.videoData.level_blocks;
    const remap = fromGop >= 0 && toGop >= 0 && levels[fromGop] && levels[toGop] ? this.levelRemap(fromGop, toGop) : null;
    const air = isVolume(this.videoData) ? this.videoData.air_level : null;
    if (!remap && air === null) {
      this.board.fill(-1);
      return;
    }
    for (let p = 0; p < this.board.length; p++) {
      const level = this.board[p];
      if (level === air) continue;
      this.board[p] = remap && level >= 0 && level < remap.length ? remap[level] : -1;
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
    // だまし絵は次のフレームで舞台全体を見直す (置けなかったマスは盤面で「不明」にしてあるが、見る範囲を人物の
    //   周りに絞っていたので、置き直されずに穴が残っていた)
    this.placeFailedRecently = true;
    warnOnce("setBlock", `Failed to place ${count} block(s): ${error}`);
    if (this.playStats) this.playStats.placeFailures = (this.playStats.placeFailures ?? 0) + count;
    if (this.placeFailureNotified) return;
    this.placeFailureNotified = true;
    const reason = String(error?.message ?? error ?? "").replace(/\s+/g, " ").slice(0, 120);
    this.say("place_failed", String(count), reason);
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
          // 拡大していれば、最後のマスの正方形の反対の角まで
          if (this.scale > 1) scaledCellCorner(video, this.anchor, lastRow * width + x1, to, 1);
          else cellToWorld(video, this.anchor, lastRow * width + x1, to);
          try {
            this.dimension.fillBlocks(new BlockVolume(from, to), permutation);
          } catch (e) {
            lastError = e;
            warnOnce("fill", `fillBlocks failed: ${e}`);
            // 箱の一部が読み込まれていないチャンク (大きな画面の遠い側など) にかかると、箱の全体が失敗する。1 マスずつ
            //   置き直し、置けないマスだけを「不明」にする (以前は箱ごと置かれず、毎フレーム同じ箱で失敗していた)
            failures += this.placeBoxCellsOneByOne(board, width, contiguousRows, x, x1, r, r1, slices, permutation, level);
          }
          operations += area * this.scale * this.scale;
        } else {
          // 1 個ずつ置く (途中で yield しても盤面と実際の表示がずれないよう、置くたびに盤面を更新する)
          for (let q = p; q <= rowStart + x1; q++) {
            board[q] = level;
            if (!permutation) continue;
            try {
              this.placeCell(q, permutation);
            } catch (e) {
              failures++;
              lastError = e;
              board[q] = -1;
            }
            if ((operations += this.scale * this.scale) >= BLOCKS_PER_YIELD) {
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
    // fillBlocks 1 回のブロック数の上限を、盤面のマスの数にしたもの (拡大していなければ MAX_FILL_BLOCKS のまま)
    const maxCells = this.maxFillCells;
    let x1 = x;
    while (x1 + 1 < width && free(p + x1 + 1 - x)) x1++;
    if (useRects && x1 - x + 1 > maxCells) x1 = x + maxCells - 1;
    let r1 = r;
    if (useRects) {
      while (r1 + 1 < rows && (r1 + 1) % contiguousRows !== 0 && !(splits && splits.includes(r1 + 1))
        && (r1 + 2 - r) * (x1 - x + 1) <= maxCells) {
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
      while (r + slices * contiguousRows < rows && (slices + 1) * faceArea <= maxCells) {
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
    // 拡大していれば、マス 1 つが s2 ブロック (1 マスだけの所も、正方形を fillBlocks 1 回で置く)
    const s2 = this.scale * this.scale;
    const single = s2 > 1 ? cost.fillCall + cost.fillBlock * s2 : cost.setBlock;
    const estimates = {
      rect: cost.fillCall * boxes + cost.fillBlock * boxBlocks * s2 + single * singles,
      list: cost.listCall * (levels.size + Math.floor((changed * s2) / LIST_BATCH)) + cost.listBlock * changed * s2,
      block: single * changed,
    };
    let best = "rect";
    for (const mode of ["list", "block"]) if (estimates[mode] < estimates[best]) best = mode;
    this.lastRenderChoice = best;
    return best;
  }

  /** 盤面上の箱 (x0..x1 列, r0..r1 行, 奥へ slices 段) を value にする。 */
  /**
   * fillBoxCells と同じマスに 1 個ずつ置く。読み込まれていないチャンクのマスは試さずに「不明」(-1) にする
   * (isChunkLoaded があるときだけ。チャンクごとに 1 回調べる)。置けなかったマスの数を返す。
   */
  placeBoxCellsOneByOne(board, width, sliceRows, x0, x1, r0, r1, slices, permutation, level) {
    const loadedChunks = new Map();
    const canCheck = typeof this.dimension.isChunkLoaded === "function";
    let failures = 0;
    for (let s = 0; s < slices; s++) {
      for (let rr = r0; rr <= r1; rr++) {
        const base = (rr + s * sliceRows) * width;
        for (let q = base + x0; q <= base + x1; q++) {
          const loc = cellToWorld(this.videoData, this.anchor, q, this.tempBlockLoc);
          if (canCheck) {
            const key = `${Math.floor(loc.x / 16)},${Math.floor(loc.z / 16)}`;
            let loaded = loadedChunks.get(key);
            if (loaded === undefined) {
              try {
                loaded = this.dimension.isChunkLoaded(loc);
              } catch (e) {
                loaded = false;
              }
              loadedChunks.set(key, loaded);
            }
            if (!loaded) {
              board[q] = -1;
              failures++;
              continue;
            }
          }
          try {
            if (this.scale === 1) this.dimension.setBlockPermutation(loc, permutation);
            else this.placeCell(q, permutation);
            board[q] = level;
          } catch (e) {
            board[q] = -1;
            failures++;
          }
        }
      }
    }
    return failures;
  }

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
          // まとまりの一部 (読み込まれていないチャンク・世界の外) で全体が失敗するので、1 マスずつ置き直す (以前はまとまり
          //   全部が置かれず、毎フレーム同じ失敗を繰り返していた)
          warnOnce("fill-list", `fillBlocks(ListBlockVolume) failed: ${e}`);
          for (let i = 0; i < group.cells.length; i++) {
            try {
              if (self.scale === 1) self.dimension.setBlockPermutation(group.locations[i], permutation);
              else self.placeCell(group.cells[i], permutation);
              board[group.cells[i]] = level;
            } catch (e2) {
              failures++;
              lastError = e2;
              board[group.cells[i]] = -1;
            }
          }
          placedLevel = null;
        }
      }
      if (placedLevel !== null) for (const cell of group.cells) board[cell] = placedLevel;
      // 色ごとではなく、置いたブロックが BLOCKS_PER_YIELD 個たまるごとに区切る。1 tick に区切れる回数には上限
      // (MAX_YIELDS_PER_TICK) があるので、色ごとに区切ると色の多いフレーム (自動パレットで 40〜60 色) が
      // 何 tick にもまたがり、映像が遅れて音声が途切れていた (実機の録画: 128×128・10 fps で実効約 3 fps)
      placedSinceYield += group.locations.length;
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
      // 拡大していれば、マスの正方形のブロックをすべて入れる (まとまりの大きさはブロックの数で数える)
      if (this.scale > 1) scaledCellBlocks(video, this.anchor, p, group.locations);
      else group.locations.push(cellToWorld(video, this.anchor, p, { x: 0, y: 0, z: 0 }));
      if (group.locations.length >= LIST_BATCH) yield* flush(level, group);
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
      if (!synced.changed && !backgroundSynced?.changed && !this.forceRedraw) return;
      this.forceRedraw = false;

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
      if (this.videoData.adaptive_palette && entry.gopId !== this.boardGopId) {
        this.invalidateForPaletteChange(this.boardGopId ?? -1, entry.gopId);
      }
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
    const projectStart = Date.now();
    projectToStage(this.figure, this.videoData, geom, eye, this.nextBoard, this.stageWork, background?.decoded ?? null,
      background ? background.decodedFrame : 0);
    const st = this.playStats;
    // 統計の時計が動いている間 (再生中) の投影だけ数える。止めた後の仕上げや bench の間の描き直しは、フレーム数・時間と合わない
    if (st && st.wallStart !== null && st.wallEnd === null) {
      const projectMs = Date.now() - projectStart;
      st.stageProjMs += projectMs;
      st.stageProjMax = Math.max(st.stageProjMax, projectMs);
      st.stageProjCount++;
    }
    const work = this.stageWork;
    if (work.rowMin <= work.rowMax) this.extendStageRows(work.rowMin, work.rowMax + 1, work.colMin, work.colMax + 1);
    this.lastEye = eye;
    this.viewDrawn = true;
    this.viewRedrawPending = false;
  }

  /** だまし絵: 描いた盤面を置き終えたら、次に探す範囲を今の人物の所だけにする。 */
  settleStageRows() {
    const work = this.stageWork;
    if (this.placeFailedRecently) {
      this.placeFailedRecently = false;
      this.stageRows = { lo: 0, hi: Infinity, left: 0, right: Infinity };
      return;
    }
    this.stageRows = work.rowMin <= work.rowMax
      ? { lo: work.rowMin, hi: work.rowMax + 1, left: work.colMin, right: work.colMax + 1 }
      : { lo: 0, hi: 0, left: 0, right: 0 };
  }

  /**
   * だまし絵: 見ている人の目が動いていれば、フレームの合間に描き直しを始める。一時停止などで描きかけのフレームや
   * 描き直しを捨てた後 (viewRedrawPending) は、目が動いていなくても、残りを最後まで描く。そのとき箱の中に人がいなければ、
   * drawStage が最後に描いた目 (lastEye) を使う。
   */
  maybeStartViewRedraw() {
    if (!this.figure || !this.viewDrawn || this.viewIterator || this.frameIterator || this.currentJobId !== null) return;
    const eye = this.viewerEye();
    const last = this.lastEye;
    const moved = eye && (!last || Math.hypot(eye.x - last.x, eye.y - last.y, eye.z - last.z) >= VIEW_REDRAW_DISTANCE);
    if (!moved && !this.viewRedrawPending) return;
    // 目の動きでの描き直しだけ数える (一時停止などで捨てたフレームの仕上げ (viewRedrawPending) は数えない)
    if (moved && this.playStats) this.playStats.viewRedraws++;
    const self = this;
    this.viewIterator = (function* () {
      self.drawStage();
      yield* self.placeChangedBlocks(boardWidthOf(self.videoData));
      self.settleStageRows();
    })();
  }

  /** 目が動いていれば描き直しを始め、この tick のうちに進める (次の tick に回すと、次のフレームが遅れる)。 */
  startViewRedraw(budget = null) {
    this.maybeStartViewRedraw();
    if (this.viewIterator && this.advanceJob(this.viewIterator, budget)) this.viewIterator = null;
  }

  /**
   * ジェネレータを 1 tick の予算 (ブロック数と時間) まで進める。終われば true。budget はスケジューラーが分けた予算
   * ({ timeMs, maxYields })。無ければ 1 tick 分の全部 (以前と同じ)。
   */
  advanceJob(iterator, budget = null) {
    const maxYields = budget?.maxYields ?? MAX_YIELDS_PER_TICK;
    const timeBudget = budget?.timeMs ?? TICK_TIME_BUDGET_MS;
    const tickStart = Date.now();
    let yields = 0;
    while (yields < maxYields) {
      const { done, value } = iterator.next();
      if (done) return true;
      if (value !== DECODE_YIELD) yields++;
      if (Date.now() - tickStart > timeBudget) break;
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
        const keyframe = findKeyframeAtOrBefore(this.decodedIndex, frameIndex);
        // 同じ GOP の少し前まで復元してあれば、キーフレームに戻らずにそこから進める (遅れてコマを飛ばすとき)
        const start = this.decodedFrame >= keyframe && this.decodedFrame < frameIndex ? this.decodedFrame + 1 : keyframe;
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
      // だまし絵の人物 (figure) は、立体の復元の作業用の配列でもある。applyFrameJob の外 (シークの途中で中断した後など)
      //   で書き換わっても、次の描き直しが古い外から見える面の一覧を使わないようにする
      if (this.stageWork && this.decodeScratch === this.figure) this.stageWork.dirty = true;
    }
    this.decodedFrame = frameIndex;
    return true;
  }

  /**
   * 途中で入ってきた人に、今の音声のチャンクを鳴らす。途中から鳴らす API が無いので、チャンクが始まって
   * LATE_JOIN_AUDIO_SECONDS 以内のときだけ (それより後は、次のチャンクの境目から全員と一緒に鳴る)。
   */
  playCurrentChunkFor(player) {
    if (!this.running || !this.videoData || this.masterVolume <= 0 || this.currentAudioChunk < 0) return;
    const seconds = frameToSeconds(this.videoData, this.currentFrame);
    if (seconds - this.currentAudioChunk * AUDIO_CHUNK_SECONDS > LATE_JOIN_AUDIO_SECONDS) return;
    const trackId = this.trackIdOf(this.currentAudioChunk);
    if (this.hooks?.playAudioFor) {
      this.hooks.playAudioFor(player, trackId, this.masterVolume);
      return;
    }
    try {
      player.playSound(trackId, { location: player.location, volume: this.masterVolume, pitch: 1.0 });
    } catch (e) { /* 鳴らせない */ }
  }

  /** 音声のチャンクの音の ID。 */
  trackIdOf(chunk) {
    return `${EVENT_NAMESPACE}.${this.audioVideoId}.chunk_${chunk}`;
  }

  syncAudioForFrame(frameIndex) {
    if (!this.videoData || this.masterVolume <= 0) return;
    const targetChunk = Math.floor(frameToSeconds(this.videoData, frameIndex) / AUDIO_CHUNK_SECONDS);
    if (targetChunk !== this.currentAudioChunk) {
      const previousChunk = this.currentAudioChunk;
      this.currentAudioChunk = targetChunk;
      const trackId = this.trackIdOf(targetChunk);
      if (this.hooks?.playAudio) {
        // スクリーンブロックの画面: この画面の前のチャンクだけを止めて鳴らす (ほかの画面の音には触らない)
        if (previousChunk >= 0) this.hooks.stopAudio?.(this.trackIdOf(previousChunk));
        this.hooks.playAudio(trackId, this.masterVolume);
        return;
      }
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

  /**
   * 1 tick 分を進める。budget はスケジューラーが分けた予算 ({ timeMs, maxYields })。無ければ 1 tick 分の全部
   * (スクリーンが 1 つなら、スケジューラーも全部を渡す)。
   */
  tick(budget = null) {
    const maxYields = budget?.maxYields ?? MAX_YIELDS_PER_TICK;
    const timeBudget = budget?.timeMs ?? TICK_TIME_BUDGET_MS;
    // だまし絵の描き直しの途中なら、それを先に進める (再生中は時計も進める)
    if (this.viewIterator) {
      if (this.running && this.startDelayTicks === 0 && this.chunkWaitTicks === 0) this.elapsedTicks++;
      if (this.advanceJob(this.viewIterator, budget)) this.viewIterator = null;
      return;
    }
    if (!this.running) {
      this.startViewRedraw(budget);
      return;
    }

    if (this.startDelayTicks > 0) {
      this.startDelayTicks--;
      return;
    }
    if (this.chunkWaitTicks > 0) {
      // 読み込まれるまで時計を進めない (待った分だけ映像と音声が遅れないように)
      if (!this.screenLoaded()) {
        this.chunkWaitTicks--;
        return;
      }
      this.chunkWaitTicks = 0;
    }
    this.elapsedTicks++;

    if (!this.frameIterator) {
      if (this.currentFrame >= this.frameCount) {
        this.stopPlayback();
        // スクリーンブロックの画面は、ループするか・止めるかを ScreenManager が決める
        if (this.hooks?.onFinished) this.hooks.onFinished();
        else notify("playback_finished", this.anchor.x, this.anchor.y, this.anchor.z);
        return;
      }
      // 動画の fps に合わせて、表示時刻が来るまで次のフレームを始めない
      if (this.currentFrame * this.ticksPerFrame >= this.elapsedTicks) {
        this.startViewRedraw(budget);
        return;
      }
      // 描くのが追いつかず 2 コマ以上遅れたら、途中のコマは置かずに今のコマへ飛ぶ (盤面との差で置くので画面は正しい。
      //   以前は全部のコマを置いて映像が遅れ続け、音声のチャンクの間が空いていた)。復元は decodeTo が順に行う
      const scheduledFrame = this.currentFrame;
      const dueFrame = Math.min(this.frameCount - 1, Math.floor((this.elapsedTicks - 1) / this.ticksPerFrame));
      if (dueFrame > this.currentFrame + 1 && !this.figure) {
        if (this.playStats) this.playStats.droppedFrames = (this.playStats.droppedFrames ?? 0) + dueFrame - this.currentFrame;
        this.currentFrame = dueFrame;
      }
      this.frameIterator = this.applyFrameJob(this.currentFrame);
      const st = this.playStats;
      if (st) {
        if (st.wallStart === null) {
          st.wallStart = Date.now();
          st.tickStart = this.elapsedTicks - 1;
        }
        // 予定の tick (表示時刻が来た最初の tick) から何 tick 遅れて描き始めたか
        // 飛ばしたときは、飛ばす前のコマの予定から数える (遅れていたことが統計に残るように)
        st.frameLate = this.elapsedTicks - (Math.floor(scheduledFrame * this.ticksPerFrame) + 1);
        st.frameMs = 0;
      }
    }

    const tickStart = Date.now();
    // ブロックを置いた後の yield は maxYields 回まで (1 tick のブロック数の上限)。GOP の展開の yield は数えない
    let yields = 0;
    while (yields < maxYields) {
      const { done, value } = this.frameIterator.next();
      if (value !== DECODE_YIELD) yields++;
      if (done) {
        this.recordFrameStats(Date.now() - tickStart);
        this.syncAudioForFrame(this.currentFrame);
        this.currentFrame++;
        this.frameIterator = null;
        return;
      }
      if (Date.now() - tickStart > timeBudget) break;
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
    // だまし絵は、止めている間も maybeStartViewRedraw が描き直せるように、描きかけのフレームを捨てる (再開すると、その
    //   フレームを最初からやり直す。復元は済んでいるので投影と残りの設置だけ増える)。描き直しの途中も、残りを描き直す。
    //   平面と普通の立体は、描きかけのフレームをそのまま残して、再開で続きから置く
    if (this.figure) {
      if (this.frameIterator !== null) {
        this.frameIterator = null;
        this.viewRedrawPending = true;
      }
      if (this.viewIterator !== null) this.viewRedrawPending = true;
    }
    this.viewIterator = null;
    const playingChunk = this.currentAudioChunk;
    this.currentAudioChunk = -1;
    this.cancelJob();
    if (this.hooks?.stopAudio) {
      // スクリーンブロックの画面: 鳴らしているこの画面のチャンクだけを止める (ほかの画面の音は止めない)
      if (playingChunk >= 0 && this.videoData) this.hooks.stopAudio(this.trackIdOf(playingChunk));
      return;
    }
    for (const p of world.getAllPlayers()) {
      try { p.stopMusic(); } catch (e) { /* 既に停止している */ }
    }
  }

  /**
   * 使い終わった (スクリーンの削除・動画の切り替え) ときに呼ぶ。再生と片付けのジョブを止め、先読みもやめさせる。
   * 置いたブロックには触らない (片付けは呼ぶ側が、作ったブロックだけを選んで行う)。
   */
  dispose() {
    if (this.disposed) return;
    this.stopPlayback();
    this.frameIterator = null;
    this.disposed = true;
    this.background?.dispose();
    this.gopCache.clear();
    this.partialGops.clear();
  }

  /** 先頭から再生する。 */
  restart() {
    this.stopPlayback();
    this.frameIterator = null;
    this.currentFrame = 0;
    this.elapsedTicks = 0;
    this.running = true;
    this.startDelayTicks = START_LOAD_DELAY_TICKS;
    this.chunkWaitTicks = CHUNK_WAIT_TICKS;
    this.placeFailureNotified = false;
    this.resetPlayStats();
  }

  /**
   * スクリーンの範囲 (向きに合わせて回した後) のチャンクがすべて読み込まれているか。各チャンクの 1 マスを getBlock で
   * 調べる (読み込まれていなければ undefined か例外)。getBlock が無い環境では読み込まれているとみなす。
   */
  screenLoaded() {
    // Script API 2.4.0 の isChunkLoaded で調べる (無い環境では、読み込まれていないチャンクの getBlock が undefined
    // になることを使う)
    const probe = typeof this.dimension.isChunkLoaded === "function"
      ? (loc) => this.dimension.isChunkLoaded(loc)
      : typeof this.dimension.getBlock === "function" ? (loc) => Boolean(this.dimension.getBlock(loc)) : null;
    if (!probe) return true;
    const box = worldBoxOf(this.videoData, this.anchor);
    const loc = { x: 0, y: this.anchor.y, z: 0 };
    for (let cz = Math.floor(box.z0 / 16); cz <= Math.floor(box.z1 / 16); cz++) {
      for (let cx = Math.floor(box.x0 / 16); cx <= Math.floor(box.x1 / 16); cx++) {
        loc.x = Math.min(Math.max(cx * 16, box.x0), box.x1);
        loc.z = Math.min(Math.max(cz * 16, box.z0), box.z1);
        try {
          if (!probe(loc)) return false;
        } catch (e) {
          return false;
        }
      }
    }
    return true;
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

  /**
   * 止めて、スクリーンを片付ける。keepBox (ワールドの箱) を渡すと、その中のマスは触らない (動画を切り替えたとき、新しい
   * 動画の範囲を残して前の動画の分だけ消す)。toAir なら平面も空気に戻す (setup で別の場所に置き直したとき。
   * ふだんの停止は、画面が消えたと分かるよう平面をパレットの先頭のブロックで塗る)。
   */
  stopAndClear(keepBox = null, announce = true, toAir = false, onDone = null) {
    // onDone: 片付けが終わったら (片付ける物が無ければすぐ) 呼ぶ。途中で止められたら呼ばない
    const done = () => {
      if (onDone) onDone();
    };
    // 切り替えのときは、前の動画が一度も描かれていなければ片付ける物が無い (setup の直後に別の動画を選んだときなど)
    if (keepBox && !(this.board && this.board.some((level) => level >= 0))) {
      this.stopPlayback();
      done();
      return;
    }
    this.stopPlayback();
    this.frameIterator = null;
    this.currentFrame = 0;
    this.forceRedraw = true;
    this.invalidateBoard();
    this.viewDrawn = false;
    this.viewRedrawPending = false;
    if (!this.videoData) {
      done();
      return;
    }

    this.initPaletteCache();
    // 平面はパレットの先頭のブロックで塗りつぶし、立体は空気にする
    const volume = isVolume(this.videoData);
    const clearPermutation = volume || toAir
      ? this.airPermutation
      : this.videoData.adaptive_palette ? this.paletteCache?.[0]?.[0] : this.paletteCache?.[0];
    if (!clearPermutation) {
      done();
      return;
    }

    // 大きなスクリーンを 1 tick で塗るとウォッチドッグに止められるため runJob で分割する
    const self = this;
    const cells = boardSizeOf(this.videoData);
    this.currentJobId = system.runJob((function* () {
      const loc = { x: 0, y: 0, z: 0 };
      // 塗った内容が分かっているマスは盤面に記録する (次の再生で置き直すマスが最小になる)。
      // 立体は空気、固定パレットの平面はパレット 0 番。自動パレットの平面は GOP ごとに番号の意味が違うので不明のまま。
      // 置けなかったマスは不明 (-1) のままにして、次の再生で必ず置き直す
      // だまし絵の舞台は空気にして片付ける (盤面の値は壁・床・人物の色なので、不明にする)
      const clearedLevel = isAnamorphic(self.videoData) || (toAir && !volume) ? -1
        : volume ? self.videoData.air_level : self.videoData.adaptive_palette ? -1 : 0;
      let operations = 0;
      const stage = isAnamorphic(self.videoData) ? stageOf(self.videoData) : null;
      for (let p = 0; p < cells; p++) {
        // だまし絵の盤面の、区画の余りのマスはワールドに無い
        if (stage && isStagePadding(stage, p)) continue;
        cellToWorld(self.videoData, self.anchor, p, loc);
        if (keepBox && loc.x >= keepBox.x0 && loc.x <= keepBox.x1 && loc.y >= keepBox.y0 && loc.y <= keepBox.y1 &&
            loc.z >= keepBox.z0 && loc.z <= keepBox.z1) continue;
        let placed = true;
        try {
          if (self.scale === 1) self.dimension.setBlockPermutation(loc, clearPermutation);
          else self.placeCell(p, clearPermutation);
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
      if (announce) notify("clear_done");
      done();
    })());
  }

  seekToFrame(targetFrame, onDone = null) {
    if (!this.videoData || !this.ensureDecodedData()) return;
    targetFrame = Math.max(0, Math.min(targetFrame, this.frameCount - 1));

    // 再生中のフレーム描画とシーク描画が混ざらないよう一旦止める
    this.stopPlayback();
    this.frameIterator = null;
    this.currentFrame = targetFrame;
    // 描きかけのフレームを捨てたり、止めて片付けた後でも、目的のフレームを必ず盤面との差で置く (以前は省略フレームに
    //   シークすると何も置かず、画面が前のままだった)
    this.forceRedraw = true;

    let startFrame;
    if (this.decodedIndex) {
      startFrame = findKeyframeAtOrBefore(this.decodedIndex, targetFrame);
    } else {
      const gop = this.videoData.keyframe_interval || 30;
      startFrame = Math.floor(targetFrame / gop) * gop;
    }

    this.say("seeking", targetFrame);

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
      self.say("seek_done");
      if (onDone) onDone();
    })());
  }
}

/**
 * 毎 tick の描画 (screen_system.js の PlaybackScheduler)。1 tick の時間とブロック数の予算を、コマンドで置いた画面と
 * スクリーンブロックの画面で分ける。画面が 1 つなら予算は全部 (以前と同じ)。runInterval は 1 つだけで、描くものが
 * 無くなったら止める。ある画面の描画が例外を投げても、ほかの画面は描き続ける。
 */
const scheduler = new PlaybackScheduler({
  runInterval: (callback, ticks) => system.runInterval(callback, ticks),
  clearRun: (id) => system.clearRun(id),
  sources: tickSources,
  budgetMs: TICK_TIME_BUDGET_MS,
  maxYields: MAX_YIELDS_PER_TICK,
  onError: (source, e) => warnOnce(`tick:${source.key}`, `Screen ${source.key} failed to draw: ${e}`),
});

/** スケジューラーが毎 tick 描くもの。コマンドで置いた画面は、止めている間も毎 tick 呼ぶ (だまし絵の描き直しのため。以前と同じ)。 */
function tickSources() {
  const sources = [];
  for (const [key, vp] of activePlayers) sources.push({ key: `cmd:${key}`, wantsTick: () => true, tick: (budget) => vp.tick(budget) });
  if (screenHooks) sources.push(...screenHooks.tickSources());
  return sources;
}

function startMainLoop() {
  scheduler.ensureRunning();
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
  // 計測の間は、一時停止で捨てたフレームの仕上げなどの描き直しを始めさせない (currentJobId が空でないと始まらない)
  vp.currentJobId = system.runJob((function* () {
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
    vp.currentJobId = null;
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

// <legacy:ticking>
/**
 * 以前の版 (v6.31.0 まで) が tickingarea コマンドで登録した区画を外す。今の版は TickingAreaManager (このパック専用の
 * 枠で、コマンドの区画とは別) を使うので、古い区画が残るとワールド全体の 10 区画の上限を使い続けてしまう。
 */
function removeLegacyTickingAreas(dimension, anchor) {
  let names = [];
  try {
    names = JSON.parse(world.getDynamicProperty(TICKING_NAMES_KEY) ?? "[]");
  } catch (e) { /* 壊れた値は無視する */ }
  if (!Array.isArray(names) || names.length === 0) return;
  for (const name of names) {
    try {
      dimension.runCommand(`tickingarea remove ${name}`);
    } catch (e) { /* 既に無い */ }
  }
  world.setDynamicProperty(TICKING_NAMES_KEY, "[]");
}

/**
 * スクリーンの範囲が読み込まれ続けるよう、TickingAreaManager (Script API 2.6.0) で常時読み込みの区画を登録する。
 * 10x10 チャンクずつに分け、このパックの枠 (maxChunkCount) に入る分だけ登録する (hasCapacity で確かめる)。
 * 区画は一時的なもので、ワールドを開き直すと消えることがあるので、再生を始めるときにも無ければ登録し直す。
 * 戻り値: { added, needed }  needed > added なら一部しか常時読み込みされない (プレイヤーが近くにいれば再生はできる)
 */
function ensureTickingArea(dimension, anchor, videoData) {
  if (!videoData) return { added: 0, needed: 0 };
  removeLegacyTickingAreas(dimension, anchor);
  // 前の setup (別の位置・別の大きさ) の区画。スクリーンブロックの区画 (名前が違う) は外さない
  removeTickingAreasNamed(`${TICKING_AREA_NAME}_`);
  return addTickingAreas(dimension, worldBoxOf(videoData, anchor), anchor.y, anchor.y, TICKING_AREA_NAME);
}

/** このパックの常時読み込みの区画のうち、名前が prefix で始まるものを外す。 */
function removeTickingAreasNamed(prefix) {
  const manager = world.tickingAreaManager;
  try {
    for (const area of manager.getAllTickingAreas()) {
      if (String(area.identifier).startsWith(prefix)) manager.removeTickingArea(area.identifier);
    }
  } catch (e) { /* 何も無い */ }
}

/** 名前が prefix で始まる区画が 1 つでもあるか。 */
function hasTickingAreaNamed(prefix) {
  try {
    return world.tickingAreaManager.getAllTickingAreas().some((area) => String(area.identifier).startsWith(prefix));
  } catch (e) {
    return true;
  }
}

/**
 * box (x・z。y は y0〜y1) を 10x10 チャンクずつの区画 (名前は <name>_<番号>) にして登録する。このパックの枠に入る分だけ。
 * 戻り値: { added, needed }
 */
function addTickingAreas(dimension, box, y0, y1, name) {
  const manager = world.tickingAreaManager;
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
  for (const [index, [fromX, fromZ, toX, toZ]] of areas.entries()) {
    const options = { dimension, from: { x: fromX, y: y0, z: fromZ }, to: { x: toX, y: y1, z: toZ } };
    try {
      if (!manager.hasCapacity(options)) continue;
      // 戻り値の Promise は区画のチャンクがすべて読み込まれたら終わる。読み込みの完了は再生の前に isChunkLoaded で待つ
      manager.createTickingArea(`${name}_${index}`, options).catch((e) => {
        console.warn(`[${EVENT_NAMESPACE}] ticking area ${index} failed: ${e}`);
      });
      added++;
    } catch (e) {
      console.warn(`[${EVENT_NAMESPACE}] ticking area ${index} failed: ${e}`);
    }
  }
  return { added, needed: areas.length };
}

/** 再生を始めるとき、常時読み込みの区画が無ければ (ワールドを開き直して消えたなど) 登録し直す。 */
function ensureTickingAreaForPlayback(vp) {
  // スクリーンブロックの区画 (名前が違う) があっても、コマンドで置いた画面の区画が無ければ登録し直す
  if (hasTickingAreaNamed(`${TICKING_AREA_NAME}_`)) return;
  const ticking = ensureTickingArea(vp.dimension, vp.anchor, vp.videoData);
  if (ticking.added < ticking.needed) notify("tickingarea_partial", ticking.added, ticking.needed);
}
// </legacy:ticking>

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
  } else if (layout.ceiling) {
    // 天井に貼る平面は、頭の真上に前後左右の中央をそろえる
    offsetX = -Math.floor(sizeX / 2);
    offsetZ = -Math.floor(sizeZ / 2);
    sizeY = 1;
  } else {
    offsetX = 0;
    offsetZ = -sizeZ;
    sizeY = 1;
  }
  // 足元のマスはローカル座標で (-offsetX, -offsetZ)。向きに合わせて回したとき、そのマスが足元に来るよう anchor を決める
  const feet = rotateCell(layout.facing, -offsetX, -offsetZ, { x: 0, z: 0 });
  const ceiling = !!layout.ceiling && !isVolume(videoData) && !isAnamorphic(videoData) && !layout.wall;
  const baseY = ceiling ? ceilingYAbove(dimension, origin) : origin.y;
  const anchor = { x: origin.x - feet.x, y: baseY, z: origin.z - feet.z, facing: layout.facing, wall: layout.wall, ceiling };
  const fittedY = fitToBuildHeight(dimension, anchor.y, sizeY);
  const shifted = fittedY !== anchor.y;
  anchor.y = fittedY;
  // 世界の高さ (オーバーワールドは 384、ネザーは 128) より高いと、どこに置いても上が切れる
  const range = dimension.heightRange;
  const worldHeight = range ? range.max - range.min : Infinity;
  return { anchor, shifted, sizeY, worldHeight, tooTall: sizeY > worldHeight, footprint: Math.max(sizeX, sizeZ) };
}

/**
 * 置けない・見えにくい大きさを知らせる。高すぎて世界に入らないときは false を返す (置かない。以前は黙って上が切れ、
 * 置けなかった行を毎フレーム置き直していた)。
 */
function checkPlacement(placement) {
  if (placement.tooTall) {
    notify("setup_too_tall", placement.sizeY, placement.worldHeight, EVENT_PREFIX);
    return false;
  }
  if (placement.footprint > LARGE_SCREEN_BLOCKS) notify("setup_large", placement.footprint);
  return true;
}

/** 画面の高さ (ブロック)。床に置く平面は 1。 */
function screenHeightOf(videoData, layout) {
  if (isAnamorphic(videoData)) return stageOf(videoData).wallHeight + 1;
  if (isVolume(videoData) || layout.wall) return videoData.height;
  return 1;
}

/** コマンドで置く画面が占める箱 (両端を含む。y も)。スクリーンブロックの画面と重ならないか調べるのに使う。 */
function commandScreenBox(videoData, anchor, sizeY = screenHeightOf(videoData, anchor)) {
  const box = worldBoxOf(videoData, anchor);
  return { x0: box.x0, x1: box.x1, z0: box.z0, z1: box.z1, y0: anchor.y, y1: anchor.y + Math.max(1, sizeY) - 1 };
}

/**
 * 今あるコマンドの画面の箱 (ディメンションの ID と箱)。メモリに無ければ (開き直した後など)、保存した位置と選んだ動画から。
 */
function commandScreenBoxes() {
  const boxes = [];
  for (const vp of activePlayers.values()) {
    if (vp.videoData) boxes.push({ dimensionId: vp.dimension.id, box: commandScreenBox(vp.videoData, vp.anchor) });
  }
  if (boxes.length > 0) return boxes;
  loadSelection();
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  const videoData = globalSelectedVideoId ? VIDEOS[globalSelectedVideoId] : null;
  if (!anchorLoc || !videoData) return boxes;
  const layout = storedLayout();
  const anchor = { x: anchorLoc.x, y: anchorLoc.y, z: anchorLoc.z, facing: layout.facing, wall: layout.wall, ceiling: !!layout.ceiling };
  let dimensionId = world.getDynamicProperty(ANCHOR_DIMENSION_KEY);
  if (typeof dimensionId !== "string") dimensionId = "minecraft:overworld";
  boxes.push({ dimensionId, box: commandScreenBox(videoData, anchor) });
  return boxes;
}

/** setup の足元から、その動画を置く位置を決める (align なら、その動画の大きさでチャンクにそろえてから)。 */
function placeVideo(videoData, origin, dimension, layout) {
  const base = layout.align ? alignOriginToChunks(videoData, origin, dimension, layout).origin : origin;
  return anchorForVideo(videoData, base, dimension, layout);
}

/** setup で保存した向き。無い・壊れていれば北向きの床置き (以前の版と同じ)。 */
function storedLayout() {
  try {
    const value = JSON.parse(world.getDynamicProperty(LAYOUT_KEY) ?? "null");
    if (value && FACING_NAMES[value.facing] !== undefined) {
      return { facing: value.facing, wall: !!value.wall, ceiling: !!value.ceiling, align: !!value.align };
    }
  } catch (e) { /* 壊れた値は無視する */ }
  return DEFAULT_LAYOUT;
}

/**
 * 天井に貼る高さ: 足元 origin の真上で、頭より上にある最初のブロック (それを画面に置き換える)。CEILING_SCAN_BLOCKS 以内に
 * 無ければ足元から CEILING_DEFAULT_GAP 上。画面を置いた後に決め直しても、見つかるのは画面そのものなので同じ高さになる。
 */
function ceilingYAbove(dimension, origin) {
  for (let dy = 2; dy <= CEILING_SCAN_BLOCKS; dy++) {
    let block;
    try {
      block = dimension.getBlock({ x: origin.x, y: origin.y + dy, z: origin.z });
    } catch (e) {
      break; // 世界の高さの外
    }
    if (block && !block.isAir) return origin.y + dy;
  }
  return origin.y + CEILING_DEFAULT_GAP;
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
// これより大きい画面は、遠い端が多くの端末の描画距離 (12 チャンク前後。実際の値は端末と設定による) の外になる
const LARGE_SCREEN_BLOCKS = 192;

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
// setup の後ろに書ける言葉。知らない言葉 (書き間違い) があれば、置かずに使える言葉を教える
const SETUP_OPTIONS = ["face", "wall", "ceiling", "align", "now"];

function setup(player, options = "") {
  if (!globalSelectedVideoId) {
    notify("no_video_selected");
    return;
  }
  const videoData = VIDEOS[globalSelectedVideoId];
  const words = options.toLowerCase().split(/\s+/).filter(Boolean);
  const unknown = words.filter((w) => !SETUP_OPTIONS.includes(w));
  if (unknown.length > 0) {
    notifyPlayer(player, "setup_unknown_option", unknown.join(" "), SETUP_OPTIONS.join(", "));
    return;
  }
  // ceiling (天井に貼る) は wall と一緒には使えない。両方あれば天井
  const ceiling = words.includes("ceiling");
  const wall = words.includes("wall") && !ceiling;
  const align = words.includes("align");
  const layout = { facing: wall || ceiling || words.includes("face") ? facingOf(player) : 0, wall, ceiling, align };
  if (ceiling && (isVolume(videoData) || isAnamorphic(videoData))) notifyPlayer(player, "setup_ceiling_flat_only");
  const loc = player.location;
  // 保存するのは足元そのもの。チャンクにそろえる (align) ときは、動画ごとに大きさが違うので、置くたびにそろえ直す
  const origin = { x: Math.floor(loc.x), y: Math.floor(loc.y), z: Math.floor(loc.z) };
  // now を付けると確認せずにすぐ置く (以前の版と同じ)。付けなければ、先に枠を見せて確定・向きの変更・中止を選ばせる
  if (words.includes("now")) commitSetup(player, videoData, origin, layout);
  else previewSetup(player, origin, layout, 0);
}

/**
 * 置く前の確認。置く範囲の枠を粒 (パーティクル) で描き、フォームで「ここに置く」「向きを変える」「やめる」を選ばせる。
 * まだブロックは触らない。向きを変えると 90 度回して描き直し、もう一度聞く。
 */
function previewSetup(player, origin, layout, busyTries) {
  if (!globalSelectedVideoId) {
    notify("no_video_selected");
    return;
  }
  const videoData = VIDEOS[globalSelectedVideoId];
  const dimension = player.dimension;
  const placement = placeVideo(videoData, origin, dimension, layout);
  if (!checkPlacement(placement)) return;
  const box = commandScreenBox(videoData, placement.anchor, placement.sizeY);
  const blocking = screenHooks?.overlapsScreen(dimension.id, box);
  if (blocking) {
    notify("setup_overlaps_screen", blocking);
    return;
  }
  drawPreviewOutline(dimension, box);
  const redraw = system.runInterval(() => drawPreviewOutline(dimension, box), PREVIEW_REDRAW_TICKS);
  const where = layout.ceiling ? "preview_where_ceiling" : layout.wall || isVolume(videoData) || isAnamorphic(videoData)
    ? "preview_where_wall" : "preview_where_floor";
  const form = new ActionFormData()
    .title(tr("preview_title"))
    .body(textLines(
      tr("preview_size", box.x1 - box.x0 + 1, box.y1 - box.y0 + 1, box.z1 - box.z0 + 1),
      tr(where, tr(`facing_${FACING_NAMES[layout.facing]}`)),
      tr("preview_replace"),
    ))
    .button(tr("preview_confirm"), "textures/items/emerald")
    .button(tr("preview_rotate"), "textures/items/item_frame")
    .button(tr("preview_cancel"), "textures/blocks/barrier");
  form.show(player).then((response) => {
    system.clearRun(redraw);
    if (response.canceled) {
      // チャットを閉じている途中などで出せなかったときは、少し待って出し直す
      if (response.cancelationReason === "UserBusy" && busyTries < PREVIEW_BUSY_RETRIES) {
        system.runTimeout(() => previewSetup(player, origin, layout, busyTries + 1), 20);
        return;
      }
      notifyPlayer(player, "preview_canceled", EVENT_PREFIX);
      return;
    }
    if (response.selection === 0) {
      const current = VIDEOS[globalSelectedVideoId];
      if (current) commitSetup(player, current, origin, layout);
    } else if (response.selection === 1) {
      previewSetup(player, origin, { ...layout, facing: (layout.facing + 1) % 4 }, 0);
    } else {
      notifyPlayer(player, "preview_canceled", EVENT_PREFIX);
    }
  }).catch((e) => {
    system.clearRun(redraw);
    warnOnce("preview", `Setup preview failed: ${e}`);
  });
}

/** 箱 box (両端を含むブロックの範囲) の 12 本の辺に粒を並べる。多すぎるときは間隔を空ける。 */
function drawPreviewOutline(dimension, box) {
  if (typeof dimension.spawnParticle !== "function") return;
  const x0 = box.x0;
  const x1 = box.x1 + 1;
  const y0 = box.y0;
  const y1 = box.y1 + 1;
  const z0 = box.z0;
  const z1 = box.z1 + 1;
  const total = 4 * ((x1 - x0) + (y1 - y0) + (z1 - z0));
  const step = Math.max(1, Math.ceil(total / PREVIEW_MAX_PARTICLES));
  const edges = [];
  for (const y of [y0, y1]) for (const z of [z0, z1]) edges.push([{ x: x0, y, z }, { x: x1, y, z }]);
  for (const x of [x0, x1]) for (const z of [z0, z1]) edges.push([{ x, y: y0, z }, { x, y: y1, z }]);
  for (const x of [x0, x1]) for (const y of [y0, y1]) edges.push([{ x, y, z: z0 }, { x, y, z: z1 }]);
  for (const [a, b] of edges) {
    const length = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y), Math.abs(b.z - a.z));
    for (let i = 0; i <= length; i += step) {
      const t = length === 0 ? 0 : i / length;
      try {
        dimension.spawnParticle(PREVIEW_PARTICLE, { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t });
      } catch (e) {
        // 読み込まれていないチャンクなど。その辺は飛ばす
        break;
      }
    }
  }
}

/** setup の確定: 向きを保存し、前の画面を片付けて、新しい位置にプレイヤー (再生役) を作る。 */
function commitSetup(player, videoData, origin, layout) {
  world.setDynamicProperty(LAYOUT_KEY, JSON.stringify(layout));
  const facingName = tr(`facing_${FACING_NAMES[layout.facing]}`);
  if (layout.ceiling) notify("setup_ceiling", facingName);
  else if (layout.facing !== 0 || layout.wall) notify(layout.wall ? "setup_wall" : "setup_facing", facingName);
  const align = layout.align;
  if (align) {
    const aligned = alignOriginToChunks(videoData, origin, player.dimension, layout);
    notify("setup_aligned", aligned.dx, aligned.dz, aligned.before, aligned.after);
  }
  // 建築できる高さ (オーバーワールドは Y=-64〜319) からはみ出すと置けないので、収まる高さへずらす
  const placement = placeVideo(videoData, origin, player.dimension, layout);
  if (!checkPlacement(placement)) return;
  const { anchor, shifted } = placement;
  // スクリーンブロックの画面と重なる位置には置かない (ブロックの画面を上書きしてしまう)
  const blocking = screenHooks?.overlapsScreen(player.dimension.id, commandScreenBox(videoData, anchor, placement.sizeY));
  if (blocking) {
    notify("setup_overlaps_screen", blocking);
    return;
  }
  if (shifted) notify("setup_shifted", anchor.y);
  world.setDynamicProperty(ORIGIN_KEY, origin);
  // 前のスクリーンの再生と片付けの仕事を止め、前の画面のうち新しい画面の外にあるマスを空気に戻す (以前は止めるだけで、
  //   床に置いた画面を壁に置き直すと床の画面がそのまま残った: 実機 2026-11-13)。新しい画面の範囲は触らないので、
  //   片付けが新しい画面を上書きすることはない。一度も描いていなければ何もしない
  // 常時読み込みの区画は、前の画面を片付け終わってから新しい画面に付け替える (先に外すと、遠くにある前の画面の
  //   チャンクが読み込まれなくなり、片付けのブロックを置けない: 実機 2026-11-13)。
  //   常時読み込みが一部しか登録できなくても、プレイヤーが近くにいれば再生できるので設置は続ける
  const setupToken = ++setupCount;
  const dimension = player.dimension;
  let pendingClears = 1;
  const clearFinished = () => {
    if (--pendingClears > 0 || setupToken !== setupCount) return;
    const ticking = ensureTickingArea(dimension, anchor, videoData);
    if (ticking.added < ticking.needed) notify("tickingarea_partial", ticking.added, ticking.needed);
  };
  const newBox = worldBoxOf(videoData, anchor);
  for (const old of activePlayers.values()) {
    old.cancelJob();
    pendingClears++;
    old.stopAndClear(newBox, false, true, clearFinished);
  }
  activePlayers.clear();
  clearFinished();
  
  const keyStr = getAnchorKeyStr(anchor);
  const vp = new VideoPlayer(anchor, player.dimension, globalSelectedVideoId);
  activePlayers.set(keyStr, vp);
  // 保存するのは位置 (x・y・z) だけ。向きは LAYOUT_KEY に別に保存してある
  world.setDynamicProperty(ANCHOR_KEY, { x: anchor.x, y: anchor.y, z: anchor.z }); // For single remote control backwards compatibility
  world.setDynamicProperty(ANCHOR_DIMENSION_KEY, player.dimension.id);
  startMainLoop();

  notify("setup_done", EVENT_PREFIX);
  // 最初の setup で、コマンドを書いた手順書の本 (とリモコンのコンパス) を渡す。チャットは流れて消えるので
  if (!player.getDynamicProperty?.(GUIDE_GIVEN_KEY)) giveGuide(player);
  // 置いた画面の見方 (床に置いた平面は足元からは見えない。立体は近すぎると全体が見えない)
  if (isAnamorphic(videoData)) return;
  if (isVolume(videoData)) notify("setup_view_volume");
  else if (anchor.ceiling) notify("setup_view_ceiling");
  else if (!layout.wall) notify("setup_view_floor", EVENT_PREFIX);
}

// 手順書の本のページ (ページごとに翻訳のキー。%1 はコマンドの接頭辞)
const GUIDE_PAGES = ["guide_page_start", "guide_page_play", "guide_page_place", "guide_page_more"];

/** 本 (書き込める本に手順を書いたもの) を作る。本のページを書けない版では、紙に説明 (lore) を付けて代わりにする。 */
function makeGuideItem() {
  try {
    const item = new ItemStack("minecraft:writable_book", 1);
    const book = item.getComponent("minecraft:book");
    if (book && typeof book.setContents === "function") {
      // 翻訳の文は改行を持てないので、題名と本文を別のキーにして、間に改行を入れる
      book.setContents(GUIDE_PAGES.map((key) => ({ rawtext: [tr("guide_title"), { text: "\n\n" }, tr(key, `/${EVENT_PREFIX}`)] })));
      try {
        if (typeof book.signBook === "function") book.signBook("Video Player Guide", "Video Player");
      } catch (e) { /* 署名できなくても、書き込める本のまま渡す */ }
      return item;
    }
  } catch (e) {
    warnOnce("guide_book", `Cannot write the guide book: ${e}`);
  }
  const paper = new ItemStack("minecraft:paper", 1);
  paper.nameTag = "Video Player Guide";
  const p = `/${EVENT_PREFIX}`;
  paper.setLore([`${p}setup wall`, `${p}setup ceiling`, `${p}setup now`, `${p}start  ${p}stop`, `${p}list  ${p}play <id>`, `${p}remote  ${p}guide`]);
  return paper;
}

/** プレイヤーに手順書を渡す。リモコン (コンパス) を持っていなければそれも渡す。持ちきれなければ足元に落とす。 */
function giveGuide(player) {
  let container;
  try {
    container = player.getComponent("minecraft:inventory")?.container;
  } catch (e) { /* 抜けた */ }
  if (!container) return false;
  const items = [makeGuideItem()];
  let hasRemote = false;
  for (let i = 0; i < container.size; i++) {
    if (container.getItem(i)?.typeId === REMOTE_CONTROL_ITEM) {
      hasRemote = true;
      break;
    }
  }
  if (!hasRemote) items.push(new ItemStack(REMOTE_CONTROL_ITEM, 1));
  let dropped = false;
  for (const item of items) {
    const left = container.addItem(item);
    if (left) {
      dropped = true;
      try {
        player.dimension.spawnItem(left, player.location);
      } catch (e) { /* 落とせなかった */ }
    }
  }
  try {
    player.setDynamicProperty(GUIDE_GIVEN_KEY, true);
  } catch (e) { /* 抜けた */ }
  notifyPlayer(player, dropped ? "guide_dropped" : "guide_given", EVENT_PREFIX);
  return true;
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
    const previous = vp;
    if (vp) vp.stopPlayback();
    const playbackDimension = getPlaybackDimension(dimension);
    let anchor = anchorLoc;
    const origin = world.getDynamicProperty(ORIGIN_KEY);
    const videoData = VIDEOS[globalSelectedVideoId];
    if (origin && videoData) {
      // 平面と立体では置く位置・高さ・常時読み込みの範囲が違うので、setup の足元から決め直す
      const layout = storedLayout();
      const fitted = placeVideo(videoData, origin, playbackDimension, layout);
      if (!checkPlacement(fitted)) {
        if (previous) previous.running = false;
        return;
      }
      const blocking = screenHooks?.overlapsScreen(playbackDimension.id, commandScreenBox(videoData, fitted.anchor, fitted.sizeY));
      if (blocking) {
        notify("setup_overlaps_screen", blocking);
        if (previous) previous.running = false;
        return;
      }
      anchor = fitted.anchor;
      if (fitted.shifted) notify("setup_shifted", anchor.y);
      const ticking = ensureTickingArea(playbackDimension, anchor, videoData);
      if (ticking.added < ticking.needed) notify("tickingarea_partial", ticking.added, ticking.needed);
      activePlayers.delete(keyStr);
      world.setDynamicProperty(ANCHOR_KEY, { x: anchor.x, y: anchor.y, z: anchor.z });
    }
    vp = new VideoPlayer(anchor, playbackDimension, globalSelectedVideoId);
    activePlayers.set(getAnchorKeyStr(anchor), vp);
    // 前の動画のマスのうち、新しい動画の外になるものを片付ける (以前は残ったままで、停止しても消えなかった)
    if (previous) previous.stopAndClear(worldBoxOf(vp.videoData, vp.anchor), false);
    startMainLoop();
  }
  ensureTickingAreaForPlayback(vp);

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
  const vp = ensureActivePlayer(dimension);
  if (!vp) {
    notify("no_anchor", EVENT_PREFIX);
    return;
  }
  vp.stopAndClear();
  notify("clearing");
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
  // だまし絵だけ: 人物を壁と床へ写す時間の平均・最大 (ms)・回数と、目の動きでの描き直しの回数
  if (isAnamorphic(vp.videoData)) {
    notify("stats_stage", fixed(summary.stageProjAvg, 2), summary.stageProjMax, summary.stageProjCount, summary.viewRedraws);
  }
}

function getActivePlayer() {
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  if (anchorLoc) {
    return activePlayers.get(getAnchorKeyStr(anchorLoc));
  }
  return null;
}

/**
 * 今のスクリーンのプレイヤー。ワールドを開き直した後はメモリに無いので、保存した位置から作り直す (以前は停止・シーク・
 * 音量が黙って効かなかった)。setup していなければ null。
 */
function ensureActivePlayer(dimension) {
  const existing = getActivePlayer();
  if (existing) return existing;
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  if (!anchorLoc || !globalSelectedVideoId || !VIDEOS[globalSelectedVideoId]) return null;
  const vp = new VideoPlayer(anchorLoc, getPlaybackDimension(dimension), globalSelectedVideoId);
  activePlayers.set(getAnchorKeyStr(anchorLoc), vp);
  startMainLoop();
  return vp;
}

function savedVolume() {
  const value = Number(world.getDynamicProperty(VOLUME_KEY) ?? 1);
  return Number.isFinite(value) && value >= 0 && value <= 1 ? value : 1.0;
}

/** 選んだ動画を覚える (ワールドを開き直したとき、同じ動画で続ける)。 */
function selectVideo(videoId) {
  globalSelectedVideoId = videoId;
  world.setDynamicProperty(SELECTED_KEY, videoId);
}

let selectionLoaded = false;
/** 保存した選択を読み直す (スクリプトの読み込み時は world を読めないので、最初の操作のときに)。 */
function loadSelection() {
  if (selectionLoaded) return;
  selectionLoaded = true;
  const saved = world.getDynamicProperty(SELECTED_KEY);
  if (typeof saved === "string" && LISTED_VIDEOS.some((v) => v.id === saved)) globalSelectedVideoId = saved;
}

function showRemoteControlGUI(player) {
  const vp = getActivePlayer();
  const running = vp ? vp.running : false;
  const currentVideoId = globalSelectedVideoId;
  const videoData = VIDEOS[currentVideoId];
  
  const statusText = tr(running ? "remote.playing" : "remote.stopped");
  const titleText = currentVideoId ? { text: currentVideoId } : tr("remote.none");
  const currentFrame = vp && vp.videoId === currentVideoId ? vp.currentFrame : 0;
  const masterVolume = vp ? vp.masterVolume : 1.0;
  
  const currentSec = videoData ? Math.floor(frameToSeconds(videoData, currentFrame)) : 0;
  const totalSec = videoData ? Math.floor(frameToSeconds(videoData, vp && vp.videoId === currentVideoId ? vp.frameCount : videoData.frame_count)) : 0;

  const form = new ActionFormData()
    .title(tr("remote.title"))
    .body(textLines(
      tr("remote.status", statusText),
      tr("remote.selected", titleText),
      tr("remote.position", currentSec, totalSec, currentFrame),
      // フォームの本文は % をもう一度読むので、% は %% と書く (1 つだと消えた: 実機 2026-11-13。チャットでは 1 つで出る)
      tr("remote.volume", `${Math.round(masterVolume * 100)}%%`),
    ))
    .button(tr(running ? "remote.pause" : "remote.play"), "textures/items/emerald")
    .button(tr("remote.stop"), "textures/blocks/redstone_block")
    .button(tr("remote.next"), "textures/items/paper")
    .button(tr("remote.prev"), "textures/items/paper")
    .button(tr("remote.volume_button"), "textures/items/repeater")
    .button(tr("remote.seek"), "textures/items/clock_item")
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

/** リモコンで選んだ動画にすぐ切り替えて再生し、リモコンを開き直す (選んだ動画と画面の動画がずれないように)。 */
function selectVideoFromRemote(videoId, player) {
  selectVideo(videoId);
  notify("video_selected", videoId);
  startPlayback(player.dimension);
  showRemoteControlGUI(player);
}

function switchVideoIndex(direction, player) {
  if (LISTED_VIDEOS.length === 0) return;
  let currentIdx = LISTED_VIDEOS.findIndex((v) => v.id === globalSelectedVideoId);
  if (currentIdx === -1) currentIdx = 0;
  let newIdx = (currentIdx + direction + LISTED_VIDEOS.length) % LISTED_VIDEOS.length;
  selectVideoFromRemote(LISTED_VIDEOS[newIdx].id, player);
}

function showVolumeGUI(player) {
  const vp = ensureActivePlayer(player.dimension);
  const mv = vp ? vp.masterVolume : savedVolume();
  const form = new ModalFormData()
    .title(tr("volume.title"));
  addSlider(form, tr("volume.slider"), 0, 100, 10, Math.round(mv * 100));

  form.show(player).then((response) => {
    if (response.canceled) return;
    // 音量はスクリーンごとではなく全体で持つ (動画を変えても 100% に戻らない)
    const volume = response.formValues[0] / 100.0;
    world.setDynamicProperty(VOLUME_KEY, volume);
    if (vp) {
      vp.masterVolume = volume;
      vp.currentAudioChunk = -1;
      if (vp.running) vp.syncAudioForFrame(vp.currentFrame);
    }
    notify("volume_set", `${Math.round(volume * 100)}%`);
  });
}

function showSeekGUI(player) {
  const vp = ensureActivePlayer(player.dimension);
  if (!vp || !vp.videoData) return;
  const maxSec = Math.floor(frameToSeconds(vp.videoData, vp.frameCount));
  const currentSec = Math.floor(frameToSeconds(vp.videoData, vp.currentFrame));

  const form = new ModalFormData()
    .title(tr("seek.title"));
  addSlider(form, tr("seek.slider"), 0, Math.max(1, maxSec), 1, currentSec);

  form.show(player).then((response) => {
    if (response.canceled) return;
    const targetSec = response.formValues[0];
    const targetFrame = secondsToFrame(vp.videoData, targetSec);
    vp.seekToFrame(targetFrame);
  });
}

function showVideoSelectGUI(player) {
  const form = new ActionFormData().title(tr("library.title")).body(tr("library.body"));
  for (const v of LISTED_VIDEOS) {
    const rawtext = [{ text: v.title }];
    if (v.id === globalSelectedVideoId) rawtext.push({ text: " " }, tr("selected_tag"));
    rawtext.push({ text: "\n" }, tr("library.item", v.frame_count, videoSizeLabel(v)));
    form.button({ rawtext });
  }

  form.show(player).then((response) => {
    if (response.canceled) return;
    const selectedVideo = LISTED_VIDEOS[response.selection];
    if (selectedVideo) {
      selectVideoFromRemote(selectedVideo.id, player);
    }
  });
}

// ワールドに入ったとき: まだ画面が無ければ、使い方を 1 回だけ案内する (以前は何も出ず、コマンドの接頭辞も分からなかった)。
// 再生中なら、途中で入った人にも今の音を鳴らす
world.afterEvents.playerSpawn.subscribe((event) => {
  if (!event.initialSpawn) return;
  const player = event.player;
  for (const vp of activePlayers.values()) vp.playCurrentChunkFor(player);
  screenHooks?.onPlayerSpawn(player);
  try {
    if (world.getDynamicProperty(ANCHOR_KEY) || player.getDynamicProperty(WELCOMED_KEY)) return;
    player.setDynamicProperty(WELCOMED_KEY, true);
  } catch (e) {
    return;
  }
  notifyPlayer(player, "welcome", EVENT_PREFIX);
});

// プレイヤーの権限 (PlayerPermissionLevel.Operator)。enum を import すると、無い版の Script API で読み込めないので数で比べる
const OPERATOR_PERMISSION_LEVEL = 2;

/**
 * オペレーターか (コマンドの画面・ほかの人のスクリーンを操作できる)。ワールドに 1 人だけなら誰でもよい (ひとりで遊ぶとき)。
 * playerPermissionLevel の無い版の Script API では、今までどおり誰でも操作できる。
 * 以前はリモコンとスクリーンの操作画面に権限の確かめが無く、来た人がほかの人のスクリーンを消せた (#198)。
 */
function isOperator(player) {
  try {
    if (world.getAllPlayers().length <= 1) return true;
    const level = player.playerPermissionLevel;
    return level === undefined || level === OPERATOR_PERMISSION_LEVEL;
  } catch (e) {
    return false;
  }
}

world.afterEvents.itemUse.subscribe((event) => {
  loadSelection();
  if (event.itemStack.typeId === REMOTE_CONTROL_ITEM) {
    // リモコンはコマンドと同じ操作 (コマンドの画面を置き直す・消す) なので、オペレーターだけ
    if (!isOperator(event.source)) {
      notifyPlayer(event.source, "remote_operator_only");
      return;
    }
    showRemoteControlGUI(event.source);
  }
});

/**
 * コマンド (/<接頭辞>:<操作> と /scriptevent <接頭辞>:<操作>) の処理。message は操作の後ろの文字 (setup face など)。
 */
function runAction(action, message, player) {
  loadSelection();
  const dimension = player ? player.dimension : world.getDimension("overworld");
  const event = { message };
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
    case "guide":
      if (player) giveGuide(player);
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
        // VIDEOS はふつうのオブジェクトなので、constructor・toString などの名前も「ある」と取り違えないよう、自分の動画だけを見る
        if (Object.prototype.hasOwnProperty.call(VIDEOS, videoId)) {
          selectVideo(videoId);
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
}

// /scriptevent <接頭辞>:<操作> [文字] (以前の版からの書き方。チートが要る)
system.afterEvents.scriptEventReceive.subscribe((event) => {
  if (!event.id.startsWith(EVENT_PREFIX)) return;
  runAction(event.id.slice(EVENT_PREFIX.length), event.message ?? "", event.sourceEntity);
});

// <legacy:slider>
/** フォームにスライダーを足す (@minecraft/server-ui 2.0.0 の書き方)。 */
function addSlider(form, label, minimum, maximum, step, value) {
  return form.slider(label, minimum, maximum, { valueStep: step, defaultValue: value });
}
// </legacy:slider>

// <legacy:commands> (旧版向けの出力では、この範囲を消して /scriptevent だけにする)
/**
 * 独自のコマンド (Script API 2.1.0 から)。/scriptevent と違ってチートを有効にしなくても使える (cheatsRequired: false)。
 * 動画 ID の enum も登録するが、入力の候補には出ない (docs/KNOWN_ISSUES.md)。例: /badapple:setup face、/badapple:play sample。権限はオペレーター (GameDirectors)。
 * [操作, 説明, 引数] の一覧。引数は "video" (動画 ID の候補) か "text" (setup face などの文字) か無し。
 */
const CUSTOM_COMMANDS = [
  ["setup", "Place the screen (options: face, wall, ceiling, align, now)", "text"],
  ["start", "Play the selected video from the start", null],
  ["play", "Play a video (by video ID)", "video"],
  ["stop", "Stop and clear the screen", null],
  ["list", "List the videos", null],
  ["remote", "Open the remote control", null],
  ["guide", "Get the guide book", null],
  ["render", "Set the render mode (auto, rect, list, block)", "text"],
  ["bench", "Measure the block placement speed", null],
  ["stats", "Show playback statistics", null],
];

function registerCustomCommands(registry) {
  const videoEnum = `${EVENT_NAMESPACE}:video`;
  const videoIds = LISTED_VIDEOS.map((v) => v.id);
  if (videoIds.length > 0) registry.registerEnum(videoEnum, videoIds);
  for (const [action, description, argument] of CUSTOM_COMMANDS) {
    const optionalParameters = [];
    if (argument === "video" && videoIds.length > 0) optionalParameters.push({ name: videoEnum, type: CustomCommandParamType.Enum });
    // String の引数は 1 語だけなので、setup face align のように並べられるよう 4 個まで受ける (1 個だと 2 語目が構文エラー)
    if (argument === "text") {
      for (let i = 0; i < 4; i++) optionalParameters.push({ name: i === 0 ? "options" : `option${i + 1}`, type: CustomCommandParamType.String });
    }
    registry.registerCommand({
      name: `${EVENT_NAMESPACE}:${action}`, description, permissionLevel: CommandPermissionLevel.GameDirectors,
      cheatsRequired: false, optionalParameters,
    }, (origin, ...values) => {
      // コマンドの処理の中ではワールドを変えられない (読み取り専用) ので、次の tick で行う
      const player = origin.sourceEntity?.typeId === "minecraft:player" ? origin.sourceEntity : undefined;
      const text = values.filter((v) => v !== undefined).map(String).join(" ");
      system.run(() => runAction(action, text, player));
      return { status: CustomCommandStatus.Success };
    });
  }
}

system.beforeEvents.startup.subscribe((event) => {
  try {
    registerCustomCommands(event.customCommandRegistry);
  } catch (e) {
    console.warn(`[${EVENT_NAMESPACE}] custom commands were not registered: ${e}`);
  }
});
// </legacy:commands>

// <legacy:screens> (旧版向けの出力では、この範囲を消す。スクリーンブロックは Script API 2.x のカスタムコンポーネントで動く)
// ===== スクリーンブロック (screen_system.js の ScreenManager とブロックモードの描画をつなぐ) =====
//
// スクリーンブロック (<接頭辞>:video_screen) を置いて使うと操作画面 (screen_ui.js) が開き、動画を選ぶと、ブロックの上に
// 壁のように画面を立てて再生する (立体の動画は、手前の面をブロックの面にそろえて奥へ)。画面はブロックの向き (置いた人が
// 見ていた方向) に向く。スクリーンごとに動画・再生位置・音量・ループ・大きさを持ち、ほかのスクリーンの再生に影響しない。
//
// 建物を壊さないために:
//   - 画面を置く範囲は、置く前にすべて調べる。空気でないブロックが 1 つでもあれば置かずに理由を出す (ワールドを変えない)
//   - このスクリーンが置いた範囲 (ScreenSession の box と pendingClear) を保存し、削除や動画の切り替えでは、その範囲の
//     「動画のパレットのブロック」だけを空気に戻す (プレイヤーが後から置いたほかのブロックは残す)
//   - 読み込まれていなくて片付けられなかった範囲は、ワールドに保存して、あとで読み込まれたときに片付ける
const SCREEN_BLOCK_ID = `${EVENT_NAMESPACE}:video_screen`;
const SCREEN_COMPONENT_ID = `${EVENT_NAMESPACE}:screen_controller`;
// スクリーンブロックの常時読み込みの区画の名前 (<接頭辞><スクリーン ID>_<番号>)。コマンドで置いた画面の区画と名前が違う
const SCREEN_TICKING_PREFIX = "bvp_screen_";
// この距離 (ブロック) より近くにいるプレイヤーにだけ、そのスクリーンの音を鳴らす (離れた所のスクリーンの音が重ならない)
const SCREEN_AUDIO_RANGE = 48;
// 拡大した画面の幅・高さの上限 (ブロック)
const SCREEN_MAX_SIDE = 256;
// 置く範囲を調べる・片付けるときに、この数のマスごとに tick を跨ぐ
const SCREEN_SCAN_YIELD = 512;
// 片付けられなかった範囲 (読み込まれていなかったなど) の一覧 (JSON の [{ dim, box }])
const SCREEN_ORPHANS_KEY = `${EVENT_NAMESPACE}:screen_orphans`;
const SCREEN_ORPHAN_RETRY_TICKS = 200;

/** スクリーン ID ("s3") から、プレイヤーに見せる番号 ("3")。 */
function screenNumber(screenId) {
  return String(screenId).slice(1);
}

/** 失敗の結果 (screen_system.js と同じ形)。 */
function screenFail(reason, detail = []) {
  return { ok: false, reason, detail };
}

/** スクリーンブロックで選べる動画 (一覧に出す動画。だまし絵の背景の映像は出さない)。 */
function isListedVideo(videoId) {
  return typeof videoId === "string" && Object.prototype.hasOwnProperty.call(VIDEOS, videoId)
    && LISTED_VIDEOS.some((v) => v.id === videoId);
}

/** 拡大して置ける動画か (平面で、盤面との差で置く形式。古い形式・立体・だまし絵は 1 倍だけ)。 */
function supportsScale(videoData) {
  const format = videoData.format;
  return !isVolume(videoData) && !isAnamorphic(videoData) && !!videoData.chunks && !!videoData.index
    && (format === FORMAT_V4 || format === FORMAT_V5 || format === FORMAT_V6);
}

let screenPaletteIdsCache = null;
/**
 * このアドオンの動画が画面に置くかもしれないブロックの ID (全部の動画のパレット)。スクリーンの片付けでは、これに当たる
 * ブロックだけを空気に戻す。置けないブロックの代わりに置く土 (resolvePermutation) も入れる。
 */
function screenPaletteIds() {
  if (screenPaletteIdsCache) return screenPaletteIdsCache;
  const ids = new Set(["minecraft:dirt"]);
  const add = (spec) => {
    if (!spec || typeof spec.block !== "string") return;
    ids.add(spec.block);
    ids.add(BLOCK_ID_ALIASES[spec.block] ?? spec.block);
  };
  for (const video of Object.values(VIDEOS)) {
    if (!video || !Array.isArray(video.level_blocks)) continue;
    if (video.adaptive_palette) for (const gop of video.level_blocks) (Array.isArray(gop) ? gop : []).forEach(add);
    else video.level_blocks.forEach(add);
  }
  ids.delete("minecraft:air");
  screenPaletteIdsCache = ids;
  return ids;
}

function isAirBlock(block) {
  return typeof block.isAir === "boolean" ? block.isAir : block.typeId === "minecraft:air";
}

/**
 * スクリーンブロックの画面の置き方。平面はブロックの 1 つ上から壁のように立て、左右の中央をブロックにそろえる。
 * 立体は手前 (見る人の側) の面をブロックと同じ面にそろえて、奥へ置く。どちらもブロックの向きに向く。
 * 戻り値: { anchor, box, width, height } か { error, detail }
 */
function screenLayoutFor(session, videoData, scale) {
  if (isAnamorphic(videoData)) return { error: "unsupported_anamorphic" };
  const volume = isVolume(videoData);
  if (scale > 1 && !supportsScale(videoData)) return { error: "scale_unsupported" };
  const s = volume ? 1 : scale;
  const width = videoData.width * s;
  const height = videoData.height * s;
  if (width > SCREEN_MAX_SIDE || height > SCREEN_MAX_SIDE) return { error: "too_big", detail: [SCREEN_MAX_SIDE] };
  // ブロックの真上に来るマス (ローカル座標)。平面は lz = 0 の面、立体は一番手前の段 (depth - 1)
  const lz = volume ? videoData.depth - 1 : 0;
  const centre = rotateCell(session.facing, Math.floor(width / 2), lz, { x: 0, z: 0 });
  const anchor = {
    x: session.position.x - centre.x, y: session.position.y + 1, z: session.position.z - centre.z,
    facing: session.facing, wall: true, scale: s,
  };
  const flat = worldBoxOf(videoData, anchor);
  const box = { x0: flat.x0, x1: flat.x1, z0: flat.z0, z1: flat.z1, y0: anchor.y, y1: anchor.y + height - 1 };
  return { anchor, box, width, height };
}

/** その動画をこのスクリーンに置ける大きさ (倍率) の一覧 [{ scale, width, height }]。 */
function screenScaleOptions(session, videoData) {
  const options = [];
  let dimension = null;
  try {
    dimension = world.getDimension(session.dimensionId);
  } catch (e) { /* 高さの確かめを省く */ }
  const range = dimension?.heightRange;
  const maxScale = supportsScale(videoData) ? screenManager.limits.maxScale : 1;
  for (let scale = 1; scale <= maxScale; scale++) {
    const layout = screenLayoutFor(session, videoData, scale);
    if (layout.error) break;
    if (range && (layout.box.y1 >= range.max || layout.box.y0 < range.min)) break;
    options.push({ scale, width: layout.width, height: layout.height });
  }
  return options;
}

/**
 * box の中を調べる (ジェネレータ。SCREEN_SCAN_YIELD マスごとに yield)。空気でないブロックがあれば置けない。ただし、
 * このスクリーンが前に置いた範囲 (ownedBoxes) の中の、動画のパレットのブロックは自分の画面なので置いてよい。
 * 置けなければ { reason, detail }、置ければ null。
 */
// 置く範囲が読み込まれていないとき、常時読み込みに入れてから待つ長さと、調べ直す間隔 (tick)。コマンドの setup の待ち (10 秒) と同じ
const SCREEN_LOAD_WAIT_TICKS = 200;
const SCREEN_LOAD_RETRY_TICKS = 20;
// 描き終わってから常時読み込みを外すまでの tick (続けて描くときに、外して入れ直さないように)
const SCREEN_TICKING_GRACE_TICKS = 100;

/**
 * box (x・z と y0) のチャンクがすべて読み込まれているか (チャンクごとに 1 マスを見る)。VideoPlayer.screenLoaded と同じく
 * isChunkLoaded で調べる (getBlock は読めても、置くときに「loaded and ticking でない」で失敗することがある)。
 */
function boxIsLoaded(dimension, box) {
  const probe = typeof dimension.isChunkLoaded === "function"
    ? (at) => dimension.isChunkLoaded(at) : (at) => Boolean(dimension.getBlock(at));
  const loc = { x: 0, y: box.y0, z: 0 };
  for (let cz = Math.floor(box.z0 / 16); cz <= Math.floor(box.z1 / 16); cz++) {
    for (let cx = Math.floor(box.x0 / 16); cx <= Math.floor(box.x1 / 16); cx++) {
      loc.x = Math.min(Math.max(cx * 16, box.x0), box.x1);
      loc.z = Math.min(Math.max(cz * 16, box.z0), box.z1);
      try {
        if (!probe(loc)) return false;
      } catch (e) {
        return false;
      }
    }
  }
  return true;
}

function* scanScreenArea(dimension, box, ownedBoxes) {
  const ids = screenPaletteIds();
  const loc = { x: 0, y: 0, z: 0 };
  let blocked = 0;
  let first = null;
  let n = 0;
  for (let y = box.y0; y <= box.y1; y++) {
    for (let x = box.x0; x <= box.x1; x++) {
      for (let z = box.z0; z <= box.z1; z++) {
        loc.x = x;
        loc.y = y;
        loc.z = z;
        let block;
        try {
          block = dimension.getBlock(loc);
        } catch (e) {
          // 世界の外 (高さの範囲の外)
          return { reason: "too_tall", detail: [box.y1 - box.y0 + 1] };
        }
        if (!block) return { reason: "not_loaded", detail: [x, y, z] };
        if (!isAirBlock(block) && !(ids.has(block.typeId) && ownedBoxes.some((owned) => boxContains(owned, loc)))) {
          blocked++;
          if (!first) first = { x, y, z };
        }
        if (++n % SCREEN_SCAN_YIELD === 0) yield;
      }
    }
  }
  return blocked > 0 ? { reason: "blocked", detail: [first.x, first.y, first.z, blocked] } : null;
}

/**
 * boxes の中の、動画のパレットのブロックだけを空気に戻す (ジェネレータ)。keep(loc) が true のマスは触らない。
 * 読み込まれていないなどで片付けきれなかった箱の一覧を返す。
 */
function* clearScreenArea(dimension, boxes, keep = null) {
  const ids = screenPaletteIds();
  const air = BlockPermutation.resolve("minecraft:air");
  const loc = { x: 0, y: 0, z: 0 };
  const leftovers = [];
  let n = 0;
  for (const box of boxes) {
    let incomplete = false;
    for (let y = box.y0; y <= box.y1; y++) {
      for (let x = box.x0; x <= box.x1; x++) {
        for (let z = box.z0; z <= box.z1; z++) {
          loc.x = x;
          loc.y = y;
          loc.z = z;
          if (keep && keep(loc)) continue;
          let block;
          try {
            block = dimension.getBlock(loc);
          } catch (e) {
            block = undefined;
          }
          if (!block) {
            incomplete = true;
          } else if (ids.has(block.typeId)) {
            try {
              dimension.setBlockPermutation(loc, air);
            } catch (e) {
              incomplete = true;
            }
          }
          if (++n % SCREEN_SCAN_YIELD === 0) yield;
        }
      }
    }
    if (incomplete) leftovers.push(box);
  }
  return leftovers;
}

/** 片付けられなかった範囲の一覧を読む (壊れていれば空)。 */
function readScreenOrphans() {
  try {
    const list = JSON.parse(world.getDynamicProperty(SCREEN_ORPHANS_KEY) ?? "[]");
    return Array.isArray(list) ? list.filter((o) => o && typeof o.dim === "string" && Array.isArray(o.box) && o.box.length === 6
      && o.box.every(Number.isInteger)) : [];
  } catch (e) {
    return [];
  }
}

function writeScreenOrphans(list) {
  world.setDynamicProperty(SCREEN_ORPHANS_KEY, list.length > 0 ? JSON.stringify(list) : undefined);
}

function addScreenOrphans(dimensionId, boxes) {
  if (boxes.length === 0) return;
  const list = readScreenOrphans();
  for (const box of boxes) list.push({ dim: dimensionId, box: [box.x0, box.y0, box.z0, box.x1, box.y1, box.z1] });
  writeScreenOrphans(list);
}

/** 今のスクリーンかコマンドの画面が使っているマスか (片付け残しを片付けるとき、それらのブロックを消さないように)。 */
function cellInUse(dimensionId, loc) {
  for (const session of screenManager.list()) {
    if (session.dimensionId === dimensionId && session.ownedBoxes.some((box) => boxContains(box, loc))) return true;
  }
  for (const { dimensionId: id, box } of commandScreenBoxes()) {
    if (id === dimensionId && boxContains(box, loc)) return true;
  }
  return false;
}

let orphanJobRunning = false;
let orphanRetryTick = -Infinity;
/** 片付け残しを、読み込まれている所から片付ける (ワールドを開いたとき・スクリーンを使ったとき。間をあけて)。 */
function retryScreenOrphans() {
  if (orphanJobRunning || system.currentTick - orphanRetryTick < SCREEN_ORPHAN_RETRY_TICKS) return;
  const list = readScreenOrphans();
  if (list.length === 0) return;
  orphanRetryTick = system.currentTick;
  orphanJobRunning = true;
  system.runJob((function* () {
    const remaining = [];
    try {
      for (const orphan of list) {
        let dimension;
        try {
          dimension = world.getDimension(orphan.dim);
        } catch (e) {
          continue;
        }
        const [x0, y0, z0, x1, y1, z1] = orphan.box;
        const leftovers = yield* clearScreenArea(dimension, [{ x0, y0, z0, x1, y1, z1 }], (loc) => cellInUse(orphan.dim, loc));
        if (leftovers.length > 0) remaining.push(orphan);
      }
      writeScreenOrphans(remaining);
    } finally {
      orphanJobRunning = false;
    }
  })());
}

/**
 * ブロックモードのスクリーンのレンダラー (screen_system.js の RENDERER_CONTRACT)。描画は VideoPlayer (コマンドの画面と
 * 同じ) に任せ、置く場所の確かめ・片付け・音声の範囲・常時読み込みをスクリーンごとに行う。
 */
class BlockScreenRenderer {
  constructor(session) {
    this.session = session;
    this.vp = null;
    this.prepareJob = null;
    this.pendingPrepare = null;
    // 最後に操作した人 (シーク・置けなかったブロックなどの知らせを出す相手)
    this.operator = null;
    this.tickingName = `${SCREEN_TICKING_PREFIX}${session.screenId}`;
    // 描き終わったら常時読み込みを外す (一時停止・停止・シーク・用意の後。releaseTickingLater)
    this.releaseTickingWhenIdle = false;
    this.idleSince = null;
    // いま常時読み込みに入れてある範囲 (loadBox。同じ範囲なら入れ直さない)
    this.tickingKey = null;
    this.onFinished = null;
    // 今のチャンクの音を鳴らしたプレイヤー (ID)。止めるときは、この人たちの分だけを止める (stopAudio)
    this.audioListeners = new Set();
    this.audioTrack = null;
  }

  dimension() {
    return world.getDimension(this.session.dimensionId);
  }

  /** VideoPlayer に渡すつなぎ (このスクリーンの音量・音声・知らせ・終わり)。 */
  hooks() {
    return {
      volume: this.session.volume,
      onFinished: () => {
        // ループしないときだけ、最後まで再生したことを操作した人に知らせる
        if (!this.session.loop) this.tell("screen.finished", screenNumber(this.session.screenId));
        this.onFinished?.();
      },
      message: (key, ...args) => {
        // シーク中・シーク完了はリモコン向けの文 (「リモコンの再生で再開」)。スクリーンでは操作画面が結果を知らせる
        if (key === "seeking" || key === "seek_done") return;
        this.tell(key, ...args);
      },
      playAudio: (trackId, volume) => this.playAudio(trackId, volume),
      stopAudio: (trackId) => this.stopAudio(trackId),
      playAudioFor: (player, trackId, volume) => {
        if (this.inAudioRange(player)) this.playSoundTo(player, trackId, volume);
      },
    };
  }

  tell(key, ...args) {
    if (this.operator) notifyPlayer(this.operator, key, ...args);
  }

  /** 画面の真ん中 (音を鳴らす人を決める基準)。 */
  centre() {
    const box = this.session.box;
    if (!box) return this.session.position;
    return { x: (box.x0 + box.x1) / 2, y: (box.y0 + box.y1) / 2, z: (box.z0 + box.z1) / 2 };
  }

  inAudioRange(player) {
    try {
      if (player.dimension?.id !== this.session.dimensionId) return false;
      const c = this.centre();
      const p = player.location;
      return Math.hypot(p.x - c.x, p.y - c.y, p.z - c.z) <= SCREEN_AUDIO_RANGE;
    } catch (e) {
      return false;
    }
  }

  playSoundTo(player, trackId, volume) {
    try {
      player.playSound(trackId, { location: player.location, volume, pitch: 1.0 });
      if (this.audioTrack !== trackId) {
        this.audioTrack = trackId;
        this.audioListeners = new Set();
      }
      this.audioListeners.add(player.id);
    } catch (e) {
      warnOnce(`audio:${trackId}`, `Cannot play sound ${trackId}: ${e}`);
    }
  }

  playAudio(trackId, volume) {
    for (const player of world.getAllPlayers()) {
      if (this.inAudioRange(player)) this.playSoundTo(player, trackId, volume);
    }
  }

  /**
   * このスクリーンが鳴らしているチャンクの音を、鳴らした人の分だけ止める。音の ID は動画ごとなので、全員に stopsound すると、
   * 同じ動画を流しているほかのスクリーンの音も消えていた (#201)。ほかのスクリーンが同じ音をその人に鳴らしていれば止めない。
   */
  stopAudio(trackId) {
    const listeners = this.audioTrack === trackId ? this.audioListeners : new Set();
    this.audioListeners = new Set();
    this.audioTrack = null;
    if (listeners.size === 0) return;
    for (const player of world.getAllPlayers()) {
      let id;
      try {
        id = player.id;
      } catch (e) {
        continue;
      }
      if (!listeners.has(id) || this.sharedWithAnotherScreen(id, trackId)) continue;
      try {
        player.runCommand(`stopsound @s ${trackId}`);
      } catch (e) { /* 抜けた・鳴っていない */ }
    }
  }

  /** ほかのスクリーンが、同じ音 (同じ動画の同じチャンク) をこの人に鳴らしているか。 */
  sharedWithAnotherScreen(playerId, trackId) {
    for (const renderer of screenManager.renderers.values()) {
      if (renderer === this || renderer.audioTrack !== trackId || !renderer.audioListeners.has(playerId)) continue;
      if (renderer.vp?.running) return true;
    }
    return false;
  }

  addTicking(vp) {
    this.releaseTickingWhenIdle = false;
    const box = this.session.box;
    if (!box) {
      this.removeTicking();
      return;
    }
    const ticking = this.loadBox(vp.dimension, box);
    if (ticking && ticking.added < ticking.needed) this.tell("tickingarea_partial", ticking.added, ticking.needed);
  }

  /**
   * box を常時読み込みに入れる。もう同じ box で入れてあれば何もしない (外して入れ直すと、入れ直した区画が読み込まれるまでの
   * 間に置けなくなる。PC の実機で、1 倍に戻した直後の描き直しで 9216 個置けなかった)。入れ直したときは addTickingAreas の結果。
   */
  loadBox(dimension, box) {
    const key = `${box.x0},${box.y0},${box.z0},${box.x1},${box.y1},${box.z1}`;
    if (this.tickingKey === key && hasTickingAreaNamed(`${this.tickingName}_0`)) return null;
    removeTickingAreasNamed(`${this.tickingName}_`);
    this.tickingKey = key;
    return addTickingAreas(dimension, box, box.y0, box.y1, this.tickingName);
  }

  removeTicking() {
    this.releaseTickingWhenIdle = false;
    this.idleSince = null;
    this.tickingKey = null;
    removeTickingAreasNamed(`${this.tickingName}_`);
  }

  /**
   * 再生していないときは常時読み込みを外す (ワールド全体の区画の数に限りがあるため)。ただし描いている途中 (片付け・シークした
   * コマ・大きさを変えたときの描き直し) は、描き終わってから外す。途中で外すと、プレイヤーが離れたときに読み込まれていない
   * 部分に置けなくなる (PC の実機で、2 倍にした直後に離れたら 9216 個置けなかった)。
   */
  releaseTickingLater() {
    // 描き終わってから少し (SCREEN_TICKING_GRACE_TICKS) 待って外す。続けて描くこと (大きさを変えた後のシークなど) が多いので
    this.releaseTickingWhenIdle = true;
    this.idleSince = null;
    scheduler.ensureRunning();
  }

  /** 描いている間だけ、今の画面の範囲を常時読み込みに入れる (描き終わったら tick で外す)。 */
  keepLoadedWhileDrawing(dimension, box) {
    if (!box) return;
    this.loadBox(dimension, box);
    this.releaseTickingLater();
  }

  /** 今の動画の VideoPlayer (ワールドを開き直した後などで無ければ作る)。動画が無い・置けなければ null。 */
  ensurePlayer() {
    const mediaId = this.session.mediaId;
    if (this.vp && !this.vp.disposed && this.vp.videoId === mediaId) return this.vp;
    if (!isListedVideo(mediaId)) return null;
    const layout = screenLayoutFor(this.session, VIDEOS[mediaId], this.session.scale);
    if (layout.error) return null;
    this.vp = new VideoPlayer(layout.anchor, this.dimension(), mediaId, this.hooks());
    return this.vp;
  }

  /** 用意の途中なら止めて、待っている Promise を「スクリーンが無い」で終わらせる。 */
  cancelPrepare() {
    if (this.prepareJob !== null) {
      system.clearJob(this.prepareJob);
      this.prepareJob = null;
    }
    this.finishPrepare(screenFail("no_screen"));
  }

  finishPrepare(result) {
    const resolve = this.pendingPrepare;
    this.pendingPrepare = null;
    this.prepareJob = null;
    if (resolve) resolve(result);
  }

  prepare(mediaId, { scale }) {
    this.cancelPrepare();
    return new Promise((resolve) => {
      const session = this.session;
      if (!isListedVideo(mediaId)) {
        resolve(screenFail("no_media", [String(mediaId)]));
        return;
      }
      const videoData = VIDEOS[mediaId];
      const layout = screenLayoutFor(session, videoData, scale);
      if (layout.error) {
        resolve(screenFail(layout.error, layout.detail ?? []));
        return;
      }
      let dimension;
      try {
        dimension = this.dimension();
      } catch (e) {
        resolve(screenFail("invalid_position"));
        return;
      }
      const range = dimension.heightRange;
      if (range && (layout.box.y1 >= range.max || layout.box.y0 < range.min)) {
        resolve(screenFail("too_tall", [layout.height]));
        return;
      }
      const other = screenManager.findOverlap(session.dimensionId, layout.box, session.screenId);
      if (other) {
        resolve(screenFail("overlaps_screen", [screenNumber(other.screenId)]));
        return;
      }
      if (commandScreenBoxes().some(({ dimensionId, box }) => dimensionId === session.dimensionId && boxesIntersect(box, layout.box))) {
        resolve(screenFail("overlaps_command_screen"));
        return;
      }
      this.pendingPrepare = resolve;
      const self = this;
      this.prepareJob = system.runJob((function* () {
        // 1. 置く範囲を調べる (ここまではワールドを変えない。置けなければ前の動画と画面のまま)
        let problem = yield* scanScreenArea(dimension, layout.box, session.ownedBoxes);
        if (problem?.reason === "not_loaded") {
          // 遠くて読み込まれていない部分がある (2 倍の 256 ブロックの画面など。PC の実機で、プレイヤーから 127 ブロック先が
          // 読み込まれていなかった)。コマンドの setup と同じく常時読み込みに入れて、読み込まれるまで待ってから調べ直す。
          // 名前は addTicking と同じ接頭辞なので、再生を始めるときに画面の区画に置き換わる
          addTickingAreas(dimension, layout.box, layout.box.y0, layout.box.y1, `${self.tickingName}_load`);
          const until = system.currentTick + SCREEN_LOAD_WAIT_TICKS;
          while (problem?.reason === "not_loaded" && system.currentTick < until) {
            const next = system.currentTick + SCREEN_LOAD_RETRY_TICKS;
            while (system.currentTick < next) yield;
            problem = yield* scanScreenArea(dimension, layout.box, session.ownedBoxes);
          }
          if (problem) removeTickingAreasNamed(`${self.tickingName}_load`);
        }
        if (problem) {
          self.finishPrepare(screenFail(problem.reason, problem.detail));
          return;
        }
        // 2. 前の画面を止めて、前の画面の範囲のうち新しい画面の外を片付ける (このスクリーンが置いたブロックだけ)
        if (self.vp) self.vp.dispose();
        self.vp = null;
        // 前の画面の区画を外し、新しい画面の範囲を描き終わるまで読み込んでおく (片付け・空の色で塗る・続く描き直しの間)
        self.keepLoadedWhileDrawing(dimension, layout.box);
        const oldBoxes = session.ownedBoxes.filter((box) => !sameBox(box, layout.box));
        // 片付ける前の画面の範囲も読み込んでおき、読み込まれるまで待つ (PC の実機で、2 倍から 1 倍に戻すと、プレイヤーから
        // 遠い左右の部分が読み込まれておらず残った)。名前は画面の区画と同じ接頭辞なので、描き終わったら一緒に外れる
        removeTickingAreasNamed(`${self.tickingName}_old`);
        oldBoxes.forEach((box, i) => addTickingAreas(dimension, box, box.y0, box.y1, `${self.tickingName}_old${i}`));
        const loadedBy = system.currentTick + SCREEN_LOAD_WAIT_TICKS;
        // 新しい画面の範囲も、区画を入れ直した直後は読み込まれていないことがあるので、描く前に一緒に待つ (PC の実機で、2 倍に
        // した直後に遠い部分の 4104 個を置けなかった)
        const mustLoad = [layout.box, ...oldBoxes];
        while (mustLoad.some((box) => !boxIsLoaded(dimension, box)) && system.currentTick < loadedBy) {
          const next = system.currentTick + SCREEN_LOAD_RETRY_TICKS;
          while (system.currentTick < next) yield;
        }
        const leftovers = yield* clearScreenArea(dimension, oldBoxes, (loc) => boxContains(layout.box, loc));
        // 3. 新しい画面を空の色で塗る (平面はパレットの先頭のブロック、立体は空気)
        self.prepareJob = null;
        const vp = new VideoPlayer(layout.anchor, dimension, mediaId, self.hooks());
        self.vp = vp;
        vp.stopAndClear(null, false, false, () => {
          if (self.vp !== vp) return;
          self.finishPrepare({
            ok: true, width: layout.width, height: layout.height, box: layout.box,
            cleared: oldBoxes.filter((box) => !leftovers.includes(box)),
          });
        });
      })());
    });
  }

  play({ fromStart, position = 0 }) {
    const vp = this.ensurePlayer();
    if (!vp) throw new Error(`video ${this.session.mediaId} is not in this add-on`);
    vp.masterVolume = this.session.volume;
    this.addTicking(vp);
    this.releaseTickingWhenIdle = false;
    if (fromStart) {
      vp.restart();
      return;
    }
    // ワールドを開き直した後など、まだ何も描いていない VideoPlayer は保存した位置から (直近のキーフレームから復元して描く)
    if (!vp.running && vp.currentFrame === 0 && position > 0) {
      vp.currentFrame = Math.min(secondsToFrame(vp.videoData, position), Math.max(0, vp.frameCount - 1));
      vp.forceRedraw = true;
    }
    vp.resume();
  }

  pause() {
    this.vp?.stopPlayback();
    this.releaseTickingLater();
  }

  stop() {
    const vp = this.ensurePlayer();
    if (vp) vp.stopAndClear(null, false);
    this.releaseTickingLater();
  }

  seek(seconds, { resume = false } = {}) {
    const vp = this.ensurePlayer();
    if (!vp) return;
    const frame = Math.min(secondsToFrame(vp.videoData, seconds), Math.max(0, vp.frameCount - 1));
    if (resume) {
      // 再生中: そのフレームから描き続ける (直近のキーフレームから復元して、盤面との差で置く)
      vp.stopPlayback();
      vp.frameIterator = null;
      vp.currentFrame = frame;
      vp.forceRedraw = true;
      this.addTicking(vp);
      vp.resume();
    } else {
      // 一時停止中: そのコマを描き終わるまで読み込んでおく
      this.keepLoadedWhileDrawing(vp.dimension, this.session.box);
      vp.seekToFrame(frame);
    }
  }

  setVolume(volume) {
    const vp = this.vp;
    if (!vp) return;
    vp.masterVolume = volume;
    // 消音にしたら、鳴っているこのスクリーンの音をすぐ止める (大きさを変えたときは次のチャンクから)
    if (volume <= 0 && vp.currentAudioChunk >= 0) {
      this.stopAudio(vp.trackIdOf(vp.currentAudioChunk));
      vp.currentAudioChunk = -1;
    }
  }

  position() {
    const vp = this.vp;
    if (!vp || vp.disposed || !vp.videoData) return this.session.playbackPosition;
    return frameToSeconds(vp.videoData, Math.min(vp.currentFrame, vp.frameCount));
  }

  duration() {
    const mediaId = this.session.mediaId;
    if (!isListedVideo(mediaId)) return 0;
    const vp = this.vp && !this.vp.disposed && this.vp.videoId === mediaId ? this.vp : null;
    const videoData = VIDEOS[mediaId];
    return frameToSeconds(videoData, vp ? vp.frameCount : videoData.frame_count);
  }

  isRunning() {
    return !!this.vp?.running;
  }

  /** 再生中か、描いている途中 (用意 (範囲の確かめと片付け)・シークしたコマ・描き直し)。 */
  isDrawing() {
    if (this.prepareJob !== null) return true;
    return !!this.vp && !this.vp.disposed && (this.vp.running || this.vp.viewIterator !== null);
  }

  wantsTick() {
    // 描き終わった後、常時読み込みを外すために 1 回は呼んでもらう
    return this.isDrawing() || this.releaseTickingWhenIdle;
  }

  tick(budget) {
    this.vp?.tick(budget);
    if (!this.releaseTickingWhenIdle) return;
    if (this.isDrawing()) {
      this.idleSince = null;
    } else if (this.idleSince === null) {
      this.idleSince = system.currentTick;
    } else if (system.currentTick - this.idleSince >= SCREEN_TICKING_GRACE_TICKS) {
      this.removeTicking();
    }
  }

  /** 止めて片付ける。clearBlocks なら、このスクリーンが置いたブロックだけを空気に戻す。片付け残しはあとで片付ける。 */
  release({ clearBlocks = true } = {}) {
    this.cancelPrepare();
    if (this.vp) this.vp.dispose();
    this.vp = null;
    this.removeTicking();
    const boxes = this.session.ownedBoxes;
    if (!clearBlocks || boxes.length === 0) return Promise.resolve({ ok: true });
    let dimension;
    try {
      dimension = this.dimension();
    } catch (e) {
      addScreenOrphans(this.session.dimensionId, boxes);
      return Promise.resolve({ ok: true, pending: true });
    }
    const dimensionId = this.session.dimensionId;
    const screenId = this.session.screenId;
    return new Promise((resolve) => {
      system.runJob((function* () {
        // ほかのスクリーンとコマンドの画面のマスは消さない (重ならないように置いているが、念のため)
        const leftovers = yield* clearScreenArea(dimension, boxes, (loc) => {
          for (const other of screenManager.list()) {
            if (other.screenId !== screenId && other.dimensionId === dimensionId
              && other.ownedBoxes.some((box) => boxContains(box, loc))) return true;
          }
          return false;
        });
        addScreenOrphans(dimensionId, leftovers);
        resolve({ ok: true, pending: leftovers.length > 0 });
      })());
    });
  }
}

/** スクリーンブロックがまだその位置にあるか ("ok" / "missing" / 読み込まれていなければ "unknown")。 */
function checkScreenMount(session) {
  let block;
  try {
    block = world.getDimension(session.dimensionId).getBlock(session.position);
  } catch (e) {
    return "unknown";
  }
  if (!block) return "unknown";
  return block.typeId === SCREEN_BLOCK_ID ? "ok" : "missing";
}

const screenManager = new ScreenManager({
  storage: {
    read: (key) => {
      const value = world.getDynamicProperty(key);
      return typeof value === "string" ? value : undefined;
    },
    write: (key, value) => world.setDynamicProperty(key, value),
  },
  createRenderer: (session) => new BlockScreenRenderer(session),
  checkMount: checkScreenMount,
  scheduler,
  keyPrefix: `${EVENT_NAMESPACE}:`,
  log: (message) => console.warn(`[${EVENT_NAMESPACE}] ${message}`),
  // 壊れて読み戻せなかったスクリーンのブロックは、片付け残しとして近づいたときに片付ける (#199)
  onDropped: (dimensionId, boxes) => addScreenOrphans(dimensionId, boxes),
});

/** ブロックの向き (minecraft:cardinal_direction。置いた人が見ていた方向) を 0 北・1 東・2 南・3 西 にする。 */
function screenFacingOf(permutation) {
  try {
    const index = FACING_NAMES.indexOf(permutation?.getState("minecraft:cardinal_direction"));
    return index >= 0 ? index : 0;
  } catch (e) {
    return 0;
  }
}

/** その位置のスクリーン (無ければ登録する)。{ ok, session } か失敗。 */
function screenAt(dimension, location, permutation) {
  const position = { x: location.x, y: location.y, z: location.z };
  return screenManager.create({ dimensionId: dimension.id, position, facing: screenFacingOf(permutation) });
}

/** 操作の結果に、成功の知らせを付ける。 */
function withNotice(result, notice, ...args) {
  if (result && typeof result.then === "function") return result.then((value) => withNotice(value, notice, ...args));
  if (result?.ok) return { ...result, notice, noticeArgs: args };
  return result;
}

/** 動画を切り替えるときの倍率 (今の倍率で置けなければ、置ける中で一番大きいもの)。 */
function scaleForMedia(session, mediaId) {
  if (!isListedVideo(mediaId)) return 1;
  const options = screenScaleOptions(session, VIDEOS[mediaId]);
  if (options.length === 0) return 1;
  return options.filter((o) => o.scale <= session.scale).pop()?.scale ?? options[0].scale;
}

/** 大きさを変える (画面を用意し直して、同じ位置から続ける)。 */
async function changeScreenScale(screenId, scale) {
  const session = screenManager.get(screenId);
  if (!session || !session.mediaId) return screenFail(session ? "no_media_selected" : "no_screen");
  const wasPlaying = session.playbackState === PLAYBACK_STATES.PLAYING;
  const at = screenManager.rendererFor(session).position();
  const result = await screenManager.setMedia(screenId, session.mediaId, { scale });
  if (!result.ok) return result;
  if (wasPlaying) {
    const played = screenManager.play(screenId);
    if (played.ok && at > 0) screenManager.seek(screenId, at);
  } else if (at > 0) {
    screenManager.seek(screenId, at);
  }
  return result;
}

/** 操作画面 (screen_ui.js) に渡すつなぎ。 */
const screenUi = {
  tr,
  status(screenId) {
    const session = screenManager.get(screenId);
    if (!session) return null;
    const renderer = screenManager.renderers.get(screenId);
    const listed = isListedVideo(session.mediaId);
    const entry = listed ? VIDEO_LIST.find((v) => v.id === session.mediaId) : null;
    return {
      screenNumber: screenNumber(screenId),
      mediaId: session.mediaId,
      mediaTitle: entry?.title ?? session.mediaId ?? "",
      state: session.playbackState,
      position: renderer ? renderer.position() : session.playbackPosition,
      duration: listed ? frameToSeconds(VIDEOS[session.mediaId], VIDEOS[session.mediaId].frame_count) : 0,
      volume: session.volume,
      loop: session.loop,
      scale: session.scale,
      width: session.screenWidth,
      height: session.screenHeight,
      rendererType: session.rendererType,
    };
  },
  media() {
    return LISTED_VIDEOS.map((v) => {
      const videoData = VIDEOS[v.id];
      const supported = !!videoData && !isAnamorphic(videoData);
      return {
        id: v.id, title: v.title, width: v.width, height: v.height,
        seconds: videoData ? frameToSeconds(videoData, videoData.frame_count) : 0,
        supported, reason: supported ? null : "unsupported_anamorphic",
      };
    });
  },
  scaleOptions(screenId, mediaId) {
    const session = screenManager.get(screenId);
    return session && isListedVideo(mediaId) ? screenScaleOptions(session, VIDEOS[mediaId]) : [];
  },
  renderers() {
    // このパックで使える描画方式。画像モードは別のパック (別のスクリプト) なので、ここでは選べない
    return [{ type: "block", label: "screen.renderer.block" }];
  },
  act(screenId, action, ...args) {
    const session = screenManager.get(screenId);
    const renderer = session ? screenManager.rendererFor(session) : null;
    if (renderer && this.player) renderer.operator = this.player;
    const n = screenNumber(screenId);
    switch (action) {
      case "play":
        // 保存した動画が、作り直したアドオンから無くなっていたとき
        if (session?.mediaId && !isListedVideo(session.mediaId)) return screenFail("no_media", [session.mediaId]);
        return withNotice(screenManager.play(screenId), "screen.playing", n);
      case "pause":
        return withNotice(screenManager.pause(screenId), "screen.paused", n);
      case "stop":
        return withNotice(screenManager.stop(screenId), "screen.stopped", n);
      case "seek":
        return screenManager.seek(screenId, args[0]);
      case "volume":
        return withNotice(screenManager.setVolume(screenId, args[0]), "screen.volume_set", n, `${Math.round(args[0] * 100)}%`);
      case "loop":
        return withNotice(screenManager.setLoop(screenId, args[0]), args[0] ? "screen.loop_set_on" : "screen.loop_set_off", n);
      case "select": {
        if (!session) return screenFail("no_screen");
        const title = VIDEO_LIST.find((v) => v.id === args[0])?.title ?? String(args[0]);
        return withNotice(screenManager.setMedia(screenId, args[0], { scale: scaleForMedia(session, args[0]) }), "screen.ready", n, title);
      }
      case "scale":
        return withNotice(changeScreenScale(screenId, args[0]), "screen.scale_set", n, args[0]);
      case "remove":
        return screenManager.remove(screenId).then((result) => (result.ok
          ? { ...result, notice: result.pending ? "screen.removed_pending" : "screen.removed", noticeArgs: [n] } : result));
      default:
        return screenFail("render_error", [String(action)]);
    }
  },
  tell(player, result) {
    if (!result) return;
    if (result.ok) {
      if (result.notice) notifyPlayer(player, result.notice, ...(result.noticeArgs ?? []));
      return;
    }
    notifyPlayer(player, `screen.error.${result.reason}`, ...(result.detail ?? []));
  },
  later(callback, ticks) {
    system.runTimeout(callback, ticks);
  },
};

/** スクリーンの操作画面を、その人向けに開く (screenUi の act が知らせる相手もその人にする)。 */
function openScreenMenu(player, screenId) {
  const ui = Object.create(screenUi);
  ui.player = player;
  showScreenMenu(player, screenId, ui);
}

/** スクリーンブロックを使ったとき。 */
function onScreenInteract(player, dimension, location) {
  let block;
  try {
    block = dimension.getBlock(location);
  } catch (e) {
    return;
  }
  if (!block || block.typeId !== SCREEN_BLOCK_ID) return;
  screenManager.validateMounts();
  retryScreenOrphans();
  const result = screenAt(dimension, location, block.permutation);
  if (!result.ok) {
    screenUi.tell(player, result);
    return;
  }
  const session = result.session;
  if (!canControlScreen(player, session)) {
    const n = screenNumber(session.screenId);
    if (session.ownerName) notifyPlayer(player, "screen.not_yours", n, session.ownerName);
    else notifyPlayer(player, "screen.operator_only", n);
    return;
  }
  openScreenMenu(player, session.screenId);
}

/** そのスクリーンを操作できるか: 持ち主 (置いた人) とオペレーター。持ち主の分からない (v6.41.0 で置いた) ものはオペレーターだけ。 */
function canControlScreen(player, session) {
  if (isOperator(player)) return true;
  try {
    return Boolean(session.ownerId) && session.ownerId === player.id;
  } catch (e) {
    return false;
  }
}

/** 置いた人を持ち主にする (ブロックの onPlace には置いた人が無いので、ワールドのイベントで)。 */
world.afterEvents.playerPlaceBlock?.subscribe((event) => {
  const { block, player } = event;
  let location, dimension, permutation;
  try {
    if (block.typeId !== SCREEN_BLOCK_ID) return;
    location = { ...block.location };
    dimension = block.dimension ?? event.dimension;
    permutation = block.permutation;
  } catch (e) {
    return;
  }
  system.run(() => {
    try {
      const result = screenAt(dimension, location, permutation);
      if (result.ok) screenManager.setOwner(result.session.screenId, player.id, player.name);
    } catch (e) { /* 抜けた・読み込まれていない */ }
  });
});

/** スクリーンブロックが壊れたとき (プレイヤー・爆発など)。画面を片付けて、登録を外す。 */
function onScreenBroken(dimensionId, location, player = null) {
  const session = screenManager.at(dimensionId, location);
  if (!session) return;
  const n = screenNumber(session.screenId);
  screenManager.remove(session.screenId).then((result) => {
    if (player && result.ok) notifyPlayer(player, result.pending ? "screen.removed_pending" : "screen.removed", n);
  }).catch((e) => console.warn(`[${EVENT_NAMESPACE}] failed to remove screen ${n}: ${e}`));
}

screenHooks = {
  tickSources: () => screenManager.tickSources(),
  overlapsScreen(dimensionId, box) {
    const session = screenManager.findOverlap(dimensionId, box);
    return session ? screenNumber(session.screenId) : null;
  },
  onPlayerSpawn(player) {
    for (const renderer of screenManager.renderers.values()) renderer.vp?.playCurrentChunkFor(player);
  },
};

world.afterEvents.worldLoad?.subscribe(() => {
  screenManager.restore();
  screenManager.validateMounts();
  retryScreenOrphans();
});

// カスタムコンポーネントの壊れたときのイベントを取りこぼしても片付ける (爆発・プレイヤー)
world.afterEvents.playerBreakBlock?.subscribe((event) => {
  if (event.brokenBlockPermutation?.type?.id !== SCREEN_BLOCK_ID) return;
  onScreenBroken(event.dimension?.id ?? event.block.dimension.id, event.block.location, event.player);
});
world.afterEvents.blockExplode?.subscribe((event) => {
  if (event.explodedBlockPermutation?.type?.id !== SCREEN_BLOCK_ID) return;
  onScreenBroken(event.dimension.id, event.block.location);
});

system.beforeEvents.startup.subscribe((event) => {
  try {
    event.blockComponentRegistry.registerCustomComponent(SCREEN_COMPONENT_ID, {
      // 置いたとき: 登録だけする (画面はまだ作らない)。イベントの中ではワールドを変えないよう、次の tick で
      onPlace(e) {
        const dimension = e.dimension;
        const location = { ...e.block.location };
        system.run(() => {
          try {
            const block = dimension.getBlock(location);
            if (block?.typeId === SCREEN_BLOCK_ID) screenAt(dimension, location, block.permutation);
          } catch (err) { /* 読み込まれていない */ }
        });
      },
      onPlayerInteract(e) {
        if (!e.player) return;
        const player = e.player;
        const dimension = e.dimension;
        const location = { ...e.block.location };
        system.run(() => onScreenInteract(player, dimension, location));
      },
      onPlayerBreak(e) {
        const dimensionId = e.dimension.id;
        const location = { ...e.block.location };
        const player = e.player;
        system.run(() => onScreenBroken(dimensionId, location, player));
      },
      onBreak(e) {
        const dimensionId = e.dimension.id;
        const location = { ...e.block.location };
        system.run(() => onScreenBroken(dimensionId, location));
      },
    });
  } catch (e) {
    console.warn(`[${EVENT_NAMESPACE}] the screen block component was not registered: ${e}`);
  }
});
// </legacy:screens>
