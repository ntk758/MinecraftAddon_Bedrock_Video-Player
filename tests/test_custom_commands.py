"""Script API 2.x への移行のテスト (モックの Minecraft、Node が要る)。

- 独自のコマンド (/badapple:setup など) をチート無しで使えるように登録し (cheatsRequired: false)、/scriptevent と同じ処理を
  次の tick に行う (コマンドの処理の中はワールドを変えられない)
- manifest の依存が 2.6.0 / 2.0.0、min_engine_version が 1.26.10
- リモコンのスライダーを @minecraft/server-ui 2.0.0 の書き方で作る
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_playback_control import drive, scene  # noqa: E402,F401 (scene はフィクスチャ)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

ACTIONS = {"setup", "start", "play", "stop", "list", "remote", "render", "bench", "stats"}


def test_manifest_uses_script_api_2():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    deps = {d["module_name"]: d["version"] for d in manifest["dependencies"]}
    assert deps == {"@minecraft/server": "2.6.0", "@minecraft/server-ui": "2.0.0"}
    assert manifest["header"]["min_engine_version"] == [1, 26, 10]


def test_commands_are_registered_without_cheats(scene, tmp_path):
    path, _ = scene
    out = drive(tmp_path / "addon", {"demo": path, "other": path}, [],
                listing_extra={"other": {"hidden": True}})
    commands = {c["name"]: c for c in out["customCommands"]}
    assert set(commands) == {f"badapple:{a}" for a in ACTIONS}
    assert all(c["cheatsRequired"] is False and c["permissionLevel"] == 1 for c in commands.values())
    # 動画 ID は候補に出る (一覧に出さない動画は除く)
    assert out["customEnums"] == {"badapple:video": ["demo"]}
    assert commands["badapple:play"]["optionalParameters"] == [{"name": "badapple:video", "type": "Enum"}]
    assert commands["badapple:setup"]["optionalParameters"] == [{"name": "options", "type": "String"}]


def test_commands_set_up_and_play_like_scriptevent(scene, tmp_path):
    path, _ = scene
    steps = [{"command": "badapple:setup"}, {"tick": 1}, {"command": "badapple:play", "args": ["demo"]},
             {"until": "bvp.playback_finished"}]
    out = drive(tmp_path / "addon", {"demo": path}, steps)
    assert out["errors"] == []
    assert all(r == {"status": 0} for r in out["commandResults"])
    assert any("bvp.setup_done" in m for m in out["messages"])
    assert any("bvp.playback_finished" in m for m in out["messages"])


def test_command_options_reach_setup(scene, tmp_path):
    """/badapple:setup wall (文字の引数) は /scriptevent badapple:setup wall と同じ。"""
    path, _ = scene
    by_command = drive(tmp_path / "a", {"demo": path}, [{"command": "badapple:setup", "args": ["wall"]}, {"tick": 1},
                                                         {"world": "w"}])
    by_event = drive(tmp_path / "b", {"demo": path}, [{"event": "badapple:setup", "message": "wall"}, {"world": "w"}])
    assert by_command["errors"] == [] and by_event["errors"] == []
    assert sorted(map(tuple, by_command["worlds"][0]["blocks"])) == sorted(map(tuple, by_event["worlds"][0]["blocks"]))


def test_volume_slider_uses_the_2_0_form(scene, tmp_path):
    """リモコンの音量のスライダーを開いて値を返す (モックは 1.x の slider の書き方を失敗させる)。"""
    path, _ = scene
    steps = [{"event": "badapple:setup"}, {"ui": [{"selection": 4}, {"formValues": [50]}]}]
    out = drive(tmp_path / "addon", {"demo": path}, steps)
    assert out["errors"] == []
    assert any("bvp.volume_set" in m for m in out["messages"]), out["messages"][-3:]
