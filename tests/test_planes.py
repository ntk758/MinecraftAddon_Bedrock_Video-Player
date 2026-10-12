"""2.5D パラメトリック立体 (Phase 8.3) のテスト: 平面 ⇔ 立体の変換、codec.js の部分的な組み立て、シーク。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.decode import iter_decoded_frames  # noqa: E402
from mvcodec.planes import planes_supported, planes_to_volume, solid_rule_for, volume_to_planes  # noqa: E402
from mvcodec.volume import AIR_LEVEL, VolumeLifter  # noqa: E402

HAS_NODE = shutil.which("node") is not None


def random_heights(rng, h, w, frames):
    """なめらかに動く高さ (0〜1) の列。ところどころ大きく跳ぶ。"""
    base = rng.random((h, w))
    out = []
    for t in range(frames):
        drift = np.roll(base, t, axis=1) * 0.8 + 0.2 * rng.random((h, w))
        out.append(np.clip(drift, 0, 1))
    return out


@pytest.mark.parametrize("mode", ["relief", "extrude", "inflate"])
@pytest.mark.parametrize("depth", [2, 7, 16])
def test_planes_roundtrip_exactly(mode, depth):
    rng = np.random.default_rng(depth)
    h, w = 9, 13
    lifter = VolumeLifter(mode, depth)
    rule = solid_rule_for(mode)
    previous = None
    for heights in random_heights(rng, h, w, 6):
        indices = rng.integers(0, 30, size=(h, w))
        volume = lifter(indices, None, heights)
        planes = volume_to_planes(volume, rule, previous)
        assert planes.shape == (2 * h, w) and planes.max() <= 127
        assert np.array_equal(planes_to_volume(planes, depth, rule), volume)
        previous = planes


def test_empty_columns_keep_the_previous_colour():
    """空の列の色は使われないので、前のフレームの色のままにする (変わったマスを増やさない)。"""
    depth = 6
    lifter = VolumeLifter("extrude", depth)
    mask_on = np.ones((2, 3))
    first = volume_to_planes(lifter(np.full((2, 3), 5), None, mask_on), "span")
    second = volume_to_planes(lifter(np.full((2, 3), 9), None, np.zeros((2, 3))), "span", first)
    assert (second[2:] == 0).all()
    assert (second[:2] == 5).all()


def test_planes_are_not_used_when_they_cannot_be_exact():
    assert planes_supported("relief", 128)
    assert not planes_supported("extrude", 128)  # 段数 0〜128 は 7bit に収まらない
    assert not planes_supported("inflate", 16, hollow=True)
    assert not planes_supported("vox", 16)
    with pytest.raises(SystemExit):
        convert.parse_args(["--frames-dir", ".", "--mode", "extrude", "--hollow", "--volume-encoding", "planes"])


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_codec_js_rebuilds_only_changed_columns_like_a_full_rebuild(tmp_path):
    """変わった列だけを作り直した結果が、毎回全部の列を作った結果と同じになる (relief の側面も含む)。"""
    rng = np.random.default_rng(3)
    h, w, depth = 7, 11, 9
    cases = []
    for rule in ("relief", "span"):
        limit = depth if rule == "relief" else depth + 1
        sequence = []
        planes = np.concatenate([rng.integers(0, 20, (h, w)), rng.integers(0, limit, (h, w))])
        for _ in range(12):
            planes = planes.copy()
            # 少しの列だけ変える (色だけ / 値だけ / 両方)
            for _ in range(int(rng.integers(1, 6))):
                y, x = int(rng.integers(0, h)), int(rng.integers(0, w))
                if rng.random() < 0.5:
                    planes[y, x] = rng.integers(0, 20)
                if rng.random() < 0.7:
                    planes[h + y, x] = rng.integers(0, limit)
            sequence.append(planes.reshape(-1).tolist())
        expected = [planes_to_volume(np.array(p).reshape(2 * h, w), depth, rule).reshape(-1).tolist() for p in sequence]
        cases.append({"rule": rule, "sequence": sequence, "expected": expected})
    script = tmp_path / "expand.mjs"
    script.write_text(
        f'import {{ expandPlanes }} from "{(ROOT / "codec.js").as_uri()}";\n'
        'import { readFileSync } from "node:fs";\n'
        'const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));\n'
        f'const video = {{ width: {w}, height: {h}, depth: {depth}, air_level: {AIR_LEVEL} }};\n'
        'const result = cases.map(({ rule, sequence }) => {\n'
        '  video.solid_rule = rule;\n'
        '  const volume = new Int16Array(video.width * video.height * video.depth);\n'
        '  const previous = new Int16Array(sequence[0].length).fill(-32768);\n'
        '  const rebuilt = [];\n'
        '  const volumes = sequence.map((planes) => {\n'
        '    rebuilt.push(expandPlanes(Int16Array.from(planes), previous, volume, video));\n'
        '    return Array.from(volume);\n'
        '  });\n'
        '  return { volumes, rebuilt };\n'
        '});\n'
        'process.stdout.write(JSON.stringify(result));\n', encoding="utf-8")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    out = subprocess.run(["node", str(script), str(cases_path)], capture_output=True, text=True, check=True)
    for case, got in zip(cases, json.loads(out.stdout)):
        assert got["volumes"] == case["expected"], case["rule"]
        assert got["rebuilt"][0] == h * w
        assert max(got["rebuilt"][1:]) < h * w // 2  # 2 回目以降は変わった列 (とその隣) だけ


@pytest.fixture(scope="module")
def relief_scene(tmp_path_factory):
    """キーフレームが少ない 2.5D の relief: シーク先は差分フレームになる。"""
    from test_codec_v4 import make_scene_frames, run_convert

    tmp = tmp_path_factory.mktemp("relief_scene")
    make_scene_frames(tmp / "frames")
    data = run_convert(tmp / "frames", tmp / "relief.js", "--keyframe-interval", "1000", "--mode", "relief",
                       "--depth", "6")
    assert data["layout"] == "heightfield" and data["solid_rule"] == "relief"
    return tmp / "relief.js", data


def reference_volumes(data):
    blocks = [spec["block"] for spec in data["level_blocks"]]
    return [[None if i == AIR_LEVEL else blocks[i] for i in board.reshape(-1)] for board, _ in iter_decoded_frames(data)]


def placed(board):
    return [None if block in (None, "minecraft:air") else block for block in board]


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_heightfield_seek_and_resume(relief_scene, tmp_path):
    """シーク (途中のフレームから復元し直す) と、シーク中の再開でも正しい立体になる。"""
    from test_playback_control import drive

    path, data = relief_scene
    frames = reference_volumes(data)
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 65},
             {"ui": [{"selection": 0}]},
             {"ui": [{"selection": 5}, {"formValues": [6]}]}, {"until": "bvp.seek_done"}, {"snap": "seek"},
             {"ui": [{"selection": 5}, {"formValues": [2]}]}, {"tick": 1},
             {"ui": [{"selection": 0}]},
             {"until": "bvp.playback_finished"}, {"snap": "final"}]
    out = drive(tmp_path / "addon", {"demo": path}, steps)
    assert out["errors"] == []
    seek, final = out["snaps"]
    assert placed(seek["board"]) == frames[60]
    assert placed(final["board"]) == frames[-1]


def test_volume_change_ratio_matches_counting_on_volumes():
    """列の区間から数えた変化率が、立体を組み立てて数えた値と同じになる。"""
    from mvcodec.planes import volume_change_ratio

    rng = np.random.default_rng(5)
    h, w, depth = 6, 9, 8
    for rule, limit in (("relief", depth), ("span", depth + 1)):
        ratio = volume_change_ratio(w, h, depth, rule)
        for _ in range(20):
            a = np.concatenate([rng.integers(0, 4, (h, w)), rng.integers(0, limit, (h, w))])
            b = a.copy()
            mask = rng.random((2 * h, w)) < 0.3
            b[:h][mask[:h]] = rng.integers(0, 4, int(mask[:h].sum()))
            b[h:][mask[h:]] = rng.integers(0, limit, int(mask[h:].sum()))
            va, vb = planes_to_volume(a, depth, rule), planes_to_volume(b, depth, rule)
            expected = np.count_nonzero(va != vb) / max(1, np.count_nonzero((va != AIR_LEVEL) | (vb != AIR_LEVEL)))
            assert ratio(a.reshape(-1), b.reshape(-1)) == pytest.approx(expected)


def test_auto_falls_back_to_voxels_with_a_message(tmp_path, capsys):
    from test_codec_v4 import make_scene_frames

    make_scene_frames(tmp_path / "frames")
    out = tmp_path / "hollow.js"
    assert convert.main(["--frames-dir", str(tmp_path / "frames"), "--output", str(out), "--width", "16", "--height",
                         "12", "--fps", "10", "--device", "cpu", "--mode", "extrude", "--depth", "6", "--hollow",
                         "--lang", "en", "--duration", "1"]) == 0
    assert "sent as voxels" in capsys.readouterr().out
    from mvcodec.decode import load_frame_data

    assert load_frame_data(out)["layout"] == "volume"
