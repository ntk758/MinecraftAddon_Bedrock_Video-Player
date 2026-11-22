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
export const log = { messages: [], sounds: [], commands: [], spawned: [], volumes: [], placed: [], spawnRotations: [], writes: [], cameras: [], playerMessages: [] };
const dynamic = globalThis.__dynamic ?? (globalThis.__dynamic = new Map());
const entities = globalThis.__entities ?? (globalThis.__entities = new Map());
globalThis.__time ??= 1000;
// 実時間 (Date.now()、ms)。tick ごとに globalThis.__tickMs (既定 50 = TPS 20) だけ進む
globalThis.__now ??= 1000000;
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
    const [lo, hi] = name.endsWith(":state") ? [0, 2] : name.endsWith(":seq") ? [0, 1023] : [0, globalThis.__maxFrame ?? 299];
    if (value < lo || value > hi) throw new Error("ArgumentOutOfBoundsError");
    this.pending[name] = value;
  }
  getProperty(name) { return this.properties[name]; }
  setRotation(r) { this.rotation = r; log.placed.push({ location: this.location, yaw: r.y }); }
  remove() { this.isValid = false; entities.delete(this.id); }
}
const dimension = {
  id: "minecraft:overworld",
  // options.initialRotation は生まれた時の向き (体も同じ向きで生まれる)。log.spawnRotations に残す
  spawnEntity(typeId, location, options) { const e = new Entity(typeId, location); entities.set(e.id, e); log.spawned.push(typeId);
    log.spawnRotations.push(options?.initialRotation ?? null); return e; },
  runCommand(command) { log.commands.push(command); return { successCount: 1 }; },
  getEntities({ families }) { return [...entities.values()].filter((e) => e.loaded && families.includes("bvp_screen")); },
};
const player = {
  typeId: "minecraft:player", id: "p1", location: { x: 0, y: 64, z: 0 }, dimension,
  // カメラ (MMD のカメラの視点)。setCamera と clear を log.cameras に残す
  camera: { setCamera(preset, options) { log.cameras.push([preset, options]); }, clear() { log.cameras.push(["clear"]); } },
  sendMessage(m) { log.playerMessages.push(m.rawtext[0].translate); },
  getViewDirection: () => ({ x: 0, y: 0, z: -1 }), getRotation: () => ({ x: 0, y: 180 }),
  playSound(id, options) { log.sounds.push([globalThis.__time, id, globalThis.__now]); log.volumes.push(options?.volume ?? null); },
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
  globalThis.__now += globalThis.__tickMs ?? 50;
  for (const e of entities.values()) {
    // クライアントに届くプロパティ (書いた次の tick に反映される)。時計の模擬 (test_image_clock.py) が使う
    if (Object.keys(e.pending).length) log.writes.push([globalThis.__now, e.id, { ...e.pending }]);
    Object.assign(e.properties, e.pending); e.pending = {};
  }
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
// image_main.js の時計 (Date.now) をモックの実時間にする
globalThis.__maxFrame = Number(process.argv[3] ?? 299);
globalThis.__now = 1000000;
Date.now = () => globalThis.__now;
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
  if ("tickMs" in step) globalThis.__tickMs = step.tickMs;
  // tick を進めずに実時間だけ進める (シングルプレイのポーズ画面など)
  if (step.wait) globalThis.__now += step.wait;
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
  if ("reload" in step) { generation++; globalThis.__time += step.reload; globalThis.__now += step.reload * 50; await load(generation); }
  if (step.state) out.push({ label: step.state, state: globalThis.__dynamic.get("bvpimg:image_state") });
  if (step.setState) globalThis.__dynamic.set("bvpimg:image_state", step.setState);
  if (step.snap) {
    const e = mock.screen();
    out.push({ label: step.snap, type: e?.typeId ?? null, props: e ? { ...e.properties } : null, time: globalThis.__time });
  }
}
console.log(JSON.stringify({ snaps: out, log: mock.log }));
"""


def run(tmp_path, steps, videos=VIDEOS, main=ROOT / "image_mode" / "image_main.js"):
    """steps をモックで動かす。videos は動画の一覧、main は動かすスクリプト (時計の模擬が古い版と比べるのに使う)。"""
    work = tmp_path / "work"
    (work / "node_modules" / "@minecraft" / "server").mkdir(parents=True)
    shutil.copy(main, work / "image_main.js")
    (work / "image_videos.js").write_text(
        f'export const NAMESPACE = "bvpimg";\nexport const IMAGE_VIDEOS = {json.dumps(videos)};\n', encoding="utf-8")
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
    max_frame = max(video["frame_count"] for video in videos) - 1
    result = subprocess.run(["node", "driver.mjs", json.dumps(steps), str(max_frame)], cwd=work, capture_output=True, text=True,
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
    # 再開は、止めたコマを含む音声のチャンク (4 秒) の頭から (音がすぐ鳴るように)。2 秒で止めたので 0 秒から 21 tick
    assert paused["frame"] >= 55 and 28 <= props(snaps["resumed"])["frame"] <= 33
    assert props(snaps["seek"])["frame"] == 240
    # seq は書くたびに変わる
    assert len({props(s)["seq"] for s in out["snaps"]}) == len(out["snaps"])


def test_audio_starts_on_chunk_boundaries_and_stops_on_pause(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 100}, {"cmd": "pause"}, {"tick": 2}])
    sounds = [s[1] for s in out["log"]["sounds"]]
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
                         {"snap": "before"}, {"reload": 0}, {"tick": 2}, {"cmd": "seek", "arg": "4"}, {"tick": 2},
                         {"snap": "after"}])
    before, after = (props(x) for x in out["snaps"])
    assert after["seq"] != before["seq"] and after["frame"] == 120


def messages(out, key):
    return [m for m in out["log"]["messages"] if m.startswith(f"bvpimg.{key} ")]


def test_seek_without_a_number_explains_the_usage(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "seek", "arg": ""}, {"cmd": "seek", "arg": "7.5"}, {"tick": 1},
                         {"snap": "seek"}])
    assert messages(out, "seek_usage")
    # 7.5 秒は、音声のチャンクの頭の 8 秒へそろえる
    assert props(out["snaps"][0])["frame"] == 240


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
    sounds = [s[1] for s in out["log"]["sounds"]]
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


def test_screen_is_spawned_already_facing_its_rotation(tmp_path):
    # setRotation だけだと体 (板) が既定の向きのまま約 1 秒残り、再生の最初が横向きに映った (実機 2026-11-15)。
    # 置くとき・動画を替えて置き直すとき・方式を替えるとき、どれも生まれた時から向いている
    hit = {"block": {"location": {"x": 10, "y": 64, "z": -5}}, "face": "East"}
    out = run(tmp_path, [{"lookAt": hit}, {"cmd": "setup", "arg": "wall"}, {"tick": 2},
                         {"cmd": "play", "arg": "second"}, {"tick": 2}, {"cmd": "play", "arg": "demo"}, {"tick": 2},
                         {"cmd": "method", "arg": "c"}, {"tick": 2}])
    assert len(out["log"]["spawned"]) == 4
    assert out["log"]["spawnRotations"] == [-90, -90, -90, -90]
    assert [p["yaw"] for p in out["log"]["placed"]] == [-90, -90, -90, -90]


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
    # 7.5 秒は、音声のチャンクの頭の 8 秒へそろえる
    assert props(out["snaps"][1])["frame"] == 240


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
    # 5 秒は、近いほうの音声のチャンクの頭 (4 秒) へそろえる
    assert 120 <= props(snaps["seek"])["frame"] <= 122
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


def test_seek_and_resume_start_the_sound_at_once(tmp_path):
    """シークと再開は音声のチャンクの頭にそろえるので、0.3 秒以内に音が鳴る (TODO.md の P0-3)。音声の無い動画はそろえない。"""
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 50}, {"cmd": "seek", "arg": "6.9"},
                         {"tick": 6}, {"snap": "seek"}, {"tick": 20}, {"cmd": "pause"}, {"tick": 10}, {"cmd": "resume"},
                         {"tick": 6}, {"snap": "resumed"}])
    sounds = out["log"]["sounds"]
    seek_at = sounds[1][2]
    # シークした tick に 8 秒のチャンク (chunk_2) が鳴り、再開した tick に同じチャンクがもう一度鳴る
    assert [s[1] for s in sounds] == ["bvpimg.img.demo.chunk_0", "bvpimg.img.demo.chunk_2", "bvpimg.img.demo.chunk_2"]
    snaps = {s["label"]: s for s in out["snaps"]}
    assert 240 <= props(snaps["seek"])["frame"] <= 250
    assert 240 <= props(snaps["resumed"])["frame"] <= 250
    assert sounds[2][2] - seek_at >= 1000


def test_seek_past_the_last_chunk_head_keeps_the_asked_position(tmp_path):
    """7 秒の動画 (チャンクは 0 秒と 4 秒) で 6.5 秒へシーク: 近いほうの頭 (8 秒) は動画の外なので、4 秒へ戻さずに頼まれた所へ行く
    (以前は 4 秒へ戻っていた)。5 秒へのシークは今までどおり 4 秒の頭へそろえる。"""
    short = [dict(VIDEOS[0], frame_count=210, audio_chunks=2)]
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "seek", "arg": "6.5"}, {"tick": 1}, {"snap": "late"},
                         {"cmd": "seek", "arg": "5"}, {"tick": 1}, {"snap": "early"}], videos=short)
    snaps = {s["label"]: props(s) for s in out["snaps"]}
    assert snaps["late"]["frame"] == 195
    assert snaps["early"]["frame"] == 120


def test_seek_without_audio_keeps_the_exact_position(tmp_path):
    silent = [dict(VIDEOS[0], audio_chunks=0)]
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"cmd": "seek", "arg": "6.9"}, {"tick": 1},
                         {"snap": "seek"}], videos=silent)
    assert props(out["snaps"][0])["frame"] == 207


CAMERA_VIDEO = {"id": "dance", "title": "dance", "fps": 20, "frame_count": 41, "audio_chunks": 0,
                "screen_blocks": [4, 4], "methods": ["a"], "width": 64, "height": 64,
                "glass": {"figure": {"rig": {"seconds": 2, "camera": {
                    "rate": 20, "points": [[0, 1, -5 - n / 10, 0, 1, 0] for n in range(41)]}}}}}


def test_mmd_camera_view_follows_the_dance_and_goes_back(tmp_path):
    """camera で MMD のカメラの視点にする (目と注視点を、スクリーンの向きで世界の座標にする)。もう一度で元に戻る。"""
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 1}, {"cmd": "start"}, {"cmd": "camera"}, {"tick": 10},
                         {"cmd": "camera"}, {"tick": 2}], videos=[CAMERA_VIDEO])
    cameras = out["log"]["cameras"]
    moves = [c for c in cameras if c[0] == "minecraft:free"]
    assert len(moves) >= 10 and cameras[-1] == ["clear"]
    first, last = moves[0][1], moves[-1][1]
    # プレイヤー (z = 0、北を向く) の前に置いたスクリーンは、人物の正面 (geometry の -z) がプレイヤーの方 (+z) を向く
    screen_z = first["facingLocation"]["z"]
    assert first["location"]["z"] > screen_z
    assert first["location"]["y"] == pytest.approx(65) and first["facingLocation"]["y"] == pytest.approx(65)
    # 時間とともに離れていく (点の z が -5 → -6)
    assert last["location"]["z"] - last["facingLocation"]["z"] > first["location"]["z"] - first["facingLocation"]["z"]
    assert first["easeOptions"]["easeTime"] > 0
    assert any("camera_on" in m for m in out["log"]["playerMessages"])
    assert any("camera_off" in m for m in out["log"]["playerMessages"])


def test_mmd_camera_is_cleared_on_stop_and_refused_without_a_camera(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 1}, {"cmd": "start"}, {"cmd": "camera"}, {"tick": 3},
                         {"cmd": "stop"}, {"tick": 2}], videos=[CAMERA_VIDEO])
    assert out["log"]["cameras"][-1] == ["clear"]
    out = run(tmp_path / "plain", [{"cmd": "setup"}, {"tick": 1}, {"cmd": "camera"}, {"tick": 2}])
    assert out["log"]["cameras"] == [] and any("camera_none" in m for m in out["log"]["playerMessages"])


def test_remote_offers_the_camera_only_when_there_is_one(tmp_path):
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 1}, {"answers": []}, {"use": "minecraft:clock"}, {"forms": True}],
              videos=[CAMERA_VIDEO])
    remote = out["snaps"][-1]["forms"][0]
    assert any("camera_on" in json.dumps(b) for b in remote["buttons"])
    out = run(tmp_path / "plain", [{"cmd": "setup"}, {"tick": 1}, {"answers": []}, {"use": "minecraft:clock"},
                                   {"forms": True}])
    assert not any("camera" in json.dumps(b) for b in out["snaps"][-1]["forms"][0]["buttons"])
