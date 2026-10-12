"""Phase 8.4 の下調べ: エントロピー符号化・LZ で、どこがどれだけ縮むかを測る。

今のデータは「varint のバイト列 (payload) → 15bit ずつ UTF-16 の文字 → .js (UTF-8) → .mcaddon (zip の deflate)」。
縮めたいものは 2 つあり、効き方が違う:
  - ゲーム内のメモリと読み込み: JS の文字列の長さ (payload × 8/15 文字、1 文字 2 バイト)
  - 配布の大きさ: .mcaddon (= .js を deflate したもの)。15bit の文字は UTF-8 で 3 バイトになるが deflate がある程度取り戻す
エントロピー符号の出力はほぼランダムなので deflate では縮まず、UTF-8 の 3 バイトのぶんがそのまま残る。

各ファイルについて次を出す (バイト):
  js            今の .js の大きさ
  js_deflate    今の .mcaddon に入る大きさ (.js を deflate)
  payload       varint のバイト列の合計 (GOP ごと)
  huff0         GOP ごとの静的ハフマン (バイト単位、表の大きさ込み) の目安: Σ(GOP の 0 次エントロピー) + 表 160 バイト/GOP
  huff_split    ラン (位置の間隔・長さ・色)・動きベクトル・タイル・ヘッダを別々のストリームにした 0 次エントロピーの合計 + 表
  lz_deflate    payload を GOP ごとに deflate した大きさ (LZ 型の目安)
  xz            payload を GOP ごとに xz した大きさ (強い LZ + エントロピーの目安)
そして、それぞれを今の 15bit 文字に詰めたときの .js の大きさ (×16/15、UTF-8 では ×24/15) と、それを deflate した大きさの目安。

使い方: python benchmark/entropy_study.py <frames_*.js> ...
"""
import json
import lzma
import math
import sys
import zlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.decode import V5_FAMILY, gop_bytes_of, load_frame_data, parse_index, read_varint  # noqa: E402

TABLE_BYTES = 160  # 静的ハフマンの符号長の表 (256 記号 × 4〜5 bit) の目安


def entropy_bits(counter):
    total = sum(counter.values())
    return -sum(c * math.log2(c / total) for c in counter.values() if c) if total else 0.0


def split_streams(buf, start, end, data, streams):
    """1 フレームのバイト列を、意味ごとのストリーム (の値) に分けて streams に数える。"""
    pos = start
    header, pos = read_varint(buf, pos)
    streams["header"].update(_varint_bytes(header))
    is_v5 = data.get("format") in V5_FAMILY
    if is_v5 and (header >> 1) & 1:
        count, pos = read_varint(buf, pos)
        streams["header"].update(_varint_bytes(count))
        for _ in range(count):
            gap, pos = read_varint(buf, pos)
            vec, pos = read_varint(buf, pos)
            streams["mv_gap"].update(_varint_bytes(gap))
            streams["mv_vec"].update(_varint_bytes(vec))
    if is_v5 and (header >> 2) & 1:
        count, pos = read_varint(buf, pos)
        streams["header"].update(_varint_bytes(count))
        for _ in range(count):
            gap, pos = read_varint(buf, pos)
            rec, pos = read_varint(buf, pos)
            streams["tile_gap"].update(_varint_bytes(gap))
            streams["tile_ref"].update(_varint_bytes(rec))
    runs, pos = read_varint(buf, pos)
    streams["header"].update(_varint_bytes(runs))
    while pos < end:
        val, pos = read_varint(buf, pos)
        gap, length, level = val >> 13, ((val >> 7) & 0x3F) + 1, val & 0x7F
        streams["run_gap"].update(_varint_bytes(gap))
        streams["run_len"][length] += 1
        streams["run_level"][level] += 1


def _varint_bytes(value):
    out = []
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return out


def study(path):
    data = load_frame_data(path)
    raw = Path(path).read_bytes()
    entries = parse_index(data)
    gops = [gop_bytes_of(data, i) for i in range(len(data["chunks"]))]  # v6 は入れ物を開いた v5 のバイト列
    payload = sum(len(g) for g in gops)
    huff0 = sum(entropy_bits(Counter(g)) / 8 + TABLE_BYTES for g in gops if g)
    huff_split = 0.0
    for gop_id, gop in enumerate(gops):
        streams = {k: Counter() for k in ("header", "mv_gap", "mv_vec", "tile_gap", "tile_ref", "run_gap", "run_len",
                                          "run_level")}
        for g, offset, length, _kf in entries:
            if g == gop_id and length:
                split_streams(gop, offset, offset + length, data, streams)
        huff_split += sum(entropy_bits(c) / 8 + (TABLE_BYTES if c else 0) for c in streams.values())
    lz = sum(len(zlib.compress(bytes(g), 9)) for g in gops if g)
    xz = sum(len(lzma.compress(bytes(g), preset=9 | lzma.PRESET_EXTREME)) for g in gops if g)
    return {
        "file": Path(path).name,
        "frames": data["frame_count"],
        "js": len(raw),
        "js_deflate": len(zlib.compress(raw, 6)),
        "payload": payload,
        "huff0": round(huff0),
        "huff_split": round(huff_split),
        "lz_deflate": lz,
        "xz": xz,
    }


def packed(n_bytes):
    """n バイトを今の 15bit 文字に詰めたときの (JS の文字列のメモリ, .js の UTF-8 のバイト数)。"""
    chars = math.ceil(n_bytes * 8 / 15) + 1
    return chars * 2, chars * 3


def main(argv=None):
    paths = (argv or sys.argv[1:])
    rows = [study(p) for p in paths]
    print(json.dumps(rows, indent=1))
    print("\n| file | payload | huff0 | huff_split | lz(deflate) | xz | 今の .mcaddon (js_deflate) | xz を 15bit に詰めた .js (UTF-8) |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        p = r["payload"]
        pct = lambda v: f"{v / 1024:.1f} KB ({(v - p) / p * 100:+.0f}%)"  # noqa: E731
        print(f"| {r['file']} | {p / 1024:.1f} KB | {pct(r['huff0'])} | {pct(r['huff_split'])} | {pct(r['lz_deflate'])} | "
              f"{pct(r['xz'])} | {r['js_deflate'] / 1024:.1f} KB | {packed(r['xz'])[1] / 1024:.1f} KB |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
