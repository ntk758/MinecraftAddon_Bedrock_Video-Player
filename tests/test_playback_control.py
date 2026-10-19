"""再生操作の結合テスト: シーク中の再開、置けなかったマスのクリア、動画の切り替え、tickingarea の後片付け。

main.js をモックの Minecraft (tests/js/minecraft_mock) で手順どおりに操作する (tests/js/drive_main.mjs)。
"""
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mvcodec.decode import iter_decoded_frames  # noqa: E402
from test_codec_v4 import HEIGHT, WIDTH, make_scene_frames, run_convert  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")


def drive(work, videos, steps):
    """videos ({動画ID: frames_*.js}) を収録したアドオンを、steps のとおりに操作した結果を返す。"""
    work.mkdir(parents=True)
    for name in ("main.js", "codec.js"):
        shutil.copy(ROOT / name, work / name)
    shutil.copy(ROOT / "tests" / "js" / "drive_main.mjs", work / "drive_main.mjs")
    imports, entries, listing = [], [], []
    for n, (video_id, path) in enumerate(videos.items()):
        shutil.copy(path, work / f"frames_{video_id}.js")
        imports.append(f'import {{ FRAME_DATA as v{n} }} from "./frames_{video_id}.js";')
        entries.append(f'"{video_id}": v{n}')
        listing.append(f'{{ id: "{video_id}", title: "{video_id}", frame_count: v{n}.frame_count, '
                       f'width: v{n}.width, height: v{n}.height }}')
    (work / "videos.js").write_text(
        "\n".join(imports) + f"\nexport const VIDEOS = {{ {', '.join(entries)} }};\n"
        f"export const VIDEO_LIST = [{', '.join(listing)}];\n", encoding="utf-8")
    for module, source in (("server", "server.js"), ("server-ui", "server-ui.js")):
        target = work / "node_modules" / "@minecraft" / module
        target.mkdir(parents=True)
        (target / "package.json").write_text(json.dumps({"name": f"@minecraft/{module}", "type": "module", "main": "index.js"}))
        shutil.copy(ROOT / "tests" / "js" / "minecraft_mock" / source, target / "index.js")
    (work / "package.json").write_text(json.dumps({"type": "module"}))
    result = subprocess.run(["node", "drive_main.mjs", json.dumps(steps)], cwd=work, capture_output=True, text=True,
                            encoding="utf-8", timeout=600)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def reference_frames(data):
    blocks = [spec["block"] for spec in data["level_blocks"]]
    return [[blocks[i] for i in board.reshape(-1)] for board, _ in iter_decoded_frames(data)]


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    """キーフレームが少ない (先頭と各シーンの先頭だけ) 動画: シーク先は差分フレームになる。"""
    tmp = tmp_path_factory.mktemp("scene")
    make_scene_frames(tmp / "frames")
    data = run_convert(tmp / "frames", tmp / "scene.js", "--keyframe-interval", "1000")
    return tmp / "scene.js", data


@pytest.mark.parametrize("wait_ticks", [0, 1, 40])
def test_resume_during_seek_shows_the_right_frames(scene, tmp_path, wait_ticks):
    """シークの描画が終わる前に再生を押しても、壊れた画面にならず例外も出ない。"""
    path, data = scene
    frames = reference_frames(data)
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 65},
             {"ui": [{"selection": 0}]},                       # 一時停止
             {"ui": [{"selection": 5}, {"formValues": [6]}]},  # 6 秒 (60 フレーム目) へシーク
             {"tick": wait_ticks},
             {"ui": [{"selection": 0}]},                       # すぐに再生
             {"tick": 3}, {"snap": "after_resume"},
             {"until": "bvp.playback_finished"}, {"snap": "final"}]
    out = drive(tmp_path / "addon", {"demo": path}, steps)
    assert out["errors"] == []
    after_resume, final = out["snaps"]
    assert after_resume["board"] in frames[60:64]
    assert final["board"] == frames[-1]


def test_seek_places_only_the_target_frame(scene, tmp_path):
    """シークでは途中のフレームを置かず、目的のフレームとの差だけを置く。"""
    path, data = scene
    frames = reference_frames(data)
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 30},
             {"ui": [{"selection": 0}]}, {"tick": 2},
             {"ui": [{"selection": 5}, {"formValues": [6]}]}, {"until": "bvp.seek_done"}, {"snap": "seek"}]
    out = drive(tmp_path / "addon", {"demo": path}, steps)
    assert out["errors"] == []
    assert out["snaps"][0]["board"] == frames[60]


def test_cells_that_failed_to_clear_are_placed_again(tmp_path):
    """停止時の塗りつぶしで置けなかったマス (読み込まれていないチャンク) は、次の再生で必ず置き直す。"""
    (tmp_path / "frames").mkdir()
    for i in range(20):
        arr = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        arr[:] = (255, 255, 255) if i < 10 else (230, 40, 40)
        Image.fromarray(arr).save(tmp_path / "frames" / f"frame_{i:04d}.png")
    data = run_convert(tmp_path / "frames", tmp_path / "f.js", "--keyframe-interval", "10", "--no-adaptive-fps")
    frames = reference_frames(data)
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"},
             {"failZ": 92}, {"event": "badapple:stop"}, {"until": "bvp.clear_done"}, {"snap": "after_clear"},
             {"failZ": None}, {"event": "badapple:start"}, {"tick": 8}, {"snap": "second_run"}]
    out = drive(tmp_path / "addon", {"demo": tmp_path / "f.js"}, steps)
    after_clear, second_run = out["snaps"]
    assert len(Counter(after_clear["board"])) == 2  # 半分は塗れていない (前提の確認)
    assert second_run["board"] == frames[0]


def test_switching_to_a_volume_refits_position_and_ticking_area(scene, tmp_path):
    """平面で setup した後に立体の動画へ切り替えても、建築限界を超えず、立体用の位置と常時読み込みになる。"""
    from test_volume3d import convert_vox, write_cube_vox

    plane_path, _ = scene
    write_cube_vox(tmp_path / "cube.vox", size=6)
    cube = convert_vox(tmp_path / "cube.vox", tmp_path / "cube.js")
    steps = [{"y": 318}, {"event": "badapple:setup"}, {"event": "badapple:play", "message": "cube"}, {"tick": 200},
             {"snap": "cube", "video": "cube"}]
    out = drive(tmp_path / "addon", {"plane": plane_path, "cube": tmp_path / "cube.js"}, steps)
    assert out["errors"] == []
    assert max(y for _x, y, _z, _id in out["allBlocks"]) <= 319
    snap = out["snaps"][0]
    assert snap["anchor"]["y"] == 320 - cube["height"]
    assert sum(1 for block in snap["board"] if block not in (None, "minecraft:air")) == 6 ** 3
    # 立体は奥行きの範囲 (z 方向に depth) を常時読み込みにする
    adds = [c for c in out["commands"] if c.startswith("tickingarea add")]
    _x0, _y0, z0, _x1, _y1, z1 = map(int, adds[-1].split()[2:8])
    assert z1 - z0 + 1 == cube["depth"]


def test_setup_elsewhere_removes_previous_ticking_areas(scene, tmp_path):
    """別の場所でもう一度 setup すると、前の場所で登録した tickingarea をすべて外す (1 ワールド 10 区画の上限)。"""
    path, _ = scene
    steps = [{"event": "badapple:setup"}, {"y": 70}, {"event": "badapple:setup"}]
    out = drive(tmp_path / "addon", {"demo": path}, steps)
    alive = set()
    for command in out["commands"]:
        m = re.match(r"tickingarea (add|remove) (?:.* )?(\S+)$", command)
        if not m:
            continue
        name = m.group(2)
        if m.group(1) == "add":
            alive.add(name)
        else:
            alive.discard(name)
    assert alive and all("_70_" in name for name in alive), alive


def chunks_spanned(start, size):
    return (start + size - 1) // 16 - start // 16 + 1


@pytest.mark.parametrize("x, z", [(5, 100), (-7, 37)])
def test_setup_align_lines_the_screen_up_with_chunks(scene, tmp_path, x, z):
    """setup align は、スクリーンがまたぐチャンクが一番少なくなるよう最大 15 ブロックずらし、再生はそこで行う。"""
    path, data = scene
    frames = reference_frames(data)
    steps = [{"x": x, "z": z}, {"event": "badapple:setup", "message": "align"}, {"event": "badapple:start"},
             {"until": "bvp.playback_finished"}, {"snap": "final"}]
    out = drive(tmp_path / "addon", {"demo": path}, steps)
    assert out["errors"] == []
    final = out["snaps"][0]
    anchor = final["anchor"]
    width, height = data["width"], data["height"]
    best = min(chunks_spanned(a, width) for a in range(anchor["x"] - 15, anchor["x"] + 16))
    assert chunks_spanned(anchor["x"], width) == best
    best = min(chunks_spanned(a, height) for a in range(anchor["z"] - 15, anchor["z"] + 16))
    assert chunks_spanned(anchor["z"], height) == best
    assert abs(anchor["x"] - x) <= 15
    assert any("bvp.setup_aligned" in m for m in out["messages"])
    assert final["board"] == frames[-1]


def test_blocks_that_cannot_be_placed_are_reported_once_in_chat(tmp_path):
    """置けなかったブロックは再生ごとに 1 回だけチャットで知らせる (理由のエラーも)。以前はコンテンツログだけで、
    実機で前の場面のブロックが残ったときに原因が分からなかった。"""
    (tmp_path / "frames").mkdir()
    for i in range(20):
        arr = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        arr[:] = (255, 255, 255) if i % 2 else (230, 40, 40)
        Image.fromarray(arr).save(tmp_path / "frames" / f"frame_{i:04d}.png")
    data = run_convert(tmp_path / "frames", tmp_path / "f.js", "--keyframe-interval", "10", "--no-adaptive-fps")
    steps = [{"event": "badapple:setup"}, {"failZ": 92}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"},
             {"failZ": None}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"}]
    out = drive(tmp_path / "addon", {"demo": tmp_path / "f.js"}, steps)
    notices = [m for m in out["messages"] if "bvp.place_failed" in m]
    assert len(notices) == 1
    assert "UnloadedChunksError" in notices[0]
    assert data["frame_count"] == 20
