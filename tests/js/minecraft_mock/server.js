// @minecraft/server の最小モック (tests/test_main_js.py が node_modules/@minecraft/server/index.js として配置する)。
// main.js が使う API だけを実装し、置かれたブロック・送られたメッセージ・開いたフォームを記録する。
export const board = new Map();
export const messages = [];
export const sounds = [];
export const forms = [];
// API 呼び出しの回数 (setBlockPermutation / fillBlocks) と、置いたブロックの数
export const stats = { setBlockCalls: 0, fillCalls: 0, blocksPlaced: 0, commandFills: 0, structurePlaces: 0 };
// fillBlocks: false にすると fillBlocks が無い環境をまねる
// failZ: 数値にすると z がその値以上のマスへの設置が失敗する (読み込まれていないチャンクをまねる)
// costs: { setBlock, fillCall, fillBlock, listCall, listBlock } (マイクロ秒) を入れると、その時間だけ待って実機の費用をまねる
export const options = { fillBlocks: true, failZ: undefined, costs: null };
// フォームを開いたときの応答 (server-ui モックが先頭から順に返す。空なら「閉じた」)
export const uiResponses = [];

function spin(micros) {
  if (!micros) return;
  const end = performance.now() + micros / 1000;
  while (performance.now() < end) { /* 待つ */ }
}

// fillBlocks の呼び出しのうち、座標リスト (ListBlockVolume) を使ったもの
stats.listFillCalls = 0;

function checkLoaded(location) {
  if (options.failZ !== undefined && location.z >= options.failZ) throw new Error("UnloadedChunksError");
}
// runCommand に渡されたコマンド (tickingarea の確認用)
export const commands = [];

const jobs = new Map();
let nextJobId = 0;
const intervals = [];
const dynamicProperties = {};
let scriptEventHandler = null;
let itemUseHandler = null;

export const dimension = {
  id: "overworld",
  heightRange: { min: -64, max: 320 },
  setBlockPermutation(location, permutation) {
    checkLoaded(location);
    spin(options.costs?.setBlock);
    stats.setBlockCalls++;
    stats.blocksPlaced++;
    board.set(`${location.x},${location.y},${location.z}`, permutation.id);
  },
  get fillBlocks() {
    if (!options.fillBlocks) return undefined;
    return (volume, permutation) => {
      stats.fillCalls++;
      for (const loc of volume.locations ?? [volume.from, volume.to]) checkLoaded(loc);
      if (volume.locations) {
        stats.listFillCalls++;
        spin((options.costs?.listCall ?? 0) + (options.costs?.listBlock ?? 0) * volume.locations.length);
        for (const loc of volume.locations) {
          stats.blocksPlaced++;
          board.set(`${loc.x},${loc.y},${loc.z}`, permutation.id);
        }
        return { volume };
      }
      const { from, to } = volume;
      const area = (Math.abs(to.x - from.x) + 1) * (Math.abs(to.y - from.y) + 1) * (Math.abs(to.z - from.z) + 1);
      spin((options.costs?.fillCall ?? 0) + (options.costs?.fillBlock ?? 0) * area);
      for (let x = Math.min(from.x, to.x); x <= Math.max(from.x, to.x); x++) {
        for (let y = Math.min(from.y, to.y); y <= Math.max(from.y, to.y); y++) {
          for (let z = Math.min(from.z, to.z); z <= Math.max(from.z, to.z); z++) {
            stats.blocksPlaced++;
            board.set(`${x},${y},${z}`, permutation.id);
          }
        }
      }
      return { volume };
    };
  },
  runCommand(command) {
    commands.push(command);
    // tickingarea add は 100 チャンクを超えると失敗する (実際の Minecraft と同じ制限)
    const m = /^tickingarea add (-?\d+) -?\d+ (-?\d+) (-?\d+) -?\d+ (-?\d+)/.exec(command);
    if (m) {
      const [x0, z0, x1, z1] = m.slice(1).map(Number);
      const chunks = (Math.floor(x1 / 16) - Math.floor(x0 / 16) + 1) * (Math.floor(z1 / 16) - Math.floor(z0 / 16) + 1);
      if (chunks > 100) throw new Error(`too many chunks: ${chunks}`);
    }
    if (command.startsWith("fill ")) {
      const [x0, y0, z0, x1, y1, z1, block] = command.split(" ").slice(1);
      for (let x = +x0; x <= +x1; x++) for (let z = +z0; z <= +z1; z++) board.set(`${x},${y0},${z}`, `minecraft:${block}`);
      stats.commandFills++;
    }
    return { successCount: 1 };
  },
};

// 立つ位置 (テストで高さの調整を確かめるため書き換えられるようにする)
export const player = {
  location: { x: 0, y: 64, z: 100 },
  dimension,
  // 見ている方向 (setup face / wall の確認用。既定は北)
  viewDirection: { x: 0, y: 0, z: -1 },
  getViewDirection() {
    return { ...this.viewDirection };
  },
  // 目の高さ (実際の Minecraft と同じく足元から 1.62 ブロック上)
  getHeadLocation() {
    return { x: this.location.x, y: this.location.y + 1.62, z: this.location.z };
  },
  stopMusic() {},
  playSound(id) {
    sounds.push(id);
  },
};

export const world = {
  get structureManager() {
    return structureManager;
  },
  afterEvents: { itemUse: { subscribe: (handler) => { itemUseHandler = handler; } } },
  getDynamicProperty: (key) => dynamicProperties[key],
  setDynamicProperty: (key, value) => { dynamicProperties[key] = value; },
  getDimension: () => dimension,
  getAllPlayers: () => [player],
  sendMessage: (message) => messages.push(message),
};

export const system = {
  afterEvents: { scriptEventReceive: { subscribe: (handler) => { scriptEventHandler = handler; } } },
  runInterval(callback) {
    intervals.push(callback);
    return intervals.length;
  },
  runJob(generator) {
    jobs.set(++nextJobId, generator);
    return nextJobId;
  },
  clearJob(id) {
    jobs.delete(id);
  },
};

export const BlockPermutation = { resolve: (id) => ({ id }) };

export class ListBlockVolume {
  constructor(locations) {
    this.locations = locations.map((loc) => ({ ...loc }));
  }
}

const structures = new Map();
const structureManager = {
  createEmpty(id, size) {
    const blocks = new Map();
    const structure = { id, size, setBlockPermutation: (loc, perm) => blocks.set(`${loc.x},${loc.y},${loc.z}`, perm) };
    structures.set(id, { structure, blocks });
    return structure;
  },
  delete(id) {
    return structures.delete(id);
  },
  place(id, _dimension, location) {
    const entry = structures.get(typeof id === "string" ? id : id.id);
    if (!entry) throw new Error(`no structure ${id}`);
    stats.structurePlaces++;
    for (const [key, perm] of entry.blocks) {
      const [x, y, z] = key.split(",").map(Number);
      board.set(`${location.x + x},${location.y + y},${location.z + z}`, perm.id);
    }
  },
};

export class BlockVolume {
  constructor(from, to) {
    this.from = { ...from };
    this.to = { ...to };
  }
}

export function getAnchor(key) {
  return dynamicProperties[key];
}

// --- テスト用の操作 ---
export function fireScriptEvent(id, message = "") {
  scriptEventHandler({ id, message, sourceEntity: player });
}

export function useItem(typeId) {
  itemUseHandler({ itemStack: { typeId }, source: player });
}

export function tick() {
  for (const callback of intervals) callback();
  for (const [id, generator] of [...jobs]) {
    for (let i = 0; i < 8; i++) {
      if (generator.next().done) {
        jobs.delete(id);
        break;
      }
    }
  }
}
