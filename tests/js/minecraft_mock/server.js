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
// unloadedZ: 数値にすると z がその値以上のマスは読み込まれていない (getBlock が undefined を返し、設置も失敗する)
// tickingOnly: true にすると、常時読み込みの区画の中とプレイヤーから 64 ブロック以内だけが読み込まれている (区画を外すと、
//   遠くのチャンクには置けなくなる実機をまねる)
// costs: { setBlock, fillCall, fillBlock, listCall, listBlock } (マイクロ秒) を入れると、その時間だけ待って実機の費用をまねる
const playerSpawnHandlers = [];
/** プレイヤーがワールドに入った (initialSpawn) ときのように、playerSpawn を送る。 */
export function spawnPlayer(initialSpawn = true) {
  for (const handler of playerSpawnHandlers) handler({ player, initialSpawn });
}
export const options = { fillBlocks: true, failZ: undefined, unloadedZ: undefined, costs: null };
// フォームを開いたときの応答 (server-ui モックが先頭から順に返す。空なら「閉じた」)
export const uiResponses = [];

function spin(micros) {
  if (!micros) return;
  const end = performance.now() + micros / 1000;
  while (performance.now() < end) { /* 待つ */ }
}

// fillBlocks の呼び出しのうち、座標リスト (ListBlockVolume) を使ったもの
stats.listFillCalls = 0;

function outsideTicking(location) {
  if (!options.tickingOnly) return false;
  if (Math.max(Math.abs(location.x - player.location.x), Math.abs(location.z - player.location.z)) <= 64) return false;
  for (const { from, to } of tickingRects.values()) {
    if (location.x >= Math.min(from.x, to.x) && location.x <= Math.max(from.x, to.x) &&
        location.z >= Math.min(from.z, to.z) && location.z <= Math.max(from.z, to.z)) return false;
  }
  return true;
}

function checkLoaded(location) {
  if (outsideTicking(location)) throw new Error("LocationInUnloadedChunkError");
  if (options.failZ !== undefined && location.z >= options.failZ) throw new Error("UnloadedChunksError");
  if (options.unloadedZ !== undefined && location.z >= options.unloadedZ) throw new Error("UnloadedChunksError");
}
// runCommand に渡されたコマンド。TickingAreaManager の登録・削除も同じ形 ("tickingarea add x0 y z0 x1 y z1 名前" /
// "tickingarea remove 名前") で記録する (テストが区画を確かめるため)
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
  getBlock(location) {
    if (options.unloadedZ !== undefined && location.z >= options.unloadedZ) return undefined;
    return { location: { ...location } };
  },
  // Script API 2.4.0
  isChunkLoaded(location) {
    return !(options.unloadedZ !== undefined && location.z >= options.unloadedZ);
  },
  setBlockPermutation(location, permutation) {
    if (restricted) throw new Error("Native function [Dimension::setBlockPermutation] does not have required privileges.");
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
  typeId: "minecraft:player",
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
  dynamicProperties: new Map(),
  getDynamicProperty(key) { return this.dynamicProperties.get(key); },
  setDynamicProperty(key, value) { this.dynamicProperties.set(key, value); },
  sendMessage(message) { messages.push({ to: "player", ...message }); },
};

// TickingAreaManager (Script API 2.6.0)。このパックの枠は maxChunkCount チャンク (実際の値は公開されていないので、
// テストでは options.tickingChunks で変えられる)。1 辺 255 チャンクまで
options.tickingChunks = 1000;
const tickingAreas = new Map(); // 名前 -> チャンク数
const tickingRects = new Map(); // 名前 -> { from, to } (tickingOnly の判定に使う)
function chunkCountOf({ from, to }) {
  const width = Math.abs(Math.floor(to.x / 16) - Math.floor(from.x / 16)) + 1;
  const depth = Math.abs(Math.floor(to.z / 16) - Math.floor(from.z / 16)) + 1;
  return { width, depth, count: width * depth };
}
const tickingAreaManager = {
  get maxChunkCount() { return options.tickingChunks; },
  get chunkCount() { return [...tickingAreas.values()].reduce((a, b) => a + b, 0); },
  hasCapacity(areaOptions) {
    const { width, depth, count } = chunkCountOf(areaOptions);
    return width <= 255 && depth <= 255 && this.chunkCount + count <= this.maxChunkCount;
  },
  createTickingArea(identifier, areaOptions) {
    if (tickingAreas.has(identifier)) throw new Error("TickingAreaError: IdentifierAlreadyExists");
    if (!areaOptions.dimension) throw new Error("dimension is required");
    if (!this.hasCapacity(areaOptions)) throw new Error("TickingAreaError: OverChunkLimit");
    const { from, to } = areaOptions;
    tickingAreas.set(identifier, chunkCountOf(areaOptions).count);
    tickingRects.set(identifier, { from: { ...from }, to: { ...to } });
    commands.push(`tickingarea add ${from.x} ${from.y} ${from.z} ${to.x} ${to.y} ${to.z} ${identifier}`);
    return Promise.resolve();
  },
  getAllTickingAreas() {
    return [...tickingAreas.keys()].map((identifier) => ({ identifier }));
  },
  hasTickingArea(identifier) { return tickingAreas.has(identifier); },
  removeTickingArea(identifier) {
    const name = typeof identifier === "string" ? identifier : identifier.identifier;
    if (!tickingAreas.delete(name)) throw new Error("TickingAreaError: UnknownIdentifier");
    tickingRects.delete(name);
    commands.push(`tickingarea remove ${name}`);
  },
  removeAllTickingAreas() {
    for (const name of [...tickingAreas.keys()]) this.removeTickingArea(name);
  },
};
/** ワールドを開き直したときのように、TickingAreaManager の区画を消す (テスト用)。 */
export function forgetTicking() {
  tickingAreas.clear();
  tickingRects.clear();
}

export const world = {
  get structureManager() {
    return structureManager;
  },
  get tickingAreaManager() {
    return tickingAreaManager;
  },
  afterEvents: {
    itemUse: { subscribe: (handler) => { itemUseHandler = handler; } },
    playerSpawn: { subscribe: (handler) => { playerSpawnHandlers.push(handler); } },
  },
  getDynamicProperty: (key) => dynamicProperties[key],
  setDynamicProperty: (key, value) => {
    if (restricted) throw new Error("Native function [World::setDynamicProperty] does not have required privileges.");
    dynamicProperties[key] = value;
  },
  getDimension: () => dimension,
  getAllPlayers: () => [player],
  sendMessage: (message) => messages.push(message),
};

// 独自のコマンド (Script API 2.4.0)。名前 -> { command, callback }
export const customCommands = new Map();
export const customEnums = new Map();
const customCommandRegistry = {
  registerCommand(command, callback) {
    if (!/^[a-z0-9_]+:[a-z0-9_]+$/.test(command.name)) throw new Error(`NamespaceNameError: ${command.name}`);
    if (command.name.startsWith("minecraft:")) throw new Error("DisallowedNamespace");
    for (const param of command.optionalParameters ?? []) {
      if (param.type === "Enum" && !customEnums.has(param.name)) throw new Error(`unknown enum ${param.name}`);
    }
    customCommands.set(command.name, { command, callback });
  },
  registerEnum(name, values) {
    if (!/^[a-z0-9_]+:[a-z0-9_]+$/.test(name)) throw new Error(`NamespaceNameError: ${name}`);
    customEnums.set(name, [...values]);
  },
};
const pendingRuns = [];
// 読み取り専用の間 (独自のコマンドの処理の中) かどうか。その間にワールドを変えると、実際の Minecraft と同じく失敗させる
let restricted = false;

export const CommandPermissionLevel = { Any: 0, GameDirectors: 1, Admin: 2, Host: 3, Owner: 4 };
export const CustomCommandParamType = { BlockType: "BlockType", Boolean: "Boolean", EntitySelector: "EntitySelector",
  EntityType: "EntityType", Enum: "Enum", Float: "Float", Integer: "Integer", ItemType: "ItemType", Location: "Location",
  PlayerSelector: "PlayerSelector", String: "String" };
export const CustomCommandStatus = { Success: 0, Failure: 1 };

export const system = {
  afterEvents: { scriptEventReceive: { subscribe: (handler) => { scriptEventHandler = handler; } } },
  // startup は main.js を読み込んだ直後に 1 回だけ呼ぶ (実際の Minecraft でもワールドを読み込む前)
  beforeEvents: { startup: { subscribe: (handler) => { handler({ customCommandRegistry }); } } },
  run(callback) {
    pendingRuns.push(callback);
    return pendingRuns.length;
  },
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

/** プレイヤーが独自のコマンドを実行する (/badapple:setup face など)。登録されていなければ例外。結果を返す。 */
export function runCustomCommand(name, ...args) {
  const entry = customCommands.get(name);
  if (!entry) throw new Error(`unknown command /${name}`);
  restricted = true;
  try {
    return entry.callback({ sourceEntity: player, sourceType: "Entity" }, ...args);
  } finally {
    restricted = false;
  }
}

export function useItem(typeId) {
  itemUseHandler({ itemStack: { typeId }, source: player });
}

export function tick() {
  for (const callback of pendingRuns.splice(0)) callback();
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
