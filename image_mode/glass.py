"""画像モードの「ガラスケースの立体」を作る計算 (docs/research/glass_case.md)。パックの組み立ては convert_image_mode.py。

- figure_from_volumes: 立体のコマ → 面の板 (planes.py) のシートと geometry のボーン
- case_texture: ケースの床と壁の絵 (--case-image) を 1 枚のテクスチャに詰める
- wall_plane: 奥の壁で流す動画の板の大きさ (9.1)
- lift_frames: 普通の動画を、ブロックモードと同じ方法 (mvcodec.volume) で浮き彫りの立体にする (9.2)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from image_mode import planes
from image_mode.glass_rp import CASE_PARTS, case_image_flips

# 人物のコマ数の上限 (geometry はコマごとにボーンと板を持つので、コマが多いと geometry が大きくなる)。設計の 5.4
MAX_FIGURE_FRAMES = 240
# 板の合計がこれを超えたら警告 (重くなるかもしれない)、QUAD_ERROR を超えたら作らない (--allow-over-budget で作る)
QUAD_WARNING = 50_000
QUAD_ERROR = 250_000
# ケースの絵の細かさ (1 ブロックの画素数) と、1 枚の一辺の上限
CASE_PIXELS_PER_BLOCK = 32
CASE_MAX_SIDE = 512
# 絵を渡さなかった部品の色 (落ち着いた暗い色。人物が目立つように) と、縁の色
CASE_DEFAULT_COLOURS = {"back": (38, 42, 56), "front": (38, 42, 56), "left": (32, 36, 48), "right": (32, 36, 48),
                        "floor": (22, 22, 28)}
FRAME_COLOUR = (200, 204, 214)
# 浮き彫りのパレットの色数 (mvcodec.volume の空気が 127 なので、それより少なく)
LIFT_COLOURS = 120
DEFAULT_LIFT_SIZE = (128, 72)


@dataclass
class CaseOptions:
    margin: float = 0.5
    images: dict = field(default_factory=dict)
    frame: bool = False


def figure_from_volumes(volumes: list[np.ndarray], rgbs: list[np.ndarray], *, figure_blocks: float,
                        split: str = planes.DEFAULT_PLANE_SPLIT, shade: bool = True,
                        mapping: planes.FaceMapping = planes.DEFAULT_MAPPING, max_sheet: int = 2048,
                        gutter: int = 1) -> dict:
    """立体のコマ (x, y, z の色番号。0 は空) から、人物のシートと geometry のボーン。

    大きさは、全コマを合わせた中身の高さが figure_blocks ブロックになるように決め、水平の中心を原点、足元を y = 0 に置く。"""
    (x0, y0, z0), (x1, y1, z1) = planes.figure_extent(volumes)
    scale = 16 * figure_blocks / (z1 - z0)
    placing = planes.Placing(scale=scale, centre=((x0 + x1) / 2, (y0 + y1) / 2), offset=(0.0, -z0 * scale, 0.0))
    all_quads = [planes.frame_quads(volume, rgb, split, shade) for volume, rgb in zip(volumes, rgbs)]
    images = [[planes.oriented_image(quad, mapping) for quad in quads] for quads in all_quads]
    sheets, placements, per_sheet, size = planes.pack_frames(images, max_sheet, gutter)
    bones = []
    for number, (quads, places) in enumerate(zip(all_quads, placements)):
        bones.append({"name": f"f{number}", "pivot": [0, 0, 0],
                      "cubes": [planes.quad_cube(quad, place, placing, mapping) for quad, place in zip(quads, places)]})
    count = sum(len(quads) for quads in all_quads)
    return {
        "frames": len(volumes), "per_sheet": per_sheet, "sheet_width": size[0], "sheet_height": size[1],
        "sheets": [Image.fromarray(sheet, "RGBA") for sheet in sheets], "bones": bones, "quads": count,
        # 人物の大きさ (ブロック): 幅 (x)・奥行き (y)・高さ
        "size_blocks": ((x1 - x0) * scale / 16, (y1 - y0) * scale / 16, figure_blocks),
    }


def case_box(figure_size: tuple, case: CaseOptions | None) -> tuple:
    """ケース (無ければ人物) の (幅, 奥行き, 高さ) ブロック。ケースは人物の周りに margin ずつ空ける。"""
    width, depth, height = figure_size
    if case is None:
        return (round(width, 4), round(depth, 4), round(height, 4))
    m = case.margin
    return (round(width + 2 * m, 4), round(depth + 2 * m, 4), round(height + m, 4))


def _part_size(part: str, box: tuple) -> tuple[float, float]:
    """部品の (横, 縦) ブロック (内側から見た向き)。"""
    width, depth, height = box
    return {"back": (width, height), "front": (width, height), "left": (depth, height), "right": (depth, height),
            "floor": (width, depth)}[part]


def _fit_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """縦横比を保って (width, height) を埋めるように縮め、はみ出した所を中央で切る。"""
    image = image.convert("RGB")
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))),
                           Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def case_texture(box: tuple, case: CaseOptions, mapping: planes.FaceMapping = planes.DEFAULT_MAPPING,
                 gutter: int = 1) -> tuple[Image.Image, dict, tuple | None]:
    """ケースの部品の絵を 1 枚に詰める。戻り値は (テクスチャ, 部品ごとの uv (x, y, 幅, 高さ), 縁の色の画素 (x, y) か None)。"""
    images = {}
    for part in CASE_PARTS:
        path = case.images.get(part) or case.images.get("all")
        if path:
            across, down = _part_size(part, box)
            longest = max(across, down)
            pixels = min(CASE_PIXELS_PER_BLOCK, CASE_MAX_SIDE / longest)
            w, h = max(1, round(across * pixels)), max(1, round(down * pixels))
            try:
                with Image.open(path) as source:
                    picture = np.asarray(_fit_cover(source, w, h))
            except (OSError, Image.DecompressionBombError) as error:  # 大きすぎる絵は Pillow が断る (AUD2-08)
                raise ValueError(f"ケースの絵を読めませんでした: {path}: {error}") from None
        else:
            picture = np.array([[CASE_DEFAULT_COLOURS[part]]], np.uint8)
        flip_u, flip_v = case_image_flips(part, mapping)
        if flip_u:
            picture = picture[:, ::-1]
        if flip_v:
            picture = picture[::-1, :]
        images[part] = np.ascontiguousarray(picture)
    order = list(CASE_PARTS) + (["frame"] if case.frame else [])
    if case.frame:
        images["frame"] = np.array([[FRAME_COLOUR]], np.uint8)
    sizes = [(images[name].shape[1], images[name].shape[0]) for name in order]
    packed = planes._shelf_pack(sizes, 4096, gutter)
    if packed is None:
        raise ValueError("ケースの絵がテクスチャに入りません")
    positions, (sheet_w, sheet_h) = packed
    canvas = np.zeros((max(1, sheet_h), max(1, sheet_w), 4), np.uint8)
    uv = {}
    for name, (x, y) in zip(order, positions):
        rgba = np.concatenate([images[name], np.full(images[name].shape[:2] + (1,), 255, np.uint8)], axis=2)
        planes._blit_with_gutter(canvas, rgba, x, y, gutter)
        uv[name] = [x, y, images[name].shape[1], images[name].shape[0]]
    frame = tuple(uv.pop("frame")[:2]) if case.frame else None
    return Image.fromarray(canvas, "RGBA"), uv, frame


def wall_plane(box: tuple, width: int, height: int) -> list:
    """奥の壁で流す動画の板の [幅, 高さ, 下端の高さ] (ブロック)。壁の 9 割に縦横比を保って収め、上下の真ん中に置く。"""
    wall_w, _depth, wall_h = box
    plane_w = min(wall_w * 0.9, wall_h * 0.9 * width / height)
    plane_h = plane_w * height / width
    return [round(plane_w, 4), round(plane_h, 4), round((wall_h - plane_h) / 2, 4)]


def video_palette(frames: list[np.ndarray], colours: int = LIFT_COLOURS) -> tuple[np.ndarray, list[np.ndarray]]:
    """動画のコマ (H×W×3) に共通のパレットを作り、コマごとのパレットの番号 (H×W) を返す。"""
    step = max(1, len(frames) // 32)
    sample = np.concatenate(frames[::step][:32], axis=0)
    palette_image = Image.fromarray(sample, "RGB").quantize(colours, method=Image.Quantize.MEDIANCUT)
    raw = np.array(palette_image.getpalette()[:colours * 3], np.uint8).reshape(-1, 3)
    indices = [np.asarray(Image.fromarray(frame, "RGB").quantize(palette=palette_image, dither=Image.Dither.NONE))
               for frame in frames]
    return raw, indices


def lift_frames(frames: list[np.ndarray], mode: str, depth: int, heights: list[np.ndarray] | None = None,
                threshold: float = 0.5, invert: bool = False) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """普通の動画のコマ (H×W×3) を、ブロックモードと同じ方法で立体にする (relief / extrude / inflate)。

    heights はコマごとの 0〜1 の高さ (relief の深度動画、extrude・inflate の切り抜き)。無ければ元の動画の明るさ。
    戻り値は planes が使う形 (x, y, z の色番号。0 は空) の立体と、そのパレット (コマで共通)。"""
    from mvcodec.volume import AIR_LEVEL, VolumeLifter, luminance01

    palette, indices = video_palette(frames)
    lifter = VolumeLifter(mode, depth, threshold, invert)
    volumes = []
    for number, (frame, index) in enumerate(zip(frames, indices)):
        heights01 = heights[number] if heights is not None and number < len(heights) else luminance01(frame)
        volumes.append(planes.lift_video_volume(lifter(index, palette, heights01), AIR_LEVEL))
    return volumes, [palette] * len(volumes)


def luminance_frames(frames) -> list[np.ndarray]:
    """深度動画・切り抜きの動画のコマを 0〜1 の明るさにする。"""
    from mvcodec.volume import luminance01

    return [luminance01(frame) for frame in frames]


def parse_case_images(items: list[str] | None) -> dict:
    """--case-image の SIDE=PATH (SIDE は back・front・left・right・floor・all) を辞書にする。"""
    from image_mode.glass_rp import CASE_IMAGE_SIDES

    images = {}
    for item in items or []:
        side, sep, path = item.partition("=")
        side = side.strip().lower()
        if not sep or side not in CASE_IMAGE_SIDES or not path:
            raise ValueError(f"--case-image は {'・'.join(CASE_IMAGE_SIDES)} のどれか=画像のパス: {item}")
        if not Path(path).is_file():
            raise ValueError(f"ケースの絵が見つかりません: {path}")
        images[side] = path
    return images
