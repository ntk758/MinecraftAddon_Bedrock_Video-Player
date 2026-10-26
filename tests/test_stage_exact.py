"""だまし絵の投影 (codec.js の projectToStage) が厳密なことの回帰テスト。

乱数 (種は固定) で作った立体 (中が空洞の殻・薄い板・ばらばらの点・詰まった塊・細い棒) を、座標が整数でない目から写し、
盤面の各マスの中心から目への線分を 3D DDA でたどった正解 (最初に当たったブロックの値、当たらなければ背景) と
食い違うマスが 0 であることを確かめる (tests/js/stage_exact.mjs)。投影の高速化で、見える面の絞り込みや
塗る範囲の求め方を変えたとき、出力が 1 マスでも変わっていないことを守る。
写る範囲の求め方の近道 (床・後ろの壁・左の壁・右の壁だけに写るブロックと、それ以外) は、それぞれを多く通る目で確かめる。
外から見える面を持つブロックの一覧を作る処理 (空気だけの区間を読み飛ばす) は、tests/js/stage_surface.mjs で、
1 ブロックずつ調べた正解と比べる。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.volume import AIR_LEVEL, fill_enclosed_air  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

SHAPES = ["shell", "plates", "points", "solid", "rods"]

# 本物の codec.js を包んで、投影の結果の一部の塗り色を 1 つずらす (正解との比較が働いているかを確かめる用)
BROKEN_CODEC = """export * from "./real_codec.js";
import { projectToStage as real } from "./real_codec.js";
export function projectToStage(...args) {
  const drawn = real(...args);
  const out = args[4];
  for (let p = 0; p < out.length; p += 97) if (out[p] >= 0 && out[p] < 20) out[p] = (out[p] + 1) % 20;
  return drawn;
}
"""


@pytest.fixture(scope="module")
def run_dir():
    """codec.js と stage_exact.mjs を置いた作業フォルダ (import "./codec.js" のため同じフォルダで動かす)。"""
    with tempfile.TemporaryDirectory(prefix="bvp_stage_exact_") as folder:
        folder = Path(folder)
        shutil.copy(ROOT / "codec.js", folder / "codec.js")
        for script in ("stage_exact.mjs", "stage_surface.mjs"):
            shutil.copy(ROOT / "tests" / "js" / script, folder / script)
        (folder / "package.json").write_text(json.dumps({"type": "module"}))
        yield folder


def run_exact(folder, dims, shape, eyes=20, inside=False, extra=()):
    command = ["node", "stage_exact.mjs", dims, shape, str(eyes), *(["--inside"] if inside else []), *extra]
    result = subprocess.run(command, cwd=folder, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("dims", ["24x40x8", "40x80x16"])
@pytest.mark.parametrize("shape", SHAPES)
def test_projection_matches_exact_ray_cast(run_dir, dims, shape):
    out = run_exact(run_dir, dims, shape)
    assert out["eyes"] == 20
    # 何も塗らない自明な通過ではないこと: 立体があり、視線が人物に当たるマスがある
    assert out["voxels"] > 50, out
    assert out["painted"] > 500, out
    assert out["mismatchCount"] == 0, out["mismatches"]


@pytest.mark.parametrize("path", ["floor", "back", "left", "right", "general"])
@pytest.mark.parametrize("shape", ["shell", "solid"])
def test_projection_is_exact_for_eyes_on_every_footprint_path(run_dir, shape, path):
    """写る範囲の求め方の近道 (1 つの面にしか写らないブロック) と一般の場合のそれぞれを、多く通る目で確かめる。"""
    out = run_exact(run_dir, "40x80x16", shape, eyes=10, extra=["--path", path])
    assert out["eyes"] == 10
    # その通り道に当たるブロック (目に向いた面を持つもの) が十分にあり、塗られたマスもある
    assert out["paths"][path] > 2000, out["paths"]
    assert out["painted"] > 500, out
    assert out["mismatchCount"] == 0, out["mismatches"]


def test_surface_list_matches_one_by_one_check(run_dir):
    """空気だけの区間を読み飛ばして作った見える面の一覧が、1 つずつ調べたものと同じ (図形の置き場所・空気の番号・NaN のビット列)。"""
    result = subprocess.run(["node", "stage_surface.mjs"], cwd=run_dir, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["cases"] > 1000
    assert out["mismatchCount"] == 0, out["mismatches"]


def test_filling_closed_cavities_does_not_change_the_projection(run_dir, tmp_path):
    """だまし絵の変換が埋める閉じた空洞 (fill_enclosed_air) は外からの視線が届かないので、埋めても投影は 1 マスも変わらず、
    調べる面だけが減る。投影するのは埋めた立体、正解はもとの立体で求める。"""
    dims, (width, height, depth) = "40x80x16", (40, 80, 16)
    figure_file = tmp_path / "figure.bin"
    plain = run_exact(run_dir, dims, "shell", extra=["--dump", str(figure_file)])
    volume = np.fromfile(figure_file, dtype=np.int16).reshape(depth, height, width)
    filled = fill_enclosed_air(volume, AIR_LEVEL)
    assert (filled != volume).sum() > 500, "殻の中に閉じた空洞があるはず"
    filled_file = tmp_path / "filled.bin"
    filled.tofile(filled_file)
    out = run_exact(run_dir, dims, "shell", extra=["--project", str(filled_file)])
    assert out["mismatchCount"] == 0, out["mismatches"]
    assert out["painted"] == plain["painted"]
    assert out["surfaces"] < plain["surfaces"]


@pytest.mark.xfail(strict=False, reason="目が人物のブロックの並びの中や後ろにあると、前方へ延びる写る範囲の端の数マスが抜ける (既知。投影の高速化で直るなら外す)")
@pytest.mark.parametrize("shape", ["shell", "rods"])
def test_projection_from_an_eye_inside_the_figure_box(run_dir, shape):
    out = run_exact(run_dir, "40x80x16", shape, inside=True)
    assert out["mismatchCount"] == 0, out["mismatches"]


def test_exact_test_notices_a_wrong_projection(tmp_path):
    """正解との比較そのものが働いていること: 投影の結果の塗り色を一部ずらした codec.js では、食い違うマスが出る。"""
    broken = tmp_path / "broken"
    broken.mkdir()
    shutil.copy(ROOT / "codec.js", broken / "real_codec.js")
    (broken / "codec.js").write_text(BROKEN_CODEC, encoding="utf-8")
    shutil.copy(ROOT / "tests" / "js" / "stage_exact.mjs", broken / "stage_exact.mjs")
    (broken / "package.json").write_text(json.dumps({"type": "module"}))
    out = run_exact(broken, "24x40x8", "solid")
    assert out["mismatchCount"] > 0
