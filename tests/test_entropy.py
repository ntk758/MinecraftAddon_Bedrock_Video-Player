"""Phase 8.4: GOP ごとのエントロピー符号化 (varint_rle_v6) のテスト。"""
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

from mvcodec.decode import gop_bytes_of, iter_decoded_frames, parse_index, utf16_str_to_bytes  # noqa: E402
from mvcodec.entropy import (  # noqa: E402
    MAX_CODE_LENGTH,
    MODE_HUFFMAN,
    MODE_RANGE,
    canonical_codes,
    code_lengths,
    decode_stream,
    encode_stream,
    pack_gop,
    unpack_gop,
)

HAS_NODE = shutil.which("node") is not None


def test_code_lengths_are_limited_and_prefix_free():
    rng = np.random.default_rng(0)
    # 偏りの大きい分布 (フィボナッチ的) でも最大長を超えない
    counts = np.array([int(1.6 ** k) for k in range(40)] + [0] * 10)
    lengths = code_lengths(counts)
    assert lengths.max() <= MAX_CODE_LENGTH
    assert sum(2.0 ** -int(l) for l in lengths if l) <= 1.0 + 1e-12  # クラフトの不等式
    codes = canonical_codes(lengths)
    words = {format(int(codes[s]), f"0{int(l)}b") for s, l in enumerate(lengths) if l}
    assert not any(a != b and b.startswith(a) for a in words for b in words)
    symbols = list(rng.choice(len(counts), size=3000, p=counts / counts.sum()))
    out = bytearray()
    encode_stream(symbols, len(counts), out)
    decoded, pos = decode_stream(bytes(out), 0)
    assert decoded == symbols and pos == len(out)


@pytest.mark.parametrize("symbols", [[], [7], [3] * 50, list(range(64))])
def test_stream_edge_cases(symbols):
    out = bytearray()
    encode_stream(symbols, 256, out)
    decoded, _ = decode_stream(bytes(out), 0)
    assert decoded == symbols


def make_video(tmp_path, *extra):
    from test_codec_v4 import make_scene_frames, run_convert

    if not (tmp_path / "frames").exists():
        make_scene_frames(tmp_path / "frames")
    out = tmp_path / ("video" + "_".join(extra).replace("-", "") + ".js")
    return out, run_convert(tmp_path / "frames", out, "--keyframe-interval", "10", "--width", "96", "--height", "64",
                            *extra)


def test_v6_is_exact_and_smaller(tmp_path):
    # タイルの大きさは固定する (auto は入れ物に入れた後の大きさで 4 と 8 を選ぶので、v5 と v6 で選ぶ方が変わりうる)
    v6_path, v6 = make_video(tmp_path, "--tile-size", "8")
    v5_path, v5 = make_video(tmp_path, "--tile-size", "8", "--no-entropy")
    assert v6["format"] == "varint_rle_v6" and v5["format"] == "varint_rle_v5"
    # 入れ物を開いた中身は v5 とバイト単位で同じ。フレームの位置の表も同じ
    assert v6["index"] == v5["index"]
    for gop_id in range(len(v5["chunks"])):
        assert gop_bytes_of(v6, gop_id) == utf16_str_to_bytes(v5["chunks"][gop_id])
    for (a, _), (b, _) in zip(iter_decoded_frames(v6), iter_decoded_frames(v5)):
        assert np.array_equal(a, b)
    size = lambda d: sum(len(utf16_str_to_bytes(c)) for c in d["chunks"])  # noqa: E731 (バイト数で比べる)
    assert size(v6) < size(v5) * 0.8, (size(v6), size(v5))
    # 小さすぎて縮まない GOP は生のまま入れるので、増えても 1 GOP あたり 1 バイト (入れ物の印) まで
    tiny, tiny_v5 = make_video(tmp_path, "--width", "24", "--height", "16")[1], make_video(tmp_path, "--width", "24",
                                                                                               "--height", "16",
                                                                                               "--no-entropy")[1]
    assert size(tiny) <= size(tiny_v5) + len(tiny_v5["chunks"])


def test_tiny_gops_are_stored_raw():
    data = bytes([0x08, 0x00])  # 変化の無い差分フレーム 1 枚 (ヘッダ + ラン数 0)
    container = pack_gop(data, [(0, len(data))])
    assert container[0] == 0 and unpack_gop(container) == data


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_codec_js_unpacks_like_python(tmp_path):
    """codec.js の unpackGop が Python の unpack_gop と同じバイト列を返し、全フレームが同じ盤面になる。"""
    path, data = make_video(tmp_path)
    script = tmp_path / "unpack.mjs"
    script.write_text(
        f'import {{ decodeUTF16BinaryToBytes, unpackGop }} from "{(ROOT / "codec.js").as_uri()}";\n'
        'import { readFileSync } from "node:fs";\n'
        'const text = readFileSync(process.argv[2], "utf8");\n'
        'const data = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));\n'
        'process.stdout.write(JSON.stringify(data.chunks.map((c) => Array.from(unpackGop(decodeUTF16BinaryToBytes(c))))));\n',
        encoding="utf-8")
    out = subprocess.run(["node", str(script), str(path)], capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    assert [bytes(g) for g in js] == [gop_bytes_of(data, i) for i in range(len(data["chunks"]))]
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(path)],
                            capture_output=True, text=True, check=True)
    decoded = json.loads(result.stdout)
    assert decoded["frames"] == [b.reshape(-1).tolist() for b, _ in iter_decoded_frames(data)]
    assert decoded["seekMismatches"] == []


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_main_js_plays_v6(tmp_path):
    from test_main_js import run_addon

    path, data = make_video(tmp_path)
    result = run_addon(tmp_path / "addon", path)
    assert result["finished"]
    *_, (board, _rgb) = iter_decoded_frames(data)
    blocks = data["level_blocks"][-1] if data["adaptive_palette"] else data["level_blocks"]
    assert result["finalBoard"] == [blocks[i]["block"] for i in board.reshape(-1)]
    assert len(parse_index(data)) == data["frame_count"]


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_decoding_a_large_gop_does_not_delay_playback(tmp_path):
    """大きな GOP の展開 (v6) は何百回も yield するが、1 tick の yield の回数の上限 (ブロック数の上限) には数えず、
    時間の予算だけで区切る。数えると再生の開始やシークのたびに何十 tick も遅れる。"""
    sys.path.insert(0, str(ROOT / "benchmark"))
    from codec_bench import make_clip, save_frames
    from test_codec_v4 import run_convert
    from test_main_js import run_addon

    save_frames(make_clip("pan", 192, 108, 40), tmp_path / "frames")
    out = tmp_path / "big.js"
    # --block-rdo 0: 最適化すると最初のキーフレームが箱にまとまり、rect 方式の 1 tick のブロック数の上限で数 tick かかる
    # (展開の遅れとは別の理由で遅れが出る) ため、展開だけを比べられるよう減色のままにする
    data = run_convert(tmp_path / "frames", out, "--width", "192", "--height", "108", "--palette", "auto",
                       "--dither-method", "blue_noise", "--keyframe-interval", "0", "--block-rdo", "0")
    assert data["format"] == "varint_rle_v6" and len(data["chunks"]) == 1
    costs = '{"setBlock": 2.3, "fillCall": 15, "fillBlock": 0.55, "listCall": 15, "listBlock": 1.42}'
    result = run_addon(tmp_path / "addon", out, "stats", f"costs={costs}")
    load = next(part for m in result["statsMessages"] for part in m["rawtext"] if part.get("translate") == "bvp.stats_load")
    late_frames, max_late_ticks = (int(load["with"]["rawtext"][i]["text"]) for i in (0, 1))
    assert max_late_ticks <= 2, (late_frames, max_late_ticks)


def make_gop(frame_runs):
    """ランだけのフレーム (frame_runs[i] は (間隔, 長さ-1, 色) の並び) を並べた v5 の GOP と、フレームの位置。"""
    from mvcodec.encode import encode_varint

    gop = bytearray()
    frames = []
    for number, runs in enumerate(frame_runs):
        start = len(gop)
        encode_varint((number << 3) | (1 if number == 0 else 0), gop)
        encode_varint(len(runs), gop)
        for gap, length, level in runs:
            encode_varint((gap << 13) | (length << 7) | level, gop)
        frames.append((start, len(gop) - start))
    return bytes(gop), frames


# 間隔の区分の境目 (3/4、7/8、…) と、2^31 を超えるランの値 (JS のビット演算が使えない) を含む
EDGE_RUNS = [[(0, 0, 0), (3, 63, 127), (4, 1, 5), (7, 2, 5), (8, 0, 9), (2 ** 18 - 1, 5, 1), (2 ** 18, 0, 0)],
             [(2 ** 30 + 12345, 63, 127), (1, 0, 0)], [], [(5, 3, 64)] * 300]


def test_range_container_roundtrips_edge_values():
    from mvcodec.rangecode import MODE_RANGE

    gop, frames = make_gop(EDGE_RUNS)
    container = pack_gop(gop, frames)
    assert container[0] == MODE_RANGE
    assert unpack_gop(container) == gop


def test_range_container_rejects_non_minimal_runs():
    """最短でない varint のラン (0x85 0x00 = 5) は入れ物に入れられない (開くと 1 バイトのランになり、元に戻らない)。"""
    from mvcodec.rangecode import pack_gop_range

    gop, frames = make_gop([[(0, 0, 5)]])
    assert pack_gop_range(gop, frames)[0] == 2
    padded = gop[:-1] + bytes([0x85, 0x00])
    with pytest.raises(ValueError):
        pack_gop_range(padded, [(0, len(padded))])


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_codec_js_unpacks_range_edge_values(tmp_path):
    gop, frames = make_gop(EDGE_RUNS)
    script = tmp_path / "unpack.mjs"
    script.write_text(
        f'import {{ unpackGop }} from "{(ROOT / "codec.js").as_uri()}";\n'
        'const input = Uint8Array.from(Buffer.from(process.argv[2], "hex"));\n'
        'process.stdout.write(Buffer.from(unpackGop(input)).toString("hex"));\n', encoding="utf-8")
    for container in (pack_gop(gop, frames), pack_gop(gop, frames, MODE_HUFFMAN)):
        out = subprocess.run(["node", str(script), container.hex()], capture_output=True, text=True, check=True)
        assert bytes.fromhex(out.stdout) == gop


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_huffman_coder_option_still_plays(tmp_path):
    """--entropy-coder huffman (v6.15 までの入れ物) も今の codec.js で同じ盤面になる。range の方が小さい。"""
    path, data = make_video(tmp_path, "--entropy-coder", "huffman")
    range_path, range_data = make_video(tmp_path)
    assert {utf16_str_to_bytes(c)[0] for c in data["chunks"]} <= {0, MODE_HUFFMAN}
    assert MODE_RANGE in {utf16_str_to_bytes(c)[0] for c in range_data["chunks"]}
    size = lambda d: sum(len(utf16_str_to_bytes(c)) for c in d["chunks"])  # noqa: E731
    assert size(range_data) < size(data)
    for p, d in ((path, data), (range_path, range_data)):
        result = subprocess.run(["node", str(ROOT / "tests" / "js" / "decode_frames.mjs"), str(p)],
                                capture_output=True, text=True, check=True)
        assert json.loads(result.stdout)["frames"] == [b.reshape(-1).tolist() for b, _ in iter_decoded_frames(d)]


@pytest.mark.skipif(not HAS_NODE, reason="node が必要")
def test_scanning_a_large_relief_does_not_delay_playback(tmp_path):
    """2.5D の 128×72×16 (14.7 万マス) は、変わったマスを探す走査だけで 1 フレームに 5〜14 回 yield する。
    走査の yield を 1 tick の yield の回数の上限 (8 回) に数えると、置くブロックが少なくても 1 フレームが 2 tick 以上に
    またがり、実機の録画で 10 fps の映像が 8 fps に落ちていた。走査は時間の予算だけで区切る。"""
    from PIL import Image
    from test_codec_v4 import run_convert
    from test_main_js import run_addon

    frames = tmp_path / "frames"
    frames.mkdir()
    yy, xx = np.mgrid[0:72, 0:128]
    for t in range(60):
        # ゆっくり動く明るさの山 (毎フレーム少しだけ変わる)
        hill = 255 * np.exp(-((xx - 30 - t // 2) ** 2 + (yy - 36) ** 2) / 300.0)
        Image.fromarray(np.repeat(hill[..., None], 3, 2).astype(np.uint8)).save(frames / f"frame_{t:04d}.png")
    out = tmp_path / "relief.js"
    data = run_convert(frames, out, "--width", "128", "--height", "72", "--fps", "10", "--mode", "relief", "--depth", "16",
                       "--dither-method", "none", "--keyframe-interval", "0", "--no-adaptive-fps")
    assert data["layout"] == "heightfield"
    costs = '{"setBlock": 2.3, "fillCall": 15, "fillBlock": 0.55, "listCall": 15, "listBlock": 1.42}'
    result = run_addon(tmp_path / "addon", out, "stats", f"costs={costs}")
    load = next(part for m in result["statsMessages"] for part in m["rawtext"] if part.get("translate") == "bvp.stats_load")
    late_frames, max_late_ticks = (int(load["with"]["rawtext"][i]["text"]) for i in (0, 1))
    # 最初のフレーム (立体をすべて置く) は重いので遅れるが、その後は追いつく (以前は最後まで遅れ続けた)
    assert late_frames <= 25, (late_frames, max_late_ticks)
