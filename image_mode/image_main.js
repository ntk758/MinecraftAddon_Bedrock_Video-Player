/**
 * Block & Image Video Player - 画像モード (テクスチャ再生方式) の再生制御
 *
 * 映像の各コマはリソースパックのテクスチャ (アトラスのシート) に焼き込んであり、コマの切り替えはクライアントの Molang が
 * 自分の時計 (q.life_time) で行う (image_mode/SPEC.md)。このスクリプトは、どのコマから進めるか (frame) と、その書き直しの
 * 通し番号 (seq) と状態 (state) をスクリーンのエンティティのプロパティ (client_sync) に書き、音声のチャンクを鳴らすだけ。
 *
 * 時計は、このスクリプトが再生中に数える実時間 (playMs。tick ごとに Date.now() の差を足す。1 tick で足すのは MAX_TICK_MS まで)。
 * 以前は tick の数 (playTicks) で数えていたので、TPS が 20 を下回るとサーバーの位置が実時間より遅れ、実時間で進むクライアントの
 * コマが合わせ直しのたびに後ろへ飛び、音声のチャンクの間が空いた (docs/research/roadmap_review_2026-11.md の 1 章)。
 * world.getAbsoluteTime() は昼夜の時刻で、眠る・/time set で飛び、昼夜の進行を止めると止まるので使わない。状態は world の dynamic property に JSON で保存する (再生中は 1 秒ごと)。プロパティの書き込みは次の tick に反映され、最大 20 回/秒なので、30 fps のコマを毎回
 * 書くことはできない。代わりに 1 秒ごとに「今のコマ」を書き直して、クライアントの時計のずれを戻す。
 */
import { world, system, CommandPermissionLevel, CustomCommandParamType, CustomCommandStatus } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { IMAGE_VIDEOS, NAMESPACE } from "./image_videos.js";

const TICKS_PER_SECOND = 20;
const MS_PER_TICK = 1000 / TICKS_PER_SECOND;
// 1 tick で再生位置を進める実時間の上限 (ms)。シングルプレイのポーズ画面やワールドの読み込みで tick が止まっていた時間は数えない
// (TPS 4 未満では、そのぶん実時間より遅れる)
const MAX_TICK_MS = 250;
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
// リモコンを開くアイテム。ブロックモードはコンパスなので、両方のアドオンを入れても 2 つの画面が同時に開かないよう時計にする
const REMOTE_ITEM = "minecraft:clock";
const VOLUME_STEP = 10;
// 画面を壁に置くとき、壁の面から少し浮かせる (ブロックの面と重なってちらつかないように)
const WALL_OFFSET = 0.02;
// 壁に置くとき、見ているブロックを探す距離
const WALL_REACH = 32;
// 4 方向のヨー (エンティティの正面がこの向き): south +z = 0、west -x = 90、north -z = 180、east +x = -90
const FACE_YAW = { South: 0, West: 90, North: 180, East: -90 };
const FACE_NORMAL = { South: [0, 1], West: [-1, 0], North: [0, -1], East: [1, 0] };

const VIDEOS = new Map(IMAGE_VIDEOS.map((v) => [v.id, v]));

/**
 * 保存する状態。playMs は再生した実時間 (ms。今のコマは playMs から決める)。playTicks は古い版のスクリプト向けに、
 * playMs を tick に直した値も一緒に保存する (古い版で保存したワールドは、playTicks × 50 ms から続ける)。
 * スクリプトの読み込み時 (early execution) は world を読めないので、worldLoad で読み直す。
 */
let state = defaultState();
let loaded = false;
// 最後に鳴らした音声のチャンク (メモリだけ。開き直したら次の境目から鳴らす)
let playedChunk = -1;
let ticksSinceSync = 0;
let ticksSinceSave = 0;
// 前の tick の Date.now() (メモリだけ。読み込み直した後の最初の tick は 1 tick 分だけ進める)
let lastTickAt = null;
// 再生 (start・再開・シーク) してから、上限のために再生位置に数えなかった実時間 (ms。stats で出す)
let uncountedMs = 0;
// 画面に書けなかった (読み込まれていなかった) 変更がある。読み込まれ次第、どの状態でも書き直す
let dirty = false;
const SAVE_INTERVAL = 20;
const SCREEN_FAMILY = "bvp_screen";

function defaultState() {
  return { videoId: IMAGE_VIDEOS[0]?.id ?? null, method: IMAGE_VIDEOS[0]?.methods?.[0] ?? "a", entityId: null,
           dimension: "minecraft:overworld", location: null, rotation: 0, mode: STOPPED, playMs: 0, pausedFrame: 0,
           volume: 100 };
}

function loadState() {
  try {
    const saved = JSON.parse(world.getDynamicProperty(STATE_KEY) ?? "null");
    if (saved && typeof saved === "object") {
      const loadedState = { ...defaultState(), ...saved };
      // 古い版 (tick で数えていた) で保存したワールド: 続きの位置を tick から作る
      if (typeof saved.playMs !== "number") loadedState.playMs = (Number(saved.playTicks) || 0) * MS_PER_TICK;
      delete loadedState.playTicks;
      return loadedState;
    }
  } catch (e) { /* 壊れた値は使わない */ }
  return defaultState();
}

function saveState() {
  world.setDynamicProperty(STATE_KEY, JSON.stringify({ ...state, playTicks: Math.round(state.playMs / MS_PER_TICK) }));
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
  return clampFrame(video, Math.floor((state.playMs / 1000) * video.fps));
}

/**
 * 音声のある動画で、コマを音声のチャンクの頭にそろえる。音声は途中から鳴らせないので、チャンクの頭から再生すれば
 * すぐに音が出る (そろえないと、次の境目まで最大 audio_chunk_seconds 秒無音)。DECISIONS.md の A17。
 * how は "nearest" (シーク: 近いほうの頭) か "floor" (再開: 今のチャンクの頭まで戻る)。
 * シークで近いほうの頭が動画の終わりより後 (最後のチャンクの後ろ半分) なら、そろえずに頼まれた所へ行く。
 * 前の頭へ戻すと頼んだ所から大きくずれる (7 秒の動画の 6 秒 → 4 秒)。終わりまでは無音だが、チャンクの半分より短い。
 */
function alignToChunk(video, frame, how) {
  if (!video.audio_chunks || !video.audio_chunk_seconds) return frame;
  const chunkFrames = video.audio_chunk_seconds * video.fps;
  const chunk = how === "nearest" ? Math.round(frame / chunkFrames) : Math.floor(frame / chunkFrames);
  if (how === "nearest" && chunk > video.audio_chunks - 1) return frame;
  return clampFrame(video, Math.min(chunk, video.audio_chunks - 1) * chunkFrames);
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
    // 向きは生まれるときに渡す。後から setRotation だけで向けると、頭だけが回って体 (板) は既定の向きのまま約 1 秒残り、
    // そこから 0.4 秒ほどかけて回ってきた (再生の最初の約 1.5 秒が横向きに映った: 実機 2026-11-15)。
    // BP のエンティティにも minecraft:body_rotation_always_follows_head を付けてある
    const entity = dimensionOf(state.dimension).spawnEntity(entityType(video, state.method), state.location,
                                                           { initialRotation: state.rotation });
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
  const seconds = state.playMs / 1000;
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
  const seconds = state.playMs / 1000;
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
  // ガラスケースの立体 (video.glass) は床に立てるもので、壁には貼らない (wall を付けても目の前に置く)
  const onWall = options.wall && !video.glass;
  if (options.wall && video.glass) notify("case_not_on_wall");
  const placed = onWall ? placeOnWall(player) : placeInFront(player, video, options.face);
  if (!placed) {
    notify("wall_not_found");
    return;
  }
  state.location = placed.location;
  state.rotation = placed.rotation;
  // ブロックの格子にそろえる (横はブロックの中心、下端はブロックの面)。壁に貼るときは、もともとそろっている
  if (options.align && !onWall) {
    const at = state.location;
    state.location = { x: Math.floor(at.x) + 0.5, y: Math.floor(at.y), z: Math.floor(at.z) + 0.5 };
  }
  state.dimension = player.dimension.id;
  // 置いた場所が分かるよう、最初のコマを止めたまま見せる (停止のままだと画面が見えず、壁に貼れたか分からなかった: 実機 2026-11-12)
  state.mode = PAUSED;
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
  state.playMs = 0;
  uncountedMs = 0;
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
  // 止めたコマを含むチャンクの頭から続ける (音声がすぐ鳴るように。最大 audio_chunk_seconds 秒戻る)
  state.pausedFrame = alignToChunk(video, state.pausedFrame, "floor");
  state.playMs = (state.pausedFrame / video.fps) * 1000;
  state.mode = PLAYING;
  uncountedMs = 0;
  saveState();
  syncScreen();
  updateAudio(video);
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
  // 音声のある動画は、近いほうのチャンクの頭へ (音声がすぐ鳴るように)
  const frame = alignToChunk(video, clampFrame(video, seconds * video.fps), "nearest");
  stopAudio(video);
  if (state.mode === PLAYING) {
    state.playMs = (frame / video.fps) * 1000;
  } else {
    state.pausedFrame = frame;
    if (state.mode === STOPPED) state.mode = PAUSED;
  }
  uncountedMs = 0;
  saveState();
  syncScreen();
  if (state.mode === PLAYING) updateAudio(video);
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
  // 再生してから、tick が止まっていた・遅すぎたために再生位置に数えなかった時間 (多いときは TPS が 4 を下回っている)
  if (state.mode === PLAYING) {
    world.sendMessage({ rawtext: [{ translate: "bvpimg.stats_clock", with: [(uncountedMs / 1000).toFixed(1)] }] });
  }
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
    case "remote": showRemote(player); break;
    default: break;
  }
}

// --- リモコン (ブロックモードと同じ並び。時計を使うか /<ns>:remote で開く) ---

function lines(...parts) {
  const rawtext = [];
  parts.forEach((part, i) => {
    if (i > 0) rawtext.push({ text: "\n" });
    rawtext.push(part);
  });
  return { rawtext };
}

function seconds(video, frame) {
  return Math.floor(frame / video.fps);
}

function showRemote(player) {
  if (!player) return;
  const video = currentVideo();
  const placed = Boolean(state.location);
  const statusKey = state.mode === PLAYING ? "remote.playing" : state.mode === PAUSED ? "remote.paused" : "remote.stopped";
  const frame = video ? currentFrame(video) : 0;
  const form = new ActionFormData()
    .title(tr("remote.title"))
    .body(lines(
      { translate: "bvpimg.remote.status", with: { rawtext: [tr(statusKey)] } },
      { translate: "bvpimg.remote.selected", with: { rawtext: [video ? { text: video.title } : tr("remote.none")] } },
      tr("remote.position", video ? seconds(video, frame) : 0, video ? seconds(video, video.frame_count) : 0, frame),
      // フォームの本文は % をもう一度読むので、% は %% と書く (1 つだと消えた: 実機 2026-11-13)
      tr("remote.volume", `${state.volume}%%`),
    ));
  const actions = [];
  const button = (key, icon, action) => {
    form.button(tr(key), icon);
    actions.push(action);
  };
  if (!placed) button("remote.setup_here", "textures/items/item_frame", () => setup(player, ""));
  button(state.mode === PLAYING ? "remote.pause" : "remote.play", "textures/items/emerald", () => {
    if (state.mode === PLAYING) pause();
    else if (state.mode === PAUSED && currentFrame() < (currentVideo()?.frame_count ?? 1) - 1) resume();
    else start();
  });
  button("remote.stop", "textures/blocks/redstone_block", () => stop());
  button("remote.next", "textures/items/paper", () => switchVideo(1, player));
  button("remote.prev", "textures/items/paper", () => switchVideo(-1, player));
  button("remote.volume_button", "textures/items/repeater", () => showVolume(player));
  button("remote.seek", "textures/items/clock_item", () => showSeek(player));
  button("remote.library", "textures/items/book_portfolio", () => showLibrary(player));
  if (placed) button("remote.setup_here", "textures/items/item_frame", () => setup(player, ""));
  form.show(player).then((response) => {
    if (response.canceled || response.selection === undefined) return;
    actions[response.selection]?.();
  }).catch(() => { /* 開けなかった */ });
}

/** 前後の動画へ切り替えて最初から再生し、リモコンを開き直す。 */
function switchVideo(direction, player) {
  if (IMAGE_VIDEOS.length === 0) return;
  let index = IMAGE_VIDEOS.findIndex((v) => v.id === state.videoId);
  if (index < 0) index = 0;
  const next = IMAGE_VIDEOS[(index + direction + IMAGE_VIDEOS.length) % IMAGE_VIDEOS.length];
  play(next.id);
  showRemote(player);
}

function showVolume(player) {
  const form = new ModalFormData().title(tr("volume.title"));
  form.slider(tr("volume.slider"), 0, 100, { valueStep: VOLUME_STEP, defaultValue: state.volume });
  form.show(player).then((response) => {
    if (response.canceled || !response.formValues) return;
    setVolume(String(response.formValues[0]));
  }).catch(() => { /* 開けなかった */ });
}

function showSeek(player) {
  const video = currentVideo();
  if (!video) return;
  const total = Math.max(1, seconds(video, video.frame_count));
  const form = new ModalFormData().title(tr("seek.title"));
  form.slider(tr("seek.slider"), 0, total, { valueStep: 1, defaultValue: Math.min(total, seconds(video, currentFrame(video))) });
  form.show(player).then((response) => {
    if (response.canceled || !response.formValues) return;
    seek(String(response.formValues[0]));
  }).catch(() => { /* 開けなかった */ });
}

function showLibrary(player) {
  const form = new ActionFormData().title(tr("library.title")).body(tr("library.body"));
  for (const video of IMAGE_VIDEOS) {
    const rawtext = [{ text: video.title }];
    if (video.id === state.videoId) rawtext.push({ text: " " }, tr("selected_tag"));
    rawtext.push({ text: "\n" }, tr("library.item", video.frame_count, `${video.width}x${video.height}, ${video.fps} fps`));
    form.button({ rawtext });
  }
  form.show(player).then((response) => {
    if (response.canceled || response.selection === undefined) return;
    const video = IMAGE_VIDEOS[response.selection];
    if (!video) return;
    play(video.id);
    showRemote(player);
  }).catch(() => { /* 開けなかった */ });
}

world.afterEvents.itemUse?.subscribe((event) => {
  if (event.itemStack?.typeId !== REMOTE_ITEM) return;
  if (!loaded) {
    state = loadState();
    loaded = true;
  }
  showRemote(event.source);
});

// --- 毎 tick ---

system.runInterval(() => {
  if (!loaded) return;
  // この tick までに進んだ実時間 (止まっていた分は MAX_TICK_MS まで)
  const now = Date.now();
  const passed = lastTickAt === null ? MS_PER_TICK : Math.max(now - lastTickAt, 0);
  const elapsed = Math.min(passed, MAX_TICK_MS);
  lastTickAt = now;
  const video = currentVideo();
  if (dirty && state.mode !== PLAYING) syncScreen();
  if (!video || state.mode !== PLAYING) return;
  state.playMs += elapsed;
  uncountedMs += passed - elapsed;
  if (++ticksSinceSave >= SAVE_INTERVAL) {
    ticksSinceSave = 0;
    saveState();
  }
  if (video.loop && state.playMs / 1000 >= video.frame_count / video.fps) {
    // 繰り返す動画 (角度で絵を切り替える立体。音声なし): 頭へ戻し、すぐ frame と seq を書き直す
    state.playMs %= (video.frame_count / video.fps) * 1000;
    syncScreen();
    return;
  }
  if (state.playMs / 1000 >= video.frame_count / video.fps) {
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
  // 入ってきた人のクライアントは、プロパティを見た時刻を起点に進めるので、書いてから見るまでの分だけ遅れる。
  // 今のコマを書き直して、次の定期の合わせ直し (SYNC_INTERVAL) を待たずにそろえる
  if (state.mode === PLAYING) syncScreen();
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
  ["remote", "Open the remote control (or use a clock)", null],
  ["method", "Switch the frame method (a or c)", "text"],
  ["list", "List the videos", null],
];

// String の引数は 1 語だけなので、setup face align のように並べられるよう、文字の引数は TEXT_WORDS 個まで受ける
// (1 個だけだと 2 語目が構文エラーになった: 実機 2026-11-12)
const TEXT_WORDS = 4;

function parametersFor(kind, videoEnum) {
  if (kind === "text") {
    return Array.from({ length: TEXT_WORDS }, (_, i) => ({ name: i === 0 ? "options" : `option${i + 1}`,
                                                          type: CustomCommandParamType.String }));
  }
  const parameter = parameterFor(kind, videoEnum);
  return parameter ? [parameter] : [];
}

function parameterFor(kind, videoEnum) {
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
      registry.registerCommand({
        name: `${NAMESPACE}:${action}`, description, permissionLevel: CommandPermissionLevel.GameDirectors,
        cheatsRequired: false,
        optionalParameters: parametersFor(kind, videoEnum),
      }, (origin, ...values) => {
        // コマンドの処理の中ではワールドを変えられないので、次の tick で行う
        const player = origin.sourceEntity?.typeId === "minecraft:player" ? origin.sourceEntity : undefined;
        const text = values.filter((v) => v !== undefined).map(String).join(" ");
        system.run(() => runAction(action, text, player));
        return { status: CustomCommandStatus.Success };
      });
    }
  } catch (e) {
    console.warn(`[${NAMESPACE}] custom commands were not registered: ${e}`);
  }
});
