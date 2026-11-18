"""画像モードの「ガラスケースの立体」: ボクセルの外に出ている面を、軸ごとの平面の板 (アルファテスト) にする。

設計は docs/research/glass_case.md の 5 章。立体 (sx, sy, sz) の x の軸には、ボクセルの境目 x = 0..sx に板を置く。
板 x = k の画素 (y, z) は、ボクセル (k-1, y, z) と (k, y, z) の片方だけが埋まっているときに、埋まっている方の色を持つ。
3 軸の板を合わせるとボクセルの表面がちょうど全部そろうので、ゲームの GPU が板を描けば、どの向きから見ても本物の
立方体と同じに見える (板の裏は必ずそのボクセルの内側にあり、手前の面に隠れる)。カメラの計算はいらない。

座標: ボクセルは impostor.py と同じ (x が右 (東)、y が奥 (北)、z が上。正面は y が小さい側)。
geometry の座標 (1/16 ブロック) への対応と、板の面 (east・north・up) の UV の向きは FaceMapping。
UV の向きは Minecraft の実機で確かめる (scripts/make_planes_test_pack.py の 16 通り。DECISIONS.md の A19)。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PLANE_SPLITS = ("rect", "tile8", "tile16")
DEFAULT_PLANE_SPLIT = "rect"
# 面の明るさ (Minecraft のブロックの面と同じ割合。impostor.FACE_SHADE と同じ)。(軸, 外向きの符号)
SHADE = {(0, 1): 0.6, (0, -1): 0.6, (1, 1): 0.8, (1, -1): 0.8, (2, 1): 1.0, (2, -1): 0.5}
# ボクセルの軸 → 板の面の名前 (geometry の面。厚み 0 の直方体のこの面だけに UV を貼る)
FACE_OF_AXIS = {0: "east", 1: "north", 2: "up"}
# 板の上の 2 つの軸 (ボクセルの軸)。画像の列が 1 つ目、行が 2 つ目
PLANE_AXES = {0: (1, 2), 1: (0, 2), 2: (0, 1)}


@dataclass(frozen=True)
class FaceMapping:
    """板の面の UV の向き。各値は、テクスチャの u (右へ) / v (下へ) が geometry のどの向きに進むか (+1 / -1)。

    north: u は x に沿う (north_u)、v は -y。east: u は z に沿う (east_u)、v は -y。up: u は x (up_u)、v は z (up_v)。
    ボクセルの右 (+x) は、正面から見た north の面の右 (= north_u の向き) に置く。スクリーンの板 (rp.py) は north の面を
    正面から見て左右が正しいことを実機で確かめてあるので、こうすると立体も左右が正しくなる。
    既定値 (1, -1, 1, -1) は Minecraft の実機 (Windows 11、v26.65、2026-11-18) で、16 通りを並べた試験用のアドオン
    (scripts/make_planes_test_pack.py の v5) だけが、前・横・後ろ・斜め上から立方体がつながって見えたもの (DECISIONS.md の A19)。
    最初の推測 (1, 1, -1, 1) では、上の面と横の面がずれた。east の面は geometry の -x を向いている (east_u * north_u が -1)。
    """
    north_u: int = 1
    east_u: int = -1
    up_u: int = 1
    up_v: int = -1

    def east_normal(self) -> int:
        """east の面が向く geometry の x の向き (+1 / -1)。各面を外から見た向きの決まりから、east_u * north_u。"""
        return self.east_u * self.north_u

    def model_axis(self, axis: int) -> tuple[int, int]:
        """ボクセルの軸 → (geometry の軸 0=x 1=y 2=z, 符号)。"""
        return {0: (0, self.north_u), 1: (2, 1), 2: (1, 1)}[axis]

    def face_uv(self, axis: int) -> tuple[tuple[int, int], tuple[int, int]]:
        """板 (ボクセルの軸 axis) の面の u と v の向き: ((geometry の軸, 符号), (geometry の軸, 符号))。"""
        return {
            0: ((2, self.east_u), (1, -1)),
            1: ((0, self.north_u), (1, -1)),
            2: ((0, self.up_u), (2, self.up_v)),
        }[axis]

    def flips(self, axis: int) -> tuple[bool, bool]:
        """板の画像を (左右, 上下) 反転して置くか。画像は列 = 板の 1 つ目の軸の小さい順、行 = 2 つ目の軸の小さい順で作る。"""
        (u_axis, u_sign), (v_axis, v_sign) = self.face_uv(axis)
        pa, pb = PLANE_AXES[axis]
        a_axis, a_sign = self.model_axis(pa)
        b_axis, b_sign = self.model_axis(pb)
        if (a_axis, b_axis) != (u_axis, v_axis):
            raise ValueError(f"板の軸と面の UV の軸が合いません: {axis}")
        return a_sign != u_sign, b_sign != v_sign


DEFAULT_MAPPING = FaceMapping()


@dataclass
class Quad:
    """板 1 枚。axis の境目 k に置き、板の上の範囲は [a0, a0 + width) × [b0, b0 + height) (ボクセル)。
    image は (height, width, 4) の RGBA で、行 = 2 つ目の軸の小さい順、列 = 1 つ目の軸の小さい順 (反転する前)。"""
    axis: int
    k: int
    a0: int
    b0: int
    width: int
    height: int
    image: np.ndarray


def face_masks(volume: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """軸 axis の板 (sx+1 枚など) ごとの (面がある画素, その面のボクセルの番号 (0 は無し), 面が + を向くか)。

    戻り値は (colour_index, positive)。どちらも形は (板の数, 1 つ目の軸, 2 つ目の軸)。
    colour_index は面を持つボクセルの色番号 (0 は面なし)、positive は面が +axis を向くとき真。"""
    solid = volume > 0
    pad = [(1, 1) if a == axis else (0, 0) for a in range(3)]
    padded_solid = np.pad(solid, pad)
    padded_volume = np.pad(volume, pad)
    count = padded_solid.shape[axis] - 1
    lo_solid = np.take(padded_solid, range(count), axis=axis)     # ボクセル k-1
    hi_solid = np.take(padded_solid, range(1, count + 1), axis=axis)  # ボクセル k
    lo_value = np.take(padded_volume, range(count), axis=axis)
    hi_value = np.take(padded_volume, range(1, count + 1), axis=axis)
    positive = lo_solid & ~hi_solid           # k-1 が埋まり、外向きは +axis
    negative = hi_solid & ~lo_solid           # k が埋まり、外向きは -axis
    colour = np.where(positive, lo_value, np.where(negative, hi_value, 0))
    return np.moveaxis(colour, axis, 0), np.moveaxis(positive, axis, 0)


def _rects(mask: np.ndarray, split: str) -> list[tuple[int, int, int, int]]:
    """板の上の面のある範囲を長方形に分ける: [(a0, b0, 幅, 高さ)]。tile は N×N の区画ごとに、中身の範囲まで縮める。"""
    if not mask.any():
        return []
    if split == "rect":
        tiles = [(0, 0, mask.shape[0], mask.shape[1])]
    else:
        size = int(split[4:])
        tiles = [(a, b, min(size, mask.shape[0] - a), min(size, mask.shape[1] - b))
                 for a in range(0, mask.shape[0], size) for b in range(0, mask.shape[1], size)]
    rects = []
    for a, b, wa, hb in tiles:
        part = mask[a:a + wa, b:b + hb]
        if not part.any():
            continue
        rows = np.nonzero(part.any(axis=1))[0]
        cols = np.nonzero(part.any(axis=0))[0]
        rects.append((a + int(rows[0]), b + int(cols[0]), int(rows[-1] - rows[0] + 1), int(cols[-1] - cols[0] + 1)))
    return rects


def frame_quads(volume: np.ndarray, rgb: np.ndarray, split: str = DEFAULT_PLANE_SPLIT,
                shade: bool = True) -> list[Quad]:
    """1 コマの立体 (x, y, z の色番号。0 は空) の板。rgb はパレット (色番号 1 が rgb[0])。"""
    if split not in PLANE_SPLITS:
        raise ValueError(f"split は {', '.join(PLANE_SPLITS)} のどれか: {split}")
    palette = np.asarray(rgb, np.float32)[:, :3]
    quads = []
    for axis in range(3):
        colour, positive = face_masks(volume, axis)
        for k in range(colour.shape[0]):
            plane = colour[k]
            mask = plane > 0
            for a0, b0, wa, hb in _rects(mask, split):
                part = plane[a0:a0 + wa, b0:b0 + hb]
                facing = positive[k][a0:a0 + wa, b0:b0 + hb]
                rgb_part = palette[np.clip(part.astype(np.int64) - 1, 0, len(palette) - 1)]
                if shade:
                    factor = np.where(facing, SHADE[(axis, 1)], SHADE[(axis, -1)]).astype(np.float32)
                    rgb_part = rgb_part * factor[..., None]
                image = np.zeros((hb, wa, 4), np.uint8)
                # 板の配列は [1 つ目の軸, 2 つ目の軸]。画像は [行 = 2 つ目, 列 = 1 つ目]
                image[..., :3] = np.clip(np.rint(rgb_part), 0, 255).astype(np.uint8).transpose(1, 0, 2)
                image[..., 3] = np.where(part > 0, 255, 0).astype(np.uint8).T
                quads.append(Quad(axis, k, a0, b0, wa, hb, image))
    return quads


def oriented_image(quad: Quad, mapping: FaceMapping = DEFAULT_MAPPING) -> np.ndarray:
    """シートに置く向きにした板の画像 (UV の uv_size をいつも正にするため、ここで反転しておく)。"""
    flip_u, flip_v = mapping.flips(quad.axis)
    image = quad.image
    if flip_u:
        image = image[:, ::-1]
    if flip_v:
        image = image[::-1, :]
    return np.ascontiguousarray(image)


@dataclass
class Placement:
    """シートの上の板の画像の位置 (gutter を除いた左上)。"""
    sheet: int
    x: int
    y: int


def _shelf_pack(sizes: list[tuple[int, int]], max_sheet: int, gutter: int):
    """大きさ [(幅, 高さ)] を max_sheet 四方に棚詰めする。入れば [(x, y)] と (使った幅, 高さ)、入らなければ None。
    位置は gutter を含む枠の左上 + gutter (板の周りに gutter 画素の縁を付ける)。高いものから詰める。"""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    positions: list[tuple[int, int] | None] = [None] * len(sizes)
    x = y = shelf_height = used_width = 0
    for i in order:
        w, h = sizes[i][0] + 2 * gutter, sizes[i][1] + 2 * gutter
        if w > max_sheet or h > max_sheet:
            return None
        if x + w > max_sheet:
            y += shelf_height
            x = shelf_height = 0
        if y + h > max_sheet:
            return None
        positions[i] = (x + gutter, y + gutter)
        x += w
        shelf_height = max(shelf_height, h)
        used_width = max(used_width, x)
    return positions, (used_width, y + shelf_height)


def pack_frames(frames: list[list[np.ndarray]], max_sheet: int = 2048, gutter: int = 1):
    """コマごとの板の画像 (oriented_image の後) を、1 枚に決まった数 K のコマずつシートに詰める。

    コマ f はシート f // K。戻り値は (シートの画像 (RGBA) のリスト, コマごとの [Placement], K, (シートの幅, 高さ))。
    全シート同じ大きさ。1 コマも入らなければ ValueError。"""
    if not frames:
        raise ValueError("コマがありません")
    areas = [sum((img.shape[1] + 2 * gutter) * (img.shape[0] + 2 * gutter) for img in images) or 1 for images in frames]
    per_sheet = max(1, min(len(frames), int(max_sheet * max_sheet * 0.9 // max(areas))))
    while True:
        groups = []
        for start in range(0, len(frames), per_sheet):
            images = [img for images in frames[start:start + per_sheet] for img in images]
            packed = _shelf_pack([(img.shape[1], img.shape[0]) for img in images], max_sheet, gutter)
            if packed is None:
                break
            groups.append(packed)
        else:
            break
        if per_sheet == 1:
            raise ValueError(f"1 コマの板がシート ({max_sheet}x{max_sheet}) に入りません。"
                             "--max-sheet を大きくするか、立体を小さくしてください")
        per_sheet = max(1, int(per_sheet * 0.85)) if per_sheet > 8 else per_sheet - 1
    sheet_w = max(1, max(size[0] for _pos, size in groups))
    sheet_h = max(1, max(size[1] for _pos, size in groups))
    sheets = []
    placements: list[list[Placement]] = []
    for number, (positions, _size) in enumerate(groups):
        canvas = np.zeros((sheet_h, sheet_w, 4), np.uint8)
        index = 0
        for images in frames[number * per_sheet:(number + 1) * per_sheet]:
            frame_places = []
            for img in images:
                x, y = positions[index]
                index += 1
                _blit_with_gutter(canvas, img, x, y, gutter)
                frame_places.append(Placement(number, x, y))
            placements.append(frame_places)
        sheets.append(canvas)
    return sheets, placements, per_sheet, (sheet_w, sheet_h)


def _blit_with_gutter(canvas: np.ndarray, image: np.ndarray, x: int, y: int, gutter: int) -> None:
    """画像を置き、周りの gutter 画素を端の画素の繰り返しで埋める (縮小やフィルタで隣の板の色が混ざらないように)。"""
    h, w = image.shape[:2]
    if gutter:
        padded = np.pad(image, ((gutter, gutter), (gutter, gutter), (0, 0)), mode="edge")
        canvas[y - gutter:y + h + gutter, x - gutter:x + w + gutter] = padded
    else:
        canvas[y:y + h, x:x + w] = image


@dataclass(frozen=True)
class Placing:
    """立体を geometry に置く大きさと位置。scale は 1 ボクセルの geometry の単位 (1/16 ブロック)、centre は立体の
    水平の中心 (ボクセルの x, y)。offset は geometry での追加の位置 (x, y, z。試験用のパックで並べるとき)。"""
    scale: float
    centre: tuple[float, float]
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _num(value: float):
    value = round(float(value), 4)
    return int(value) if value == int(value) else value


def quad_cube(quad: Quad, place: Placement, placing: Placing, mapping: FaceMapping = DEFAULT_MAPPING) -> dict:
    """板 1 枚の geometry の直方体 (厚み 0)。面 (east・north・up) の 1 つだけに UV を貼る (ほかの面は描かれない)。"""
    s = placing.scale
    cx, cy = placing.centre
    ox, oy, oz = placing.offset
    lo = [0.0, 0.0, 0.0]
    hi = [0.0, 0.0, 0.0]
    pa, pb = PLANE_AXES[quad.axis]
    ranges = {quad.axis: (quad.k, quad.k), pa: (quad.a0, quad.a0 + quad.width), pb: (quad.b0, quad.b0 + quad.height)}
    centres = {0: cx, 1: cy, 2: 0.0}
    offsets = (ox, oy, oz)
    for vox_axis, (start, end) in ranges.items():
        model, sign = mapping.model_axis(vox_axis)
        a = sign * (start - centres[vox_axis]) * s
        b = sign * (end - centres[vox_axis]) * s
        lo[model], hi[model] = min(a, b) + offsets[model], max(a, b) + offsets[model]
    return {
        "origin": [_num(v) for v in lo],
        "size": [_num(h - l) for l, h in zip(lo, hi)],
        "uv": {FACE_OF_AXIS[quad.axis]: {"uv": [place.x, place.y], "uv_size": [quad.width, quad.height]}},
    }


def model_point(voxel, placing: Placing, mapping: FaceMapping = DEFAULT_MAPPING) -> list[float]:
    """ボクセルの座標の点 (x, y, z。実数) → geometry の座標 (quad_cube と同じ置き方。ボーンの pivot に使う)。"""
    centres = (placing.centre[0], placing.centre[1], 0.0)
    out = [0.0, 0.0, 0.0]
    for vox_axis in range(3):
        model, sign = mapping.model_axis(vox_axis)
        out[model] = sign * (float(voxel[vox_axis]) - centres[vox_axis]) * placing.scale + placing.offset[model]
    return out


def figure_extent(volumes: list[np.ndarray]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """全コマを合わせた、埋まっているボクセルの範囲 ((x0, y0, z0), (x1, y1, z1)。1 は含まない)。"""
    union = np.zeros(volumes[0].shape, bool)
    for volume in volumes:
        union |= volume > 0
    if not union.any():
        raise ValueError("立体が空です (どのコマにもボクセルがありません)")
    lo, hi = [], []
    for axis in range(3):
        others = tuple(a for a in range(3) if a != axis)
        filled = np.nonzero(union.any(axis=others))[0]
        lo.append(int(filled[0]))
        hi.append(int(filled[-1]) + 1)
    return tuple(lo), tuple(hi)


def lift_video_volume(volume: np.ndarray, air: int) -> np.ndarray:
    """mvcodec.volume の立体 (奥行き D, 高さ H, 幅 W。値はパレットの番号、air は空気。奥行き 0 が一番奥、高さ 0 が一番上)
    を、この形 (x, y, z の色番号。0 は空、色番号 = パレットの番号 + 1) にする。"""
    volume = np.asarray(volume)
    out = np.where(volume == air, 0, volume.astype(np.int32) + 1)
    # (D, H, W) → (x = W, y = 奥 (D の逆), z = 上 (H の逆))
    out = out[::-1, ::-1, :]                 # D: 0 が一番手前に、H: 0 が一番下に
    return np.ascontiguousarray(out.transpose(2, 0, 1)).astype(np.int32)
