/**
 * Block & Image Video Player - スクリーンの管理 (Screen System Foundation)
 *
 * 複数のスクリーンを、それぞれ独立した再生状態 (ScreenSession) として持つための土台。
 * このファイルは Minecraft の API を import しない。ワールドへの保存・時計・ブロックの設置・音声は、main.js が渡す
 * アダプター (storage・runInterval・レンダラー) を通す。そのため Node だけで単体テストできる (tests/js/screen_system_test.mjs)。
 *
 *   ScreenSession     1 つのスクリーンの状態 (動画・描画方式・設置方式・位置・再生状態・音量・ループ・大きさ・占める範囲)
 *   ScreenRegistry    設置済みのスクリーンを ID と位置で引く表 (同じ位置に 2 つ登録しない。ディメンションが違えば別の位置)
 *   PlaybackScheduler 1 tick の時間とブロック数の予算を、動いているスクリーンで分ける (runInterval は 1 つだけ)
 *   ScreenManager     登録・削除・再生・一時停止・停止・シーク・動画の切り替え・保存・復元
 *
 * 描画方式 (renderer_type: ブロックモード / 画像モード) と設置方式 (mount_type: スクリーンブロック / 将来のプロジェクター) は
 * 別々の値で、どの組み合わせもレンダラーのアダプター (createRenderer) が受け持つ。レンダラーの約束は RENDERER_CONTRACT を参照。
 */

export const STORAGE_VERSION = 1;

/** 再生状態。idle = 動画がまだ無い。stopped = 動画はあるが止めて画面を空にした。 */
export const PLAYBACK_STATES = Object.freeze({ IDLE: "idle", PLAYING: "playing", PAUSED: "paused", STOPPED: "stopped" });

/** 設置方式。projector は将来 (Phase 4) のために値だけ決めておく。 */
export const MOUNT_TYPES = Object.freeze({ BLOCK: "block", PROJECTOR: "projector" });

/** 描画方式。 */
export const RENDERER_TYPES = Object.freeze({ BLOCK: "block", IMAGE: "image" });

/** 既定の上限。スクリーンの数と、同時に再生できる数 (端末の負荷を抑えるため。どちらも無制限にはしない)。 */
export const DEFAULT_LIMITS = Object.freeze({ maxScreens: 16, maxPlaying: 4, maxScale: 4 });

/** 座標として受け付ける範囲 (Minecraft のワールドの端より少し広い)。 */
const MAX_COORDINATE = 30000000;
const SCREEN_ID = /^s[1-9][0-9]{0,8}$/;

/**
 * レンダラー (描画方式ごとのアダプター) が持つ関数。ScreenManager はこれ以外を呼ばない。
 *   prepare(mediaId, options) → Promise<{ ok, reason?, detail?, width, height, box }>
 *       動画を表示できるか調べ (置く場所が空いているか・世界の高さに入るか・読み込まれているか)、できれば空の画面を用意する。
 *       options: { scale }。失敗したときはワールドを変えない (部分的な残骸を残さない)。
 *   play({ fromStart }) / pause() / stop() / seek(seconds) / setVolume(volume)
 *   position() → 秒、duration() → 秒、isRunning() → boolean
 *   wantsTick() → boolean (この tick に描くものがあるか) / tick(budget) (budget: { timeMs, maxYields })
 *   release({ clearBlocks }) → Promise<{ ok, pending? }>  止めて、作ったブロック・常時読み込み・ジョブを片付ける
 *   onFinished: ScreenManager が入れる関数。動画の最後まで再生したらレンダラーが呼ぶ
 */
export const RENDERER_CONTRACT = Object.freeze([
  "prepare", "play", "pause", "stop", "seek", "setVolume", "position", "duration", "isRunning", "wantsTick", "tick", "release",
]);

/** ディメンションと座標から、位置の鍵を作る (違うディメンションの同じ座標は別の鍵)。 */
export function locationKey(dimensionId, position) {
  return `${dimensionId}|${position.x},${position.y},${position.z}`;
}

/** 箱 { x0, y0, z0, x1, y1, z1 } (両端を含む) が重なるか。 */
export function boxesIntersect(a, b) {
  return a.x0 <= b.x1 && b.x0 <= a.x1 && a.y0 <= b.y1 && b.y0 <= a.y1 && a.z0 <= b.z1 && b.z0 <= a.z1;
}

/** 点が箱の中か。 */
export function boxContains(box, p) {
  return p.x >= box.x0 && p.x <= box.x1 && p.y >= box.y0 && p.y <= box.y1 && p.z >= box.z0 && p.z <= box.z1;
}

/** 2 つの角から、小さい方・大きい方をそろえた箱を作る。 */
export function makeBox(a, b) {
  return {
    x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), z0: Math.min(a.z, b.z),
    x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y), z1: Math.max(a.z, b.z),
  };
}

export function sameBox(a, b) {
  return a.x0 === b.x0 && a.y0 === b.y0 && a.z0 === b.z0 && a.x1 === b.x1 && a.y1 === b.y1 && a.z1 === b.z1;
}

function isCoordinate(value) {
  return Number.isInteger(value) && Math.abs(value) <= MAX_COORDINATE;
}

function boxToArray(box) {
  return [box.x0, box.y0, box.z0, box.x1, box.y1, box.z1];
}

function boxFromArray(value) {
  if (!Array.isArray(value) || value.length !== 6 || !value.every(isCoordinate)) return null;
  const [x0, y0, z0, x1, y1, z1] = value;
  if (x0 > x1 || y0 > y1 || z0 > z1) return null;
  return { x0, y0, z0, x1, y1, z1 };
}

/**
 * 1 つのスクリーンの状態。保存・復元できる値だけを持つ (描画の途中の盤面などはレンダラーが持つ)。
 * owned は、このスクリーンが作ったかもしれないブロックの範囲 (今の画面と、片付け終わっていない前の画面)。
 * 削除や動画の切り替えでは、この範囲の外のブロックには触らない。
 */
export class ScreenSession {
  constructor(fields) {
    this.screenId = fields.screenId;
    this.mediaId = fields.mediaId ?? null;
    this.rendererType = fields.rendererType ?? RENDERER_TYPES.BLOCK;
    this.mountType = fields.mountType ?? MOUNT_TYPES.BLOCK;
    this.dimensionId = fields.dimensionId;
    this.position = { x: fields.position.x, y: fields.position.y, z: fields.position.z };
    this.facing = fields.facing ?? 0;
    this.playbackState = fields.playbackState ?? (this.mediaId ? PLAYBACK_STATES.STOPPED : PLAYBACK_STATES.IDLE);
    this.playbackPosition = fields.playbackPosition ?? 0;
    this.volume = fields.volume ?? 1;
    this.loop = fields.loop ?? false;
    this.scale = fields.scale ?? 1;
    this.screenWidth = fields.screenWidth ?? 0;
    this.screenHeight = fields.screenHeight ?? 0;
    this.creationTime = fields.creationTime ?? 0;
    // 今の画面の範囲 (無ければ null) と、前の画面のうち片付け終わっていない範囲
    this.box = fields.box ?? null;
    this.pendingClear = fields.pendingClear ?? [];
    // 操作の途中 (準備・削除) の印。保存しない (開き直したら操作は終わっている)
    this.busy = null;
  }

  get locationKey() {
    return locationKey(this.dimensionId, this.position);
  }

  /** 作ったかもしれないブロックの範囲すべて。 */
  get ownedBoxes() {
    return this.box ? [this.box, ...this.pendingClear] : [...this.pendingClear];
  }

  toJSON() {
    return {
      v: STORAGE_VERSION,
      id: this.screenId,
      media: this.mediaId,
      renderer: this.rendererType,
      mount: this.mountType,
      dim: this.dimensionId,
      pos: [this.position.x, this.position.y, this.position.z],
      facing: this.facing,
      state: this.playbackState,
      at: Math.round(this.playbackPosition * 1000) / 1000,
      volume: this.volume,
      loop: this.loop,
      scale: this.scale,
      w: this.screenWidth,
      h: this.screenHeight,
      created: this.creationTime,
      box: this.box ? boxToArray(this.box) : null,
      pending: this.pendingClear.map(boxToArray),
    };
  }

  /**
   * 保存した値から作り直す。壊れた値 (型が違う・範囲の外・知らない版) なら null。
   * 再生中だったものは一時停止にする (ワールドを開き直したとき、勝手に再生を始めない)。
   */
  static fromJSON(value, limits = DEFAULT_LIMITS) {
    if (!value || typeof value !== "object" || value.v !== STORAGE_VERSION) return null;
    if (typeof value.id !== "string" || !SCREEN_ID.test(value.id)) return null;
    if (value.media !== null && (typeof value.media !== "string" || value.media.length === 0 || value.media.length > 128)) return null;
    if (!Object.values(RENDERER_TYPES).includes(value.renderer)) return null;
    if (!Object.values(MOUNT_TYPES).includes(value.mount)) return null;
    if (typeof value.dim !== "string" || value.dim.length === 0 || value.dim.length > 64) return null;
    if (!Array.isArray(value.pos) || value.pos.length !== 3 || !value.pos.every(isCoordinate)) return null;
    if (![0, 1, 2, 3].includes(value.facing)) return null;
    if (!Object.values(PLAYBACK_STATES).includes(value.state)) return null;
    if (!Number.isFinite(value.at) || value.at < 0) return null;
    if (!Number.isFinite(value.volume) || value.volume < 0 || value.volume > 1) return null;
    if (typeof value.loop !== "boolean") return null;
    if (!Number.isInteger(value.scale) || value.scale < 1 || value.scale > limits.maxScale) return null;
    if (!Number.isInteger(value.w) || value.w < 0 || !Number.isInteger(value.h) || value.h < 0) return null;
    if (!Number.isFinite(value.created)) return null;
    const box = value.box === null ? null : boxFromArray(value.box);
    if (value.box !== null && !box) return null;
    if (!Array.isArray(value.pending)) return null;
    const pendingClear = value.pending.map(boxFromArray);
    if (pendingClear.some((b) => !b)) return null;
    let state = value.state;
    if (state === PLAYBACK_STATES.PLAYING) state = PLAYBACK_STATES.PAUSED;
    if (!value.media) state = PLAYBACK_STATES.IDLE;
    return new ScreenSession({
      screenId: value.id, mediaId: value.media, rendererType: value.renderer, mountType: value.mount, dimensionId: value.dim,
      position: { x: value.pos[0], y: value.pos[1], z: value.pos[2] }, facing: value.facing, playbackState: state,
      playbackPosition: value.at, volume: value.volume, loop: value.loop, scale: value.scale, screenWidth: value.w,
      screenHeight: value.h, creationTime: value.created, box, pendingClear,
    });
  }
}

/** 設置済みのスクリーンの表。ID と位置の両方で引ける。 */
export class ScreenRegistry {
  constructor(limits = DEFAULT_LIMITS) {
    this.limits = limits;
    this.byId = new Map();
    this.byLocation = new Map();
  }

  get size() {
    return this.byId.size;
  }

  /** 登録する。{ ok: true } か、{ ok: false, reason: "duplicate_id" | "location_taken" | "limit" }。 */
  register(session) {
    if (this.byId.has(session.screenId)) return { ok: false, reason: "duplicate_id" };
    if (this.byLocation.has(session.locationKey)) return { ok: false, reason: "location_taken" };
    if (this.byId.size >= this.limits.maxScreens) return { ok: false, reason: "limit" };
    this.byId.set(session.screenId, session);
    this.byLocation.set(session.locationKey, session.screenId);
    return { ok: true };
  }

  /** 登録を外して、外したスクリーンを返す (無ければ null)。 */
  unregister(screenId) {
    const session = this.byId.get(screenId);
    if (!session) return null;
    this.byId.delete(screenId);
    if (this.byLocation.get(session.locationKey) === screenId) this.byLocation.delete(session.locationKey);
    return session;
  }

  get(screenId) {
    return this.byId.get(screenId) ?? null;
  }

  getAt(dimensionId, position) {
    const id = this.byLocation.get(locationKey(dimensionId, position));
    return id ? this.byId.get(id) ?? null : null;
  }

  list() {
    return [...this.byId.values()];
  }

  /** 同じディメンションで、箱 box が作ったかもしれない範囲と重なるスクリーン (exceptId 以外)。無ければ null。 */
  findOverlap(dimensionId, box, exceptId = null) {
    for (const session of this.byId.values()) {
      if (session.screenId === exceptId || session.dimensionId !== dimensionId) continue;
      if (session.ownedBoxes.some((owned) => boxesIntersect(owned, box))) return session;
      // スクリーンの操作のブロックそのものにも重ねない
      if (boxContains(box, session.position)) return session;
    }
    return null;
  }

  /** まだ使われていない ID を作る (保存した通し番号 next から)。返すのは [id, 次の通し番号]。 */
  allocateId(next) {
    let n = Number.isInteger(next) && next > 0 ? next : 1;
    while (this.byId.has(`s${n}`)) n++;
    return [`s${n}`, n + 1];
  }
}

/**
 * 1 tick の描画の予算を、描くものがあるスクリーンで分ける。runInterval は 1 つだけ持ち、描くものが無い状態が
 * idleStopTicks 続いたら止める (使っていないタイマーを残さない)。描くものができたら ensureRunning で動かし直す。
 *
 * sources: () => 描画するもの { key, wantsTick(), tick(budget) } の配列。budget は { timeMs, maxYields }。
 *   1 つだけなら予算は全部 (以前の 1 画面と同じ)。複数なら、残りの時間を残りの数で割る。順番は tick ごとにずらす (公平に)。
 * あるスクリーンの tick が例外を投げても、ほかのスクリーンの tick は続ける (onError に渡す)。
 */
export class PlaybackScheduler {
  constructor({ runInterval, clearRun, now = () => Date.now(), sources, budgetMs = 30, maxYields = 8, minSliceMs = 2,
                idleStopTicks = 100, onError = null }) {
    this.runInterval = runInterval;
    this.clearRun = clearRun;
    this.now = now;
    this.sources = sources;
    this.budgetMs = budgetMs;
    this.maxYields = maxYields;
    this.minSliceMs = minSliceMs;
    this.idleStopTicks = idleStopTicks;
    this.onError = onError;
    this.intervalId = null;
    this.idleTicks = 0;
    this.rotation = 0;
    this.stats = { ticks: 0, started: 0, stopped: 0, lastActive: 0, maxTickMs: 0, errors: 0 };
  }

  get running() {
    return this.intervalId !== null;
  }

  /** 動いていなければ runInterval を 1 つ登録する (何度呼んでも 1 つ)。 */
  ensureRunning() {
    this.idleTicks = 0;
    if (this.intervalId !== null) return;
    this.intervalId = this.runInterval(() => this.tickOnce(), 1);
    this.stats.started++;
  }

  stop() {
    if (this.intervalId === null) return;
    try {
      this.clearRun?.(this.intervalId);
    } catch (e) { /* もう無い */ }
    this.intervalId = null;
    this.stats.stopped++;
  }

  /** 1 tick 分 (runInterval から呼ばれる。テストからも呼べる)。 */
  tickOnce() {
    this.stats.ticks++;
    const active = [];
    for (const source of this.sources()) {
      try {
        if (source.wantsTick()) active.push(source);
      } catch (e) {
        this.reportError(source, e);
      }
    }
    this.stats.lastActive = active.length;
    if (active.length === 0) {
      if (++this.idleTicks >= this.idleStopTicks) this.stop();
      return;
    }
    this.idleTicks = 0;
    const count = active.length;
    const start = this.rotation++ % count;
    const maxYields = Math.max(1, Math.floor(this.maxYields / count));
    const tickStart = this.now();
    for (let i = 0; i < count; i++) {
      const source = active[(start + i) % count];
      const remaining = Math.max(0, this.budgetMs - (this.now() - tickStart));
      const timeMs = Math.max(this.minSliceMs, remaining / (count - i));
      try {
        source.tick({ timeMs, maxYields });
      } catch (e) {
        this.reportError(source, e);
      }
    }
    this.stats.maxTickMs = Math.max(this.stats.maxTickMs, this.now() - tickStart);
  }

  reportError(source, error) {
    this.stats.errors++;
    if (this.onError) {
      try {
        this.onError(source, error);
      } catch (e) { /* 知らせる側の失敗で止めない */ }
    }
  }
}

/** 失敗の結果。reason はプレイヤーに見せる文の鍵 (screen.error.<reason>)。detail は文に入れる値。 */
function fail(reason, detail = []) {
  return { ok: false, reason, detail };
}

/**
 * すべてのスクリーンの登録・操作・保存。操作は { ok, reason } を返す (Promise のものもある)。
 *
 * storage: { read(key) → 文字列 | undefined, write(key, 文字列 | undefined) }。undefined を書くと消す。
 *   1 つのスクリーンを 1 つの鍵 (<prefix>screen.<id>) に、一覧と通し番号を <prefix>screens に保存する
 *   (ワールドの動的プロパティは 1 つ 32 KB までなので、まとめて 1 つにしない)。
 * createRenderer(session) → レンダラー (RENDERER_CONTRACT)。描画方式ごとに main.js が作る。
 * checkMount(session) → "ok" | "missing" | "unknown"。スクリーンのブロックがまだあるか ("unknown" は読み込まれていない)。
 */
export class ScreenManager {
  constructor({ storage, createRenderer, checkMount = () => "ok", scheduler = null, now = () => Date.now(),
                limits = DEFAULT_LIMITS, keyPrefix = "bvp:", log = () => {} }) {
    this.storage = storage;
    this.createRenderer = createRenderer;
    this.checkMount = checkMount;
    this.scheduler = scheduler;
    this.now = now;
    this.limits = limits;
    this.indexKey = `${keyPrefix}screens`;
    this.sessionKeyPrefix = `${keyPrefix}screen.`;
    this.log = log;
    this.registry = new ScreenRegistry(limits);
    this.renderers = new Map();
    this.nextId = 1;
    this.restored = false;
    this.housekeepingTicks = 0;
  }

  // --- 保存と復元 ---

  sessionKey(screenId) {
    return `${this.sessionKeyPrefix}${screenId}`;
  }

  /** 保存した一覧から、すべてのスクリーンを読み直す (1 回だけ)。壊れたものは捨てて数を返す。 */
  restore() {
    if (this.restored) return { restored: 0, dropped: 0 };
    this.restored = true;
    let index = null;
    try {
      index = JSON.parse(this.storage.read(this.indexKey) ?? "null");
    } catch (e) {
      index = null;
    }
    if (!index || typeof index !== "object" || !Array.isArray(index.ids)) {
      return { restored: 0, dropped: index === null ? 0 : 1 };
    }
    this.nextId = Number.isInteger(index.next) && index.next > 0 ? index.next : 1;
    let restored = 0;
    let dropped = 0;
    for (const id of index.ids) {
      let session = null;
      try {
        session = typeof id === "string" ? ScreenSession.fromJSON(JSON.parse(this.storage.read(this.sessionKey(id)) ?? "null"), this.limits) : null;
      } catch (e) {
        session = null;
      }
      if (!session || session.screenId !== id || !this.registry.register(session).ok) {
        dropped++;
        this.log(`dropped a broken or duplicate screen record: ${id}`);
        continue;
      }
      restored++;
      // 再生中で保存されていたもの (ワールドを閉じたとき再生中だった) は、一時停止にしたことを保存し直す
      if (session.playbackState !== PLAYBACK_STATES.PLAYING) {
        try {
          if (JSON.parse(this.storage.read(this.sessionKey(id)) ?? "null")?.state === PLAYBACK_STATES.PLAYING) this.save(session);
        } catch (e) { /* 読めたものだけ */ }
      }
    }
    // 同じ番号を二度使わないよう、通し番号は登録済みの最大より後ろにする
    for (const session of this.registry.list()) {
      this.nextId = Math.max(this.nextId, Number(session.screenId.slice(1)) + 1);
    }
    if (dropped > 0) this.saveIndex();
    return { restored, dropped };
  }

  saveIndex() {
    this.storage.write(this.indexKey, JSON.stringify({ v: STORAGE_VERSION, next: this.nextId, ids: this.registry.list().map((s) => s.screenId) }));
  }

  save(session) {
    if (this.registry.get(session.screenId) !== session) return;
    this.storage.write(this.sessionKey(session.screenId), JSON.stringify(session.toJSON()));
  }

  // --- 登録と削除 ---

  /** スクリーンを登録する (動画はまだ無い)。{ ok, session } か失敗。同じ位置に既にあれば、それを返す (ok, existing)。 */
  create({ dimensionId, position, facing = 0, mountType = MOUNT_TYPES.BLOCK, rendererType = RENDERER_TYPES.BLOCK }) {
    this.restore();
    if (typeof dimensionId !== "string" || !position || ![position.x, position.y, position.z].every(isCoordinate)) {
      return fail("invalid_position");
    }
    if (![0, 1, 2, 3].includes(facing)) return fail("invalid_position");
    if (!Object.values(MOUNT_TYPES).includes(mountType) || !Object.values(RENDERER_TYPES).includes(rendererType)) {
      return fail("invalid_type");
    }
    const existing = this.registry.getAt(dimensionId, position);
    if (existing) return { ok: true, session: existing, existing: true };
    if (this.registry.size >= this.limits.maxScreens) return fail("too_many_screens", [this.limits.maxScreens]);
    const [screenId, next] = this.registry.allocateId(this.nextId);
    const session = new ScreenSession({
      screenId, dimensionId, position, facing, mountType, rendererType, creationTime: this.now(),
    });
    const result = this.registry.register(session);
    if (!result.ok) return fail(result.reason === "limit" ? "too_many_screens" : "invalid_position", [this.limits.maxScreens]);
    this.nextId = next;
    this.save(session);
    this.saveIndex();
    return { ok: true, session };
  }

  get(screenId) {
    this.restore();
    return this.registry.get(screenId);
  }

  at(dimensionId, position) {
    this.restore();
    return this.registry.getAt(dimensionId, position);
  }

  list() {
    this.restore();
    return this.registry.list();
  }

  /** 同じディメンションで box と重なるスクリーン (exceptId 以外)。 */
  findOverlap(dimensionId, box, exceptId = null) {
    this.restore();
    return this.registry.findOverlap(dimensionId, box, exceptId);
  }

  /** レンダラー (無ければ作る)。描画方式が変わったら作り直す。 */
  rendererFor(session) {
    let renderer = this.renderers.get(session.screenId);
    if (renderer && renderer.rendererType !== session.rendererType) {
      this.renderers.delete(session.screenId);
      renderer = null;
    }
    if (!renderer) {
      renderer = this.createRenderer(session);
      renderer.rendererType = session.rendererType;
      renderer.onFinished = () => this.handleFinished(session.screenId);
      this.renderers.set(session.screenId, renderer);
    }
    return renderer;
  }

  /**
   * スクリーンを削除する。再生を止め、作ったブロックを片付けてから表から外す。片付けが終わらなかった範囲
   * (読み込まれていないなど) があれば { ok: true, pending: true } (表からは外し、片付けはレンダラーに任せる)。
   */
  async remove(screenId, { clearBlocks = true } = {}) {
    this.restore();
    const session = this.registry.get(screenId);
    if (!session) return fail("no_screen");
    if (session.busy === "removing") return fail("busy");
    session.busy = "removing";
    const renderer = this.renderers.get(screenId) ?? (session.ownedBoxes.length > 0 ? this.rendererFor(session) : null);
    let pending = false;
    if (renderer) {
      try {
        const result = await renderer.release({ clearBlocks });
        pending = !!result?.pending;
      } catch (e) {
        pending = true;
        this.log(`failed to release screen ${screenId}: ${e}`);
      }
    }
    this.renderers.delete(screenId);
    this.registry.unregister(screenId);
    this.storage.write(this.sessionKey(screenId), undefined);
    this.saveIndex();
    session.busy = null;
    session.playbackState = PLAYBACK_STATES.IDLE;
    return { ok: true, pending, session };
  }

  // --- 再生の操作 ---

  /** 再生中のスクリーンの数 (exceptId 以外)。 */
  playingCount(exceptId = null) {
    let count = 0;
    for (const session of this.registry.list()) {
      if (session.screenId !== exceptId && session.playbackState === PLAYBACK_STATES.PLAYING) count++;
    }
    return count;
  }

  /** 操作できるスクリーンか (あって、準備・削除の途中でない)。できなければ失敗の結果、できれば null。 */
  checkUsable(session) {
    if (!session || this.registry.get(session.screenId) !== session) return fail("no_screen");
    if (session.busy) return fail("busy");
    return null;
  }

  /**
   * 動画を選ぶ (切り替える)。レンダラーが置く場所を調べて空の画面を用意する (Promise)。scale を渡すと大きさも変える。
   * 失敗したときは前の動画と画面のまま。
   */
  async setMedia(screenId, mediaId, { scale = null } = {}) {
    this.restore();
    const session = this.registry.get(screenId);
    const unusable = this.checkUsable(session);
    if (unusable) return unusable;
    if (typeof mediaId !== "string" || mediaId.length === 0) return fail("no_media", [String(mediaId)]);
    const targetScale = scale ?? session.scale;
    if (!Number.isInteger(targetScale) || targetScale < 1 || targetScale > this.limits.maxScale) return fail("invalid_scale");
    session.busy = "preparing";
    const renderer = this.rendererFor(session);
    let result;
    try {
      result = await renderer.prepare(mediaId, { scale: targetScale });
    } catch (e) {
      result = fail("render_error", [String(e?.message ?? e).slice(0, 120)]);
    }
    // 削除が始まっていたら (busy は "removing")、その印は削除が下ろす
    if (session.busy === "preparing") session.busy = null;
    // 準備の間に削除されていたら何もしない (レンダラーは削除で片付け済み)
    if (this.registry.get(screenId) !== session) return fail("no_screen");
    if (!result?.ok) return result ?? fail("render_error");
    const oldBox = session.box;
    session.mediaId = mediaId;
    session.scale = targetScale;
    session.screenWidth = result.width;
    session.screenHeight = result.height;
    session.box = result.box ?? null;
    if (oldBox && !(session.box && sameBox(oldBox, session.box)) && !session.pendingClear.some((b) => sameBox(b, oldBox))) {
      // 前の画面の範囲はレンダラーが片付ける。終わるまでは「作ったかもしれない範囲」として覚えておく
      session.pendingClear.push(oldBox);
    }
    if (Array.isArray(result.cleared)) {
      session.pendingClear = session.pendingClear.filter((b) => !result.cleared.some((c) => sameBox(b, c)));
    }
    session.playbackState = PLAYBACK_STATES.STOPPED;
    session.playbackPosition = 0;
    this.save(session);
    return { ok: true, session };
  }

  /** 再生する (止めた位置から。fromStart なら先頭から)。 */
  play(screenId, { fromStart = false } = {}) {
    this.restore();
    const session = this.registry.get(screenId);
    const unusable = this.checkUsable(session);
    if (unusable) return unusable;
    if (!session.mediaId) return fail("no_media_selected");
    if (session.playbackState !== PLAYBACK_STATES.PLAYING && this.playingCount(screenId) >= this.limits.maxPlaying) {
      return fail("too_many_playing", [this.limits.maxPlaying]);
    }
    const renderer = this.rendererFor(session);
    const start = fromStart || session.playbackState === PLAYBACK_STATES.STOPPED || session.playbackState === PLAYBACK_STATES.IDLE;
    try {
      renderer.setVolume(session.volume);
      renderer.play({ fromStart: start, position: start ? 0 : session.playbackPosition });
    } catch (e) {
      return fail("render_error", [String(e?.message ?? e).slice(0, 120)]);
    }
    session.playbackState = PLAYBACK_STATES.PLAYING;
    if (start) session.playbackPosition = 0;
    this.save(session);
    this.scheduler?.ensureRunning();
    return { ok: true, session };
  }

  pause(screenId) {
    this.restore();
    const session = this.registry.get(screenId);
    const unusable = this.checkUsable(session);
    if (unusable) return unusable;
    if (session.playbackState !== PLAYBACK_STATES.PLAYING) return fail("not_playing");
    const renderer = this.rendererFor(session);
    renderer.pause();
    session.playbackPosition = renderer.position();
    session.playbackState = PLAYBACK_STATES.PAUSED;
    this.save(session);
    return { ok: true, session };
  }

  /** 止めて画面を空にする (動画は選んだまま。次の再生は先頭から)。 */
  stop(screenId) {
    this.restore();
    const session = this.registry.get(screenId);
    const unusable = this.checkUsable(session);
    if (unusable) return unusable;
    if (!session.mediaId) return fail("no_media_selected");
    const renderer = this.rendererFor(session);
    renderer.stop();
    session.playbackState = PLAYBACK_STATES.STOPPED;
    session.playbackPosition = 0;
    this.save(session);
    return { ok: true, session };
  }

  /** seconds 秒へ移る (再生中なら続けて再生、そうでなければ一時停止の状態でその画面を出す)。 */
  seek(screenId, seconds) {
    this.restore();
    const session = this.registry.get(screenId);
    const unusable = this.checkUsable(session);
    if (unusable) return unusable;
    if (!session.mediaId) return fail("no_media_selected");
    if (!Number.isFinite(seconds) || seconds < 0) return fail("invalid_seek");
    const renderer = this.rendererFor(session);
    const target = Math.min(seconds, Math.max(0, renderer.duration()));
    const wasPlaying = session.playbackState === PLAYBACK_STATES.PLAYING;
    renderer.seek(target, { resume: wasPlaying });
    session.playbackPosition = target;
    if (!wasPlaying) session.playbackState = PLAYBACK_STATES.PAUSED;
    this.save(session);
    if (wasPlaying) this.scheduler?.ensureRunning();
    return { ok: true, session };
  }

  setVolume(screenId, volume) {
    this.restore();
    const session = this.registry.get(screenId);
    const unusable = this.checkUsable(session);
    if (unusable) return unusable;
    if (!Number.isFinite(volume)) return fail("invalid_volume");
    session.volume = Math.min(1, Math.max(0, volume));
    this.renderers.get(screenId)?.setVolume(session.volume);
    this.save(session);
    return { ok: true, session };
  }

  setLoop(screenId, loop) {
    this.restore();
    const session = this.registry.get(screenId);
    const unusable = this.checkUsable(session);
    if (unusable) return unusable;
    session.loop = !!loop;
    this.save(session);
    return { ok: true, session };
  }

  /** レンダラーが「最後まで再生した」と知らせたとき。ループなら先頭から、そうでなければ止めた状態にする。 */
  handleFinished(screenId) {
    const session = this.registry.get(screenId);
    if (!session) return;
    const renderer = this.renderers.get(screenId);
    if (session.loop && renderer) {
      renderer.play({ fromStart: true, position: 0 });
      session.playbackPosition = 0;
      session.playbackState = PLAYBACK_STATES.PLAYING;
    } else {
      session.playbackPosition = renderer ? renderer.duration() : session.playbackPosition;
      session.playbackState = PLAYBACK_STATES.PAUSED;
    }
    this.save(session);
  }

  // --- tick ごとの仕事 ---

  /** スケジューラーに渡す、描画するもの (レンダラーと、保存・見回りの仕事)。 */
  tickSources() {
    const sources = [];
    for (const [screenId, renderer] of this.renderers) {
      sources.push({ key: screenId, wantsTick: () => renderer.wantsTick(), tick: (budget) => renderer.tick(budget) });
    }
    sources.push({ key: "screen-housekeeping", wantsTick: () => this.playingCount() > 0, tick: () => this.housekeeping() });
    return sources;
  }

  /**
   * 再生中の位置を 2 秒ごとに保存し、5 秒ごとにスクリーンのブロックがまだあるか見る (壊された・爆発で消えたなど、
   * イベントを取りこぼしても片付ける)。
   */
  housekeeping() {
    this.housekeepingTicks++;
    if (this.housekeepingTicks % 40 === 0) {
      for (const session of this.registry.list()) {
        if (session.playbackState !== PLAYBACK_STATES.PLAYING) continue;
        const renderer = this.renderers.get(session.screenId);
        if (!renderer) continue;
        session.playbackPosition = renderer.position();
        this.save(session);
      }
    }
    if (this.housekeepingTicks % 100 === 0) this.validateMounts();
  }

  /** スクリーンのブロックが無くなったスクリーンを削除する。削除したものの ID を返す。 */
  validateMounts() {
    const removed = [];
    for (const session of this.registry.list()) {
      if (session.busy) continue;
      let status = "unknown";
      try {
        status = this.checkMount(session);
      } catch (e) {
        status = "unknown";
      }
      if (status === "missing") {
        removed.push(session.screenId);
        this.remove(session.screenId).catch((e) => this.log(`failed to remove screen ${session.screenId}: ${e}`));
      }
    }
    return removed;
  }
}
