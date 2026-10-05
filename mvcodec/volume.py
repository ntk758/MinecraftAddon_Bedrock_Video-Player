"""3D 立体 (ホログラム) 再生用のボクセル生成。numpy だけで動く。

立体は (奥行き D, 高さ H, 幅 W) の配列で、値はパレット番号、空気は AIR_LEVEL。
軸の向き: 高さ方向は画像と同じく 0 が一番上、奥行きは 0 が一番奥 (プレイヤーから遠い側)。
符号化では (D*H) 行 × W 列の 2D 画像とみなし、2D と同じ差分・動き補償の仕組みをそのまま使う。

入力の種類:
  relief   各画素の明るさ (または別に用意した深度動画) を奥行きにした立体レリーフ。表面と段差の側面だけを作る
  extrude  明るい (または暗い) 部分を前後に押し出した立体シルエット (Bad Apple のような白黒映像向け)
  vox      MagicaVoxel の .vox (複数モデル = 複数フレーム、またはフレームごとの .vox が並んだフォルダ)
"""
import struct
from pathlib import Path

import numpy as np

AIR_LEVEL = 127
VOLUME_MODES = ("relief", "extrude", "vox")


def luminance01(rgb):
    """(…, 3) の RGB (0〜255) から 0〜1 の明るさ。"""
    rgb = np.asarray(rgb, dtype=np.float32)
    return (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114) / 255.0


def relief_volume(indices, heights01, depth):
    """2D のパレット番号と 0〜1 の高さから立体レリーフを作る。

    高さ 1 が一番手前。各画素は奥行き round(h*(depth-1)) の位置に表面のボクセルを置き、
    隣の画素の方が低い (奥にある) ときは、その段差を埋める側面も置く (横から見ても穴が空かない)。
    """
    indices = np.asarray(indices)
    h, w = indices.shape
    level = np.clip(np.rint(np.asarray(heights01, dtype=np.float32) * (depth - 1)), 0, depth - 1).astype(np.int32)
    padded = np.pad(level, 1, mode="edge")
    neighbor_min = np.minimum.reduce([padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]])
    low = np.minimum(level, neighbor_min + 1)

    k = np.arange(depth)[:, None, None]
    solid = (k >= low[None]) & (k <= level[None])
    volume = np.full((depth, h, w), AIR_LEVEL, dtype=np.int16)
    volume[solid] = np.broadcast_to(indices[None], (depth, h, w))[solid]
    return volume


def extrude_volume(indices, mask, depth, thickness=None):
    """mask が真の画素を、奥行き方向の中央に thickness 段ぶん押し出した立体 (既定は全段)。"""
    indices = np.asarray(indices)
    h, w = indices.shape
    thickness = depth if thickness is None else max(1, min(depth, thickness))
    start = (depth - thickness) // 2
    volume = np.full((depth, h, w), AIR_LEVEL, dtype=np.int16)
    volume[start:start + thickness][:, mask] = indices[mask]
    return volume


def hollow(volume):
    """外から見えない内部のボクセルを空気にする (置くブロック数を減らす)。

    6 方向の隣がすべて埋まっているボクセルを内部とみなす。範囲外は空気扱いなので、外周は残る。
    """
    solid = volume != AIR_LEVEL
    padded = np.pad(solid, 1, constant_values=False)
    interior = solid.copy()
    for axis in range(3):
        for shift in (-1, 1):
            interior &= np.roll(padded, shift, axis=axis)[1:-1, 1:-1, 1:-1]
    out = volume.copy()
    out[interior] = AIR_LEVEL
    return out


def lift_frame(mode, indices, palette_rgb, depth, heights01=None, threshold=0.5, invert=False,
               thickness=None, make_hollow=True):
    """2D のパレット番号フレームを立体にする (relief / extrude)。

    heights01 が無ければ、各画素のブロック色の明るさを高さ (relief) や押し出す範囲 (extrude) に使う。
    """
    indices = np.asarray(indices)
    if heights01 is None:
        heights01 = luminance01(np.asarray(palette_rgb)[indices])
    if mode == "relief":
        volume = relief_volume(indices, heights01, depth)
    elif mode == "extrude":
        mask = heights01 < threshold if invert else heights01 >= threshold
        volume = extrude_volume(indices, mask, depth, thickness)
    else:
        raise ValueError(f"未対応の立体モード: {mode}")
    return hollow(volume) if make_hollow else volume


# ---------------------------------------------------------------------------
# MagicaVoxel .vox
# ---------------------------------------------------------------------------

def _read_chunks(data, start, end):
    """[(id, content, children_bytes_range)] を順に返す。"""
    pos = start
    while pos + 12 <= end:
        chunk_id = data[pos:pos + 4].decode("ascii", errors="replace")
        content_size, children_size = struct.unpack_from("<ii", data, pos + 4)
        content_start = pos + 12
        children_start = content_start + content_size
        yield chunk_id, data[content_start:children_start], (children_start, children_start + children_size)
        pos = children_start + children_size


def parse_vox(data):
    """.vox のバイト列から (models, palette) を返す。

    models: [(size_xyz, voxels)]  voxels は (N, 4) の uint8 配列 [x, y, z, 色番号(1〜255)]
    palette: (256, 4) の RGBA。色番号 i は palette[i - 1]。RGBA チャンクが無いファイルは灰色の階調で代用する。
    """
    if data[:4] != b"VOX ":
        raise ValueError("MagicaVoxel の .vox ファイルではありません")
    main = next(_read_chunks(data, 8, len(data)), None)
    if main is None or main[0] != "MAIN":
        raise ValueError(".vox に MAIN チャンクがありません")
    models, palette = [], None
    size = None
    for chunk_id, content, _children in _read_chunks(data, *main[2]):
        if chunk_id == "SIZE":
            size = struct.unpack_from("<iii", content, 0)
        elif chunk_id == "XYZI":
            count = struct.unpack_from("<i", content, 0)[0]
            voxels = np.frombuffer(content, dtype=np.uint8, count=count * 4, offset=4).reshape(count, 4)
            models.append((size, voxels.copy()))
        elif chunk_id == "RGBA":
            palette = np.frombuffer(content, dtype=np.uint8, count=256 * 4).reshape(256, 4).copy()
    if palette is None:
        ramp = np.linspace(255, 32, 256).astype(np.uint8)
        palette = np.stack([ramp, ramp, ramp, np.full(256, 255, np.uint8)], axis=1)
    if not models:
        raise ValueError(".vox にモデルがありません")
    return models, palette


def write_vox(models, palette=None):
    """テスト・サンプル作成用の最小の .vox 書き出し。models は [(size_xyz, voxels)]。"""
    def chunk(chunk_id, content=b"", children=b""):
        return chunk_id.encode("ascii") + struct.pack("<ii", len(content), len(children)) + content + children

    body = b""
    if len(models) > 1:
        body += chunk("PACK", struct.pack("<i", len(models)))
    for size, voxels in models:
        voxels = np.asarray(voxels, dtype=np.uint8).reshape(-1, 4)
        body += chunk("SIZE", struct.pack("<iii", *size))
        body += chunk("XYZI", struct.pack("<i", len(voxels)) + voxels.tobytes())
    if palette is not None:
        body += chunk("RGBA", np.asarray(palette, dtype=np.uint8).reshape(256, 4).tobytes())
    return b"VOX " + struct.pack("<i", 150) + chunk("MAIN", children=body)


def load_vox_frames(path):
    """.vox ファイル (各モデルが 1 フレーム) またはフレームごとの .vox が並んだフォルダを読み込む。

    戻り値: (frames, palette)  frames は [(size_xyz, voxels)]、palette は最初のファイルのもの。
    """
    path = Path(path)
    files = sorted(path.glob("*.vox")) if path.is_dir() else [path]
    if not files:
        raise ValueError(f".vox ファイルが見つかりません: {path}")
    frames, palette = [], None
    for file in files:
        models, file_palette = parse_vox(file.read_bytes())
        palette = file_palette if palette is None else palette
        frames.extend(models)
    return frames, palette


def vox_volume_shape(frames):
    """全フレームが入る (D, H, W)。vox の (x, y, z) は (幅, 奥行き, 高さ)。"""
    sx = max(size[0] for size, _ in frames)
    sy = max(size[1] for size, _ in frames)
    sz = max(size[2] for size, _ in frames)
    return sy, sz, sx


def vox_to_volume(size_and_voxels, shape, color_to_level):
    """1 フレームの vox を (D, H, W) のパレット番号の立体にする。

    vox の x → 幅、y → 奥行き (0 = 奥)、z → 高さ (0 = 床なので、配列の行は H-1-z)。
    color_to_level は vox の色番号 (0〜255) → ブロックのパレット番号の配列。
    """
    depth, height, width = shape
    _size, voxels = size_and_voxels
    volume = np.full(shape, AIR_LEVEL, dtype=np.int16)
    if len(voxels):
        x = voxels[:, 0].astype(np.int64)
        y = voxels[:, 1].astype(np.int64)
        z = voxels[:, 2].astype(np.int64)
        keep = (x < width) & (y < depth) & (z < height)
        volume[y[keep], height - 1 - z[keep], x[keep]] = color_to_level[voxels[keep, 3]]
    return volume


def nearest_levels(colors_rgb, palette_rgb):
    """各色に一番近いブロックのパレット番号 (知覚に近い重み付き RGB 距離)。"""
    colors = np.asarray(colors_rgb, dtype=np.float32)[:, None, :]
    palette = np.asarray(palette_rgb, dtype=np.float32)[None, :, :]
    weights = np.array([0.30, 0.59, 0.11], dtype=np.float32)
    distance = (((colors - palette) ** 2) * weights).sum(axis=2)
    return distance.argmin(axis=1).astype(np.int16)


def front_view(volume, palette_rgb, background=(0, 0, 0)):
    """立体を手前から見た画像 (H, W, 3)。各画素は一番手前にあるブロックの色 (サムネイル用)。"""
    volume = np.asarray(volume)
    depth, height, width = volume.shape
    solid = volume != AIR_LEVEL
    # 手前 (奥行きの大きい方) から最初に当たるボクセル
    front_index = depth - 1 - np.argmax(solid[::-1], axis=0)
    hit = solid.any(axis=0)
    levels = np.take_along_axis(volume, front_index[None], axis=0)[0]
    table = np.vstack([np.asarray(palette_rgb, dtype=np.uint8), np.zeros((128, 3), np.uint8)])
    image = np.empty((height, width, 3), np.uint8)
    image[:] = background
    image[hit] = table[levels[hit]]
    return image
