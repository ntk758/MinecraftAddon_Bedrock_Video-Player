/**
 * Block Video Player - 画像モード (テクスチャ再生方式) の再生制御
 *
 * 映像の各コマはリソースパックのテクスチャ (アトラスのシート) に焼き込んであり、コマの切り替えはクライアントの Molang が
 * 自分の時計 (q.life_time) で行う (image_mode/SPEC.md)。このスクリプトは、どのコマから進めるか (frame) と、その書き直しの
 * 通し番号 (seq) と状態 (state) をスクリーンのエンティティのプロパティ (client_sync) に書き、音声のチャンクを鳴らすだけ。
 *
 * 時計は、このスクリプトが再生中に数える tick (playTicks)。world.getAbsoluteTime() は昼夜の時刻で、眠る・/time set で飛び、
 * 昼夜の進行を止めると止まるので使わない。状態は world の dynamic property に JSON で保存する (再生中は 1 秒ごと)。プロパティの書き込みは次の tick に反映され、最大 20 回/秒なので、30 fps のコマを毎回
 * 書くことはできない。代わりに 1 秒ごとに「今のコマ」を書き直して、クライアントの時計のずれを戻す。
 */
import { world, system, CommandPermissionLevel, CustomCommandParamType, CustomCommandStatus } from "@minecraft/server";
import { IMAGE_VIDEOS, NAMESPACE } from "./image_videos.js";

const TICKS_PER_SECOND = 20;
// 再生中に frame と seq を書き直す間隔 (tick)。短いほどクライアントのずれが小さいが、そのたびに全員へ送られる
const SYNC_INTERVAL = 20;
const SEQ_MODULO = 1024;
const STATE_KEY = `${NAMESPACE}:image_state`;
const STOPPED = 0;
const PLAYING = 1;
const PAUSED = 2;
const PROPERTY = { state: `${NAMESPACE}:state`, frame: `${NAMESPACE}:frame`, seq: `${NAMESPACE}:seq` };
// 音声のチャンクの頭から、この秒数以内なら鳴らし始める (途中からは鳴らせないので、遅れたら次の境目まで待つ)
const AUDIO_START_WINDOW = 0.3;
const METHODS = ["a", "c"];

const VIDEOS = new Map(IMAGE_VIDEOS.map((v) => [v.id, v]));

/**
 * 保存する状態。playTicks は再生した tick の数 (今のコマは playTicks から決める)。
 * スクリプトの読み込み時 (early execution) は world を読めないので、worldLoad で読み直す。
 */
let state = defaultState();
let loaded = false;
// 最後に鳴らした音声のチャンク (メモリだけ。開き直したら次の境目から鳴らす)
let playedChunk = -1;
let ticksSinceSync = 0;
// 画面に書けなかった (読み込まれていなかった) 変更がある。読み込まれ次第、どの状態でも書き直す
let dirty = false;
const SAVE_INTERVAL = 20;
const SCREEN_FAMILY = "bvp_screen";

function defaultState() {
  return { videoId: IMAGE_VIDEOS[0]?.id ?? null, method: IMAGE_VIDEOS[0]?.methods?.[0] ?? "a", entityId: null,
           dimension: "minecraft:overworld", location: null, rotation: 0, mode: STOPPED, playTicks: 0, pausedFrame: 0 };
}

function loadState() {
  try {
    const saved = JSON.parse(world.getDynamicProperty(STATE_KEY) ?? "null");
    if (saved && typeof saved === "object") return { ...defaultState(), ...saved };
  } catch (e) { /* 壊れた値は使わない */ }
  return defaultState();
}

function saveState() {
  world.setDynamicProperty(STATE_KEY, JSON.stringify(state));
}

function tr(key, ...args) {
  const message = { translate: `bvpimg.${key}` };
  if (args.length > 0) message.with = { rawtext: args.map((a) => ({ text: String(a) })) };
  return { rawtext: [message] };
}

function notify(key, ...args) {
  world.sendMessage(tr(key, ...args));
}

function currentVideo() {
  return VIDEOS.get(state.videoId);
}

function entityType(video, method) {
  return `${NAMESPACE}:screen_${video.id}_${method}`;
}

/** 今のコマ (再生中は時計から、それ以外は止めたコマ)。範囲の外にはしない。 */
function currentFrame(video = currentVideo()) {
  if (!video) return 0;
  if (state.mode !== PLAYING) return clampFrame(video, state.pausedFrame);
  const elapsed = state.playTicks / TICKS_PER_SECOND;
  return clampFrame(video, Math.floor(elapsed * video.fps));
}

function clampFrame(video, frame) {
  return Math.max(0, Math.min(video.frame_count - 1, Math.floor(frame)));
}

function screenEntity() {
  if (!state.entityId) return undefined;
  try {
    const entity = world.getEntity(state.entityId);
    return entity?.isValid ? entity : undefined;
  } catch (e) {
    return undefined;
  }
}

/**
 * スクリーンのプロパティを書き直す。エンティティが読み込まれていない (遠くにいる) ときは書けないので false を返す。
 * プロパティはエンティティに保存されるので、次に読み込まれたときにクライアントは最後に書いたコマから進め直す。
 */
function syncScreen() {
  const video = currentVideo();
  const entity = screenEntity();
  if (!video || !entity) {
    dirty = true;
    return false;
  }
  try {
    // seq はエンティティに残っている値から進める (スクリプトを読み込み直しても、クライアントが見た値に戻らない)
    const seq = ((Number(entity.getProperty(PROPERTY.seq)) || 0) + 1) % SEQ_MODULO;
    // スクリプトからの書き込みは範囲の外だと例外になる (イベントの set_property と違って丸められない) ので、先に収める
    entity.setProperty(PROPERTY.state, state.mode);
    entity.setProperty(PROPERTY.frame, currentFrame(video));
    entity.setProperty(PROPERTY.seq, seq);
  } catch (e) {
    console.warn(`[${NAMESPACE}] could not update the screen: ${e}`);
    dirty = true;
    return false;
  }
  dirty = false;
  ticksSinceSync = 0;
  return true;
}

function dimensionOf(id) {
  try {
    return world.getDimension(id);
  } catch (e) {
    return world.getDimension("minecraft:overworld");
  }
}

function removeScreen() {
  const entity = screenEntity();
  if (entity) {
    try {
      entity.remove();
    } catch (e) { /* もう無い */ }
  }
  state.entityId = null;
}

/**
 * このパックのスクリーン (family bvp_screen) のうち、今のスクリーン以外を消す。遠くで置き直したときなど、読み込まれて
 * いなくて消せなかった古いスクリーンは、読み込まれたとき (entityLoad) にここで消える。
 */
function removeStrayScreens(dimension) {
  try {
    for (const entity of dimension.getEntities({ families: [SCREEN_FAMILY] })) {
      if (entity.id !== state.entityId) entity.remove();
    }
  } catch (e) { /* 読めない */ }
}

/** 保存した位置と向きに、今の動画と方式のスクリーンを置く。 */
function spawnScreen() {
  const video = currentVideo();
  if (!video || !state.location) return false;
  removeScreen();
  try {
    const entity = dimensionOf(state.dimension).spawnEntity(entityType(video, state.method), state.location);
    entity.setRotation({ x: 0, y: state.rotation });
    state.entityId = entity.id;
    removeStrayScreens(entity.dimension);
  } catch (e) {
    console.warn(`[${NAMESPACE}] could not place the screen: ${e}`);
    return false;
  }
  saveState();
  // 生まれたエンティティのプロパティは次の tick から書ける
  system.run(() => syncScreen());
  return true;
}

// --- 音声 ---

function soundId(video, chunk) {
  return `${NAMESPACE}.img.${video.id}.chunk_${chunk}`;
}

function stopAudio(video = currentVideo()) {
  if (!video || playedChunk < 0) return;
  try {
    world.getDimension("minecraft:overworld").runCommand(`stopsound @a ${soundId(video, playedChunk)}`);
  } catch (e) { /* 鳴っていない */ }
  playedChunk = -1;
}

/**
 * 再生中、チャンクの境目で全員に次のチャンクを鳴らす。途中から鳴らす API が無いので、一時停止・シーク・開き直しの後や、
 * 途中で入ってきた人には、次の境目まで音が出ない (チャンクを短くしているのはこのため。既定 4 秒)。
 */
function updateAudio(video) {
  if (!video.audio_chunks) return;
  const seconds = state.playTicks / TICKS_PER_SECOND;
  const chunk = Math.floor(seconds / video.audio_chunk_seconds);
  if (chunk === playedChunk || chunk < 0 || chunk >= video.audio_chunks) return;
  if (seconds - chunk * video.audio_chunk_seconds > AUDIO_START_WINDOW) return;
  stopAudio(video);
  for (const player of world.getAllPlayers()) {
    try {
      player.playSound(soundId(video, chunk));
    } catch (e) { /* その人には鳴らせない */ }
  }
  playedChunk = chunk;
}

// --- 再生の操作 ---

function setup(player, args) {
  const words = args.split(/\s+/).filter(Boolean);
  for (const word of words) {
    if (VIDEOS.has(word)) state.videoId = word;
    else if (METHODS.includes(word.toLowerCase())) state.method = word.toLowerCase();
    else {
      notify("video_not_found", word, NAMESPACE);
      return;
    }
  }
  const video = currentVideo();
  if (!video || !player) return;
  if (!video.methods.includes(state.method)) state.method = video.methods[0];
  // プレイヤーの前、スクリーンの幅ほど離れた所に、こちらを向けて立てる
  const view = player.getViewDirection();
  const flat = Math.hypot(view.x, view.z) || 1;
  const distance = Math.max(4, video.screen_blocks[0]);
  const at = player.location;
  state.location = { x: at.x + (view.x / flat) * distance, y: at.y, z: at.z + (view.z / flat) * distance };
  state.dimension = player.dimension.id;
  state.rotation = player.getRotation().y + 180;
  state.mode = STOPPED;
  state.pausedFrame = 0;
  stopAudio();
  if (spawnScreen()) notify("setup_done", video.id, state.method.toUpperCase(), NAMESPACE);
}

function start() {
  const video = currentVideo();
  if (!video || !screenEntity()) {
    notify("not_setup", NAMESPACE);
    return;
  }
  stopAudio(video);
  state.mode = PLAYING;
  state.playTicks = 0;
  saveState();
  syncScreen();
  updateAudio(video);
  notify("playing", video.id);
}

function pause() {
  if (state.mode !== PLAYING) return;
  state.pausedFrame = currentFrame();
  state.mode = PAUSED;
  stopAudio();
  saveState();
  syncScreen();
  notify("paused", state.pausedFrame);
}

function resume() {
  const video = currentVideo();
  if (!video || state.mode !== PAUSED) return;
  // 止めたコマを今見せていることにして、時計の起点をずらす
  state.playTicks = Math.round((state.pausedFrame / video.fps) * TICKS_PER_SECOND);
  state.mode = PLAYING;
  saveState();
  syncScreen();
  notify("resumed");
}

function stop() {
  stopAudio();
  state.mode = STOPPED;
  state.pausedFrame = 0;
  saveState();
  syncScreen();
  notify("stopped");
}

function seek(args) {
  const video = currentVideo();
  const seconds = Number(args.trim());
  if (!video || !Number.isFinite(seconds)) return;
  const frame = clampFrame(video, seconds * video.fps);
  stopAudio(video);
  if (state.mode === PLAYING) {
    state.playTicks = Math.round((frame / video.fps) * TICKS_PER_SECOND);
  } else {
    state.pausedFrame = frame;
    if (state.mode === STOPPED) state.mode = PAUSED;
  }
  saveState();
  syncScreen();
  notify("seek_done", (frame / video.fps).toFixed(1));
}

/** 方式 A と C を、同じ場所・同じ再生位置のまま入れ替える (見比べ用)。 */
function setMethod(args) {
  const method = args.trim().toLowerCase();
  const video = currentVideo();
  if (!video || !video.methods.includes(method)) {
    notify("video_not_found", method, NAMESPACE);
    return;
  }
  if (state.entityId && !screenEntity()) {
    // 古いスクリーンが読み込まれていないと消せないので、近くで切り替えてもらう
    notify("not_setup", NAMESPACE);
    return;
  }
  state.method = method;
  if (state.location) spawnScreen();
  notify("method_set", method.toUpperCase());
}

function list() {
  for (const video of IMAGE_VIDEOS) {
    notify("list_item", video.id, video.title, video.frame_count, video.fps);
  }
}

function runAction(action, args, player) {
  if (!loaded) {
    state = loadState();
    loaded = true;
  }
  switch (action) {
    case "setup": setup(player, args); break;
    case "start": start(); break;
    case "pause": pause(); break;
    case "resume": resume(); break;
    case "stop": stop(); break;
    case "seek": seek(args); break;
    case "method": setMethod(args); break;
    case "list": list(); break;
    default: break;
  }
}

// --- 毎 tick ---

system.runInterval(() => {
  if (!loaded) return;
  const video = currentVideo();
  if (dirty && state.mode !== PLAYING) syncScreen();
  if (!video || state.mode !== PLAYING) return;
  state.playTicks++;
  if (state.playTicks % SAVE_INTERVAL === 0) saveState();
  if (state.playTicks / TICKS_PER_SECOND >= video.frame_count / video.fps) {
    // 最後のコマで止める (もう一度 start で最初から)
    state.pausedFrame = video.frame_count - 1;
    state.mode = PAUSED;
    stopAudio(video);
    saveState();
    syncScreen();
    return;
  }
  updateAudio(video);
  ticksSinceSync++;
  // エンティティが読み込まれていない間は書けないので、次の tick でもう一度試す (書けたら ticksSinceSync が 0 に戻る)
  if (ticksSinceSync >= SYNC_INTERVAL || dirty) syncScreen();
}, 1);

// ワールドを開き直した直後: 再生中なら続きのコマを書き直す (音声は次のチャンクの境目から)
world.afterEvents.worldLoad.subscribe(() => {
  state = loadState();
  loaded = true;
  if (state.mode === PLAYING && currentVideo()) {
    system.runTimeout(() => {
      syncScreen();
      notify("reload_resumed", currentFrame());
    }, TICKS_PER_SECOND);
  }
});

// 読み込まれたスクリーン: 古いものは消し、今のものに書けなかった変更があれば書く
world.afterEvents.entityLoad.subscribe((event) => {
  const entity = event.entity;
  if (!loaded || !entity?.isValid || !entity.typeId.startsWith(`${NAMESPACE}:screen_`)) return;
  if (entity.id !== state.entityId) {
    try {
      entity.remove();
    } catch (e) { /* もう無い */ }
  } else {
    syncScreen();
  }
});

// --- コマンド ---

system.afterEvents.scriptEventReceive.subscribe((event) => {
  const prefix = `${NAMESPACE}:`;
  if (!event.id.startsWith(prefix)) return;
  runAction(event.id.slice(prefix.length), event.message ?? "",
    event.sourceEntity?.typeId === "minecraft:player" ? event.sourceEntity : undefined);
});

const COMMANDS = [
  ["setup", "Place the image mode screen in front of you (options: video ID, a or c)", true],
  ["start", "Play from the start", false],
  ["pause", "Pause", false],
  ["resume", "Resume", false],
  ["stop", "Stop and hide the screen", false],
  ["seek", "Jump to a time in seconds", true],
  ["method", "Switch the frame method (a or c)", true],
  ["list", "List the videos", false],
];

system.beforeEvents.startup.subscribe((event) => {
  try {
    for (const [action, description, takesText] of COMMANDS) {
      event.customCommandRegistry.registerCommand({
        name: `${NAMESPACE}:${action}`, description, permissionLevel: CommandPermissionLevel.GameDirectors,
        cheatsRequired: false,
        optionalParameters: takesText ? [{ name: "options", type: CustomCommandParamType.String }] : [],
      }, (origin, value) => {
        // コマンドの処理の中ではワールドを変えられないので、次の tick で行う
        const player = origin.sourceEntity?.typeId === "minecraft:player" ? origin.sourceEntity : undefined;
        system.run(() => runAction(action, value === undefined ? "" : String(value), player));
        return { status: CustomCommandStatus.Success };
      });
    }
  } catch (e) {
    console.warn(`[${NAMESPACE}] custom commands were not registered: ${e}`);
  }
});
