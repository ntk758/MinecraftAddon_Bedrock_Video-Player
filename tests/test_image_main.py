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
           "screen_blocks": [16, 9], "methods": ["a", "c"], "width": 256, "height": 144},
          {"id": "second", "title": "second", "fps": 30, "frame_count": 300, "audio_chunks": 3, "audio_chunk_seconds": 4,
           "screen_blocks": [16, 9], "methods": ["a"], "width": 256, "height": 144}]

MOCK = r"""
export const log = { messages: [], sounds: [], commands: [], spawned: [], volumes: [], placed: [] };
const dynamic = globalThis.__dynamic ?? (globalThis.__dynamic = new Map());
const entities = globalThis.__entities ?? (globalThis.__entities = new Map());
globalThis.__time ??= 1000;
let nextId = globalThis.__nextId ?? 1;
const intervals = [];
const timeouts = [];
let worldLoad = null;
let entityLoad = null;
let playerSpawn = null;
class Entity {
  constructor(typeId, location) { this.typeId = typeId; this.location = location; this.id = String(nextId++); globalThis.__nextId = nextId;
    this.properties = {}; this.pending = {}; this.isValid = true; this.rotation = null; this.dimension = dimension;
    this.loaded = true; }
  setProperty(name, value) {
    if (!Number.isInteger(value)) throw new Error("not int");
    const [lo, hi] = name.endsWith(":state") ? [0, 2] : name.endsWith(":seq") ? [0, 1023] : [0, 299];
    if (value < lo || value > hi) throw new Error("ArgumentOutOfBoundsError");
    this.pending[name] = value;
  }
  getProperty(name) { return this.properties[name]; }
  setRotation(r) { this.rotation = r; log.placed.push({ location: this.location, yaw: r.y }); }
  remove() { this.isValid = false; entities.delete(this.id); }
}
const dimension = {
  id: "minecraft:overworld",
  spawnEntity(typeId, location) { const e = new Entity(typeId, location); entities.set(e.id, e); log.spawned.push(typeId); return e; },
  runCommand(command) { log.commands.push(command); return { successCount: 1 }; },
  getEntities({ families }) { return [...entities.values()].filter((e) => e.loaded && families.includes("bvp_screen")); },
};
const player = {
  typeId: "minecraft:player", location: { x: 0, y: 64, z: 0 }, dimension,
  getViewDirection: () => ({ x: 0, y: 0, z: -1 }), getRotation: () => ({ x: 0, y: 180 }),
  playSound(id, options) { log.sounds.push([globalThis.__time, id]); log.volumes.push(options?.volume ?? null); },
  // 見ているブロック (テストで globalThis.__lookAt に入れる)
  getBlockFromViewDirection: () => globalThis.__lookAt ?? undefined,
};
export const world = {
  getAbsoluteTime: () => globalThis.__time,
  getDynamicProperty: (k) => dynamic.get(k), setDynamicProperty: (k, v) => dynamic.set(k, v),
  getEntity: (id) => { const e = entities.get(id); return e?.loaded ? e : undefined; }, getDimension: () => dimension, getAllPlayers: () => [player],
  sendMessage: (m) => log.messages.push(m.rawtext[0].translate + " " + JSON.stringify(m.rawtext[0].with ?? {})),
  afterEvents: { worldLoad: { subscribe: (h) => { worldLoad = h; } }, entityLoad: { subscribe: (h) => { entityLoad = h; } },
                 playerSpawn: { subscribe: (h) => { playerSpawn = h; } },
                 itemUse: { subscribe: (h) => { globalThis.__itemUse = h; } } },
};
export const system = {
  runInterval: (f) => intervals.push(f), run: (f) => timeouts.push([0, f]), runTimeout: (f, t) => timeouts.push([t, f]),
  afterEvents: { scriptEventReceive: { subscribe: (h) => { globalThis.__script = h; } } },
  // 独自のコマンドの登録を覚えておき、テストから引数つきで呼ぶ
  beforeEvents: { startup: { subscribe: (h) => h({ customCommandRegistry: {
    registerEnum() {}, registerCommand(definition, callback) { (globalThis.__commands ??= {})[definition.name] = { definition, callback }; },
  } }) } },
};
export const CommandPermissionLevel = { GameDirectors: 1 };
export const CustomCommandParamType = { String: "String", Float: "Float", Integer: "Integer", Enum: "Enum" };
export const CustomCommandStatus = { Success: 0 };
export function loadWorld() { worldLoad?.(); }
export function join() { playerSpawn?.({ player, initialSpawn: true }); }
export const playerEntity = player;
// ワールドを開き直したときのように、スクリプトの登録を消す (dynamic property・時計・エンティティは残す)
export function resetScripts() { intervals.length = 0; timeouts.length = 0; worldLoad = null; }
export function tick() {
  globalThis.__time++;
  for (const e of entities.values()) { Object.assign(e.properties, e.pending); e.pending = {}; }
  for (const f of intervals) f();
  for (const item of timeouts.splice(0)) { if (item[0] <= 0) item[1](); else timeouts.push([item[0] - 1, item[1]]); }
}
export function command(action, message = "") { globalThis.__script({ id: `bvpimg:${action}`, message, sourceEntity: player }); }
export function screen() { return [...entities.values()].find((e) => e.isValid); }
export function screens() { return [...entities.values()].filter((e) => e.isValid); }
// プレイヤーが離れて、スクリーンのチャンクが読み込まれていない / 戻って読み込まれた
export function unloadAll() { for (const e of entities.values()) e.loaded = false; }
export function loadAll() { for (const e of entities.values()) { e.loaded = true; entityLoad?.({ entity: e }); } }
"""

# フォーム: 開いた中身を globalThis.__forms に残し、答えは globalThis.__answers の先頭から使う (無ければ閉じた扱い)
MOCK_UI = r"""
class Form {
  constructor(kind) { this.kind = kind; this.buttons = []; this.sliders = []; globalThis.__forms ??= []; }
  title(t) { this.titleText = t; return this; }
  body(b) { this.bodyText = b; return this; }
  button(label) { this.buttons.push(label.rawtext?.[0]?.translate ?? label.rawtext?.[0]?.text); return this; }
  slider(label, min, max, options) { this.sliders.push({ min, max, ...options }); return this; }
  show() {
    globalThis.__forms.push({ kind: this.kind, title: this.titleText?.rawtext?.[0]?.translate, buttons: this.buttons, sliders: this.sliders });
    const answer = (globalThis.__answers ?? []).shift();
    if (answer === undefined) return Promise.resolve({ canceled: true });
    return Promise.resolve(this.kind === "action" ? { canceled: false, selection: answer } : { canceled: false, formValues: [answer] });
  }
}
export class ActionFormData extends Form { constructor() { super("action"); } }
export class ModalFormData extends Form { constructor() { super("modal"); } }
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
  if (step.cmd) { mock.command(step.cmd, step.arg ?? ""); await new Promise((r) => setTimeout(r, 0)); }
  if (step.tick) for (let i = 0; i < step.tick; i++) mock.tick();
  if (step.unload) mock.unloadAll();
  if (step.load) mock.loadAll();
  if (step.join) mock.join();
  if (step.answers) globalThis.__answers = [...step.answers];
  if (step.use) { globalThis.__itemUse?.({ itemStack: { typeId: step.use }, source: mock.playerEntity }); await new Promise((r) => setTimeout(r, 0)); }
  if (step.forms) out.push({ label: "forms", forms: globalThis.__forms ?? [] });
  if (step.custom) {
    const [name, ...values] = step.custom;
    globalThis.__commands[`bvpimg:${name}`].callback({ sourceEntity: mock.playerEntity }, ...values);
  }
  if (step.defs) out.push({ label: "defs", defs: Object.fromEntries(Object.entries(globalThis.__commands)
    .map(([k, v]) => [k, v.definition.optionalParameters.map((x) => x.type)])) });
  if ("lookAt" in step) globalThis.__lookAt = step.lookAt;
  if (step.count) out.push({ label: step.count, screens: mock.screens().map((e) => e.typeId) });
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
    main = main.replace('from "@minecraft/server-ui"', 'from "./mock_ui.js"')
    (work / "image_main.js").write_text(main.replace('from "@minecraft/server"', 'from "./mock_proxy.js"'), encoding="utf-8")
    (work / "mock_ui.js").write_text(MOCK_UI, encoding="utf-8")
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
    # 置いた直後は最初のコマを止めたまま見せる (置き場所が分かるように)
    assert snaps["setup"]["type"] == "bvpimg:screen_demo_a" and props(snaps["setup"])["state"] == 2
    assert props(snaps["setup"])["frame"] == 0
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


def test_stop_while_far_away_reaches_the_screen_when_it_loads(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 30}, {"unload": True},
                         {"cmd": "pause"}, {"tick": 40}, {"load": True}, {"tick": 2}, {"snap": "back"}])
    back = props(out["snaps"][0])
    assert back["state"] == 2 and 40 <= back["frame"] <= 50


def test_switching_method_far_away_is_refused_and_no_orphan_is_left(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"unload": True}, {"cmd": "method", "arg": "c"}, {"load": True},
                         {"tick": 2}, {"cmd": "method", "arg": "c"}, {"tick": 2}, {"count": "screens"}])
    assert out["snaps"][0]["screens"] == ["bvpimg:screen_demo_c"]


def test_seq_continues_from_the_screen_after_a_script_reload(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 5}, {"cmd": "pause"}, {"tick": 2},
                         {"snap": "before"}, {"reload": 0}, {"tick": 2}, {"cmd": "seek", "arg": "5"}, {"tick": 2},
                         {"snap": "after"}])
    before, after = (props(x) for x in out["snaps"])
    assert after["seq"] != before["seq"] and after["frame"] == 150


def messages(out, key):
    return [m for m in out["log"]["messages"] if m.startswith(f"bvpimg.{key} ")]


def test_seek_without_a_number_explains_the_usage(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "seek", "arg": ""}, {"cmd": "seek", "arg": "7.5"}, {"tick": 1},
                         {"snap": "seek"}])
    assert messages(out, "seek_usage")
    assert props(out["snaps"][0])["frame"] == 225


def test_play_switches_the_video_in_place_and_starts(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "play", "arg": "second"}, {"tick": 21}, {"snap": "play"},
                         {"cmd": "play", "arg": "nothing"}])
    snap = out["snaps"][0]
    assert snap["type"] == "bvpimg:screen_second_a" and props(snap)["state"] == 1
    # 同じ場所に置き直す
    assert out["log"]["placed"][0]["location"] == out["log"]["placed"][1]["location"]
    assert messages(out, "video_not_found")


def test_play_before_setup_asks_for_setup(tmp_path):
    out = run(tmp_path, [{"cmd": "play", "arg": "second"}, {"tick": 2}])
    assert messages(out, "not_setup") and not out["log"]["spawned"]


def test_volume_is_used_for_the_sound_and_zero_mutes(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "volume", "arg": "40"}, {"cmd": "start"}, {"tick": 2},
                         {"cmd": "volume", "arg": "0"}, {"tick": 80}, {"cmd": "volume", "arg": "abc"}])
    assert out["log"]["volumes"] == [0.4]
    assert messages(out, "volume_set") and messages(out, "volume_usage")


def test_stats_reports_the_state(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 20}, {"cmd": "stats"}])
    [line] = messages(out, "stats")
    assert "demo" in line and "bvpimg.state_playing" in line and "bvpimg.screen_loaded" in line


def test_late_joiner_hears_the_chunk_only_near_its_start(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 10}, {"join": True},
                         {"tick": 40}, {"join": True}])
    sounds = [s for _, s in out["log"]["sounds"]]
    # 開始 0.5 秒で入った人には鳴らす、2.5 秒では鳴らさない (次の境目から)
    assert sounds == ["bvpimg.img.demo.chunk_0", "bvpimg.img.demo.chunk_0"]


def test_setup_on_a_wall_faces_out_of_the_block(tmp_path):
    hit = {"block": {"location": {"x": 10, "y": 64, "z": -5}}, "face": "East"}
    out = run(tmp_path, [{"lookAt": hit}, {"cmd": "setup", "arg": "wall"}, {"tick": 2},
                         {"lookAt": {"block": {"location": {"x": 0, "y": 63, "z": 0}}, "face": "Up"}},
                         {"cmd": "setup", "arg": "wall"}])
    [placed] = out["log"]["placed"]
    assert placed["yaw"] == -90
    assert placed["location"] == {"x": 11.02, "y": 64, "z": -4.5}
    # 上の面は壁ではない
    assert messages(out, "wall_not_found")


def test_setup_face_and_align_snap_to_the_grid(tmp_path):
    out = run(tmp_path, [{"cmd": "setup", "arg": "face align"}, {"tick": 2}])
    [placed] = out["log"]["placed"]
    assert placed["yaw"] % 90 == 0
    location = placed["location"]
    assert location["x"] % 1 == 0.5 and location["z"] % 1 == 0.5 and location["y"] % 1 == 0


def test_custom_commands_take_several_words_and_typed_numbers(tmp_path):
    out = run(tmp_path, [{"defs": True}, {"custom": ["setup", "face", "align"]}, {"tick": 2},
                         {"custom": ["seek", 7.5]}, {"tick": 2}, {"snap": "seek"}])
    defs = out["snaps"][0]["defs"]
    # 文字の引数は 4 語まで、seek は小数、volume は整数
    assert defs["bvpimg:setup"] == ["String"] * 4
    assert defs["bvpimg:seek"] == ["Float"] and defs["bvpimg:volume"] == ["Integer"]
    [placed] = out["log"]["placed"]
    assert placed["yaw"] % 90 == 0 and placed["location"]["x"] % 1 == 0.5
    assert props(out["snaps"][1])["frame"] == 225


def test_clock_opens_the_remote_and_its_buttons_work(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2},
                         # 1 番目のボタン (再生) を押す
                         {"answers": [0]}, {"use": "minecraft:clock"}, {"tick": 21}, {"snap": "play"},
                         # 音量のスライダーで 30
                         {"answers": [4, 30]}, {"use": "minecraft:clock"}, {"tick": 1},
                         # シークのスライダーで 5 秒
                         {"answers": [5, 5]}, {"use": "minecraft:clock"}, {"tick": 1}, {"snap": "seek"},
                         # 動画リストから 2 本目
                         {"answers": [6, 1]}, {"use": "minecraft:clock"}, {"tick": 2}, {"snap": "library"},
                         # コンパス (ブロックモードのリモコン) では開かない
                         {"answers": []}, {"use": "minecraft:compass"}, {"forms": True}])
    snaps = {x["label"]: x for x in out["snaps"]}
    assert props(snaps["play"])["state"] == 1
    assert messages(out, "volume_set") and "30" in messages(out, "volume_set")[0]
    assert 150 <= props(snaps["seek"])["frame"] <= 152
    assert snaps["library"]["type"] == "bvpimg:screen_second_a"
    forms = snaps["forms"]["forms"]
    remote = forms[0]
    assert remote["title"] == "bvpimg.remote.title"
    assert remote["buttons"][:3] == ["bvpimg.remote.play", "bvpimg.remote.stop", "bvpimg.remote.next"]
    assert {"kind": "modal"}.items() <= forms[2].items() and forms[2]["sliders"][0]["max"] == 100
    # 時計で開いた回数だけリモコンが開いている (コンパスでは開かない)。動画リストの後はリモコンを開き直す
    assert sum(1 for f in forms if f["title"] == "bvpimg.remote.title") == 5


def test_remote_before_setup_offers_to_place_the_screen(tmp_path):
    out = run(tmp_path, [{"answers": [0]}, {"cmd": "remote"}, {"tick": 3}, {"snap": "placed"}, {"forms": True}])
    assert out["snaps"][0]["type"] == "bvpimg:screen_demo_a"
    assert out["snaps"][1]["forms"][0]["buttons"][0] == "bvpimg.remote.setup_here"
