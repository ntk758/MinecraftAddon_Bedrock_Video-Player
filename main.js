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
 *
 * 未検証事項(実機で必ず確認すること):
 * - 64x64=4096ブロックの差分を1tick内に処理しきれるか(重い場合は間引きが必要。
 *   その場合は runInterval の第2引数を 2 以上にして更新頻度を落とす)
 */

import { world, system, BlockPermutation } from "@minecraft/server";
import { FRAME_DATA } from "./frames_data.js";

// video_player_gui.py がパック作成時にこの2つの値を設定する。
const EVENT_NAMESPACE = "badapple";
const FRAME_INTERVAL_TICKS = 1;
const EVENT_PREFIX = `${EVENT_NAMESPACE}:`;
const ANCHOR_KEY = `${EVENT_NAMESPACE}:anchor`;
const ANCHOR_DIMENSION_KEY = `${EVENT_NAMESPACE}:dimension`;
const MESSAGE_PREFIX = `§b[${EVENT_NAMESPACE}]`;
const START_LOAD_DELAY_TICKS = 5;
const TICKING_AREA_NAME = "badapple_area";

// 旧v1.3.0パックの生成データとの後方互換性。
// 統合版の無色テラコッタは minecraft:terracotta ではなく minecraft:hardened_clay。
const BLOCK_ID_ALIASES = {
  "minecraft:terracotta": "minecraft:hardened_clay",
};

let currentFrame = 0;
let running = false;
let intervalId = null;

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
  try {
    dimension.runCommand(`tickingarea remove ${TICKING_AREA_NAME}`);
  } catch (e) {
    // 既存エリアがない場合のエラーは無視する。
  }

  // 終点は盤面内の最後のブロック。余分な1列・1行を含めない。
  const toX = anchor.x + FRAME_DATA.width - 1;
  const toZ = anchor.z + FRAME_DATA.height - 1;
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
  const diffs = FRAME_DATA.frames[frameIndex];
  if (!diffs) return;

  for (const [x, y, level] of diffs) {
    const spec = FRAME_DATA.level_blocks[level];
    const blockId = BLOCK_ID_ALIASES[spec.block] ?? spec.block;
    const worldX = anchorLoc.x + x;
    const worldY = anchorLoc.y;
    const worldZ = anchorLoc.z + y;

    try {
      const permutation = BlockPermutation.resolve(blockId, spec.states);
      dimension.setBlockPermutation({ x: worldX, y: worldY, z: worldZ }, permutation);
    } catch (e) {
      console.warn(
        `[${EVENT_NAMESPACE}] block resolve/apply failed at (${worldX},${worldY},${worldZ}) level=${level} block=${blockId}: ${e}`
      );
    }
  }
}

function stopPlayback() {
  running = false;
  if (intervalId !== null) {
    system.clearRun(intervalId);
    intervalId = null;
  }
}

function startPlayback(fallbackDimension) {
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

  // intervalId は最初 runTimeout の識別子を保持し、その遅延後に
  // runInterval の識別子で上書きされる。system.clearRun() は
  // run/runTimeout/runInterval いずれの識別子でも安全にキャンセルできるため、
  // stopPlayback() 側は intervalId がどちらの状態でも正しく動作する
  // (Bedrock Wiki "Script Core Features" で確認済み)。
  // tickingareaの反映を待つ。これを待たずにsetBlockすると未ロードチャンク例外になる。
  intervalId = system.runTimeout(() => {
    if (!running) return;
    intervalId = system.runInterval(() => {
      if (!running || currentFrame >= FRAME_DATA.frame_count) {
        stopPlayback();
        world.sendMessage(`§a[${EVENT_NAMESPACE}] 再生終了`);
        return;
      }
      applyFrame(dimension, anchorLoc, currentFrame);
      currentFrame++;
    }, FRAME_INTERVAL_TICKS);
  }, START_LOAD_DELAY_TICKS);
  world.sendMessage(`§a[${EVENT_NAMESPACE}] 読み込み完了後に再生します。/scriptevent ${EVENT_PREFIX}stop で停止できます`);
}

function setup(player) {
  const loc = player.location;
  const anchor = {
    x: Math.floor(loc.x),
    y: Math.floor(loc.y),
    z: Math.floor(loc.z) - FRAME_DATA.height,
  };
  world.setDynamicProperty(ANCHOR_KEY, anchor);
  world.setDynamicProperty(ANCHOR_DIMENSION_KEY, player.dimension.id);

  const dimension = player.dimension;
  if (!ensureTickingArea(dimension, anchor)) {
    world.sendMessage(`§c[${EVENT_NAMESPACE}] tickingareaの設定に失敗しました。ワールドのチート設定を確認してください`);
    return;
  }
  world.sendMessage(`§a[${EVENT_NAMESPACE}] セットアップ完了。/scriptevent ${EVENT_PREFIX}start で再生します`);
}

function stopAndClear(fallbackDimension) {
  stopPlayback();
  const anchorLoc = getAnchor();
  if (!anchorLoc) return;
  const dimension = getPlaybackDimension(fallbackDimension);

  const clearSpec = FRAME_DATA.level_blocks[0];
  const clearPermutation = BlockPermutation.resolve(clearSpec.block, clearSpec.states);

  for (let y = 0; y < FRAME_DATA.height; y++) {
    for (let x = 0; x < FRAME_DATA.width; x++) {
      dimension.setBlockPermutation(
        { x: anchorLoc.x + x, y: anchorLoc.y, z: anchorLoc.z + y },
        clearPermutation
      );
    }
  }
  world.sendMessage(`§a[${EVENT_NAMESPACE}] 停止・盤面クリア完了`);
}

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
    default:
      break;
  }
});
