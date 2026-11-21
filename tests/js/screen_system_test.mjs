// screen_system.js の単体テスト (Minecraft の API を使わないので Node だけで動く)。tests/test_screen_system.py から
// `node --test` で実行する。レンダラーは呼ばれた操作を記録するだけの偽物を使う。
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_LIMITS, PLAYBACK_STATES, PlaybackScheduler, RENDERER_CONTRACT, ScreenManager, ScreenRegistry, ScreenSession,
  boxesIntersect, locationKey, makeBox,
} from "../../screen_system.js";

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    writes: 0,
    read(key) { return data[key]; },
    write(key, value) {
      this.writes++;
      if (value === undefined) delete data[key];
      else data[key] = value;
    },
  };
}

/** 呼ばれた操作を記録する偽のレンダラー。prepare は options で結果を変えられる。 */
function fakeRendererFactory(log, behavior = {}) {
  return (session) => {
    const r = {
      id: session.screenId,
      running: false,
      at: 0,
      volume: 1,
      released: false,
      calls: [],
      prepare(mediaId, { scale }) {
        r.calls.push(["prepare", mediaId, scale]);
        log.push([session.screenId, "prepare", mediaId]);
        if (behavior.prepare) return behavior.prepare(session, mediaId, scale);
        const box = makeBox({ x: session.position.x, y: session.position.y + 1, z: session.position.z },
          { x: session.position.x + 3 * scale, y: session.position.y + 2 * scale, z: session.position.z });
        return Promise.resolve({ ok: true, width: 4 * scale, height: 2 * scale, box });
      },
      play({ fromStart }) { r.calls.push(["play", fromStart]); log.push([session.screenId, "play"]); r.running = true; if (fromStart) r.at = 0; },
      pause() { r.calls.push(["pause"]); log.push([session.screenId, "pause"]); r.running = false; },
      stop() { r.calls.push(["stop"]); log.push([session.screenId, "stop"]); r.running = false; r.at = 0; },
      seek(seconds, { resume }) { r.calls.push(["seek", seconds, resume]); r.at = seconds; },
      setVolume(volume) { r.volume = volume; },
      position() { return r.at; },
      duration() { return behavior.duration ?? 60; },
      isRunning() { return r.running; },
      wantsTick() { return r.running; },
      tick(budget) { r.calls.push(["tick", budget]); r.at += 0.05; },
      release({ clearBlocks }) { r.calls.push(["release", clearBlocks]); log.push([session.screenId, "release"]); r.released = true; r.running = false; return Promise.resolve({ ok: true }); },
    };
    return r;
  };
}

function manager(options = {}) {
  const log = [];
  const storage = options.storage ?? memoryStorage();
  const m = new ScreenManager({
    storage,
    createRenderer: options.createRenderer ?? fakeRendererFactory(log, options.behavior),
    checkMount: options.checkMount,
    now: () => 1700000000000,
    limits: options.limits ?? DEFAULT_LIMITS,
    keyPrefix: "ns:",
  });
  return { m, log, storage };
}

const OVERWORLD = "minecraft:overworld";
const at = (x, y = 64, z = 0) => ({ x, y, z });

test("the renderer contract lists every operation the manager calls", () => {
  assert.deepEqual([...RENDERER_CONTRACT].sort(), ["duration", "isRunning", "pause", "play", "position", "prepare", "release",
    "seek", "setVolume", "stop", "tick", "wantsTick"]);
});

test("a new session has an id, no media and the idle state", () => {
  const { m } = manager();
  const result = m.create({ dimensionId: OVERWORLD, position: at(10), facing: 2 });
  assert.equal(result.ok, true);
  const s = result.session;
  assert.equal(s.screenId, "s1");
  assert.equal(s.mediaId, null);
  assert.equal(s.playbackState, PLAYBACK_STATES.IDLE);
  assert.equal(s.facing, 2);
  assert.equal(s.rendererType, "block");
  assert.equal(s.mountType, "block");
  assert.equal(s.creationTime, 1700000000000);
  assert.deepEqual(s.position, { x: 10, y: 64, z: 0 });
});

test("screen ids are unique and are not reused after a removal", async () => {
  const { m } = manager();
  const a = m.create({ dimensionId: OVERWORLD, position: at(0) }).session;
  const b = m.create({ dimensionId: OVERWORLD, position: at(5) }).session;
  assert.notEqual(a.screenId, b.screenId);
  await m.remove(a.screenId);
  const c = m.create({ dimensionId: OVERWORLD, position: at(0) }).session;
  assert.equal(c.screenId, "s3");
  assert.equal(new Set(m.list().map((s) => s.screenId)).size, 2);
});

test("the same position cannot hold two screens, but another dimension can", () => {
  const { m } = manager();
  const first = m.create({ dimensionId: OVERWORLD, position: at(1) });
  const again = m.create({ dimensionId: OVERWORLD, position: at(1) });
  assert.equal(again.existing, true);
  assert.equal(again.session, first.session);
  const nether = m.create({ dimensionId: "minecraft:nether", position: at(1) });
  assert.equal(nether.ok, true);
  assert.notEqual(nether.session, first.session);
  assert.equal(m.list().length, 2);
  assert.notEqual(locationKey(OVERWORLD, at(1)), locationKey("minecraft:nether", at(1)));
});

test("the registry refuses duplicates and enforces its limit", () => {
  const registry = new ScreenRegistry({ ...DEFAULT_LIMITS, maxScreens: 2 });
  const make = (id, x) => new ScreenSession({ screenId: id, dimensionId: OVERWORLD, position: at(x) });
  assert.equal(registry.register(make("s1", 0)).ok, true);
  assert.equal(registry.register(make("s1", 1)).reason, "duplicate_id");
  assert.equal(registry.register(make("s2", 0)).reason, "location_taken");
  assert.equal(registry.register(make("s2", 1)).ok, true);
  assert.equal(registry.register(make("s3", 2)).reason, "limit");
  assert.equal(registry.unregister("s9"), null);
  assert.equal(registry.unregister("s1").screenId, "s1");
  assert.equal(registry.getAt(OVERWORLD, at(0)), null);
});

test("the manager refuses more screens than the limit", () => {
  const { m } = manager({ limits: { ...DEFAULT_LIMITS, maxScreens: 1 } });
  assert.equal(m.create({ dimensionId: OVERWORLD, position: at(0) }).ok, true);
  const second = m.create({ dimensionId: OVERWORLD, position: at(9) });
  assert.equal(second.ok, false);
  assert.equal(second.reason, "too_many_screens");
});

test("invalid positions, facings and types are refused", () => {
  const { m } = manager();
  assert.equal(m.create({ dimensionId: OVERWORLD, position: { x: 0.5, y: 0, z: 0 } }).reason, "invalid_position");
  assert.equal(m.create({ dimensionId: OVERWORLD, position: at(0), facing: 7 }).reason, "invalid_position");
  assert.equal(m.create({ dimensionId: 3, position: at(0) }).reason, "invalid_position");
  assert.equal(m.create({ dimensionId: OVERWORLD, position: at(0), rendererType: "laser" }).reason, "invalid_type");
  assert.equal(m.list().length, 0);
});

test("three screens play, pause and stop independently", async () => {
  const { m, log } = manager();
  const ids = [0, 10, 20].map((x) => m.create({ dimensionId: OVERWORLD, position: at(x) }).session.screenId);
  for (const [i, id] of ids.entries()) assert.equal((await m.setMedia(id, `video${i}`)).ok, true);
  assert.equal(m.play(ids[0]).ok, true);
  assert.equal(m.play(ids[1]).ok, true);
  assert.equal(m.play(ids[2]).ok, true);
  assert.equal(m.pause(ids[2]).ok, true);
  const before = ids.map((id) => ({ ...m.get(id).toJSON() }));
  const rB = m.renderers.get(ids[1]);
  const rC = m.renderers.get(ids[2]);
  const callsB = rB.calls.length;
  const callsC = rC.calls.length;
  log.length = 0;
  assert.equal(m.stop(ids[0]).ok, true);
  // A だけが止まり、B と C のレンダラーには何も呼ばれず、状態も変わらない
  assert.deepEqual(log, [[ids[0], "stop"]]);
  assert.equal(rB.calls.length, callsB);
  assert.equal(rC.calls.length, callsC);
  assert.equal(m.get(ids[0]).playbackState, PLAYBACK_STATES.STOPPED);
  assert.deepEqual(m.get(ids[1]).toJSON(), before[1]);
  assert.deepEqual(m.get(ids[2]).toJSON(), before[2]);
  assert.equal(m.get(ids[1]).playbackState, PLAYBACK_STATES.PLAYING);
  assert.equal(m.get(ids[2]).playbackState, PLAYBACK_STATES.PAUSED);
  assert.equal(m.get(ids[1]).mediaId, "video1");
  // A を削除しても同じ
  await m.remove(ids[0]);
  assert.equal(rB.calls.length, callsB);
  assert.equal(m.get(ids[1]).playbackState, PLAYBACK_STATES.PLAYING);
});

test("play needs a video, and pause needs playback", async () => {
  const { m } = manager();
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  assert.equal(m.play(id).reason, "no_media_selected");
  assert.equal(m.stop(id).reason, "no_media_selected");
  await m.setMedia(id, "v");
  assert.equal(m.pause(id).reason, "not_playing");
  assert.equal(m.play(id).ok, true);
  assert.equal(m.renderers.get(id).calls.at(-1)[1], true, "the first play starts from the beginning");
  m.pause(id);
  m.play(id);
  assert.equal(m.renderers.get(id).calls.at(-1)[1], false, "after a pause it resumes");
});

test("seek clamps to the video, keeps playing, and shows a stopped screen as paused", async () => {
  const { m } = manager({ behavior: { duration: 30 } });
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  await m.setMedia(id, "v");
  assert.equal(m.seek(id, 12).ok, true);
  assert.equal(m.get(id).playbackState, PLAYBACK_STATES.PAUSED);
  assert.deepEqual(m.renderers.get(id).calls.at(-1), ["seek", 12, false]);
  m.play(id);
  m.seek(id, 99);
  assert.deepEqual(m.renderers.get(id).calls.at(-1), ["seek", 30, true]);
  assert.equal(m.get(id).playbackState, PLAYBACK_STATES.PLAYING);
  assert.equal(m.seek(id, -1).reason, "invalid_seek");
  assert.equal(m.seek(id, Number.NaN).reason, "invalid_seek");
});

test("volume is clamped and loop is stored per screen", async () => {
  const { m } = manager();
  const a = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  const b = m.create({ dimensionId: OVERWORLD, position: at(9) }).session.screenId;
  m.setVolume(a, 1.7);
  m.setVolume(b, 0.25);
  m.setLoop(a, true);
  assert.equal(m.get(a).volume, 1);
  assert.equal(m.get(b).volume, 0.25);
  assert.equal(m.get(a).loop, true);
  assert.equal(m.get(b).loop, false);
  assert.equal(m.setVolume(a, Number.NaN).reason, "invalid_volume");
});

test("at most maxPlaying screens play at the same time", async () => {
  const { m } = manager({ limits: { ...DEFAULT_LIMITS, maxPlaying: 2 } });
  const ids = [0, 10, 20].map((x) => m.create({ dimensionId: OVERWORLD, position: at(x) }).session.screenId);
  for (const id of ids) await m.setMedia(id, "v");
  assert.equal(m.play(ids[0]).ok, true);
  assert.equal(m.play(ids[1]).ok, true);
  const third = m.play(ids[2]);
  assert.equal(third.reason, "too_many_playing");
  assert.deepEqual(third.detail, [2]);
  // 再生中のものを押し直すのは数に入れない
  assert.equal(m.play(ids[0]).ok, true);
  m.pause(ids[1]);
  assert.equal(m.play(ids[2]).ok, true);
});

test("a screen being prepared refuses other operations, and removal during preparation wins", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { m } = manager({ behavior: { prepare: () => gate } });
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  const pending = m.setMedia(id, "v");
  assert.equal(m.get(id).busy, "preparing");
  assert.equal(m.play(id).reason, "busy");
  assert.equal((await m.setMedia(id, "w")).reason, "busy");
  const removal = m.remove(id);
  release({ ok: true, width: 4, height: 2, box: makeBox(at(0, 65), at(3, 66)) });
  assert.equal((await pending).reason, "no_screen");
  assert.equal((await removal).ok, true);
  assert.equal(m.get(id), null);
});

test("a failed preparation keeps the previous video and screen", async () => {
  let fail = false;
  const { m } = manager({
    behavior: {
      prepare: (session, mediaId, scale) => Promise.resolve(fail
        ? { ok: false, reason: "blocked", detail: [1, 2, 3] }
        : { ok: true, width: 4 * scale, height: 2, box: makeBox(at(0, 65), at(3 * scale, 66)) }),
    },
  });
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  await m.setMedia(id, "first");
  const box = { ...m.get(id).box };
  fail = true;
  const result = await m.setMedia(id, "second", { scale: 2 });
  assert.equal(result.reason, "blocked");
  assert.deepEqual(result.detail, [1, 2, 3]);
  assert.equal(m.get(id).mediaId, "first");
  assert.equal(m.get(id).scale, 1);
  assert.deepEqual(m.get(id).box, box);
});

test("switching to a bigger screen remembers the old area until the renderer clears it", async () => {
  const { m } = manager();
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  await m.setMedia(id, "a");
  const first = m.get(id).box;
  await m.setMedia(id, "b", { scale: 2 });
  const s = m.get(id);
  assert.equal(s.scale, 2);
  assert.equal(s.screenWidth, 8);
  assert.deepEqual(s.pendingClear, [first]);
  assert.equal(s.ownedBoxes.length, 2);
  assert.equal((await m.setMedia(id, "c", { scale: 9 })).reason, "invalid_scale");
});

test("finished playback loops or stops at the end", async () => {
  const { m } = manager();
  const a = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  const b = m.create({ dimensionId: OVERWORLD, position: at(9) }).session.screenId;
  for (const id of [a, b]) {
    await m.setMedia(id, "v");
    m.play(id);
  }
  m.setLoop(a, true);
  m.renderers.get(a).onFinished();
  m.renderers.get(b).onFinished();
  assert.equal(m.get(a).playbackState, PLAYBACK_STATES.PLAYING);
  assert.deepEqual(m.renderers.get(a).calls.at(-1), ["play", true]);
  assert.equal(m.get(b).playbackState, PLAYBACK_STATES.PAUSED);
  assert.equal(m.get(b).playbackPosition, 60);
});

test("sessions survive a restart; playing ones come back paused", async () => {
  const storage = memoryStorage();
  const first = manager({ storage }).m;
  const a = first.create({ dimensionId: OVERWORLD, position: at(0), facing: 1 }).session.screenId;
  const b = first.create({ dimensionId: "minecraft:nether", position: at(4) }).session.screenId;
  await first.setMedia(a, "clip", { scale: 3 });
  first.play(a);
  first.renderers.get(a).at = 12.5;
  first.setVolume(a, 0.4);
  first.setLoop(a, true);
  first.pause(a);
  first.play(a);

  const second = manager({ storage }).m;
  const restored = second.restore();
  assert.deepEqual(restored, { restored: 2, dropped: 0 });
  const s = second.get(a);
  assert.equal(s.mediaId, "clip");
  assert.equal(s.playbackState, PLAYBACK_STATES.PAUSED);
  assert.equal(s.playbackPosition, 12.5);
  assert.equal(s.volume, 0.4);
  assert.equal(s.loop, true);
  assert.equal(s.scale, 3);
  assert.equal(s.facing, 1);
  assert.deepEqual(s.box, first.get(a).box);
  assert.equal(second.get(b).dimensionId, "minecraft:nether");
  // 通し番号も続きから (消した番号を使い直さない)
  assert.equal(second.create({ dimensionId: OVERWORLD, position: at(30) }).session.screenId, "s3");
});

test("broken saved records are dropped without losing the good ones", () => {
  const good = new ScreenSession({ screenId: "s2", dimensionId: OVERWORLD, position: at(1), creationTime: 5 }).toJSON();
  const storage = memoryStorage({
    "ns:screens": JSON.stringify({ v: 1, next: 9, ids: ["s1", "s2", "s3", "s4", 7] }),
    "ns:screen.s1": "{not json",
    "ns:screen.s2": JSON.stringify(good),
    "ns:screen.s3": JSON.stringify({ ...good, id: "s3", volume: 9 }),
    "ns:screen.s4": JSON.stringify({ ...good, id: "s4" }), // s2 と同じ位置
  });
  const { m } = manager({ storage });
  assert.deepEqual(m.restore(), { restored: 1, dropped: 4 });
  assert.equal(m.list().length, 1);
  assert.equal(m.get("s2").position.x, 1);
  assert.deepEqual(JSON.parse(storage.data["ns:screens"]).ids, ["s2"]);
  assert.equal(m.create({ dimensionId: OVERWORLD, position: at(50) }).session.screenId, "s9");
});

test("a broken index is ignored", () => {
  for (const index of ["oops", JSON.stringify({ ids: "s1" }), JSON.stringify(null)]) {
    const { m } = manager({ storage: memoryStorage({ "ns:screens": index }) });
    assert.equal(m.restore().restored, 0);
    assert.equal(m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId, "s1");
  }
});

test("fromJSON validates every field", () => {
  const base = new ScreenSession({ screenId: "s1", mediaId: "v", dimensionId: OVERWORLD, position: at(1), creationTime: 1,
    box: makeBox(at(0, 65), at(3, 66)) }).toJSON();
  assert.ok(ScreenSession.fromJSON(base));
  const broken = [
    { v: 2 }, { id: "x1" }, { id: "s0" }, { media: 5 }, { renderer: "laser" }, { mount: "wall" }, { dim: "" }, { pos: [0, 0] },
    { pos: [0.5, 0, 0] }, { pos: [0, 0, 1e12] }, { facing: 4 }, { state: "dancing" }, { at: -1 }, { volume: 2 }, { loop: "yes" },
    { scale: 0 }, { scale: 99 }, { w: -1 }, { created: "now" }, { box: [3, 0, 0, 0, 0, 0] }, { box: [1, 2] }, { pending: [[1]] },
    { pending: "none" },
  ];
  for (const change of broken) assert.equal(ScreenSession.fromJSON({ ...base, ...change }), null, JSON.stringify(change));
  assert.equal(ScreenSession.fromJSON(null), null);
  assert.equal(ScreenSession.fromJSON("s1"), null);
  // 動画の無い記録は、保存した状態に関係なく idle で戻る
  assert.equal(ScreenSession.fromJSON({ ...base, media: null, state: "paused" }).playbackState, PLAYBACK_STATES.IDLE);
});

test("operations on unknown or removed screens fail cleanly", async () => {
  const { m } = manager();
  for (const result of [m.play("s9"), m.pause("s9"), m.stop("s9"), m.seek("s9", 1), m.setVolume("s9", 1), m.setLoop("s9", true),
    await m.setMedia("s9", "v"), await m.remove("s9")]) {
    assert.equal(result.reason, "no_screen");
  }
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  assert.equal((await m.setMedia(id, "")).reason, "no_media");
  assert.equal((await m.setMedia(id, undefined)).reason, "no_media");
});

test("removing a screen releases its renderer and deletes its record", async () => {
  const { m, storage } = manager();
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  await m.setMedia(id, "v");
  m.play(id);
  const renderer = m.renderers.get(id);
  const result = await m.remove(id);
  assert.equal(result.ok, true);
  assert.equal(renderer.released, true);
  assert.deepEqual(renderer.calls.at(-1), ["release", true]);
  assert.equal(storage.data["ns:screen.s1"], undefined);
  assert.deepEqual(JSON.parse(storage.data["ns:screens"]).ids, []);
  assert.equal(m.renderers.size, 0);
  assert.equal((await m.remove(id)).reason, "no_screen");
});

test("a renderer that throws on release still lets the screen go, marked pending", async () => {
  const factory = (session) => ({ ...fakeRendererFactory([])(session), release() { throw new Error("boom"); } });
  const { m } = manager({ createRenderer: factory });
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  await m.setMedia(id, "v");
  const result = await m.remove(id);
  assert.equal(result.ok, true);
  assert.equal(result.pending, true);
  assert.equal(m.get(id), null);
});

test("screens whose block is gone are removed by the mount check", async () => {
  const missing = new Set();
  const { m } = manager({ checkMount: (s) => (missing.has(s.screenId) ? "missing" : s.screenId === "s3" ? "unknown" : "ok") });
  for (const x of [0, 10, 20]) m.create({ dimensionId: OVERWORLD, position: at(x) });
  missing.add("s2");
  assert.deepEqual(m.validateMounts(), ["s2"]);
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(m.list().map((s) => s.screenId), ["s1", "s3"]);
});

test("overlap checks use the owned area and the control block", async () => {
  const { m } = manager();
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  await m.setMedia(id, "v");
  const owned = m.get(id).box;
  assert.equal(m.findOverlap(OVERWORLD, owned)?.screenId, id);
  assert.equal(m.findOverlap(OVERWORLD, owned, id), null);
  assert.equal(m.findOverlap("minecraft:nether", owned), null);
  assert.equal(m.findOverlap(OVERWORLD, makeBox(at(0, 64), at(0, 64)))?.screenId, id, "the control block itself");
  assert.equal(m.findOverlap(OVERWORLD, makeBox(at(100), at(101))), null);
  assert.equal(boxesIntersect(makeBox(at(0), at(2)), makeBox(at(2), at(4))), true);
  assert.equal(boxesIntersect(makeBox(at(0), at(1)), makeBox(at(2), at(4))), false);
});

test("housekeeping saves the playing position every 2 seconds", async () => {
  const { m, storage } = manager();
  const id = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  await m.setMedia(id, "v");
  m.play(id);
  m.renderers.get(id).at = 7.25;
  for (let i = 0; i < 39; i++) m.housekeeping();
  assert.equal(JSON.parse(storage.data["ns:screen.s1"]).at, 0);
  m.housekeeping();
  assert.equal(JSON.parse(storage.data["ns:screen.s1"]).at, 7.25);
});

// --- PlaybackScheduler ---

function fakeSystem() {
  const sys = {
    intervals: new Map(),
    nextId: 0,
    cleared: [],
    runInterval(callback) { sys.intervals.set(++sys.nextId, callback); return sys.nextId; },
    clearRun(id) { sys.cleared.push(id); sys.intervals.delete(id); },
    tick() { for (const callback of [...sys.intervals.values()]) callback(); },
  };
  return sys;
}

function source(key, log, { wants = true, cost = 0, clock = null, throws = false } = {}) {
  return {
    key,
    wants,
    wantsTick() { return this.wants; },
    tick(budget) {
      log.push([key, budget]);
      if (clock) clock.t += cost;
      if (throws) throw new Error(`${key} broke`);
    },
  };
}

test("a single source gets the whole budget, like the old single screen", () => {
  const sys = fakeSystem();
  const log = [];
  const sources = [source("a", log)];
  const scheduler = new PlaybackScheduler({ runInterval: sys.runInterval, clearRun: sys.clearRun, now: () => 0, sources: () => sources });
  scheduler.ensureRunning();
  sys.tick();
  assert.deepEqual(log, [["a", { timeMs: 30, maxYields: 8 }]]);
});

test("several sources share the time and block budget fairly", () => {
  const sys = fakeSystem();
  const clock = { t: 0 };
  const log = [];
  const sources = [source("a", log, { cost: 10, clock }), source("b", log, { cost: 10, clock }), source("c", log, { cost: 10, clock })];
  const scheduler = new PlaybackScheduler({ runInterval: sys.runInterval, clearRun: sys.clearRun, now: () => clock.t, sources: () => sources });
  scheduler.ensureRunning();
  sys.tick();
  assert.deepEqual(log.map(([key, b]) => [key, b.timeMs, b.maxYields]), [["a", 10, 2], ["b", 10, 2], ["c", 10, 2]]);
  log.length = 0;
  sys.tick();
  // 順番は tick ごとにずれる
  assert.deepEqual(log.map(([key]) => key), ["b", "c", "a"]);
  // 前のものが予算を使い切っても、後のものにも最低の時間を渡す (3 tick 目の順番は c, a, b)
  log.length = 0;
  sources[2].tick = function (budget) { log.push(["c", budget]); clock.t += 100; };
  sys.tick();
  assert.deepEqual(log.map(([key, b]) => [key, b.timeMs]), [["c", 10], ["a", 2], ["b", 2]]);
});

test("ensureRunning registers one interval however often it is called", () => {
  const sys = fakeSystem();
  const scheduler = new PlaybackScheduler({ runInterval: sys.runInterval, clearRun: sys.clearRun, sources: () => [] });
  scheduler.ensureRunning();
  scheduler.ensureRunning();
  scheduler.ensureRunning();
  assert.equal(sys.intervals.size, 1);
  assert.equal(scheduler.stats.started, 1);
});

test("the scheduler stops its interval when nothing needs to draw, and restarts on demand", () => {
  const sys = fakeSystem();
  const log = [];
  const s = source("a", log, { wants: false });
  const scheduler = new PlaybackScheduler({ runInterval: sys.runInterval, clearRun: sys.clearRun, sources: () => [s], idleStopTicks: 5 });
  scheduler.ensureRunning();
  for (let i = 0; i < 4; i++) sys.tick();
  assert.equal(scheduler.running, true);
  sys.tick();
  assert.equal(scheduler.running, false);
  assert.deepEqual(sys.cleared, [1]);
  assert.equal(sys.intervals.size, 0);
  s.wants = true;
  scheduler.ensureRunning();
  sys.tick();
  assert.equal(log.length, 1);
  assert.equal(sys.intervals.size, 1);
});

test("a source that throws does not stop the others", () => {
  const sys = fakeSystem();
  const log = [];
  const errors = [];
  const sources = [source("a", log, { throws: true }), source("b", log)];
  const scheduler = new PlaybackScheduler({
    runInterval: sys.runInterval, clearRun: sys.clearRun, now: () => 0, sources: () => sources,
    onError: (src, e) => errors.push([src.key, e.message]),
  });
  scheduler.ensureRunning();
  sys.tick();
  sys.tick();
  assert.deepEqual(log.map(([key]) => key).sort(), ["a", "a", "b", "b"]);
  assert.deepEqual(errors, [["a", "a broke"], ["a", "a broke"]]);
  assert.equal(scheduler.stats.errors, 2);
});

test("the manager's tick sources follow its renderers", async () => {
  const sys = fakeSystem();
  const { m } = manager();
  const scheduler = new PlaybackScheduler({ runInterval: sys.runInterval, clearRun: sys.clearRun, now: () => 0,
    sources: () => m.tickSources(), idleStopTicks: 3 });
  m.scheduler = scheduler;
  const a = m.create({ dimensionId: OVERWORLD, position: at(0) }).session.screenId;
  const b = m.create({ dimensionId: OVERWORLD, position: at(9) }).session.screenId;
  await m.setMedia(a, "v");
  await m.setMedia(b, "v");
  m.play(a);
  m.play(b);
  assert.equal(scheduler.running, true);
  sys.tick();
  const ticksOf = (id) => m.renderers.get(id).calls.filter(([op]) => op === "tick");
  assert.equal(ticksOf(a).length, 1);
  assert.equal(ticksOf(b).length, 1);
  assert.equal(ticksOf(a)[0][1].maxYields, 2, "two screens and the housekeeping share the yields");
  m.pause(a);
  sys.tick();
  assert.equal(ticksOf(a).length, 1, "a paused screen is released from the schedule");
  assert.equal(ticksOf(b).length, 2);
  m.stop(b);
  for (let i = 0; i < 3; i++) sys.tick();
  assert.equal(scheduler.running, false, "the interval is freed when nothing plays");
});

// --- 2026-11 の外部の監査 (muse) ---

/** 保存先と onDropped だけを渡した ScreenManager (保存の名前は既定の bvp:)。 */
function plain(storage, extra = {}) {
  return new ScreenManager({ storage, createRenderer: fakeRendererFactory([]), now: () => 1700000000000, ...extra });
}

test("the owner is saved and restored, and older records without one still load", () => {
  const storage = memoryStorage();
  const first = plain(storage);
  const { session } = first.create({ dimensionId: OVERWORLD, position: at(0) });
  assert.equal(first.setOwner(session.screenId, "-1001", "Steve"), true);
  // もう持ち主がいれば変えない
  assert.equal(first.setOwner(session.screenId, "-1002", "Alex"), false);
  const again = plain(storage).get(session.screenId);
  assert.equal(again.ownerId, "-1001");
  assert.equal(again.ownerName, "Steve");
  const old = JSON.parse(storage.data[`bvp:screen.${session.screenId}`]);
  delete old.owner;
  storage.data[`bvp:screen.${session.screenId}`] = JSON.stringify(old);
  const restored = plain(storage).get(session.screenId);
  assert.ok(restored);
  assert.equal(restored.ownerId, null);
  assert.equal(ScreenSession.fromJSON({ ...old, owner: ["", "x"] }), null);
  assert.equal(ScreenSession.fromJSON({ ...old, owner: "Steve" }), null);
});

test("long media ids from older add-ons are restored", () => {
  const storage = memoryStorage();
  const first = plain(storage);
  const { session } = first.create({ dimensionId: OVERWORLD, position: at(0) });
  const record = { ...session.toJSON(), media: "m".repeat(200), state: PLAYBACK_STATES.PAUSED };
  storage.data[`bvp:screen.${session.screenId}`] = JSON.stringify(record);
  assert.equal(plain(storage).get(session.screenId).mediaId, "m".repeat(200));
});

test("a record that cannot be restored hands its areas to onDropped", () => {
  const storage = memoryStorage();
  const first = plain(storage);
  const { session } = first.create({ dimensionId: OVERWORLD, position: at(0) });
  const record = { ...session.toJSON(), media: "m".repeat(300), box: [0, 65, 0, 3, 66, 0], pending: [[10, 65, 0, 12, 66, 0]] };
  storage.data[`bvp:screen.${session.screenId}`] = JSON.stringify(record);
  const dropped = [];
  const second = plain(storage, { onDropped: (dimensionId, boxes) => dropped.push([dimensionId, boxes]) });
  assert.equal(second.get(session.screenId), null);
  assert.equal(dropped.length, 1);
  assert.equal(dropped[0][0], OVERWORLD);
  assert.deepEqual(dropped[0][1].map((b) => [b.x0, b.x1]), [[0, 3], [10, 12]]);
});
