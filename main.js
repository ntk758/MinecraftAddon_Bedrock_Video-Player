/**
 * Bad Apple Cushion Player - 統合版移植版 (v2: カラー対応)
 *
 * Java版 mc-cushion-bad-apple (datapack + .mcfunction) を Script API に置き換えたもの。
 * Bad Apple専用ではなく、convert.py で変換した任意のカラー動画を再生できる。
 * - .mcfunctionを大量生成する代わりに、フレーム差分を frames_data.js に1本化して起動時に読み込む
 * - schedule function の連鎖の代わりに system.runInterval(fn, 1) で1tickごとにコールバック
 * - コマンドパーサを経由せず dimension.setBlockPermutation() を直接呼ぶ
 * - 原点座標(アンカー)は Java版の marker エンティティの代わりに
 *   world.setDynamicProperty()/getDynamicProperty() で保存する。
 *   (実機で判明: 統合版に "minecraft:marker" というエンティティは存在せず、
 *    spawnEntity("minecraft:marker", ...) は InvalidArgumentError になる)
 * - 各ピクセルの色は、Minecraft公式マップカラー定義由来の16色concreteパレットに
 *   最近傍マッチングされる(convert.py側の処理)。発光ブロックの明度で階調を
 *   作っていたv1は色相がバラバラで見た目が絵にならなかったため廃止した。
 *
 * 操作方法 (datapackのfunctionコマンドの代わりに /scriptevent を使う):
 *   /scriptevent badapple:setup   … プレイヤーの足元を起点に原点座標を保存
 *   /scriptevent badapple:start   … 再生開始
 *   /scriptevent badapple:stop    … 停止して盤面をクリア
 *   /scriptevent badapple:list    … 収録動画一覧を表示
 *   /scriptevent badapple:play <id> … 指定した動画を選択して再生
 *
 * 未検証事項(実機で必ず確認すること):
 * - 64x64=4096ブロックの差分を1tick内に処理しきれるか(重い場合は間引きが必要。
 *   その場合は runInterval の第2引数を 2 以上にして更新頻度を落とす)
 */

import { world, system, BlockPermutation } from "@minecraft/server";
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";
import { VIDEOS, VIDEO_LIST } from "./videos.js";

// video_player_gui.py がパック作成時にこの2つの値を設定する。
const EVENT_NAMESPACE = "badapple";
const FRAME_INTERVAL_TICKS = 1;
const EVENT_PREFIX = `${EVENT_NAMESPACE}:`;
const ANCHOR_KEY = `${EVENT_NAMESPACE}:anchor`;
const ANCHOR_DIMENSION_KEY = `${EVENT_NAMESPACE}:dimension`;
const MESSAGE_PREFIX = `§b[${EVENT_NAMESPACE}]`;
const START_LOAD_DELAY_TICKS = 5;
const TICKING_AREA_NAME = "badapple_area";

// リモコン用設定
const REMOTE_CONTROL_ITEM = "minecraft:compass";
const TICKS_PER_AUDIO_CHUNK = 200; // 10秒 = 200 ticks

// 旧v1.3.0パックの生成データとの後方互換性。
// 統合版の無色テラコッタは minecraft:terracotta ではなく minecraft:hardened_clay。
const BLOCK_ID_ALIASES = {
  "minecraft:terracotta": "minecraft:hardened_clay",
};

let currentFrame = 0;
let running = false;
let timeoutId = null;
let intervalId = null;
let paletteCache = null;
const tempBlockLoc = { x: 0, y: 0, z: 0 };

let currentVideoId = null;
let currentVideoData = null;
let currentAudioChunk = -1;
let masterVolume = 1.0;

function selectVideo(videoId) {
  if (VIDEOS[videoId]) {
    currentVideoId = videoId;
    currentVideoData = VIDEOS[videoId];
    return true;
  }
  return false;
}

// Auto-select first available video
if (VIDEO_LIST.length > 0) {
  selectVideo(VIDEO_LIST[0].id);
}

const B64_MAP = new Uint8Array(256);
const B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
for (let i = 0; i < B64_CHARS.length; i++) {
  B64_MAP[B64_CHARS.charCodeAt(i)] = i;
}

function decodeBase64ToBytes(b64Str) {
  if (!b64Str) return new Uint8Array(0);
  if (b64Str.startsWith("K:")) {
    b64Str = b64Str.slice(2);
  }
  const len = b64Str.length;
  if (len === 0) return new Uint8Array(0);
  let validLen = len;
  if (b64Str[len - 1] === "=") validLen--;
  if (b64Str[len - 2] === "=") validLen--;

  const byteLen = (validLen * 3) >> 2;
  const bytes = new Uint8Array(byteLen);
  let p = 0;
  for (let i = 0; i < validLen; i += 4) {
    const b0 = B64_MAP[b64Str.charCodeAt(i)];
    const b1 = B64_MAP[b64Str.charCodeAt(i + 1)];
    const b2 = B64_MAP[b64Str.charCodeAt(i + 2)];
    const b3 = B64_MAP[b64Str.charCodeAt(i + 3)];

    bytes[p++] = (b0 << 2) | (b1 >> 4);
    if (p < byteLen) bytes[p++] = ((b1 & 15) << 4) | (b2 >> 2);
    if (p < byteLen) bytes[p++] = ((b2 & 3) << 6) | b3;
  }
  return bytes;
}

function initPaletteCache() {
  if (!currentVideoData) return;
  if (!paletteCache || paletteCache.length !== currentVideoData.level_blocks.length) {
    paletteCache = currentVideoData.level_blocks.map((spec) => {
      const blockId = BLOCK_ID_ALIASES[spec.block] ?? spec.block;
      try {
        return BlockPermutation.resolve(blockId, spec.states);
      } catch (e) {
        console.warn(`[${EVENT_NAMESPACE}] Failed to resolve permutation for ${blockId}: ${e}`);
        return BlockPermutation.resolve("minecraft:dirt");
      }
    });
  }
}

function getAnchor() {
  // Vector3 { x, y, z } または undefined(未設定)が返る
  return world.getDynamicProperty(ANCHOR_KEY);
}

function getPlaybackDimension(fallbackDimension) {
  const dimensionId = world.getDynamicProperty(ANCHOR_DIMENSION_KEY);
  if (typeof dimensionId === "string") {
    try {
      return world.getDimension(dimensionId);
    } catch (e) {
      console.warn(`[${EVENT_NAMESPACE}] saved dimension is unavailable: ${e}`);
    }
  }
  return fallbackDimension;
}

function ensureTickingArea(dimension, anchor) {
  if (!currentVideoData) return false;
  try {
    dimension.runCommand(`tickingarea remove ${TICKING_AREA_NAME}`);
  } catch (e) {
    // 既存エリアがない場合のエラーは無視する。
  }

  // 終点は盤面内の最後のブロック。余分な1列・1行を含めない。
  const toX = anchor.x + currentVideoData.width - 1;
  const toZ = anchor.z + currentVideoData.height - 1;
  try {
    dimension.runCommand(
      `tickingarea add ${anchor.x} ${anchor.y} ${anchor.z} ${toX} ${anchor.y} ${toZ} ${TICKING_AREA_NAME}`
    );
    return true;
  } catch (e) {
    console.warn(`[${EVENT_NAMESPACE}] tickingarea add failed: ${e}`);
    return false;
  }
}

/** フレーム番号の差分を実際にブロックへ適用する */
function applyFrame(dimension, anchorLoc, frameIndex) {
  if (!currentVideoData) return;
  const diffData = currentVideoData.frames[frameIndex];
  if (!diffData) return;

  initPaletteCache();
  const width = currentVideoData.width;

  if (typeof diffData === "string") {
    if (diffData.length === 0) return;
    const bytes = decodeBase64ToBytes(diffData);
    let offset = 0;
    let currIdx = 0;
    const len = bytes.length;

    while (offset < len) {
      let val = 0;
      let shift = 0;
      while (true) {
        const b = bytes[offset++];
        val |= (b & 0x7f) << shift;
        if ((b & 0x80) === 0) break;
        shift += 7;
      }
      const delta = val >> 6;
      const level = val & 0x3f;
      currIdx += delta;

      const x = currIdx % width;
      const y = (currIdx / width) | 0;

      const permutation = paletteCache[level];
      if (!permutation) continue;

      tempBlockLoc.x = anchorLoc.x + x;
      tempBlockLoc.y = anchorLoc.y;
      tempBlockLoc.z = anchorLoc.z + y;

      try {
        dimension.setBlockPermutation(tempBlockLoc, permutation);
      } catch (e) {
        console.warn(
          `[${EVENT_NAMESPACE}] block resolve/apply failed at (${tempBlockLoc.x},${tempBlockLoc.y},${tempBlockLoc.z}) level=${level}: ${e}`
        );
      }
    }
  } else if (Array.isArray(diffData)) {
    for (let i = 0; i < diffData.length; i++) {
      const item = diffData[i];
      let x, y, level;

      if (typeof item === "number") {
        const idx = item & 0xffff;
        level = item >>> 16;
        x = idx % width;
        y = (idx / width) | 0;
      } else if (Array.isArray(item)) {
        x = item[0];
        y = item[1];
        level = item[2];
      } else {
        continue;
      }

      const permutation = paletteCache[level];
      if (!permutation) continue;

      tempBlockLoc.x = anchorLoc.x + x;
      tempBlockLoc.y = anchorLoc.y;
      tempBlockLoc.z = anchorLoc.z + y;

      try {
        dimension.setBlockPermutation(tempBlockLoc, permutation);
      } catch (e) {
        console.warn(
          `[${EVENT_NAMESPACE}] block resolve/apply failed at (${tempBlockLoc.x},${tempBlockLoc.y},${tempBlockLoc.z}) level=${level}: ${e}`
        );
      }
    }
  }
}

function stopPlayback() {
  running = false;
  if (timeoutId !== null) {
    system.clearRun(timeoutId);
    timeoutId = null;
  }
  if (intervalId !== null) {
    system.clearRun(intervalId);
    intervalId = null;
  }
  currentAudioChunk = -1;
  // 音楽停止
  for (const player of world.getAllPlayers()) {
    try {
      player.stopMusic();
    } catch (e) {}
  }
}

function syncAudioForFrame(frameIndex) {
  if (!currentVideoData || masterVolume <= 0) return;
  const targetChunk = Math.floor((frameIndex * FRAME_INTERVAL_TICKS) / TICKS_PER_AUDIO_CHUNK);
  if (targetChunk !== currentAudioChunk) {
    currentAudioChunk = targetChunk;
    const trackId = `${EVENT_NAMESPACE}.${currentVideoId}.chunk_${targetChunk}`;
    for (const player of world.getAllPlayers()) {
      try {
        player.stopMusic();
        player.playSound(trackId, { location: player.location, volume: masterVolume, pitch: 1.0 });
      } catch (e) {
        console.warn(`[${EVENT_NAMESPACE}] playSound error: ${e}`);
        try {
          player.playMusic(trackId, { volume: masterVolume, loop: false });
        } catch (e2) {
          console.warn(`[${EVENT_NAMESPACE}] playMusic error: ${e2}`);
        }
      }
    }
  }
}

function seekToFrame(targetFrame, dimension, anchorLoc) {
  if (!currentVideoData) return;
  targetFrame = Math.max(0, Math.min(targetFrame, currentVideoData.frame_count - 1));
  currentFrame = targetFrame;
  currentAudioChunk = -1;

  const gop = currentVideoData.keyframe_interval || 30;
  const startFrame = Math.floor(targetFrame / gop) * gop;

  // 直前のキーフレームから目標フレームまでを短時間一括適用して完璧に画面を復元
  for (let f = startFrame; f <= targetFrame; f++) {
    applyFrame(dimension, anchorLoc, f);
  }
  syncAudioForFrame(currentFrame);
}

function startPlayback(fallbackDimension) {
  if (!currentVideoData) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] 動画が選択されていません。/scriptevent ${EVENT_PREFIX}list で一覧を確認してください`);
    return;
  }
  const anchorLoc = getAnchor();
  if (!anchorLoc) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] 原点が見つかりません。先に /scriptevent ${EVENT_PREFIX}setup を実行してください`);
    return;
  }
  const dimension = getPlaybackDimension(fallbackDimension);
  if (!ensureTickingArea(dimension, anchorLoc)) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] tickingareaの設定に失敗しました。setupをやり直してください`);
    return;
  }

  stopPlayback();
  currentFrame = 0;
  running = true;

  timeoutId = system.runTimeout(() => {
    timeoutId = null;
    if (!running) return;
    intervalId = system.runInterval(() => {
      if (!running || currentFrame >= currentVideoData.frame_count) {
        stopPlayback();
        world.sendMessage(`§a[${EVENT_NAMESPACE}] 再生終了`);
        return;
      }
      applyFrame(dimension, anchorLoc, currentFrame);
      syncAudioForFrame(currentFrame);
      currentFrame++;
    }, FRAME_INTERVAL_TICKS);
  }, START_LOAD_DELAY_TICKS);
  world.sendMessage(`§a[${EVENT_NAMESPACE}] 読み込み完了後に再生します。リモコン(コンパス)右クリックで操作GUIが開きます`);
}

function setup(player) {
  if (!currentVideoData) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] 動画が読み込まれていません`);
    return;
  }
  const loc = player.location;
  const anchor = {
    x: Math.floor(loc.x),
    y: Math.floor(loc.y),
    z: Math.floor(loc.z) - currentVideoData.height,
  };
  world.setDynamicProperty(ANCHOR_KEY, anchor);
  world.setDynamicProperty(ANCHOR_DIMENSION_KEY, player.dimension.id);

  const dimension = player.dimension;
  if (!ensureTickingArea(dimension, anchor)) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] tickingareaの設定に失敗しました。ワールドのチート設定を確認してください`);
    return;
  }
  world.sendMessage(`§a[${EVENT_NAMESPACE}] セットアップ完了。リモコン(コンパス)を使用するか /scriptevent ${EVENT_PREFIX}start で再生します`);
}

function stopAndClear(fallbackDimension) {
  stopPlayback();
  if (!currentVideoData) return;
  const anchorLoc = getAnchor();
  if (!anchorLoc) return;
  const dimension = getPlaybackDimension(fallbackDimension);

  const clearSpec = currentVideoData.level_blocks[0];
  const clearPermutation = BlockPermutation.resolve(clearSpec.block, clearSpec.states);

  for (let y = 0; y < currentVideoData.height; y++) {
    for (let x = 0; x < currentVideoData.width; x++) {
      dimension.setBlockPermutation(
        { x: anchorLoc.x + x, y: anchorLoc.y, z: anchorLoc.z + y },
        clearPermutation
      );
    }
  }
  world.sendMessage(`§a[${EVENT_NAMESPACE}] 停止・盤面クリア完了`);
}

// --- リモコン GUI コントローラー (ActionFormData) ---
function showRemoteControlGUI(player) {
  const statusStr = running ? "§a再生中" : "§c停止中";
  const titleStr = currentVideoId ? currentVideoId : "未選択";
  const currentSec = Math.floor((currentFrame * FRAME_INTERVAL_TICKS) / 20);
  const totalSec = currentVideoData ? Math.floor((currentVideoData.frame_count * FRAME_INTERVAL_TICKS) / 20) : 0;

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
      case 0: // ▶ 再生 / 一時停止
        if (running) {
          stopPlayback();
          world.sendMessage(`${MESSAGE_PREFIX} §e一時停止しました`);
        } else {
          startPlayback(dimension);
        }
        break;

      case 1: // ⏹ 停止
        stopAndClear(dimension);
        break;

      case 2: // ⏭ 次の動画
        switchVideoIndex(1, player);
        break;

      case 3: // ⏮ 前の動画
        switchVideoIndex(-1, player);
        break;

      case 4: // 🔊 音量設定
        showVolumeGUI(player);
        break;

      case 5: // ⏩ シーク
        showSeekGUI(player);
        break;

      case 6: // 📜 動画リスト
        showVideoSelectGUI(player);
        break;
    }
  });
}

function switchVideoIndex(direction, player) {
  if (VIDEO_LIST.length === 0) return;
  let currentIdx = VIDEO_LIST.findIndex((v) => v.id === currentVideoId);
  if (currentIdx === -1) currentIdx = 0;
  let newIdx = (currentIdx + direction + VIDEO_LIST.length) % VIDEO_LIST.length;
  selectVideo(VIDEO_LIST[newIdx].id);
  world.sendMessage(`${MESSAGE_PREFIX} §a動画 '${currentVideoId}' に切り替えました`);
  showRemoteControlGUI(player);
}

function showVolumeGUI(player) {
  const form = new ModalFormData()
    .title("🔊 音量設定")
    .slider("マスター音量 (%)", 0, 100, 10, Math.round(masterVolume * 100));

  form.show(player).then((response) => {
    if (response.canceled) return;
    masterVolume = response.formValues[0] / 100.0;
    world.sendMessage(`${MESSAGE_PREFIX} §a音量を ${Math.round(masterVolume * 100)}% に設定しました`);
    currentAudioChunk = -1;
    if (running) syncAudioForFrame(currentFrame);
  });
}

function showSeekGUI(player) {
  if (!currentVideoData) return;
  const maxSec = Math.floor((currentVideoData.frame_count * FRAME_INTERVAL_TICKS) / 20);
  const currentSec = Math.floor((currentFrame * FRAME_INTERVAL_TICKS) / 20);

  const form = new ModalFormData()
    .title("⏩ シーク (時間ジャンプ)")
    .slider("再生位置 (秒)", 0, Math.max(1, maxSec), 1, currentSec);

  form.show(player).then((response) => {
    if (response.canceled) return;
    const targetSec = response.formValues[0];
    const targetFrame = Math.floor((targetSec * 20) / FRAME_INTERVAL_TICKS);
    const anchorLoc = getAnchor();
    if (anchorLoc) {
      seekToFrame(targetFrame, player.dimension, anchorLoc);
      world.sendMessage(`${MESSAGE_PREFIX} §a${targetSec}秒目 (Frame: ${targetFrame}) にシークしました`);
    } else {
      world.sendMessage(`§c[${EVENT_NAMESPACE}] 先に setup を実行してください`);
    }
  });
}

function showVideoSelectGUI(player) {
  const form = new ActionFormData().title("📜 動画ライブラリ").body("再生する動画タイトルを選択してください:");
  for (const v of VIDEO_LIST) {
    const isSel = v.id === currentVideoId ? " §a[選択中]" : "";
    form.button(`${v.title}${isSel}\n${v.frame_count} frames (${v.width}x${v.height})`);
  }

  form.show(player).then((response) => {
    if (response.canceled) return;
    const selectedVideo = VIDEO_LIST[response.selection];
    if (selectedVideo) {
      selectVideo(selectedVideo.id);
      world.sendMessage(`${MESSAGE_PREFIX} §a動画 '${selectedVideo.id}' を選択しました`);
      showRemoteControlGUI(player);
    }
  });
}

// リモコンアイテム右クリックイベント
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
      stopAndClear(dimension);
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
          const selected = v.id === currentVideoId ? " §a[選択中]" : "";
          world.sendMessage(`  §f- §b${v.id}§f: ${v.title} (${v.frame_count} frames, ${v.width}x${v.height})${selected}`);
        }
        world.sendMessage(`${MESSAGE_PREFIX} §f再生: /scriptevent ${EVENT_PREFIX}play <動画ID>`);
      }
      break;
    case "play":
      const videoId = event.message?.trim();
      if (!videoId) {
        startPlayback(dimension);
      } else if (selectVideo(videoId)) {
        world.sendMessage(`${MESSAGE_PREFIX} §a動画 '${videoId}' を選択しました`);
        startPlayback(dimension);
      } else {
        world.sendMessage(`§c${MESSAGE_PREFIX} 動画 '${videoId}' が見つかりません。/scriptevent ${EVENT_PREFIX}list で一覧を確認してください`);
      }
      break;
    default:
      break;
  }
});
