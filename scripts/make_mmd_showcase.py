"""CurseForge で見せる MMD の画像モードのショーケース (.mcaddon) を作る (docs/research/mmd_image_mode_design_2026-11.md の 6 章)。

    python scripts/make_mmd_showcase.py --pmx Alicia_solid.pmx --bvh 94_05.bvh -o dist/mmd_showcase.mcaddon

- ダンス: CMU のモーションキャプチャ (BVH) を、モデルの骨に移し替える (mvcodec/bvh.py)。
- 曲: ショーケースの自作の曲 (scripts/make_demo_video.py の music) を、ダンスの長さで作る。
- 表情: BVH には表情が無いので、まばたきと、曲の拍に合わせた口の開け閉めの VMD をここで作る (モーフ名は --blink・--mouth)。
- 人物: 画像モードのガラスケース、細かさ high、物理 standard (--detail・--physics で変えられる)。

素材の規約 (2026-11-22 に確認):
- CMU Graphics Lab Motion Capture Database: 研究でも商用でも自由に使える。"The data used in this project was obtained
  from mocap.cs.cmu.edu." と書く。BVH にした Bruce Hahne も制限を足していない。
- ニコニ立体ちゃん (Alicia Solid): 個人が作った二次創作物 (このアドオンの板・テクスチャ・アニメーション) は配れる。
  元の PMX は配れない (このスクリプトはアドオンに PMX を入れない)。暴力的な表現・政治・宗教などには使わない。
"""
from __future__ import annotations

import argparse
import struct
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import convert_image_mode  # noqa: E402
from mvcodec import bvh, mmd  # noqa: E402

SAMPLE_RATE = 44100
BEAT_SECONDS = 0.5   # 自作の曲は 120 BPM


def write_song(path: Path, seconds: float) -> None:
    from make_demo_video import music
    audio = (music(duration=seconds, rate=SAMPLE_RATE) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(audio.tobytes())


def face_vmd(path: Path, seconds: float, blink: str, mouth: str) -> None:
    """まばたき (4 秒ごと) と、拍に合わせた口の開け閉め (表と裏で大きさを変える) の表情の VMD。"""
    keys = []
    frames = int(seconds * 30)
    for start in range(60, frames, 120):
        keys += [(blink, start, 0.0), (blink, start + 3, 1.0), (blink, start + 6, 0.0)]
    beat = int(BEAT_SECONDS * 30)
    for n, start in enumerate(range(0, frames, beat)):
        open_ = 0.8 if n % 2 == 0 else 0.4
        keys += [(mouth, start, 0.0), (mouth, start + beat // 3, open_), (mouth, start + beat - 2, 0.0)]
    out = bytearray(b"Vocaloid Motion Data 0002".ljust(30, b"\0") + b"showcase".ljust(20, b"\0"))
    out += struct.pack("<I", 0)
    out += struct.pack("<I", len(keys))
    for name, frame, weight in keys:
        out += name.encode("shift_jis").ljust(15, b"\0") + struct.pack("<I", frame) + struct.pack("<f", weight)
    out += struct.pack("<I", 0) * 3
    path.write_bytes(bytes(out))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MMD の画像モードのショーケースを作ります")
    parser.add_argument("--pmx", required=True, help="モデル (.pmx)")
    parser.add_argument("--bvh", required=True, help="ダンス (CMU などの .bvh)。.vmd でもよい")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--seconds", type=float, default=40.0, help="長さ (秒。ダンスが短ければその長さ)")
    parser.add_argument("--detail", default="high", choices=tuple(convert_image_mode.FIGURE_DETAILS))
    parser.add_argument("--physics", default="standard", choices=convert_image_mode.PHYSICS_CHOICES)
    parser.add_argument("--blink", default="まばたき", help="まばたきのモーフの名前")
    parser.add_argument("--mouth", default="あ", help="口を開けるモーフの名前")
    parser.add_argument("--id", default="alicia_dance")
    parser.add_argument("--namespace", default="mmdshow")
    parser.add_argument("--pack-name", default="Block & Image Video Player MMD Showcase")
    args = parser.parse_args(argv)
    model = mmd.read_pmx(args.pmx)
    names = {m["name"] for m in model.morphs}
    for morph in (args.blink, args.mouth):
        if morph not in names:
            print(f"注意: モデルにモーフ {morph!r} がありません (表情はその分だけ動きません)", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="mmd-showcase-") as temp:
        temp = Path(temp)
        motion = Path(args.bvh)
        if motion.suffix.lower() == ".bvh":
            skeleton = bvh.read_bvh(motion)
            seconds = min(args.seconds, (len(skeleton.motion) - 1) * skeleton.frame_time)
            motion = bvh.bvh_to_vmd(motion, args.pmx, temp / "dance.vmd", max_seconds=seconds)
        else:
            seconds = args.seconds
        song = temp / f"{args.id}.wav"
        write_song(song, seconds)
        face = temp / "face.vmd"
        face_vmd(face, seconds, args.blink, args.mouth)
        return convert_image_mode.main([
            args.pmx, "--motion", str(motion), "--face-motion", str(face), "--audio", str(song), "--case",
            "--figure-detail", args.detail, "--physics", args.physics, "--ids", args.id,
            "--namespace", args.namespace, "--pack-name", args.pack_name, "-o", args.output])


if __name__ == "__main__":
    sys.exit(main())
