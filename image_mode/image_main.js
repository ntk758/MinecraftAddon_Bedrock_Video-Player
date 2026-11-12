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
// 途中で入ってきた人に今のチャンクを鳴らすのは、チャンクの頭からこの秒数以内だけ (ブロックモードと同じ)
const LATE_JOIN_SECONDS = 1;
// 画面を壁に置くとき、壁の面から少し浮かせる (ブロックの面と重なってちらつかないように)
const WALL_OFFSET = 0.02;
// 壁に置くとき、見ているブロックを探す距離
const WALL_REACH = 32;
// 4 方向のヨー (エンティティの正面がこの向き): south +z = 0、west -x = 90、north -z = 180、east +x = -90
const FACE_YAW = { South: 0, West: 90, North: 180, East: -90 };
const FACE_NORMAL = { South: [0, 1], West: [-1, 0], North: [0, -1], East: [1, 0] };

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
           dimension: "minecraft:overworld", location: null, rotation: 0, mode: STOPPED, playTicks: 0, pausedFrame: 0,
           volume: 100 };
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
  for (const player of world.getAllPlayers()) playChunkFor(player, video, chunk);
  playedChunk = chunk;
}

function playChunkFor(player, video, chunk) {
  if (state.volume <= 0) return;
  try {
    player.playSound(soundId(video, chunk), { volume: state.volume / 100 });
  } catch (e) { /* その人には鳴らせない */ }
}

/** 途中で入ってきた人: 今のチャンクが始まって LATE_JOIN_SECONDS 以内なら鳴らす (それより後は次の境目から全員と一緒)。 */
function playForLateJoiner(player) {
  const video = currentVideo();
  if (!video || state.mode !== PLAYING || !video.audio_chunks || playedChunk < 0) return;
  const seconds = state.playTicks / TICKS_PER_SECOND;
  if (seconds - playedChunk * video.audio_chunk_seconds > LATE_JOIN_SECONDS) return;
  playChunkFor(player, video, playedChunk);
}

// --- 画面の置き場所 ---

/** プレイヤーの前、スクリーンの幅ほど離れた所に、こちらを向けて立てる。face なら向きを東西南北にそろえる。 */
function placeInFront(player, video, face) {
  const view = player.getViewDirection();
  const flat = Math.hypot(view.x, view.z) || 1;
  const distance = Math.max(4, video.screen_blocks[0]);
  const at = player.location;
  let rotation = player.getRotation().y + 180;
  if (face) rotation = Math.round(rotation / 90) * 90;
  return { location: { x: at.x + (view.x / flat) * distance, y: at.y, z: at.z + (view.z / flat) * distance }, rotation };
}

/** 見ているブロックの横の面に、面から少し浮かせて貼る (下端はそのブロックの下の面)。横の面でなければ null。 */
function placeOnWall(player) {
  let hit;
  try {
    hit = player.getBlockFromViewDirection({ maxDistance: WALL_REACH });
  } catch (e) {
    return null;
  }
  const normal = hit && FACE_NORMAL[hit.face];
  if (!normal) return null;
  const b = hit.block.location;
  const location = {
    x: b.x + 0.5 + normal[0] * (0.5 + WALL_OFFSET),
    y: b.y,
    z: b.z + 0.5 + normal[1] * (0.5 + WALL_OFFSET),
  };
  return { location, rotation: FACE_YAW[hit.face] };
}

// --- 再生の操作 ---

function setup(player, args) {
  const words = args.split(/\s+/).filter(Boolean);
  const options = { face: false, wall: false, align: false };
  for (const word of words) {
    const lower = word.toLowerCase();
    if (VIDEOS.has(word)) state.videoId = word;
    else if (METHODS.includes(lower)) state.method = lower;
    else if (lower in options) options[lower] = true;
    else {
      notify("video_not_found", word, NAMESPACE);
      return;
    }
  }
  const video = currentVideo();
  if (!video || !player) return;
  if (!video.methods.includes(state.method)) state.method = video.methods[0];
  const placed = options.wall ? placeOnWall(player) : placeInFront(player, video, options.face);
  if (!placed) {
    notify("wall_not_found");
    return;
  }
  state.location = placed.location;
  state.rotation = placed.rotation;
  // ブロックの格子にそろえる (横はブロックの中心、下端はブロックの面)。壁に貼るときは、もともとそろっている
  if (options.align && !options.wall) {
    const at = state.location;
    state.location = { x: Math.floor(at.x) + 0.5, y: Math.floor(at.y), z: Math.floor(at.z) + 0.5 };
  }
  state.dimension = player.dimension.id;
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
  const text = String(args).trim();
  const seconds = Number(text);
  if (!video) return;
  if (text === "" || !Number.isFinite(seconds)) {
    notify("seek_usage");
    return;
  }
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

/** 動画を選んで最初から再生する (画面はそのままの場所で、その動画のものに置き換える)。ID が無ければ start と同じ。 */
function play(args) {
  const id = args.trim();
  if (!id) {
    start();
    return;
  }
  if (!VIDEOS.has(id)) {
    notify("video_not_found", id, NAMESPACE);
    return;
  }
  if (!state.location || (state.entityId && !screenEntity())) {
    state.videoId = id;
    notify("not_setup", NAMESPACE);
    return;
  }
  stopAudio();
  state.videoId = id;
  const video = currentVideo();
  if (!video.methods.includes(state.method)) state.method = video.methods[0];
  state.mode = STOPPED;
  state.pausedFrame = 0;
  if (!spawnScreen()) return;
  start();
}

function setVolume(args) {
  const text = String(args).trim();
  const value = Number(text);
  if (text === "" || !Number.isFinite(value)) {
    notify("volume_usage");
    return;
  }
  state.volume = Math.max(0, Math.min(100, Math.round(value)));
  if (state.volume === 0) stopAudio();
  saveState();
  notify("volume_set", state.volume);
}

function showStats() {
  const video = currentVideo();
  if (!video) {
    notify("not_setup", NAMESPACE);
    return;
  }
  const modeKey = state.mode === PLAYING ? "state_playing" : state.mode === PAUSED ? "state_paused" : "state_stopped";
  const frame = currentFrame(video);
  world.sendMessage({ rawtext: [{ translate: "bvpimg.stats", with: { rawtext: [
    { text: video.id }, { translate: `bvpimg.${modeKey}` }, { text: String(frame) }, { text: String(video.frame_count) },
    { text: (frame / video.fps).toFixed(1) }, { text: String(video.fps) }, { text: `${video.width}x${video.height}` },
    { text: String(state.volume) }, { translate: screenEntity() ? "bvpimg.screen_loaded" : "bvpimg.screen_unloaded" },
  ] } }] });
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
    case "play": play(args); break;
    case "volume": setVolume(args); break;
    case "stats": showStats(); break;
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

// 途中で入ってきた人には、間に合えば今の音を鳴らす
world.afterEvents.playerSpawn?.subscribe((event) => {
  if (!loaded || !event.initialSpawn) return;
  playForLateJoiner(event.player);
});

// --- コマンド ---

system.afterEvents.scriptEventReceive.subscribe((event) => {
  const prefix = `${NAMESPACE}:`;
  if (!event.id.startsWith(prefix)) return;
  runAction(event.id.slice(prefix.length), event.message ?? "",
    event.sourceEntity?.typeId === "minecraft:player" ? event.sourceEntity : undefined);
});

// [操作, 説明, 引数の型]。引数の型は "text" (文字)・"number" (秒など、小数も可)・"int"・"video" (動画 ID の候補)・null
// 数を "text" (String) で登録すると、/<ns>:seek 7 のように数だけを入れたときに構文エラーになる (実機の確認、2026-11-12)
const COMMANDS = [
  ["setup", "Place the screen (options: video ID, face, wall, align)", "text"],
  ["start", "Play from the start", null],
  ["play", "Play a video from the start (by video ID)", "video"],
  ["pause", "Pause", null],
  ["resume", "Resume", null],
  ["stop", "Stop and hide the screen", null],
  ["seek", "Jump to a time in seconds", "number"],
  ["volume", "Set the volume (0-100)", "int"],
  ["stats", "Show the playback status", null],
  ["method", "Switch the frame method (a or c)", "text"],
  ["list", "List the videos", null],
];

function parameterFor(kind, videoEnum) {
  if (kind === "text") return { name: "options", type: CustomCommandParamType.String };
  if (kind === "number") return { name: "seconds", type: CustomCommandParamType.Float };
  if (kind === "int") return { name: "volume", type: CustomCommandParamType.Integer };
  if (kind === "video" && videoEnum) return { name: videoEnum, type: CustomCommandParamType.Enum };
  return null;
}

system.beforeEvents.startup.subscribe((event) => {
  try {
    const registry = event.customCommandRegistry;
    let videoEnum = null;
    if (IMAGE_VIDEOS.length > 0) {
      videoEnum = `${NAMESPACE}:video`;
      registry.registerEnum(videoEnum, IMAGE_VIDEOS.map((v) => v.id));
    }
    for (const [action, description, kind] of COMMANDS) {
      const parameter = parameterFor(kind, videoEnum);
      registry.registerCommand({
        name: `${NAMESPACE}:${action}`, description, permissionLevel: CommandPermissionLevel.GameDirectors,
        cheatsRequired: false,
        optionalParameters: parameter ? [parameter] : [],
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
