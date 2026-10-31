"""変換したデータ (FRAME_DATA) から、ゲーム内でどう見えるかのプレビューの絵を作る。

復元はゲームと同じ規則の参照デコーダ (mvcodec.decode.iter_decoded_frames) で行う。平面はブロックの色をそのまま
1 ブロック = 1 画素で、立体 (レリーフ・押し出し・ふくらみ・.vox・だまし絵の人物) は南東の斜め上から見た等角投影
(mvcodec.volume.iso_view) で描く。ブラウザ版の「できあがりのプレビュー」と、Windows 版のプレビューの GIF が使う。

長い動画で重くならないよう、フレームを間引く (step) と、合計の大きさ (max_bytes) で上限を決める。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from mvcodec.decode import iter_decoded_frames, parse_index
from mvcodec.volume import iso_view

# プレビューの絵の合計の上限 (バイト)。ブラウザのメモリに置くので控えめにする
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
# 立体の等角投影は 1 枚に時間がかかるので、枚数の上限を別に決める
DEFAULT_MAX_VOLUME_FRAMES = 240
# 等角投影の絵の長い辺の目安 (画素)
VOLUME_TARGET_SIDE = 480


@dataclass
class Preview:
    width: int
    height: int
    fps: float  # プレビューの 1 秒あたりの枚数 (元の fps ÷ step)
    step: int  # 何フレームごとに描いたか
    total_frames: int  # 元のフレーム数
    volume: bool
    frames: list = field(default_factory=list)  # (高さ, 幅, 3) の uint8

    def packed(self) -> bytes:
        """全フレームを続けた RGB のバイト列 (ブラウザへ 1 回で渡す)。"""
        return b"".join(np.ascontiguousarray(f).tobytes() for f in self.frames)


def is_volume(data) -> bool:
    return data.get("layout") in ("volume", "heightfield")


def volume_unit(data) -> int:
    """等角投影の 1 ブロックの画素数。立体が大きいほど小さくして、絵の長い辺を VOLUME_TARGET_SIDE 前後にする。"""
    span = data["width"] + data.get("depth", 1) + data["height"]
    return max(1, min(6, VOLUME_TARGET_SIDE // max(1, span)))


def frame_count(data) -> int:
    """フレーム数 (変換の出力の frame_count。無ければ索引の数)。"""
    if data.get("frame_count"):
        return int(data["frame_count"])
    return len(parse_index(data))


def choose_step(total: int, frame_bytes: int, volume: bool,
                max_bytes: int = DEFAULT_MAX_BYTES, max_volume_frames: int = DEFAULT_MAX_VOLUME_FRAMES) -> int:
    """合計が max_bytes (立体は枚数も max_volume_frames) に収まる、一番小さい間引きの幅。"""
    if total <= 0:
        return 1
    limit = max(1, max_bytes // max(1, frame_bytes))
    if volume:
        limit = min(limit, max_volume_frames)
    return max(1, math.ceil(total / limit))


def render_preview(data, max_bytes: int = DEFAULT_MAX_BYTES, max_volume_frames: int = DEFAULT_MAX_VOLUME_FRAMES,
                   progress=None) -> Preview:
    """data (load_frame_data の結果) のプレビュー。progress(done, total) を数十フレームごとに呼ぶ。"""
    volume = is_volume(data)
    fps = float(data.get("fps") or 10)
    unit = volume_unit(data) if volume else 1
    frames = []
    total = None
    step = None
    sides = iter_decoded_frames(data, face="side") if volume else None
    count = 0
    for i, (index, rgb) in enumerate(iter_decoded_frames(data)):
        side_rgb = next(sides)[1] if volume else None
        count = i + 1
        if step is None:
            # 1 枚目の大きさから間引きの幅を決める (フレーム数は索引から分かる)
            first = iso_view(index, rgb=rgb, unit=unit, side_rgb=side_rgb) if volume else rgb
            total = frame_count(data) or 1
            step = choose_step(total, first.nbytes, volume, max_bytes, max_volume_frames)
            frames.append(first)
            continue
        if i % step == 0:
            frames.append(iso_view(index, rgb=rgb, unit=unit, side_rgb=side_rgb) if volume else rgb.copy())
        if progress is not None and i % 20 == 0:
            progress(i, total)
    if not frames:
        raise ValueError("プレビューにするフレームがありません")
    height, width = frames[0].shape[:2]
    return Preview(width=width, height=height, fps=fps / (step or 1), step=step or 1,
                   total_frames=count, volume=volume, frames=frames)


def save_gif(preview: Preview, path, scale: int | None = None, max_side: int = 480) -> None:
    """プレビューを GIF に保存する (Windows 版)。平面は max_side に収まるよう整数倍に拡大する。"""
    from PIL import Image

    if scale is None:
        scale = 1 if preview.volume else max(1, max_side // max(preview.width, preview.height))
    images = []
    for frame in preview.frames:
        image = Image.fromarray(frame)
        if scale > 1:
            image = image.resize((preview.width * scale, preview.height * scale), Image.Resampling.NEAREST)
        images.append(image)
    duration = max(20, round(1000 / preview.fps))
    images[0].save(path, save_all=True, append_images=images[1:], duration=duration, loop=0, optimize=False)
