"""ガラスケースの立体 (面の板) の、実機で UV の向きを決めるための試験用のアドオンを作る (DECISIONS.md の A19)。

    python scripts/make_planes_test_pack.py planes_test.mcaddon

入っているもの (/bvpimg:setup <ID> → /bvpimg:start):
- planes_test: 同じ小さな立体を、UV の向きの 16 通り (image_mode/planes.py の FaceMapping) で 4×4 に並べたもの。
  正しい 1 つだけが、どの向きから見ても立方体がつながって見え、正面から見て赤い腕が右・緑の腕が奥・青い腕が上を向く。
  立体の右手前の 2 本の柱 (紫と水色) の高さ (2 ボクセルずつの段が 1〜4) が番号: 番号 = (紫 - 1) * 4 + (水色 - 1)。
- v0〜v15: 同じ立体を 1 つずつ、1 ボクセル = 1 ブロックの大きさで (番号は UV の向きの番号)。`/bvpimg:play v<番号>` で
  同じ場所に差し替えて、同じ位置から見比べる。
- case_test: 既定の向きの立体とケース。壁に BACK・LEFT・RIGHT・FRONT・FLOOR と矢印を描いてある。
  回り込むと、手前の壁が消えて奥の壁だけが見えること、文字が鏡写しでないこと (床は文字の上が奥) を確かめる。

番号と向きの対応はこのスクリプトが表で出す。正しい番号が分かったら convert_image_mode.py の --face-mapping で
試し、planes.FaceMapping の既定値を直す。
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import convert_image_mode  # noqa: E402
from image_mode import glass, planes  # noqa: E402
from mvcodec.resources import use_utf8_for_pipes  # noqa: E402
from pack_builder import write_file_atomic  # noqa: E402

WHITE, RED, GREEN, BLUE, YELLOW, MAGENTA, CYAN = range(1, 8)
COLOURS = np.array([(240, 240, 240), (220, 40, 40), (40, 190, 60), (50, 80, 230), (240, 220, 40), (200, 60, 200),
                    (60, 210, 220)], np.uint8)
MAPPINGS = [planes.FaceMapping(*signs) for signs in itertools.product((1, -1), repeat=4)]
# 1 ボクセルの大きさ (geometry の単位 = 1/16 ブロック) と、並べる間隔 (ブロック)
VOXEL = 2.0
SPACING = 2.5


def test_figure(index: int) -> np.ndarray:
    """向きが分かる小さな立体 (x 右・y 奥・z 上)。白い角から赤 (+x)・緑 (+y)・青 (+z) の腕。赤の先の上に黄色。
    右手前に、番号を表す紫と水色の柱 (高さ 1〜4 ボクセル × 2)。"""
    volume = np.zeros((12, 9, 9), np.uint8)
    volume[1:3, 3:5, 0:2] = WHITE
    volume[3:8, 3:4, 0:1] = RED
    volume[7:8, 3:4, 1:2] = YELLOW
    volume[1:2, 5:9, 0:1] = GREEN
    volume[1:2, 3:4, 2:7] = BLUE
    a, b = index // 4 + 1, index % 4 + 1
    volume[9:10, 0:1, 0:2 * a] = MAGENTA
    volume[11:12, 0:1, 0:2 * b] = CYAN
    return volume


def grid_figure() -> dict:
    """16 通りの向きを 1 つのコマ (ボーン f0) に並べた人物 (glass.figure_from_volumes の戻り値と同じ形)。"""
    instances = []
    for index, mapping in enumerate(MAPPINGS):
        volume = test_figure(index)
        quads = planes.frame_quads(volume, COLOURS)
        instances.append((index, mapping, quads, [planes.oriented_image(q, mapping) for q in quads]))
    images = [img for _i, _m, _q, imgs in instances for img in imgs]
    sheets, [placements], _per_sheet, size = planes.pack_frames([images], max_sheet=2048, gutter=1)
    cubes = []
    cursor = 0
    for index, mapping, quads, _imgs in instances:
        row, col = divmod(index, 4)
        # 並べる位置は geometry の座標 (向きに関係なく、見ている人から見て左右・前後に並ぶとは限らない。番号は柱で読む)
        offset = ((col - 1.5) * SPACING * 16, 0.0, (row - 1.5) * SPACING * 16)
        placing = planes.Placing(scale=VOXEL, centre=(6.0, 4.5), offset=offset)
        for quad in quads:
            cubes.append(planes.quad_cube(quad, placements[cursor], placing, mapping))
            cursor += 1
    span = SPACING * 4
    return {"frames": 1, "per_sheet": 1, "sheet_width": size[0], "sheet_height": size[1],
            "sheets": [Image.fromarray(sheets[0], "RGBA")], "bones": [{"name": "f0", "pivot": [0, 0, 0], "cubes": cubes}],
            "quads": len(cubes), "size_blocks": (span, span, 9 * VOXEL / 16)}


def label(text: str, size=(256, 160), colour=(30, 60, 140)) -> Image.Image:
    """壁の絵: 文字と、上向きの矢印 (左右・上下の向きが分かる)。"""
    image = Image.new("RGB", size, colour)
    draw = ImageDraw.Draw(image)
    draw.text((16, 16), text, fill=(255, 255, 255))
    draw.text((16, size[1] - 28), "L", fill=(255, 220, 0))
    draw.text((size[0] - 28, size[1] - 28), "R", fill=(255, 220, 0))
    draw.polygon([(size[0] // 2, 30), (size[0] // 2 - 24, 80), (size[0] // 2 + 24, 80)], fill=(255, 255, 255))
    draw.rectangle([size[0] // 2 - 8, 80, size[0] // 2 + 8, 130], fill=(255, 255, 255))
    return image


def main(argv=None) -> int:
    use_utf8_for_pipes()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", help="出力の .mcaddon")
    parser.add_argument("--pack-name", default="BVP planes test", help="パック名 (作り直して別のパックとして読み込むとき)")
    args = parser.parse_args(argv)
    output = Path(args.output)
    work = output.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    walls = {}
    for side, colour in (("back", (30, 60, 140)), ("front", (140, 40, 40)), ("left", (40, 120, 60)),
                         ("right", (120, 90, 30)), ("floor", (60, 60, 60))):
        path = work / f"{side}.png"
        label(f"{side.upper()}  (inside view, not mirrored)", colour=colour).save(path)
        walls[side] = str(path)
    case_volume = test_figure(0)
    case_figure = glass.figure_from_volumes([case_volume], [COLOURS], figure_blocks=1.5)
    entries = [
        {"id": "planes_test", "title": "planes test (16 UV variants)", "figure": grid_figure(), "figure_fps": 1,
         "figure_loop": True, "case": None, "wall_video": None, "audio": None},
        {"id": "case_test", "title": "case test", "figure": case_figure, "figure_fps": 1, "figure_loop": True,
         "case": glass.CaseOptions(margin=1.0, images=walls, frame=True), "wall_video": None, "audio": None},
    ]
    # 1 つずつ大きく (1 ボクセル = 1 ブロック) 見るための v0〜v15。/bvpimg:play v<番号> で同じ場所に差し替えられる
    for index, mapping in enumerate(MAPPINGS):
        figure = glass.figure_from_volumes([test_figure(index)], [COLOURS], figure_blocks=8, mapping=mapping)
        entries.append({"id": f"v{index}", "title": f"UV variant {index}", "figure": figure, "figure_fps": 1,
                        "figure_loop": True, "case": None, "wall_video": None, "audio": None})
    files, _report = convert_image_mode.build_glass_files(entries, namespace="bvpimg", pack_name=args.pack_name)
    write_file_atomic(output, convert_image_mode.zip_bytes(files))
    print(f"出力: {output}")
    print("番号 = (紫の柱の段 - 1) * 4 + (水色の柱の段 - 1)。--face-mapping の値:")
    for index, mapping in enumerate(MAPPINGS):
        print(f"  {index:2d}: {mapping.north_u},{mapping.east_u},{mapping.up_u},{mapping.up_v}"
              + ("  (今の既定)" if mapping == planes.DEFAULT_MAPPING else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
