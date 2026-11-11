"""はじめての人の案内: ワールドに入ったときの使い方 (1 回だけ)、setup の後の見方、途中で入った人の音、パックの説明。"""
import io
import json
import shutil
import zipfile

import pytest

import pack_builder
from test_playback_control import drive, scene  # noqa: F401  (scene は fixture)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")


def keys(out, key):
    return [m for m in out["messages"] if f"bvp.{key}" in m]


def test_welcome_is_shown_once_and_not_after_setup(scene, tmp_path):
    steps = [{"spawn": True}, {"spawn": True}, {"event": "badapple:setup"}]
    out = drive(tmp_path / "a", {"demo": scene[0]}, steps)
    assert out["errors"] == []
    welcome = keys(out, "welcome")
    assert len(welcome) == 1 and "badapple:" in welcome[0] and '"to":"player"' in welcome[0]
    # 画面を置いた後のワールドでは、新しく入った人にも出さない
    later = drive(tmp_path / "b", {"demo": scene[0]}, [{"event": "badapple:setup"}, {"spawn": True}])
    assert keys(later, "welcome") == []


def test_respawning_does_not_repeat_the_welcome(scene, tmp_path):
    out = drive(tmp_path / "a", {"demo": scene[0]}, [{"spawn": "respawn"}])
    assert keys(out, "welcome") == []


@pytest.mark.parametrize("options,hint", [("", "setup_view_floor"), ("wall", None)])
def test_setup_explains_how_to_look_at_a_flat_screen(scene, tmp_path, options, hint):
    out = drive(tmp_path / "a", {"demo": scene[0]}, [{"event": "badapple:setup", "message": options}])
    assert out["errors"] == []
    assert bool(keys(out, "setup_view_floor")) == (hint == "setup_view_floor")


def test_a_player_who_joins_right_after_a_chunk_starts_hears_it(scene, tmp_path):
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 12}, {"spawn": True}]
    out = drive(tmp_path / "a", {"demo": scene[0]}, steps, listing_extra={"demo": {"audio_chunks": 1}})
    assert out["errors"] == []
    # 1 回目は再生の開始のとき、2 回目は入ってきた人の分
    assert out["sounds"].count("badapple.demo.chunk_0") == 2
    late = drive(tmp_path / "b", {"demo": scene[0]}, [{"event": "badapple:setup"}, {"event": "badapple:start"},
                                                      {"tick": 60}, {"spawn": True}],
                 listing_extra={"demo": {"audio_chunks": 1}})
    # チャンクが始まって 1 秒より後に入った人には、ずれるので鳴らさない (次の境目から)
    assert late["sounds"].count("badapple.demo.chunk_0") == 1


def test_pack_description_is_a_short_english_how_to():
    video = {"id": "a", "title": "a", "frames_js": 'export const FRAME_DATA = {"frame_count":1,"width":1,"height":1};',
             "thumbnail": None, "sounds": []}
    z = zipfile.ZipFile(io.BytesIO(pack_builder.build_addon_bytes([video], "P", "movie")))
    for path in ("BP/manifest.json", "RP/manifest.json"):
        description = json.loads(z.read(path))["header"]["description"]
        assert "/movie:setup wall" in description and "/movie:start" in description
        assert description.isascii()
