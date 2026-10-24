"""MMD のモデル (.pmx) と踊り (.vmd) から、立体のアニメ (MagicaVoxel .vox) を作る (Phase 12.3、mvcodec/mmd.py)。

    python scripts/mmd_to_vox.py model.pmx motion.vmd out.vox --height 56 --fps 10
    python convert.py --input-vox out.vox --output out.js --fps 10 --view anamorphic

物理 (揺れる髪・スカート) と表情 (モーフ) は使わない。モデルと踊りは、作者の利用規約で許された範囲で使うこと。
Windows 版の GUI では、動画リストに .pmx を追加すると踊り (.vmd) を選ぶ画面が出て、同じことをする。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mvcodec.mmd import mmd_to_vox  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", help="MMD のモデル (.pmx)")
    parser.add_argument("motion", help="踊り (.vmd)")
    parser.add_argument("output", help="書き出す .vox")
    parser.add_argument("--height", type=int, default=56, help="人物の高さ (ブロック)")
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--duration", type=float, default=None, help="長さの上限 (秒)")
    parser.add_argument("--keep-travel", action="store_true",
                        help="踊りの前後左右の移動を残す (既定はその場で踊らせる。範囲が広がり、だまし絵が重くなる)")
    args = parser.parse_args(argv)
    frames, size = mmd_to_vox(args.model, args.motion, args.output, args.height, args.fps, args.duration,
                              progress=lambda done, total: print(f"\r{done} / {total}", end="", flush=True),
                              in_place=not args.keep_travel)
    print(f"\n{args.output}: {frames} frames, {size[0]}x{size[1]}x{size[2]}")


if __name__ == "__main__":
    main()
