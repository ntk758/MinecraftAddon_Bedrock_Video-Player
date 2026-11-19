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
export function spawnPlayer(initialSpawn = true, who = "first") {
  const target = who === "second" ? secondPlayer : player;
  for (const handler of playerSpawnHandlers) handler({ player: target, initialSpawn });
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
const timeouts = [];
let timeoutCount = 0;
let currentTick = 0;
const dynamicProperties = {};
let scriptEventHandler = null;
let itemUseHandler = null;

export const dimension = {
  id: "overworld",
  heightRange: { min: -64, max: 320 },
  getBlock(location) {
    if (options.unloadedZ !== undefined && location.z >= options.unloadedZ) return undefined;
    if (location.y < this.heightRange.min || location.y >= this.heightRange.max) throw new Error("LocationOutOfWorldBoundariesError");
    return blockAt(location);
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

// 2 人目 (複数人の確かめ用)。鳴らした音は "p2:<音の ID>" として sounds に残す。立つ位置は画面から離れた所
export const secondPlayer = {
  ...player,
  location: { x: 200, y: 64, z: 300 },
  viewDirection: { x: 0, y: 0, z: -1 },
  playSound(id) {
    sounds.push(`p2:${id}`);
  },
  dynamicProperties: new Map(),
  sendMessage(message) { messages.push({ to: "player2", ...message }); },
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

// スクリーンブロックなどのブロックの状態 ("x,y,z" -> { 状態名: 値 })
export const blockStates = new Map();
const worldEventHandlers = { playerBreakBlock: [], blockExplode: [], worldLoad: [] };

/** その位置のブロック (getBlock の戻り値)。board に無ければ空気。 */
function blockAt(location) {
  const key = `${location.x},${location.y},${location.z}`;
  const typeId = board.get(key) ?? "minecraft:air";
  const states = blockStates.get(key) ?? {};
  return {
    location: { x: location.x, y: location.y, z: location.z },
    typeId,
    isAir: typeId === "minecraft:air",
    dimension,
    permutation: { type: { id: typeId }, getState: (name) => states[name] },
  };
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
    playerBreakBlock: { subscribe: (handler) => { worldEventHandlers.playerBreakBlock.push(handler); } },
    blockExplode: { subscribe: (handler) => { worldEventHandlers.blockExplode.push(handler); } },
    worldLoad: { subscribe: (handler) => { worldEventHandlers.worldLoad.push(handler); } },
  },
  getDynamicProperty: (key) => dynamicProperties[key],
  setDynamicProperty: (key, value) => {
    if (restricted) throw new Error("Native function [World::setDynamicProperty] does not have required privileges.");
    dynamicProperties[key] = value;
  },
  getDimension: () => dimension,
  // options.secondPlayer を true にすると 2 人 (2 人目が入った・抜けたは drive_main.mjs の secondPlayer の手順)
  getAllPlayers: () => (options.secondPlayer ? [player, secondPlayer] : [player]),
  sendMessage: (message) => messages.push(message),
};

// ブロックのカスタムコンポーネント (Script API 2.x の custom components V2)。名前 -> コンポーネントの関数の組
export const blockComponents = new Map();
const blockComponentRegistry = {
  registerCustomComponent(name, component) {
    if (!/^[a-z0-9_]+:[a-z0-9_]+$/.test(name)) throw new Error(`NamespaceNameError: ${name}`);
    if (blockComponents.has(name)) throw new Error(`CustomComponentNameError: ${name} is already registered`);
    blockComponents.set(name, component);
  },
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
  beforeEvents: { startup: { subscribe: (handler) => { handler({ customCommandRegistry, blockComponentRegistry }); } } },
  run(callback) {
    pendingRuns.push(callback);
    return pendingRuns.length;
  },
  runInterval(callback) {
    intervals.push(callback);
    return intervals.length;
  },
  clearRun(id) {
    // 番号は intervals の位置 + 1。消した所は null にして、ほかの番号をずらさない
    if (intervals[id - 1]) {
      intervals[id - 1] = null;
      stats.clearedRuns = (stats.clearedRuns ?? 0) + 1;
    }
    const timeout = timeouts.findIndex((t) => t.id === id);
    if (timeout >= 0) timeouts.splice(timeout, 1);
  },
  runTimeout(callback, ticks = 1) {
    const id = 100000 + ++timeoutCount;
    timeouts.push({ id, at: currentTick + Math.max(1, ticks), callback });
    return id;
  },
  get currentTick() {
    return currentTick;
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
  currentTick++;
  for (const callback of pendingRuns.splice(0)) callback();
  for (const t of timeouts.filter((t) => t.at <= currentTick)) {
    timeouts.splice(timeouts.indexOf(t), 1);
    t.callback();
  }
  for (const callback of intervals) if (callback) callback();
  for (const [id, generator] of [...jobs]) {
    for (let i = 0; i < 8; i++) {
      if (generator.next().done) {
        jobs.delete(id);
        break;
      }
    }
  }
}

// --- スクリーンブロックのテスト用の操作 ---

function componentOf(typeId) {
  // ブロックの定義 (BP/blocks/video_screen.json) と同じく、<接頭辞>:video_screen は <接頭辞>:screen_controller を持つ
  const match = /^([a-z0-9_]+):video_screen$/.exec(typeId);
  return match ? blockComponents.get(`${match[1]}:screen_controller`) : undefined;
}

/** プレイヤーがブロックを置いたように、board に置いて onPlace を送る。facing は置いた人が見ていた方向。 */
export function placeBlock(location, typeId, facing = "north") {
  const key = `${location.x},${location.y},${location.z}`;
  board.set(key, typeId);
  blockStates.set(key, { "minecraft:cardinal_direction": facing });
  componentOf(typeId)?.onPlace?.({ block: blockAt(location), dimension, previousBlock: { type: { id: "minecraft:air" } } }, { params: {} });
}

/** プレイヤーがブロックを使う (右クリック)。 */
export function interactBlock(location, who = player) {
  const block = blockAt(location);
  componentOf(block.typeId)?.onPlayerInteract?.({ block, dimension, player: who, face: "Up" }, { params: {} });
}

/** プレイヤーがブロックを壊す (onPlayerBreak と world.afterEvents.playerBreakBlock の両方)。 */
export function breakBlock(location, who = player) {
  const key = `${location.x},${location.y},${location.z}`;
  const typeId = board.get(key) ?? "minecraft:air";
  board.delete(key);
  blockStates.delete(key);
  const event = { block: blockAt(location), dimension, player: who, brokenBlockPermutation: { type: { id: typeId } } };
  componentOf(typeId)?.onPlayerBreak?.(event, { params: {} });
  for (const handler of worldEventHandlers.playerBreakBlock) handler(event);
}

/** 爆発でブロックが消える (コンポーネントの onBreak は送らず、world.afterEvents.blockExplode だけ)。 */
export function explodeBlock(location) {
  const key = `${location.x},${location.y},${location.z}`;
  const typeId = board.get(key) ?? "minecraft:air";
  board.delete(key);
  blockStates.delete(key);
  for (const handler of worldEventHandlers.blockExplode) {
    handler({ block: blockAt(location), dimension, explodedBlockPermutation: { type: { id: typeId } } });
  }
}

/** /setblock のように、イベントを送らずにブロックを置き換える。 */
export function setBlockSilently(location, typeId) {
  const key = `${location.x},${location.y},${location.z}`;
  if (typeId === "minecraft:air") board.delete(key);
  else board.set(key, typeId);
  blockStates.delete(key);
}

/** ワールドを読み込んだ (worldLoad)。 */
export function fireWorldLoad() {
  for (const handler of worldEventHandlers.worldLoad) handler({});
}

/** ワールドの動的プロパティ (テストが保存の内容を見る)。 */
export function getDynamicProperties() {
  return { ...dynamicProperties };
}

/** 動いているタイマー (runInterval) の数。 */
export function activeIntervals() {
  return intervals.filter(Boolean).length;
}

/**
 * スクリプトを読み込み直したとき (ワールドを開き直したとき) のように、タイマー・ジョブ・イベントの登録・
 * コンポーネント・コマンドを消す。ワールドのブロック・動的プロパティ・常時読み込みの区画は残す。
 */
export function restartScripts() {
  intervals.length = 0;
  jobs.clear();
  pendingRuns.length = 0;
  timeouts.length = 0;
  scriptEventHandler = null;
  itemUseHandler = null;
  playerSpawnHandlers.length = 0;
  for (const list of Object.values(worldEventHandlers)) list.length = 0;
  blockComponents.clear();
  customCommands.clear();
  customEnums.clear();
}
