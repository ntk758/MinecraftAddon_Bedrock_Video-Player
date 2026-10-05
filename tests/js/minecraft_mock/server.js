// @minecraft/server の最小モック (tests/test_main_js.py が node_modules/@minecraft/server/index.js として配置する)。
// main.js が使う API だけを実装し、置かれたブロック・送られたメッセージ・開いたフォームを記録する。
export const board = new Map();
export const messages = [];
export const sounds = [];
export const forms = [];

const jobs = new Map();
let nextJobId = 0;
const intervals = [];
const dynamicProperties = {};
let scriptEventHandler = null;
let itemUseHandler = null;

export const dimension = {
  id: "overworld",
  setBlockPermutation(location, permutation) {
    board.set(`${location.x},${location.z}`, permutation.id);
  },
  runCommand() {},
};

const player = {
  location: { x: 0, y: 64, z: 100 },
  dimension,
  stopMusic() {},
  playSound(id) {
    sounds.push(id);
  },
};

export const world = {
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
