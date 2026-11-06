/**
 * 画像（テクスチャ）再生方式のスクリプト。ブロック方式の main.js / codec.js は使わない。
 *
 * 動画ごとに 1 つのエンティティ種別 (`__NAMESPACE__:<動画ID>`) を使う。画面の位置はワールドの動的なプロパティ
 * (`__NAMESPACE__:screens`) に動画 ID ごとに保存し、再生している動画の一覧を (`__NAMESPACE__:playing`) に保存する。
 * フレーム番号はエンティティプロパティ (`__NAMESPACE__:frame`, client_sync: true) に書き、
 * Resource Pack の render controller が `Array.frames[q.property('<namespace>:frame')]` でフレームの PNG を選ぶ。
 * (出典: Render Controllers / Entity Properties / World の公式ドキュメント。docs/IMAGE_MODE.md に URL を書いた)
 *
 * 複数の動画を同時に再生できる (動画ごとに 1 画面・1 本の runInterval)。
 * 再生速度は動画ごとの ticks_per_frame (20 tick/秒 ÷ fps)。fps > 20 はビルド時に 20fps へ clamp している。
 * 実機での再生速度・見た目は未確認 (game test: docs/IMAGE_MODE.md の手順)。
 */
import { CommandPermissionLevel, CustomCommandStatus, CustomCommandParamType, system, world } from "@minecraft/server";
import { IMAGE_VIDEO_LIST } from "./image_videos.js";

const EVENT_NAMESPACE = "__NAMESPACE__";
const EVENT_PREFIX = `${EVENT_NAMESPACE}:`;
const PROPERTY_NAME = `${EVENT_PREFIX}frame`;
const SCREENS_KEY = `${EVENT_NAMESPACE}:screens`;
const PLAYING_KEY = `${EVENT_NAMESPACE}:playing`;
const MESSAGE_PREFIX = `§b[${EVENT_NAMESPACE}]`;
const VIDEO_ENUM = `${EVENT_NAMESPACE}:video`;
// 画面を置く距離 (プレイヤーの見ている方向へ)
const DISTANCE_BLOCKS = 4;
// 画面の位置からエンティティを探すときの距離 (置く位置は整数に丸めるので 3 で探す)
const SEARCH_DISTANCE = 3;

/** 動画の一覧。id で引く。hidden の動画は「次の動画」と一覧に出さない。 */
const VIDEOS = IMAGE_VIDEO_LIST.filter((v) => !v.hidden);
const VIDEO_BY_ID = new Map(IMAGE_VIDEO_LIST.map((v) => [v.id, v]));

/** 再生している動画の runInterval のハンドル。動画 ID → handle */
const playback = new Map();

function note(player, text) {
  if (player !== undefined) player.sendMessage(`${MESSAGE_PREFIX} ${text}`);
  console.warn(`[${EVENT_NAMESPACE}] ${text}`);
}

/** 画面の位置の一覧 (動画 ID → {x, y, z, dimension})。壊れた値は無視する。 */
function readScreens() {
  const raw = world.getDynamicProperty(SCREENS_KEY);
  if (typeof raw !== "string") return new Map();
  try {
    const saved = JSON.parse(raw);
    const screens = new Map();
    for (const [videoId, spot] of Object.entries(saved ?? {})) {
      if (VIDEO_BY_ID.has(videoId) && typeof spot?.dimension === "string"
        && Number.isFinite(spot.x) && Number.isFinite(spot.y) && Number.isFinite(spot.z)) {
        screens.set(videoId, spot);
      }
    }
    return screens;
  } catch (e) {
    return new Map();
  }
}

function saveScreens(screens) {
  const plain = {};
  for (const [videoId, spot] of screens) plain[videoId] = spot;
  world.setDynamicProperty(SCREENS_KEY, JSON.stringify(plain));
}

/** 再生している動画 ID の一覧。 */
function readPlaying() {
  const raw = world.getDynamicProperty(PLAYING_KEY);
  if (typeof raw !== "string") return [];
  try {
    return JSON.parse(raw).filter((id) => typeof id === "string" && VIDEO_BY_ID.has(id));
  } catch (e) {
    return [];
  }
}

function savePlaying(ids) {
  if (ids.length === 0) {
    world.setDynamicProperty(PLAYING_KEY, null);
    return;
  }
  world.setDynamicProperty(PLAYING_KEY, JSON.stringify(ids));
}

function dimensionOf(id) {
  try {
    return world.getDimension(id);
  } catch (e) {
    return world.getDimension("overworld");
  }
}

/** 動画の画面のエンティティを探す (ワールドに保存された位置から再取得する)。 */
function findScreen(videoId) {
  const spot = readScreens().get(videoId);
  if (spot === undefined) return null;
  // 検索は公式に記載のある location / maxDistance だけ使い、動画のエンティティは typeId で選ぶ
  const location = { x: spot.x, y: spot.y, z: spot.z };
  for (const entity of dimensionOf(spot.dimension).getEntities({ location, maxDistance: SEARCH_DISTANCE })) {
    if (entity.typeId === `${EVENT_PREFIX}${videoId}` && entity.isValid) return entity;
  }
  return null;
}

/** 動画の画面を、プレイヤーの前に置く。すでにその動画の画面があればそれを再利用する。 */
function placeScreen(player, video) {
  if (video === undefined || VIDEO_BY_ID.get(video.id) === undefined) return null;
  const found = findScreen(video.id);
  if (found !== null) return found;
  const target = player ?? world.getPlayers()[0];
  if (target === undefined) return null;
  const view = target.getViewDirection();
  const length = Math.hypot(view.x, view.y, view.z);
  if (length === 0) return null;
  const from = target.location;
  const spot = {
    x: Math.floor(from.x + (view.x / length) * DISTANCE_BLOCKS),
    y: Math.floor(from.y + 1.5),
    z: Math.floor(from.z + (view.z / length) * DISTANCE_BLOCKS),
    dimension: target.dimension.id,
  };
  const entity = target.dimension.spawnEntity(`${EVENT_PREFIX}${video.id}`, spot);
  entity.nameTag = `${video.title}`;
  entity.setProperty(PROPERTY_NAME, 0);
  const screens = readScreens();
  screens.set(video.id, spot);
  saveScreens(screens);
  return entity;
}

/** その動画の再生をやめる (runInterval を消す)。画面とそのフレームはそのまま残す。 */
function stopVideo(videoId) {
  const handle = playback.get(videoId);
  if (handle !== undefined) {
    system.clearRunInterval(handle);
    playback.delete(videoId);
  }
  savePlaying([...playback.keys()]);
}

/** すべての再生をやめる。 */
function stopAll() {
  for (const videoId of [...playback.keys()]) stopVideo(videoId);
}

/** 動画の再生。画面が無ければ置く。フレームは 0 から順に進める。 */
function playVideo(player, videoId) {
  const video = VIDEO_BY_ID.get(videoId);
  if (video === undefined) return `unknown video: ${videoId}`;
  const entity = placeScreen(player, video);
  if (entity === null) return `no screen for ${videoId} (no player nearby)`;
  stopVideo(videoId);
  const frameCount = video.frame_count | 0;
  if (frameCount <= 0) return `no frames for ${videoId}`;
  const ticksPerFrame = Math.max(1, video.ticks_per_frame | 0);
  playback.set(videoId, system.runInterval(() => {
    if (!entity.isValid) {
      stopVideo(videoId);
      return;
    }
    const next = (((entity.getProperty(PROPERTY_NAME) ?? 0) + 1) % frameCount) | 0;
    entity.setProperty(PROPERTY_NAME, next);
  }, ticksPerFrame));
  savePlaying([...playback.keys()]);
  return `playing ${video.title}: ${frameCount} frames, 1 frame per ${ticksPerFrame} tick`;
}

/** 1 枚だけ表示 (再生は止める)。 */
function showFrame(player, videoId, value) {
  const video = VIDEO_BY_ID.get(videoId);
  if (video === undefined) return `unknown video: ${videoId}`;
  const entity = placeScreen(player, video);
  if (entity === null) return `no screen for ${videoId} (no player nearby)`;
  stopVideo(videoId);
  const frameCount = video.frame_count | 0;
  const index = Math.min(Math.max(Number(value) | 0, 0), Math.max(frameCount - 1, 0));
  entity.setProperty(PROPERTY_NAME, index);
  return `frame ${index} of ${video.title}`;
}

/** 画面を消す (その動画、または全部)。 */
function removeScreen(player, videoId) {
  const screens = readScreens();
  const targets = videoId === undefined ? [...screens.keys()] : [videoId];
  const removed = [];
  for (const id of targets) {
    stopVideo(id);
    const entity = findScreen(id);
    if (entity !== null) entity.remove();
    screens.delete(id);
    removed.push(id);
  }
  saveScreens(screens);
  savePlaying([...playback.keys()]);
  return removed.length > 0 ? `removed ${removed.join(", ")}` : "no screen";
}

/** 一覧の次の動画を再生する (他の画面は消さない)。 */
function nextVideo(player) {
  if (VIDEOS.length === 0) return "no video in this pack";
  const screens = readScreens();
  let index = VIDEOS.findIndex((v) => screens.has(v.id));
  if (index === -1) index = -1;
  const video = VIDEOS[(index + 1) % VIDEOS.length];
  return playVideo(player, video.id);
}

function describe(player) {
  const screens = readScreens();
  const playing = new Set(readPlaying());
  const lines = VIDEOS.map((video) => {
    const spot = screens.get(video.id);
    const where = spot === undefined ? "画面なし" : `画面 ${spot.x},${spot.y},${spot.z} (${spot.dimension})`;
    return `  ${video.id}: ${video.title} (${video.frame_count} フレーム ${video.width}x${video.height} ${video.fps}fps) ${where}${playing.has(video.id) ? " · 再生中" : ""}`;
  });
  note(player, lines.length > 0 ? lines.join("\n") : "このパックに動画がない");
  return "listed";
}

/** 動画の引数が省略されたときの動画 (画面があるもの、なければ最初)。 */
function currentVideoId(videoArg) {
  if (typeof videoArg === "string" && videoArg.length > 0) return videoArg;
  const screens = readScreens();
  const shown = VIDEOS.find((v) => screens.has(v.id));
  return shown?.id ?? VIDEOS[0]?.id;
}

/** 操作の一覧。引数があるものはその候補を受け取る。 */
const ACTIONS = [
  ["list", "このパックの動画の一覧と画面の位置", null],
  ["setup", "動画の画面を出す (引数を省略すると画面のある動画、なければ最初)", "video"],
  ["play", "動画の再生 (引数を省略すると画面のある動画、なければ最初)", "video"],
  ["stop", "再生をやめる (引数を省略すると全部)", "video"],
  ["next", "一覧の次の動画を再生する", null],
  ["frame", "1 枚だけ表示 (0 から フレーム数-1)", "video_frame"],
  ["remove", "画面を消す (引数を省略すると全部)", "video"],
];

function runAction(action, args, player) {
  switch (action) {
    case "list":
      return describe(player);
    case "setup": {
      const videoId = currentVideoId(args[0]);
      const video = VIDEO_BY_ID.get(videoId);
      const entity = video === undefined ? null : placeScreen(player, video);
      if (entity === null) return "no video or no player nearby";
      const at = entity.location;
      note(player, `placed ${entity.typeId} at ${Math.floor(at.x)},${Math.floor(at.y)},${Math.floor(at.z)}`);
      return "placed";
    }
    case "play": {
      const videoId = currentVideoId(args[0]);
      if (videoId === undefined) return "no video in this pack";
      const message = playVideo(player, videoId);
      note(player, message);
      return message;
    }
    case "stop": {
      if (args[0] === undefined || args[0] === "") {
        stopAll();
        note(player, "stopped all playback");
        return "stopped";
      }
      stopVideo(args[0]);
      note(player, `stopped ${args[0]}`);
      return "stopped";
    }
    case "next": {
      const message = nextVideo(player);
      note(player, message);
      return message;
    }
    case "frame": {
      const videoId = currentVideoId(args[0]);
      if (videoId === undefined) return "no video in this pack";
      const message = showFrame(player, videoId, args[1]);
      note(player, message);
      return message;
    }
    case "remove": {
      const message = removeScreen(player, args[0] === "" ? undefined : args[0]);
      note(player, message);
      return message;
    }
    default:
      return `unknown action: ${action}`;
  }
}

// /scriptevent <接頭辞>:<操作> [引数]。main.js と同じ書き方。
system.afterEvents.scriptEventReceive.subscribe((event) => {
  if (!event.id.startsWith(EVENT_PREFIX)) return;
  const player = event.sourceEntity?.typeId === "minecraft:player" ? event.sourceEntity : undefined;
  const args = typeof event.message === "string" && event.message.length > 0 ? event.message.split(/\s+/) : [];
  const result = runAction(event.id.slice(EVENT_PREFIX.length), args, player);
  if (player !== undefined) console.warn(`[${EVENT_NAMESPACE}] ${result}`);
});

function registerCustomCommands(registry) {
  const ids = VIDEOS.map((v) => v.id);
  if (ids.length > 0) {
    try {
      registry.registerEnum(VIDEO_ENUM, ids);
    } catch (e) {
      console.warn(`[${EVENT_NAMESPACE}] video enum was not registered: ${e}`);
    }
  }
  for (const [action, description, argument] of ACTIONS) {
    const optionalParameters = [];
    if (argument === "video" && ids.length > 0) optionalParameters.push({ name: "video", type: CustomCommandParamType.Enum, enumName: VIDEO_ENUM });
    if (argument === "video_frame") {
      if (ids.length > 0) optionalParameters.push({ name: "video", type: CustomCommandParamType.Enum, enumName: VIDEO_ENUM });
      optionalParameters.push({ name: "frame", type: CustomCommandParamType.Integer });
    }
    registry.registerCommand({
      name: `${EVENT_NAMESPACE}:${action}`, description, permissionLevel: CommandPermissionLevel.GameDirectors,
      cheatsRequired: false, optionalParameters,
    }, (origin, ...values) => {
      // コマンドの処理の中ではワールドを変えられない (読み取り専用) ので、次の tick で行う
      const player = origin.sourceEntity?.typeId === "minecraft:player" ? origin.sourceEntity : undefined;
      const args = values.map((value) => (value === undefined ? "" : String(value)));
      system.run(() => runAction(action, args, player));
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
