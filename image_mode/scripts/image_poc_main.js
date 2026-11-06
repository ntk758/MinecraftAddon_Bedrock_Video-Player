/**
 * 画像（テクスチャ）再生方式の PoC のスクリプト。ブロック方式の main.js / codec.js は使わない。
 *
 * 画面は独自エンティティ (`__NAMESPACE__:screen`) で、フレーム番号はエンティティプロパティ
 * (`__NAMESPACE__:frame`, client_sync: true) に書く。Resource Pack の render controller が
 * `Array.frames[q.property('<namespace>:frame')]` でその値を読み、フレームの PNG を選ぶ。
 * (出典: Render Controllers / Entity Properties の公式ドキュメント。docs/IMAGE_MODE.md に URL を書いた)
 *
 * 再生速度は ticksPerFrame (20 tick/秒 ÷ fps) で決まる。fps > 20 は 1 tick に 1 フレームが上限なので
 * 20fps に clamp する。実機でのfpsは未確認。
 */
import { CommandPermissionLevel, CustomCommandStatus, CustomCommandParamType, system, world } from "@minecraft/server";

const EVENT_NAMESPACE = "__NAMESPACE__";
const EVENT_PREFIX = `${EVENT_NAMESPACE}:`;
const ENTITY_ID = `${EVENT_PREFIX}screen`;
const PROPERTY_NAME = `${EVENT_PREFIX}frame`;
const FRAME_COUNT = __FRAME_COUNT__;
const TICKS_PER_FRAME = __TICKS_PER_FRAME__;
const DISTANCE_BLOCKS = 3;

let screen = null;
let playbackHandle = null;

/** 画面のエンティティを生かしておく。無ければプレイヤーの前に出す。 */
function ensureScreen(player) {
  if (screen !== null && screen.isValid) return screen;
  const target = player === undefined ? world.getPlayers()[0] : player;
  if (target === undefined) return null;
  const view = target.getViewDirection();
  const length = Math.hypot(view.x, view.y, view.z);
  if (length === 0) return null;
  const from = target.location;
  const location = {
    x: Math.floor(from.x + (view.x / length) * DISTANCE_BLOCKS),
    y: Math.floor(from.y + 1.5),
    z: Math.floor(from.z + (view.z / length) * DISTANCE_BLOCKS),
  };
  screen = target.dimension.spawnEntity(ENTITY_ID, location);
  screen.nameTag = `${EVENT_NAMESPACE} screen`;
  screen.setProperty(PROPERTY_NAME, 0);
  return screen;
}

function stopPlayback() {
  if (playbackHandle !== null) {
    system.clearRunInterval(playbackHandle);
    playbackHandle = null;
  }
}

function startPlayback() {
  stopPlayback();
  playbackHandle = system.runInterval(() => {
    if (screen === null || !screen.isValid) {
      stopPlayback();
      return;
    }
    const next = (((screen.getProperty(PROPERTY_NAME) ?? 0) + 1) % FRAME_COUNT) | 0;
    screen.setProperty(PROPERTY_NAME, next);
  }, TICKS_PER_FRAME);
}

/** 操作の一覧。引数があるものはその候補を受け取る。 */
const ACTIONS = [
  ["place", "Place the image screen in front of you", null],
  ["play", "Play the frames", null],
  ["stop", "Stop the playback", null],
  ["frame", "Show one frame (0 to FRAME_COUNT-1)", "number"],
  ["remove", "Remove the screen entity", null],
];

function runAction(action, value, player) {
  switch (action) {
    case "place": {
      const entity = ensureScreen(player);
      if (entity === null) return "no player nearby";
      return `placed ${ENTITY_ID} at ${Math.floor(entity.location.x)},${Math.floor(entity.location.y)},${Math.floor(entity.location.z)}`;
    }
    case "play": {
      if (ensureScreen(player) === null) return "no player nearby";
      startPlayback();
      return `playing ${FRAME_COUNT} frames, 1 frame per ${TICKS_PER_FRAME} tick`;
    }
    case "stop": {
      stopPlayback();
      if (screen !== null && screen.isValid) screen.setProperty(PROPERTY_NAME, 0);
      return "stopped";
    }
    case "frame": {
      const entity = ensureScreen(player);
      if (entity === null) return "no player nearby";
      const index = Math.min(Math.max(Number(value) | 0, 0), FRAME_COUNT - 1);
      stopPlayback();
      entity.setProperty(PROPERTY_NAME, index);
      return `frame ${index}`;
    }
    case "remove": {
      stopPlayback();
      if (screen !== null && screen.isValid) screen.remove();
      screen = null;
      return "removed";
    }
    default:
      return `unknown action: ${action}`;
  }
}

// /scriptevent <接頭辞>:<操作> [数字] (チートが要る)。main.js と同じ書き方。
system.afterEvents.scriptEventReceive.subscribe((event) => {
  if (!event.id.startsWith(EVENT_PREFIX)) return;
  const note = runAction(event.id.slice(EVENT_PREFIX.length), event.message ?? "", event.sourceEntity);
  if (event.sourceEntity?.typeId === "minecraft:player") console.warn(`[${EVENT_NAMESPACE}] ${note}`);
});

function registerCustomCommands(registry) {
  for (const [action, description, argument] of ACTIONS) {
    const optionalParameters = [];
    if (argument === "number") optionalParameters.push({ name: "frame", type: CustomCommandParamType.Integer });
    registry.registerCommand({
      name: `${EVENT_NAMESPACE}:${action}`, description, permissionLevel: CommandPermissionLevel.GameDirectors,
      cheatsRequired: false, optionalParameters,
    }, (origin, value) => {
      // コマンドの処理の中ではワールドを変えられない (読み取り専用) ので、次の tick で行う
      const player = origin.sourceEntity?.typeId === "minecraft:player" ? origin.sourceEntity : undefined;
      system.run(() => runAction(action, value === undefined ? "" : String(value), player));
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
