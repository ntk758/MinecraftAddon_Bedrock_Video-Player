"""長時間の再生 (TODO.md の P0-6 のうち、モックで確かめられる分): ブロックモードの GOP のキャッシュと、展開中の GOP の数が
上限を超えて増えないこと。何度も最後まで再生し、シークを繰り返しても、メモリに持つ GOP の数は MAX_CACHED_GOPS まで。

Minecraft の実機でのメモリ (working set) と TPS は、ここでは測れない (TODO.md の P0-6 の実機の計測)。
"""
import shutil

import pytest

import numpy as np
from PIL import Image

from test_codec_v4 import HEIGHT, WIDTH, run_convert
from test_playback_control import drive

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

PROBE = """
const __probe = globalThis.__bvpProbe ??= { maxCached: 0, maxPartial: 0, samples: 0 };
function __measure(player) {
  __probe.maxCached = Math.max(__probe.maxCached, player.gopCache.size);
  __probe.maxPartial = Math.max(__probe.maxPartial, player.partialGops.size);
  __probe.limit = player.MAX_CACHED_GOPS;
  __probe.samples++;
}
"""


def add_probe(work):
    """GOP をキャッシュに入れるたびと、展開を始めるたびに、キャッシュと展開中の数を測る。"""
    main = (work / "main.js").read_text(encoding="utf-8")
    cache = "    this.gopCache.set(gopId, decoded);\n"
    assert main.count(cache) == 1
    main = main.replace(cache, cache + "    __measure(this);\n")
    start = "  startGopDecode(gopId) {\n"
    assert main.count(start) == 1
    body_end = main.index("\n  }\n", main.index(start))
    # 返す直前ではなく、関数の頭で測る (展開を始める前の数。始めた分は次の測定に入る)
    main = main.replace(start, start + "    __measure(this);\n")
    (work / "main.js").write_text(PROBE + main, encoding="utf-8")


@pytest.fixture(scope="module")
def many_gops(tmp_path_factory):
    """背景の色が 1 秒ごとに大きく変わる 12 シーン (GOP はキャッシュの上限 4 より多くなる)。"""
    tmp = tmp_path_factory.mktemp("gops")
    frames = tmp / "frames"
    frames.mkdir()
    n = 0
    for scene in range(12):
        color = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)][scene % 4]
        color = tuple(int(c * (0.4 + 0.6 * ((scene // 4) % 3) / 2)) for c in color)
        for t in range(10):
            arr = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            arr[:] = color
            arr[2:6, t:t + 4] = (255 - color[0], 128, 255 - color[2])
            n += 1
            Image.fromarray(arr).save(frames / f"frame_{n:04d}.png")
    data = run_convert(frames, tmp / "gops.js")
    assert len(data["gop_boundaries"]) - 1 >= 5   # キャッシュの上限 (4) より多い
    return tmp / "gops.js"


def test_the_gop_cache_stays_bounded_over_repeated_playback_and_seeks(many_gops, tmp_path):
    # until は、そのメッセージが一度出ると以後は待たないので、tick の数で進める (動画は 12 秒 = 240 tick)
    steps = [{"event": "badapple:setup"}]
    for _ in range(3):   # 最初から最後まで 3 回
        steps += [{"event": "badapple:start"}, {"tick": 300}]
    for seconds in [10, 1, 7, 2, 11, 0, 5, 9, 3, 8]:   # シーク 10 回 (前後へ)。シークの後は少し再生する
        steps += [{"ui": [{"selection": 5}, {"formValues": [seconds]}]}, {"tick": 30}]
    steps += [{"event": "badapple:start"}, {"tick": 300}]
    out = drive(tmp_path / "addon", {"demo": many_gops}, steps, prepare=add_probe)
    assert out["errors"] == []
    probe = out["probe"]
    assert probe["samples"] > 40
    assert probe["maxCached"] <= probe["limit"] == 4
    # 展開中の GOP は、今の GOP と先読みの分だけ (キャッシュの上限を超えない)
    assert probe["maxPartial"] <= probe["limit"]
