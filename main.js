/**
 * Bad Apple Cushion Player - 統合版移植版 (v2: カラー対応)
 * (v3: Object-Oriented Multi-Screen & Adaptive Palette Support)
 */

import { world, system, BlockPermutation } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { VIDEOS, VIDEO_LIST } from "./videos.js";

const EVENT_NAMESPACE = "badapple";
const FRAME_INTERVAL_TICKS = 1;
const EVENT_PREFIX = `${EVENT_NAMESPACE}:`;
const ANCHOR_KEY = `${EVENT_NAMESPACE}:anchor`;
const ANCHOR_DIMENSION_KEY = `${EVENT_NAMESPACE}:dimension`;
const MESSAGE_PREFIX = `§b[${EVENT_NAMESPACE}]`;
const START_LOAD_DELAY_TICKS = 5;
const TICKING_AREA_NAME = "badapple_area";

const REMOTE_CONTROL_ITEM = "minecraft:compass";
const TICKS_PER_AUDIO_CHUNK = 200;

const BLOCK_ID_ALIASES = {
  "minecraft:terracotta": "minecraft:hardened_clay",
};

let mainIntervalId = null;
const activePlayers = new Map(); // key: "x,y,z"

let globalSelectedVideoId = VIDEO_LIST.length > 0 ? VIDEO_LIST[0].id : null;

function decodeUTF16BinaryToBytes(utf16Str) {
  if (!utf16Str) return new Uint8Array(0);
  if (utf16Str.startsWith("K:")) {
    utf16Str = utf16Str.slice(2);
  }
  const len = utf16Str.length;
  if (len === 0) return new Uint8Array(0);

  const padLen = utf16Str.charCodeAt(0) - 0x1000;
  const bitCount = (len - 1) * 15 - padLen;
  if (bitCount <= 0) return new Uint8Array(0);
  
  const byteLen = Math.floor(bitCount / 8);
  const bytes = new Uint8Array(byteLen);
  
  let byteIdx = 0;
  let bitBuffer = 0;
  let bitsInBuffer = 0;
  
  for (let i = 1; i < len; i++) {
    const val15 = utf16Str.charCodeAt(i) - 0x1000;
    bitBuffer = (bitBuffer << 15) | val15;
    bitsInBuffer += 15;
    
    while (bitsInBuffer >= 8) {
      bitsInBuffer -= 8;
      if (byteIdx < byteLen) {
        bytes[byteIdx++] = (bitBuffer >>> bitsInBuffer) & 0xff;
      }
    }
  }
  return bytes;
}

class VideoPlayer {
  constructor(anchor, dimension, videoId) {
    this.anchor = anchor;
    this.dimension = dimension;
    this.videoId = videoId;
    this.videoData = VIDEOS[videoId];
    
    this.currentFrame = 0;
    this.running = false;
    this.currentAudioChunk = -1;
    this.masterVolume = 1.0;
    
    this.decodedIndex = null;
    this.decodedVideoId = null;
    this.gopCache = new Map();
    this.MAX_CACHED_GOPS = 4;
    this.decodedBinary = null;
    
    this.paletteCache = null;
    this.currentGopId = -1;
    this.activePalette = null;
    
    this.frameIterator = null;
    this.startDelayTicks = 0;
    this.currentJobId = null;

    this.tempBlockLoc = { x: 0, y: 0, z: 0 };
  }

  ensureDecodedData() {
    if (!this.videoData) return false;
    if (this.decodedVideoId === this.videoId && this.decodedIndex) return true;

    if (this.videoData.format === "varint_rle_v4" && this.videoData.chunks && this.videoData.index) {
      const idxBytes = decodeUTF16BinaryToBytes(this.videoData.index);
      this.decodedIndex = [];
      let pos = 0;
      const idxLen = idxBytes.length;
      while (pos < idxLen) {
        let gopId = 0, sh0 = 0;
        while (pos < idxLen) {
          const b = idxBytes[pos++];
          gopId |= (b & 0x7f) << sh0;
          if ((b & 0x80) === 0) break;
          sh0 += 7;
        }
        let val1 = 0, sh1 = 0;
        while (pos < idxLen) {
          const b = idxBytes[pos++];
          val1 |= (b & 0x7f) << sh1;
          if ((b & 0x80) === 0) break;
          sh1 += 7;
        }
        let val2 = 0, sh2 = 0;
        while (pos < idxLen) {
          const b = idxBytes[pos++];
          val2 |= (b & 0x7f) << sh2;
          if ((b & 0x80) === 0) break;
          sh2 += 7;
        }
        this.decodedIndex.push({
          gopId: gopId,
          offset: val1 >>> 1,
          length: val2,
          isKeyframe: (val1 & 1) === 1
        });
      }
      this.decodedBinary = null;
      this.gopCache.clear();
      this.decodedVideoId = this.videoId;
      return true;
    }

    if (this.videoData.format === "varint_rle_v3" && this.videoData.binary && this.videoData.index) {
      this.decodedBinary = decodeUTF16BinaryToBytes(this.videoData.binary);
      const idxBytes = decodeUTF16BinaryToBytes(this.videoData.index);
      this.decodedIndex = [];
      let pos = 0;
      const idxLen = idxBytes.length;
      while (pos < idxLen) {
        let val1 = 0, sh1 = 0;
        while (pos < idxLen) {
          const b = idxBytes[pos++];
          val1 |= (b & 0x7f) << sh1;
          if ((b & 0x80) === 0) break;
          sh1 += 7;
        }
        let val2 = 0, sh2 = 0;
        while (pos < idxLen) {
          const b = idxBytes[pos++];
          val2 |= (b & 0x7f) << sh2;
          if ((b & 0x80) === 0) break;
          sh2 += 7;
        }
        this.decodedIndex.push({
          gopId: -1,
          offset: val1 >>> 1,
          length: val2,
          isKeyframe: (val1 & 1) === 1
        });
      }
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

  ensureGopDecoded(gopId) {
    if (this.gopCache.has(gopId)) return this.gopCache.get(gopId);
    if (!this.videoData || !this.videoData.chunks) return null;
    if (gopId < 0 || gopId >= this.videoData.chunks.length) return null;
    
    const decoded = decodeUTF16BinaryToBytes(this.videoData.chunks[gopId]);
    if (this.gopCache.size >= this.MAX_CACHED_GOPS) {
      const oldestKey = this.gopCache.keys().next().value;
      this.gopCache.delete(oldestKey);
    }
    this.gopCache.set(gopId, decoded);
    return decoded;
  }

  initPaletteCache() {
    if (!this.videoData) return;
    if (this.videoData.adaptive_palette) {
      if (!this.paletteCache) {
        this.paletteCache = this.videoData.level_blocks.map(gopPalettes => 
          gopPalettes.map(spec => {
            const blockId = BLOCK_ID_ALIASES[spec.block] ?? spec.block;
            try {
              return BlockPermutation.resolve(blockId, spec.states);
            } catch (e) {
              return BlockPermutation.resolve("minecraft:dirt");
            }
          })
        );
      }
    } else {
      if (!this.paletteCache || this.paletteCache.length !== this.videoData.level_blocks.length) {
        this.paletteCache = this.videoData.level_blocks.map((spec) => {
          const blockId = BLOCK_ID_ALIASES[spec.block] ?? spec.block;
          try {
            return BlockPermutation.resolve(blockId, spec.states);
          } catch (e) {
            return BlockPermutation.resolve("minecraft:dirt");
          }
        });
      }
    }
  }

  *applyBinarySlice(width, bytes, startOff, endOff) {
    let offset = startOff;
    let currIdx = 0;
    let operations = 0;
    const MAX_OPS_PER_TICK = 4000;

    while (offset < endOff) {
      const b = bytes[offset++];
      if ((b & 0x80) === 0) break;
    }
    while (offset < endOff) {
      const b = bytes[offset++];
      if ((b & 0x80) === 0) break;
    }

    while (offset < endOff) {
      let val = 0;
      let shift = 0;
      while (offset < endOff) {
        const b = bytes[offset++];
        val |= (b & 0x7f) << shift;
        if ((b & 0x80) === 0) break;
        shift += 7;
      }
      const delta = val >>> 13;
      const length = ((val >>> 7) & 0x3f) + 1;
      const level = val & 0x7f;
      currIdx = delta;

      const x = currIdx % width;
      const y = (currIdx / width) | 0;

      const permutation = this.activePalette ? this.activePalette[level] : null;
      if (!permutation) continue;

      const bx = this.anchor.x + x;
      const bz = this.anchor.z + y;

      try {
        if (length === 1) {
          this.tempBlockLoc.x = bx;
          this.tempBlockLoc.y = this.anchor.y;
          this.tempBlockLoc.z = bz;
          this.dimension.setBlockPermutation(this.tempBlockLoc, permutation);
          operations++;
          if (operations > MAX_OPS_PER_TICK) { yield; operations = 0; }
        } else {
          for (let i = 0; i < length; i++) {
            this.tempBlockLoc.x = bx + i;
            this.tempBlockLoc.y = this.anchor.y;
            this.tempBlockLoc.z = bz;
            this.dimension.setBlockPermutation(this.tempBlockLoc, permutation);
            operations++;
            if (operations > MAX_OPS_PER_TICK) { yield; operations = 0; }
          }
        }
      } catch (e) {}
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
      if (entry.length === 0) return;

      this.currentGopId = entry.gopId;
      this.activePalette = this.videoData.adaptive_palette ? this.paletteCache[this.currentGopId] : this.paletteCache;

      const gopBytes = this.ensureGopDecoded(entry.gopId);
      const gopSize = this.videoData.gop_size || 200;
      if (frameIndex % gopSize >= gopSize * 0.8) {
        this.ensureGopDecoded(entry.gopId + 1);
      }
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
    if (!diffData) return;
    if (typeof diffData === "string") {
      if (diffData.length === 0) return;
      let bytes;
      if (diffData.length > 0 && diffData.charCodeAt(diffData.startsWith("K:") ? 2 : 0) >= 0x1000) {
        bytes = decodeUTF16BinaryToBytes(diffData);
      } else {
        return;
      }
      yield* this.applyBinarySlice(width, bytes, 0, bytes.length);
    }
  }

  syncAudioForFrame(frameIndex) {
    if (!this.videoData || this.masterVolume <= 0) return;
    const targetChunk = Math.floor((frameIndex * FRAME_INTERVAL_TICKS) / TICKS_PER_AUDIO_CHUNK);
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
          } catch (e2) {}
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

    if (!this.frameIterator) {
      if (this.currentFrame >= this.videoData.frame_count) {
        this.stopPlayback();
        world.sendMessage(`§a[${EVENT_NAMESPACE}] 再生終了 (${this.anchor.x},${this.anchor.y},${this.anchor.z})`);
        return;
      }
      this.frameIterator = this.applyFrameJob(this.currentFrame);
    }

    const MAX_YIELDS_PER_TICK = 12;
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

  stopPlayback() {
    this.running = false;
    this.currentAudioChunk = -1;
    if (this.currentJobId !== null) {
      system.clearJob(this.currentJobId);
      this.currentJobId = null;
    }
    for (const p of world.getAllPlayers()) {
      try { p.stopMusic(); } catch (e) {}
    }
  }

  stopAndClear() {
    this.stopPlayback();
    if (!this.videoData) return;
    
    this.initPaletteCache();
    let clearPermutation = null;
    if (this.videoData.adaptive_palette && this.paletteCache && this.paletteCache[0]) {
      clearPermutation = this.paletteCache[0][0];
    } else if (this.paletteCache) {
      clearPermutation = this.paletteCache[0];
    }
    if (!clearPermutation) return;

    for (let y = 0; y < this.videoData.height; y++) {
      for (let x = 0; x < this.videoData.width; x++) {
        this.dimension.setBlockPermutation(
          { x: this.anchor.x + x, y: this.anchor.y, z: this.anchor.z + y },
          clearPermutation
        );
      }
    }
  }

  seekToFrame(targetFrame) {
    if (!this.videoData) return;
    targetFrame = Math.max(0, Math.min(targetFrame, this.videoData.frame_count - 1));
    this.currentFrame = targetFrame;
    this.currentAudioChunk = -1;

    const gop = this.videoData.keyframe_interval || 30;
    const startFrame = Math.floor(targetFrame / gop) * gop;

    if (this.currentJobId !== null) {
      system.clearJob(this.currentJobId);
      this.currentJobId = null;
    }
    
    world.sendMessage(`§e[${EVENT_NAMESPACE}] フレーム ${targetFrame} へシーク中...`);

    const self = this;
    this.currentJobId = system.runJob((function* () {
      for (let f = startFrame; f <= targetFrame; f++) {
        yield* self.applyFrameJob(f);
      }
      self.syncAudioForFrame(self.currentFrame);
      world.sendMessage(`§a[${EVENT_NAMESPACE}] シーク完了 (一時停止中)`);
      self.running = false;
    })());
  }
}

function startMainLoop() {
  if (mainIntervalId !== null) return;
  mainIntervalId = system.runInterval(() => {
    for (const player of activePlayers.values()) {
      player.tick();
    }
  }, FRAME_INTERVAL_TICKS);
}

function getAnchorKeyStr(anchor) {
  return `${anchor.x},${anchor.y},${anchor.z}`;
}

function ensureTickingArea(dimension, anchor, videoData) {
  if (!videoData) return false;
  const areaName = `${TICKING_AREA_NAME}_${anchor.x}_${anchor.y}_${anchor.z}`;
  try {
    dimension.runCommand(`tickingarea remove ${areaName}`);
  } catch (e) {}

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
    } catch (e) {}
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
  if (!vp) {
    if (!globalSelectedVideoId) return;
    vp = new VideoPlayer(anchorLoc, dimension, globalSelectedVideoId);
    activePlayers.set(keyStr, vp);
    startMainLoop();
  }
  
  vp.stopPlayback();
  vp.currentFrame = 0;
  vp.running = true;
  vp.startDelayTicks = START_LOAD_DELAY_TICKS;
  world.sendMessage(`§a[${EVENT_NAMESPACE}] 読み込み完了後に再生します`);
}

function stopAndClearAll(dimension) {
  const anchorLoc = world.getDynamicProperty(ANCHOR_KEY);
  if (anchorLoc) {
    const keyStr = getAnchorKeyStr(anchorLoc);
    let vp = activePlayers.get(keyStr);
    if (vp) {
      vp.stopAndClear();
      world.sendMessage(`§a[${EVENT_NAMESPACE}] 停止・盤面クリア完了`);
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
  
  const currentSec = Math.floor((currentFrame * FRAME_INTERVAL_TICKS) / 20);
  const totalSec = videoData ? Math.floor((videoData.frame_count * FRAME_INTERVAL_TICKS) / 20) : 0;

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
          startPlayback(dimension);
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
  const maxSec = Math.floor((vp.videoData.frame_count * FRAME_INTERVAL_TICKS) / 20);
  const currentSec = Math.floor((vp.currentFrame * FRAME_INTERVAL_TICKS) / 20);

  const form = new ModalFormData()
    .title("⏩ シーク (時間ジャンプ)")
    .slider("再生位置 (秒)", 0, Math.max(1, maxSec), 1, currentSec);

  form.show(player).then((response) => {
    if (response.canceled) return;
    const targetSec = response.formValues[0];
    const targetFrame = Math.floor((targetSec * 20) / FRAME_INTERVAL_TICKS);
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
