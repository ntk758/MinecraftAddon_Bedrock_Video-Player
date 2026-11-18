"""骨で動かす人物 (docs/research/glass_case_practical.md の 7 章の 4・5) を実機で見比べる試験用のアドオンを作る。

    python scripts/make_rig_figure_test_pack.py model.pmx dance.vmd rig_figure_test.mcaddon

同じ MMD の人物を、作り方を変えて 5 体入れる (どれもケースなし、踊りを繰り返す)。並べて置き、同じ姿勢で見比べる。
    /bvpimg:setup j0 → /bvpimg:start → /bvpimg:play j0 (ほかの ID も、少し離れた所で同じように)
- j0・j1・j2: 関節の重ね代 0・1・2 ボクセル (陰を焼き込む、bright)。ひざ・ひじ・肩を大きく曲げた所で、関節に細い
  すき間 (向こうが透ける線) が見えるか、重ねた所がちらつくかを見る (4.3)。
- sn: 陰を焼き込まない (bright)。sw: 陰を焼き込まない (world。ゲームの光と陰を受ける)。j1 (焼き込む) と、昼と夜
  (`/time set night`) で見比べる。部品が回ったとき、焼き込んだ「上の面の明るさ」が横を向いて変に見えないか (4.6)。

MMD のモデル・踊りには作者の利用規約があります。試験用のアドオンも、規約で許された範囲で使ってください。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import convert_image_mode  # noqa: E402
from image_mode import glass_rp, rig_anim  # noqa: E402
from mvcodec.resources import use_utf8_for_pipes  # noqa: E402
from pack_builder import write_file_atomic  # noqa: E402

NAMESPACE = "bvpimg"
# (ID, 重ね代, 陰を焼き込むか, 明るさ)
VARIANTS = [
    ("j0", 0, True, "bright"),
    ("j1", 1, True, "bright"),
    ("j2", 2, True, "bright"),
    ("sn", 1, False, "bright"),
    ("sw", 1, False, "world"),
]


def build(pmx, vmd, *, seconds: float, voxels: int, figure_blocks: float, pack_name: str, log=print):
    dance = rig_anim.PosedDance(pmx, vmd, seconds=seconds, log=log)
    entries = []
    for video_id, overlap, shade, lighting in VARIANTS:
        log(f"[{video_id}] 重ね代 {overlap}・陰を{'焼き込む' if shade else '焼き込まない'}・{lighting}")
        figure = rig_anim.figure_from_dance(dance, identifier=glass_rp.animation_identifier(video_id, NAMESPACE),
                                            figure_blocks=figure_blocks, voxels=voxels, overlap=overlap, shade=shade,
                                            loop=True, log=log)
        entries.append({"id": video_id, "title": f"rig figure {video_id}", "figure": figure,
                        "figure_fps": convert_image_mode.RIG_CLOCK_FPS, "figure_loop": True, "case": None,
                        "wall_video": None, "audio": None, "lighting": lighting})
    return convert_image_mode.build_glass_files(entries, namespace=NAMESPACE, pack_name=pack_name, log=log)


def main(argv=None) -> int:
    use_utf8_for_pipes()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pmx", help="MMD のモデル (.pmx)")
    parser.add_argument("vmd", help="踊り (.vmd)")
    parser.add_argument("output", help="出力の .mcaddon")
    parser.add_argument("--seconds", type=float, default=30.0, help="使う踊りの長さ (秒、既定 30。最初から)")
    parser.add_argument("--voxels", type=int, default=128, help="人物の細かさ (初期姿勢の高さのボクセル数、既定 128)")
    parser.add_argument("--figure-blocks", type=float, default=3.0, help="人物の高さ (ブロック、既定 3)")
    parser.add_argument("--pack-name", default="BVP rig figure test", help="パック名")
    args = parser.parse_args(argv)
    print(convert_image_mode.MMD_NOTICE)
    try:
        files, _report = build(args.pmx, args.vmd, seconds=args.seconds, voxels=args.voxels,
                               figure_blocks=args.figure_blocks, pack_name=args.pack_name)
    except (convert_image_mode.ConvertError, OSError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    output = Path(args.output)
    write_file_atomic(output, convert_image_mode.zip_bytes(files))
    print(f"出力: {output}")
    for video_id, overlap, shade, lighting in VARIANTS:
        print(f"  {video_id}: 重ね代 {overlap}、陰 {'焼き込む' if shade else 'なし'}、{lighting}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
