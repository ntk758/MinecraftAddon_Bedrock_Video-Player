"""モーションキャプチャ (BVH。CMU のデータベースなど) を、MMD のモデル (.pmx) 用の踊り (.vmd) にする。

    python scripts/bvh_to_vmd.py dance.bvh model.pmx dance.vmd [--max-seconds 60]

骨の向きを合わせて移し替える (mvcodec/bvh.py)。できた VMD は、MMD でも、このツールの MMD の入力 (--motion) でも使える。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mvcodec import bvh  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BVH を MMD のモデル用の VMD にします")
    parser.add_argument("bvh")
    parser.add_argument("pmx")
    parser.add_argument("vmd")
    parser.add_argument("--max-seconds", type=float, default=None, help="この秒数までにする")
    args = parser.parse_args(argv)
    try:
        bvh.bvh_to_vmd(args.bvh, args.pmx, args.vmd, max_seconds=args.max_seconds)
    except (OSError, ValueError) as error:
        print(f"作れませんでした: {error}", file=sys.stderr)
        return 1
    print(f"書きました: {args.vmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
