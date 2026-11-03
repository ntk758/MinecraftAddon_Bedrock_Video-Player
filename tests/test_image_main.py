"""画像モードの再生制御 (image_mode/image_main.js) を、小さな Minecraft のモックで動かす。

モックが持つのは image_main.js が使う API だけ (world の時計・dynamic property・エンティティ・プロパティ・音・コマンド)。
ワールドの開き直しは、dynamic property・時計・エンティティを残したまま、スクリプトを新しく読み込み直して確かめる。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

VIDEOS = [{"id": "demo", "title": "demo", "fps": 30, "frame_count": 300, "audio_chunks": 3, "audio_chunk_seconds": 4,
           "screen_blocks": [16, 9], "methods": ["a", "c"]}]

MOCK = r"""
export const log = { messages: [], sounds: [], commands: [], spawned: [] };
const dynamic = globalThis.__dynamic ?? (globalThis.__dynamic = new Map());
const entities = globalThis.__entities ?? (globalThis.__entities = new Map());
globalThis.__time ??= 1000;
let nextId = globalThis.__nextId ?? 1;
const intervals = [];
const timeouts = [];
let worldLoad = null;
class Entity {
  constructor(typeId, location) { this.typeId = typeId; this.location = location; this.id = String(nextId++); globalThis.__nextId = nextId;
    this.properties = {}; this.pending = {}; this.isValid = true; this.rotation = null; }
  setProperty(name, value) {
    if (!Number.isInteger(value)) throw new Error("not int");
    const [lo, hi] = name.endsWith(":state") ? [0, 2] : name.endsWith(":seq") ? [0, 1023] : [0, 299];
    if (value < lo || value > hi) throw new Error("ArgumentOutOfBoundsError");
    this.pending[name] = value;
  }
  getProperty(name) { return this.properties[name]; }
  setRotation(r) { this.rotation = r; }
  remove() { this.isValid = false; entities.delete(this.id); }
}
const dimension = {
  id: "minecraft:overworld",
  spawnEntity(typeId, location) { const e = new Entity(typeId, location); entities.set(e.id, e); log.spawned.push(typeId); return e; },
  runCommand(command) { log.commands.push(command); return { successCount: 1 }; },
};
const player = {
  typeId: "minecraft:player", location: { x: 0, y: 64, z: 0 }, dimension,
  getViewDirection: () => ({ x: 0, y: 0, z: -1 }), getRotation: () => ({ x: 0, y: 180 }),
  playSound(id) { log.sounds.push([globalThis.__time, id]); },
};
export const world = {
  getAbsoluteTime: () => globalThis.__time,
  getDynamicProperty: (k) => dynamic.get(k), setDynamicProperty: (k, v) => dynamic.set(k, v),
  getEntity: (id) => entities.get(id), getDimension: () => dimension, getAllPlayers: () => [player],
  sendMessage: (m) => log.messages.push(m.rawtext[0].translate + " " + JSON.stringify(m.rawtext[0].with ?? {})),
  afterEvents: { worldLoad: { subscribe: (h) => { worldLoad = h; } } },
};
export const system = {
  runInterval: (f) => intervals.push(f), run: (f) => timeouts.push([0, f]), runTimeout: (f, t) => timeouts.push([t, f]),
  afterEvents: { scriptEventReceive: { subscribe: (h) => { globalThis.__script = h; } } },
  beforeEvents: { startup: { subscribe: () => {} } },
};
export const CommandPermissionLevel = { GameDirectors: 1 };
export const CustomCommandParamType = { String: "String" };
export const CustomCommandStatus = { Success: 0 };
export function loadWorld() { worldLoad?.(); }
// ワールドを開き直したときのように、スクリプトの登録を消す (dynamic property・時計・エンティティは残す)
export function resetScripts() { intervals.length = 0; timeouts.length = 0; worldLoad = null; }
export function tick() {
  globalThis.__time++;
  for (const e of entities.values()) { Object.assign(e.properties, e.pending); e.pending = {}; }
  for (const f of intervals) f();
  for (const item of timeouts.splice(0)) { if (item[0] <= 0) item[1](); else timeouts.push([item[0] - 1, item[1]]); }
}
export function command(action, message = "") { globalThis.__script({ id: `bvpimg:${action}`, message, sourceEntity: player }); }
export function screen() { return [...entities.values()][0]; }
"""

DRIVER = r"""
const steps = JSON.parse(process.argv[2]);
const out = [];
let mock = null;
async function load(n) {
  if (!mock) {
    mock = await import("./node_modules/@minecraft/server/index.js");
    globalThis.__mock = mock;
  } else {
    mock.resetScripts();
  }
  await import(`./image_main.js?v=${n}`);
  mock.loadWorld();
}
let generation = 0;
await load(generation);
for (const step of steps) {
  if (step.cmd) mock.command(step.cmd, step.arg ?? "");
  if (step.tick) for (let i = 0; i < step.tick; i++) mock.tick();
  if ("reload" in step) { generation++; globalThis.__time += step.reload; await load(generation); }
  if (step.snap) {
    const e = mock.screen();
    out.push({ label: step.snap, type: e?.typeId ?? null, props: e ? { ...e.properties } : null, time: globalThis.__time });
  }
}
console.log(JSON.stringify({ snaps: out, log: mock.log }));
"""


def run(tmp_path, steps):
    work = tmp_path / "work"
    (work / "node_modules" / "@minecraft" / "server").mkdir(parents=True)
    shutil.copy(ROOT / "image_mode" / "image_main.js", work / "image_main.js")
    (work / "image_videos.js").write_text(
        f'export const NAMESPACE = "bvpimg";\nexport const IMAGE_VIDEOS = {json.dumps(VIDEOS)};\n', encoding="utf-8")
    (work / "node_modules" / "@minecraft" / "server" / "index.js").write_text(MOCK, encoding="utf-8")
    # image_main.js の "@minecraft/server" をモックへ向ける (読み込み直しで同じモジュールの状態を使うため)
    main = (work / "image_main.js").read_text(encoding="utf-8")
    (work / "image_main.js").write_text(main.replace('from "@minecraft/server"', 'from "./mock_proxy.js"'), encoding="utf-8")
    (work / "mock_proxy.js").write_text(
        "const m = globalThis.__mock;\nexport const { world, system, CommandPermissionLevel, CustomCommandParamType, "
        "CustomCommandStatus } = m;\n", encoding="utf-8")
    (work / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    (work / "driver.mjs").write_text(DRIVER, encoding="utf-8")
    result = subprocess.run(["node", "driver.mjs", json.dumps(steps)], cwd=work, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def props(snap):
    return {k.split(":")[1]: v for k, v in snap["props"].items()}


def test_play_pause_resume_and_seek(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"snap": "setup"},
                         {"cmd": "start"}, {"tick": 41}, {"snap": "2s"},
                         {"cmd": "pause"}, {"tick": 40}, {"snap": "paused"},
                         {"cmd": "resume"}, {"tick": 21}, {"snap": "resumed"},
                         {"cmd": "seek", "arg": "8"}, {"tick": 1}, {"snap": "seek"}])
    snaps = {s["label"]: s for s in out["snaps"]}
    assert snaps["setup"]["type"] == "bvpimg:screen_demo_a" and props(snaps["setup"])["state"] == 0
    # 2 秒 (40 tick) で 60 コマ前後。書き直しは 1 秒ごと
    two = props(snaps["2s"])
    assert two["state"] == 1 and 55 <= two["frame"] <= 61
    paused = props(snaps["paused"])
    assert paused["state"] == 2
    # 一時停止の後は止めたコマから進める
    assert props(snaps["resumed"])["frame"] >= paused["frame"] + 25
    assert props(snaps["seek"])["frame"] == 240
    # seq は書くたびに変わる
    assert len({props(s)["seq"] for s in out["snaps"]}) == len(out["snaps"])


def test_audio_starts_on_chunk_boundaries_and_stops_on_pause(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 100}, {"cmd": "pause"}, {"tick": 2}])
    sounds = [s for _, s in out["log"]["sounds"]]
    assert sounds == ["bvpimg.img.demo.chunk_0", "bvpimg.img.demo.chunk_1"]
    assert any(c.startswith("stopsound @a bvpimg.img.demo.chunk_1") for c in out["log"]["commands"])


def test_reload_resumes_where_it_was(tmp_path):
    # ワールドを閉じている間は時計 (absolute time) が進まないので、続きのコマから戻る
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 60}, {"snap": "before"},
                         {"reload": 0}, {"tick": 25}, {"snap": "after"}])
    before, after = (props(s) for s in out["snaps"])
    assert after["state"] == 1 and after["frame"] >= before["frame"]
    assert any("reload_resumed" in m for m in out["log"]["messages"])


def test_method_switch_keeps_position(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 30}, {"cmd": "method", "arg": "c"},
                         {"tick": 3}, {"snap": "c"}])
    snap = out["snaps"][0]
    assert snap["type"] == "bvpimg:screen_demo_c"
    assert props(snap)["state"] == 1 and props(snap)["frame"] >= 40


def test_end_of_video_stops_on_the_last_frame(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 230}, {"snap": "end"}])
    end = props(out["snaps"][0])
    assert end["state"] == 2 and end["frame"] == 299
