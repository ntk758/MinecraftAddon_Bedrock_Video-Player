"""だまし絵 (--view anamorphic) の見本用に、踊る人物の MagicaVoxel アニメ (.vox) を作る (このプロジェクトのための自作)。

オレンジの髪・猫耳の人物が、腕を振り、足を踏み、体を左右に回しながら踊る (横から見ても立体だと分かるように)。

    python scripts/make_demo_dancer.py out.vox [--frames 40] [--height 56]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mvcodec.volume import write_vox  # noqa: E402

# パレットの番号 (1 から。0 は空)
SKIN, HAIR, SHIRT, SKIRT, LEG, SHOE, EYE, RIBBON = range(1, 9)
COLOURS = {
    SKIN: (250, 214, 180), HAIR: (240, 130, 40), SHIRT: (250, 250, 250), SKIRT: (40, 70, 160),
    LEG: (30, 30, 40), SHOE: (180, 40, 40), EYE: (40, 120, 200), RIBBON: (220, 40, 60),
}


def capsule(p, a, b, radius):
    """点 p (…, 3) が、a と b を結ぶ半径 radius のカプセルの中か。"""
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(1e-6, ab @ ab), 0, 1)
    closest = a + t[..., None] * ab
    return np.sum((p - closest) ** 2, axis=-1) <= radius * radius


def frame(t, n, size, scale):
    """t 番目のフレームの立体 (x, y, z) の色の番号。z が上、y が奥行き。"""
    w, d, h = size
    zz, yy, xx = np.mgrid[0:h, 0:d, 0:w].astype(np.float32) + 0.5
    phase = 2 * np.pi * t / n
    # 体を縦の軸のまわりに左右へ回す (最大 ±35 度)
    turn = np.radians(35) * np.sin(phase)
    cx, cy = w / 2, d / 2
    lx = (xx - cx) * np.cos(turn) + (yy - cy) * np.sin(turn)
    ly = -(xx - cx) * np.sin(turn) + (yy - cy) * np.cos(turn)
    bounce = 1.2 * scale * abs(np.sin(2 * phase))
    p = np.stack([lx, ly, zz - bounce], -1) / scale
    v = lambda *c: np.array(c, np.float32)  # noqa: E731
    out = np.zeros((h, d, w), np.uint8)

    def paint(mask, colour):
        out[mask & (out == 0)] = colour

    # 顔 (目と口は前 = y の負の側)
    head = v(0, 0, 47)
    eyes = (np.abs(np.abs(p[..., 0]) - 1.6) < 0.8) & (np.abs(p[..., 2] - 47.5) < 0.9) & (p[..., 1] < -3.2)
    paint(eyes & (np.sum((p - head) ** 2, -1) <= 4.4 ** 2), EYE)
    # 髪: 頭の後ろと上、猫耳、後ろで結んだ 2 本の髪
    hair = (np.sum((p - head - v(0, 0.6, 0.6)) ** 2, -1) <= 4.6 ** 2) & ~((p[..., 1] < -1.5) & (p[..., 2] < 49))
    paint(hair, HAIR)
    for side in (-1, 1):
        ear = capsule(p, head + v(2.6 * side, 0, 3.4), head + v(3.4 * side, 0, 6.2), 1.1)
        paint(ear, HAIR)
        swing = 2.5 * np.sin(phase + side)
        tail = capsule(p, head + v(3.5 * side, 2.5, 1), v(5.5 * side + swing, 4.0, 33), 1.3)
        paint(tail, HAIR)
        paint(capsule(p, head + v(3.4 * side, 2.2, 1.6), head + v(4.4 * side, 2.2, 1.6), 1.2), RIBBON)
    paint(np.sum((p - head) ** 2, -1) <= 4.2 ** 2, SKIN)
    paint(capsule(p, v(0, 0, 41), v(0, 0, 43), 1.4), SKIN)  # 首
    # 胴とスカート
    paint(capsule(p, v(0, 0, 31), v(0, 0, 39.5), 3.4) & (np.abs(p[..., 1]) < 2.6), SHIRT)
    paint(capsule(p, v(0, 0, 39), v(0, 0, 39), 1.2) & (p[..., 1] < -2), RIBBON)
    r = np.hypot(p[..., 0], p[..., 1])
    skirt = (p[..., 2] > 24) & (p[..., 2] < 31.5) & (r < 3.2 + (31.5 - p[..., 2]) * 0.55)
    paint(skirt, SKIRT)
    # 腕: 肩から肘、肘から手。左右で逆に振り、上げ下げする
    for side in (-1, 1):
        shoulder = v(3.8 * side, 0, 38.5)
        lift = 0.6 + 0.9 * (0.5 + 0.5 * np.sin(phase * 2 + (0 if side > 0 else np.pi)))
        elbow = shoulder + v(4.2 * side * np.cos(lift - 0.6), -2.0 * np.sin(phase) * side, -5 + 6 * np.sin(lift - 0.6))
        hand = elbow + v(2.5 * side, -2.5, 3.0 * np.sin(lift))
        paint(capsule(p, shoulder, elbow, 1.2), SHIRT)
        paint(capsule(p, elbow, hand, 1.0), SKIN)
        # 足: 交互に踏む
        step = max(0.0, np.sin(phase * 2 + (0 if side > 0 else np.pi)))
        hip = v(1.6 * side, 0, 25)
        knee = hip + v(0.3 * side, -2.2 * step, -6 + 2 * step)
        foot = v(2.0 * side, -0.5 * step, 1.5 + 3.5 * step)
        paint(capsule(p, hip, knee, 1.3), LEG)
        paint(capsule(p, knee, foot, 1.2), LEG)
        paint(capsule(p, foot, foot + v(0, -1.8, 0), 1.3), SHOE)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output")
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--height", type=int, default=56, help="立体の高さ (ブロック数。幅と奥行きは高さに合わせる)")
    args = parser.parse_args(argv)
    scale = args.height / 56
    size = (max(8, round(32 * scale)), max(8, round(20 * scale)), args.height)
    palette = np.zeros((256, 4), np.uint8)
    for index, rgb in COLOURS.items():
        palette[index - 1] = (*rgb, 255)
    models = []
    for t in range(args.frames):
        volume = frame(t, args.frames, size, scale)
        z, y, x = np.nonzero(volume)
        voxels = np.stack([x, y, z, volume[z, y, x]], 1).astype(np.uint8)
        models.append((size, voxels))
    Path(args.output).write_bytes(write_vox(models, palette))
    print(f"{args.output}: {args.frames} frames, {size[0]}x{size[1]}x{size[2]}")


if __name__ == "__main__":
    main()
