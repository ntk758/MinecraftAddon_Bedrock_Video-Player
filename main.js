/**
 * Block Video Player - Bedrock Script API 再生スクリプト
 * (v3: Object-Oriented Multi-Screen & Adaptive Palette Support)
 */

import { world, system, BlockPermutation } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { VIDEOS, VIDEO_LIST } from "./videos.js";
import {
  decodeUTF16BinaryToBytes,
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
// 何フレーム先の GOP を先読みするか
const GOP_PREFETCH_FRAMES = 10;

const BLOCK_ID_ALIASES = {
  "minecraft:terracotta": "minecraft:hardened_clay",
};

let mainIntervalId = null;
const activePlayers = new Map(); // key: "x,y,z"

let globalSelectedVideoId = VIDEO_LIST.length > 0 ? VIDEO_LIST[0].id : null;

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
    warnOnce(`resolve:${blockId}`, `ブロック ${blockId} を解決できないため dirt で代用します: ${e}`);
    return BlockPermutation.resolve("minecraft:dirt");
  }
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
  }

  ensureDecodedData() {
    if (!this.videoData) return false;
    if (this.decodedVideoId === this.videoId && (this.decodedIndex || this.videoData.frames)) return true;

    if (this.videoData.format === "varint_rle_v4" && this.videoData.chunks && this.videoData.index) {
      this.decodedIndex = parseFrameIndexV4(decodeUTF16BinaryToBytes(this.videoData.index));
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
      warnOnce("setBlock", `ブロック設置に失敗しました (${failures} 件)。スクリーンが読み込み範囲外の可能性があります`);
    }
  }

  *applyFrameJob(frameIndex) {
    if (!this.videoData) return;
    if (!this.ensureDecodedData()) return;
    this.initPaletteCache();
    const width = this.videoData.width;

    if (this.videoData.format === "varint_rle_v4" && this.decodedIndex) {
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
      yield* this.applyBinarySlice(width, gopBytes, entry.offset, entry.offset + entry.length);
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
            warnOnce(`audio:${trackId}`, `音声 ${trackId} を再生できませんでした: ${e2}`);
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
        world.sendMessage(`§a[${EVENT_NAMESPACE}] 再生終了 (${this.anchor.x},${this.anchor.y},${this.anchor.z})`);
        return;
      }
      // 動画の fps に合わせて、表示時刻が来るまで次のフレームを始めない
      if (this.currentFrame * this.ticksPerFrame >= this.elapsedTicks) return;
      this.frameIterator = this.applyFrameJob(this.currentFrame);
    }

    for (let yc = 0; yc < MAX_YIELDS_PER_TICK; yc++) {
      const { done } = this.frameIterator.next();
      if (done) {
        this.syncAudioForFrame(this.currentFrame);
        this.currentFrame++;
        this.frameIterator = null;
        break;
      }
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
    if (!this.videoData) return;

    this.initPaletteCache();
    const clearPermutation = this.videoData.adaptive_palette ? this.paletteCache?.[0]?.[0] : this.paletteCache?.[0];
    if (!clearPermutation) return;

    // 大きなスクリーンを 1 tick で塗るとウォッチドッグに止められるため runJob で分割する
    const self = this;
    const { width, height } = this.videoData;
    this.currentJobId = system.runJob((function* () {
      const loc = { x: 0, y: self.anchor.y, z: 0 };
      let operations = 0;
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          loc.x = self.anchor.x + x;
          loc.z = self.anchor.z + y;
          try {
            self.dimension.setBlockPermutation(loc, clearPermutation);
          } catch (e) {
            warnOnce("clear", `盤面クリア中にブロック設置に失敗しました: ${e}`);
          }
          if (++operations >= BLOCKS_PER_YIELD) {
            operations = 0;
            yield;
          }
        }
      }
      self.currentJobId = null;
      world.sendMessage(`§a[${EVENT_NAMESPACE}] 停止・盤面クリア完了`);
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

    world.sendMessage(`§e[${EVENT_NAMESPACE}] フレーム ${targetFrame} へシーク中...`);

    const self = this;
    this.currentJobId = system.runJob((function* () {
      for (let f = startFrame; f <= targetFrame; f++) {
        yield* self.applyFrameJob(f);
      }
      // targetFrame まで描画済みなので、再開時は次のフレームから
      self.currentFrame = targetFrame + 1;
      self.currentJobId = null;
      world.sendMessage(`§a[${EVENT_NAMESPACE}] シーク完了 (一時停止中) — リモコンの ▶ で再開します`);
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

  const toX = anchor.x + videoData.width - 1;
  const toZ = anchor.z + videoData.height - 1;
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
    world.sendMessage(`§c[${EVENT_NAMESPACE}] 動画が選択されていません`);
    return;
  }
  const videoData = VIDEOS[globalSelectedVideoId];
  const loc = player.location;
  const anchor = {
    x: Math.floor(loc.x),
    y: Math.floor(loc.y),
    z: Math.floor(loc.z) - videoData.height,
  };
  
  if (!ensureTickingArea(player.dimension, anchor, videoData)) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] tickingareaの設定に失敗しました。チート設定を確認してください`);
    return;
  }
  
  const keyStr = getAnchorKeyStr(anchor);
  const vp = new VideoPlayer(anchor, player.dimension, globalSelectedVideoId);
  activePlayers.set(keyStr, vp);
  world.setDynamicProperty(ANCHOR_KEY, anchor); // For single remote control backwards compatibility
  world.setDynamicProperty(ANCHOR_DIMENSION_KEY, player.dimension.id);
  startMainLoop();

  world.sendMessage(`§a[${EVENT_NAMESPACE}] セットアップ完了。リモコン(コンパス)を使用するか /scriptevent ${EVENT_PREFIX}start で再生します`);
}

function getPlaybackDimension(fallbackDimension) {
  const dimensionId = world.getDynamicProperty(ANCHOR_DIMENSION_KEY);
  if (typeof dimensionId === "string") {
    try {
      return world.getDimension(dimensionId);
    } catch (e) {
      warnOnce(`dimension:${dimensionId}`, `ディメンション ${dimensionId} を取得できません: ${e}`);
    }
  }
  return fallbackDimension;
}

function startPlayback(dimension) {
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  if (!anchorLoc) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] 原点が見つかりません。先に /scriptevent ${EVENT_PREFIX}setup を実行してください`);
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
  world.sendMessage(`§a[${EVENT_NAMESPACE}] 読み込み完了後に再生します`);
}

function resumeOrStartPlayback(dimension) {
  const vp = getActivePlayer();
  if (vp && vp.canResume() && (!globalSelectedVideoId || vp.videoId === globalSelectedVideoId)) {
    vp.resume();
    world.sendMessage(`${MESSAGE_PREFIX} §a再生を再開しました`);
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
      world.sendMessage(`§e[${EVENT_NAMESPACE}] 盤面をクリアしています...`);
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
  
  const statusStr = running ? "§a再生中" : "§c停止中";
  const titleStr = currentVideoId ? currentVideoId : "未選択";
  const currentFrame = vp ? vp.currentFrame : 0;
  const masterVolume = vp ? vp.masterVolume : 1.0;
  
  const currentSec = videoData ? Math.floor(frameToSeconds(videoData, currentFrame)) : 0;
  const totalSec = videoData ? Math.floor(frameToSeconds(videoData, videoData.frame_count)) : 0;

  const form = new ActionFormData()
    .title("🎬 動画プレイヤー リモコン")
    .body(`【ステータス】: ${statusStr}\n【選択中】: §b${titleStr}\n【再生位置】: ${currentSec}s / ${totalSec}s (Frame: ${currentFrame})\n【音量】: ${Math.round(masterVolume * 100)}%`)
    .button(running ? "⏸ 一時停止" : "▶ 再生 / 再開", "textures/items/emerald")
    .button("⏹ 停止 ＆ クリア", "textures/blocks/redstone_block")
    .button("⏭ 次の動画", "textures/items/paper")
    .button("⏮ 前の動画", "textures/items/paper")
    .button("🔊 音量設定", "textures/items/repeater")
    .button("⏩ シーク (時間移動)", "textures/items/clock")
    .button("📜 動画リストから選択", "textures/items/book_portfolio");

  form.show(player).then((response) => {
    if (response.canceled) return;
    const selection = response.selection;
    const dimension = player.dimension;

    switch (selection) {
      case 0:
        if (vp && vp.running) {
          vp.stopPlayback();
          world.sendMessage(`${MESSAGE_PREFIX} §e一時停止しました`);
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
  world.sendMessage(`${MESSAGE_PREFIX} §a動画 '${globalSelectedVideoId}' を次回から再生します。またはsetupし直してください。`);
  showRemoteControlGUI(player);
}

function showVolumeGUI(player) {
  const vp = getActivePlayer();
  const mv = vp ? vp.masterVolume : 1.0;
  const form = new ModalFormData()
    .title("🔊 音量設定")
    .slider("マスター音量 (%)", 0, 100, 10, Math.round(mv * 100));

  form.show(player).then((response) => {
    if (response.canceled) return;
    if (vp) {
      vp.masterVolume = response.formValues[0] / 100.0;
      vp.currentAudioChunk = -1;
      if (vp.running) vp.syncAudioForFrame(vp.currentFrame);
      world.sendMessage(`${MESSAGE_PREFIX} §a音量を ${Math.round(vp.masterVolume * 100)}% に設定しました`);
    }
  });
}

function showSeekGUI(player) {
  const vp = getActivePlayer();
  if (!vp || !vp.videoData) return;
  const maxSec = Math.floor(frameToSeconds(vp.videoData, vp.videoData.frame_count));
  const currentSec = Math.floor(frameToSeconds(vp.videoData, vp.currentFrame));

  const form = new ModalFormData()
    .title("⏩ シーク (時間ジャンプ)")
    .slider("再生位置 (秒)", 0, Math.max(1, maxSec), 1, currentSec);

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
  const form = new ActionFormData().title("📜 動画ライブラリ").body("再生する動画タイトルを選択してください:");
  for (const v of VIDEO_LIST) {
    const isSel = v.id === cvId ? " §a[選択中]" : "";
    form.button(`${v.title}${isSel}\n${v.frame_count} frames (${v.width}x${v.height})`);
  }

  form.show(player).then((response) => {
    if (response.canceled) return;
    const selectedVideo = VIDEO_LIST[response.selection];
    if (selectedVideo) {
      globalSelectedVideoId = selectedVideo.id;
      world.sendMessage(`${MESSAGE_PREFIX} §a動画 '${selectedVideo.id}' を選択しました (setupし直すかstartすると切り替わります)`);
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
        world.sendMessage(`§c[${EVENT_NAMESPACE}] setupはプレイヤーから実行してください`);
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
        world.sendMessage(`${MESSAGE_PREFIX} 動画が登録されていません`);
      } else {
        world.sendMessage(`${MESSAGE_PREFIX} §e収録動画一覧:`);
        for (const v of VIDEO_LIST) {
          const selected = v.id === globalSelectedVideoId ? " §a[選択中]" : "";
          world.sendMessage(`  §f- §b${v.id}§f: ${v.title} (${v.frame_count} frames, ${v.width}x${v.height})${selected}`);
        }
        world.sendMessage(`${MESSAGE_PREFIX} §f再生: /scriptevent ${EVENT_PREFIX}play <動画ID>`);
      }
      break;
    case "play":
      const videoId = event.message?.trim();
      if (!videoId) {
        startPlayback(dimension);
      } else {
        if (VIDEOS[videoId]) {
          globalSelectedVideoId = videoId;
          world.sendMessage(`${MESSAGE_PREFIX} §a動画 '${videoId}' を選択しました`);
          startPlayback(dimension);
        } else {
          world.sendMessage(`§c${MESSAGE_PREFIX} 動画 '${videoId}' が見つかりません。/scriptevent ${EVENT_PREFIX}list で一覧を確認してください`);
        }
      }
      break;
    default:
      break;
  }
});
