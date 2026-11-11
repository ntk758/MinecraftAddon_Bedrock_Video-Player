"""ブラウザ版の旧版向けの出力 (legacy_target.py): Script API 1.x のモックで最後まで再生できること、Windows 版の出力が
変わらないこと。

旧版のモックは、今のモック (tests/js/minecraft_mock) から 2.x にしか無いもの (独自のコマンド・TickingAreaManager・
isChunkLoaded・beforeEvents.startup) を消し、スライダーを 1.x の書き方 (刻み・既定値を引数で渡す) だけにしたもの。
"""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

import legacy_target
import pack_builder
from test_playback_control import drive, reference_frames, scene  # noqa: F401  (scene は fixture)

ROOT = Path(__file__).resolve().parent.parent


def _sub(text, old, new, count=1):
    assert text.count(old) == count, old[:60]
    return text.replace(old, new)


def make_legacy_mock(work):
    """drive() の work に置いたモックを、Script API 1.x (@minecraft/server 1.19.0 / server-ui 1.2.0) にする。"""
    server = work / "node_modules" / "@minecraft" / "server" / "index.js"
    s = server.read_text(encoding="utf-8")
    s = _sub(s, "export const CommandPermissionLevel = { Any: 0, GameDirectors: 1, Admin: 2, Host: 3, Owner: 4 };\n", "")
    s, n = re.subn(r"export const CustomCommandParamType = \{.*?\};\n", "", s, flags=re.S)
    assert n == 1
    s = _sub(s, "export const CustomCommandStatus = { Success: 0, Failure: 1 };\n", "")
    s = _sub(s, "  get tickingAreaManager() {\n    return tickingAreaManager;\n  },\n", "")
    s, n = re.subn(r"  isChunkLoaded\(location\) \{.*?\n  \},\n", "", s, count=1, flags=re.S)
    assert n == 1
    s, n = re.subn(r"  beforeEvents: \{ startup:.*?\n", "", s, count=1)
    assert n == 1
    server.write_text(s, encoding="utf-8")
    ui = work / "node_modules" / "@minecraft" / "server-ui" / "index.js"
    u = ui.read_text(encoding="utf-8")
    u = _sub(u, 'if (typeof sliderOptions === "number" || arguments.length > 4) throw new Error("slider: 2.0 takes (label, min, max, options)");',
             'if (typeof sliderOptions === "object") throw new Error("slider: 1.x takes (label, min, max, step, default)");')
    ui.write_text(u, encoding="utf-8")


def legacy_prepare(work):
    main = work / "main.js"
    main.write_text(legacy_target.main_text(main.read_text(encoding="utf-8")), encoding="utf-8")
    make_legacy_mock(work)


def test_every_marker_is_replaced_and_no_2x_api_is_left():
    text = legacy_target.main_text((ROOT / "main.js").read_text(encoding="utf-8"))
    for token in ("CustomCommand", "CommandPermissionLevel", "tickingAreaManager", "valueStep", "beforeEvents", "<legacy:"):
        assert token not in text, token
    assert "tickingarea add" in text and "form.slider(label, minimum, maximum, step, value)" in text


def test_a_broken_marker_is_an_error():
    text = (ROOT / "main.js").read_text(encoding="utf-8").replace("// </legacy:ticking>\n", "")
    with pytest.raises(ValueError, match="legacy:ticking"):
        legacy_target.main_text(text)


def test_latest_target_changes_nothing():
    text = (ROOT / "main.js").read_text(encoding="utf-8")
    assert legacy_target.main_text(text, "latest") == text
    assert legacy_target.lang_text("a /%1setup", "latest") == "a /%1setup"


def test_manifest_and_lang_for_the_legacy_target():
    manifest = json.loads(legacy_target.manifest_text((ROOT / "manifest.json").read_text(encoding="utf-8")))
    assert manifest["header"]["min_engine_version"] == [1, 21, 80]
    assert {d["module_name"]: d["version"] for d in manifest["dependencies"] if "module_name" in d} == {
        "@minecraft/server": "1.19.0", "@minecraft/server-ui": "1.2.0"}
    lang = legacy_target.lang_text("bvp.setup_done=§aDone. /%1start\nbvp.video_not_found=§c'%1' ... /%2list")
    assert lang == "bvp.setup_done=§aDone. /scriptevent %1start\nbvp.video_not_found=§c'%1' ... /scriptevent %2list"


def _addon(**kwargs):
    video = {"id": "demo", "title": "demo", "frames_js": 'export const FRAME_DATA = {"frame_count":1,"width":1,"height":1};',
             "thumbnail": None, "sounds": []}
    return zipfile.ZipFile(io.BytesIO(pack_builder.build_addon_bytes([video], "P", "ns", **kwargs)))


def test_legacy_addon_from_pack_builder():
    z = _addon(main_text=legacy_target.main_text(pack_builder.MAIN_SCRIPT.read_text(encoding="utf-8")),
               manifest_text=legacy_target.manifest_text(pack_builder.MANIFEST.read_text(encoding="utf-8")),
               lang_transform=legacy_target.lang_text)
    assert json.loads(z.read("RP/manifest.json"))["header"]["min_engine_version"] == [1, 21, 80]
    main = z.read("BP/scripts/main.js").decode("utf-8")
    assert 'const EVENT_NAMESPACE = "ns";' in main and "CustomCommand" not in main
    lang = z.read("RP/texts/en_US.lang").decode("utf-8")
    assert "/scriptevent %1start" in lang and not re.search(r"(?<!scriptevent )/%\d", lang)


def test_windows_output_does_not_use_the_transform():
    """Windows 版 (既定の引数) は main.js・manifest・翻訳を書き換えない。"""
    z = _addon()
    main = z.read("BP/scripts/main.js").decode("utf-8")
    assert main == pack_builder.MAIN_SCRIPT.read_text(encoding="utf-8").replace(
        'const EVENT_NAMESPACE = "badapple";', 'const EVENT_NAMESPACE = "ns";')
    assert json.loads(z.read("BP/manifest.json"))["header"]["min_engine_version"] == [1, 26, 10]
    assert "/%1start" in z.read("RP/texts/en_US.lang").decode("utf-8")


def test_unchanged_main_does_not_load_on_the_legacy_api(scene, tmp_path):
    with pytest.raises(AssertionError):
        drive(tmp_path / "addon", {"demo": scene[0]}, [{"event": "badapple:setup"}], prepare=make_legacy_mock)


def test_legacy_addon_plays_like_the_latest_one(scene, tmp_path):
    path, data = scene
    steps = [{"event": "badapple:setup"}, {"event": "badapple:play", "message": "demo"}, {"until": "bvp.playback_finished"},
             {"settle": 3}, {"snap": "end", "video": "demo"}]
    legacy = drive(tmp_path / "legacy", {"demo": path}, steps, prepare=legacy_prepare)
    latest = drive(tmp_path / "latest", {"demo": path}, steps)
    assert legacy["errors"] == []
    assert any("playback_finished" in m for m in legacy["messages"])
    assert legacy["snaps"][0]["board"] == latest["snaps"][0]["board"] == reference_frames(data)[-1]
    # 常時読み込みは tickingarea コマンドで登録する
    assert any(c.startswith("tickingarea add") for c in legacy["commands"])


def test_legacy_sliders_and_setup_again(scene, tmp_path):
    steps = [{"event": "badapple:setup"}, {"ui": [{"selection": 4}, {"formValues": [50]}]},
             {"event": "badapple:start"}, {"tick": 5}, {"ui": [{"selection": 5}, {"formValues": [1]}]}, {"tick": 5},
             {"x": 200, "z": 300}, {"event": "badapple:setup"}]
    out = drive(tmp_path / "addon", {"demo": scene[0]}, steps, prepare=legacy_prepare)
    assert out["errors"] == []
    assert any("volume_set" in m for m in out["messages"])
    # 2 回目の setup は、前に登録した区画を外してから登録し直す
    adds = [c for c in out["commands"] if c.startswith("tickingarea add")]
    removes = [c for c in out["commands"] if c.startswith("tickingarea remove")]
    assert len(adds) >= 2 and any(adds[0].split()[-1] in r for r in removes)
