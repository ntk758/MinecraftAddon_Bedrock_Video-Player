"""骨で動かす人物 (docs/research/glass_case_practical.md の 4 章) のために、ボーンのアニメーションの回転の
向き・順番、pivot、時計を実機で決める試験用のアドオンを作る。

    python scripts/make_rig_test_pack.py rig_test.mcaddon

入っているもの (/bvpimg:setup r_rest → /bvpimg:start → /bvpimg:play <ID> で同じ場所に差し替える):
- どれも、左に回していない見本、右にアニメーションで回した同じ形を置く。形は白い角 (2×2×2) から 3 本の腕:
  赤 (長さ 7、ボクセルの +x)・緑 (長さ 5、+y = 奥)・青 (長さ 4、+z = 上)。赤の先の上に黄色の印。
  白い角の真下に紫の板 (動かない)。pivot は白い角の中心なので、pivot が正しければ回しても白い角は紫の板の上に残る。
- r_rest: 回さない (左右が同じに見えること)。
- r_x・r_y・r_z: geometry の x・y・z の 1 つだけを 90 度 (`rotation` の値)。腕の向きから、どの軸の周りにどちら向きに回るかを読む。
- r_xy・r_yz・r_xz: 2 つの軸を 90 度ずつ。回す順番を読む。
- r_chain: 子のボーン (赤の先から奥へ伸びる水色の腕) を持つ形。親を z に 90 度、子を x に 90 度。
  子の回転が親の回した後の軸で効くか (ローカル)、元の軸で効くかを、水色の腕の向きで読む。
- r_clock: y の周りに 1 秒 90 度で回り続ける (キーフレームを 1 秒ごと、`anim_time_update` に pre_animation の
  時計 v.t を渡す)。止まらずに同じ速さで回り、180 度の所で戻らないこと。
- r_px・r_py・r_pz: 回さずに、アニメーションの `position` の 1 つの値だけを 8 (半ブロック) にする。形が見本から
  どちら (正面から見て左右・上下・手前と奥) へ半ブロック動いたかを読む。image_mode/rig_anim.py の POSITION_SIGNS
  (今は見込みで (-1, 1, 1)。r_px は左、r_py は上、r_pz は奥へ動くはず) を決める。

回転は全部 90 度の倍数にしてある (腕は必ず軸の向きを向くので、読み違えにくい)。

結果 (Windows 11・Minecraft v26.65、2026-11-18。DECISIONS.md の A20): ボクセルの軸で R = Ry(+c) · Rz(−b) · Rx(+a)
((a, b, c) は rotation の値)。子の回転はローカル、pivot は全体の座標、anim_time_update の時計でなめらかに回った。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import convert_image_mode  # noqa: E402
from image_mode import planes  # noqa: E402
from mvcodec.resources import use_utf8_for_pipes  # noqa: E402
from pack_builder import write_file_atomic  # noqa: E402

NAMESPACE = "bvpimg"
WHITE, RED, GREEN, BLUE, YELLOW, MAGENTA, CYAN = range(1, 8)
COLOURS = np.array([(240, 240, 240), (220, 40, 40), (40, 190, 60), (50, 80, 230), (240, 220, 40), (200, 60, 200),
                    (60, 210, 220)], np.uint8)
VOXEL = 8.0      # 1 ボクセル = geometry の 8 単位 (半ブロック)
LIFT = 8         # 白い角の下の高さ (ボクセル)。腕を下へ回しても地面に埋まらないように
REF_X = -100.0   # 見本と、回す形の geometry の x
TEST_X = 60.0
# 回す形の pivot (白い角の中心) は、形の中のボクセルの (1, 1, 1)
PIVOT = (1, 1, 1)
CHILD_PIVOT = (8, 1, 1)  # 赤の先の中心

POSES = {
    "r_rest": {"test": [0, 0, 0]},
    "r_x": {"test": [90, 0, 0]},
    "r_y": {"test": [0, 90, 0]},
    "r_z": {"test": [0, 0, 90]},
    "r_xy": {"test": [90, 90, 0]},
    "r_yz": {"test": [0, 90, 90]},
    "r_xz": {"test": [90, 0, 90]},
    "r_chain": {"test": [0, 0, 90], "child": [90, 0, 0]},
}
CLOCK_ID = "r_clock"
# アニメーションの position の値 (geometry の単位)。動いた向きを読む
POSITIONS = {"r_px": [8, 0, 0], "r_py": [0, 8, 0], "r_pz": [0, 0, 8]}


def arm_shape(with_child: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
    """回す形 (x 右・y 奥・z 上) と、子の腕 (同じ座標)。"""
    body = np.zeros((9, 7, 6), np.uint8)
    body[0:2, 0:2, 0:2] = WHITE
    body[2:9, 0:2, 0:2] = RED
    body[7:9, 0:2, 2:3] = YELLOW
    body[0:2, 2:7, 0:2] = GREEN
    body[0:2, 0:2, 2:6] = BLUE
    if not with_child:
        return body, None
    child = np.zeros((9, 7, 6), np.uint8)
    child[7:9, 2:6, 0:2] = CYAN
    return body, child


def plate() -> np.ndarray:
    volume = np.zeros((2, 2, 1), np.uint8)
    volume[:, :, :] = MAGENTA
    return volume


def model_point(voxel: tuple[float, float, float], x: float) -> list[float]:
    """形の中のボクセルの点 → geometry の座標 (A19 の向き: geometry の x = ボクセルの x、y = z、z = y)。"""
    vx, vy, vz = voxel
    mapping = planes.DEFAULT_MAPPING
    sign = mapping.model_axis(0)[1]
    return [sign * (vx - PIVOT[0]) * VOXEL + x, (vz + LIFT) * VOXEL, (vy - PIVOT[1]) * VOXEL]


def build_figure(with_child: bool) -> dict:
    """見本 (動かない f0)・板 (f0)・回す形 (ボーン test)・子 (child) の geometry と、シート。"""
    body, child = arm_shape(with_child)
    mapping = planes.DEFAULT_MAPPING
    parts = [  # (ボーン, 立体, geometry の x, 持ち上げる高さ)
        ("f0", body, REF_X, LIFT),
        ("f0", plate(), REF_X, LIFT - 1),
        ("f0", plate(), TEST_X, LIFT - 1),
        ("test", body, TEST_X, LIFT),
    ]
    if with_child:
        parts.insert(1, ("f0", child, REF_X, LIFT))
        parts.append(("child", child, TEST_X, LIFT))
    quads_of = [planes.frame_quads(volume, COLOURS) for _bone, volume, _x, _lift in parts]
    images = [planes.oriented_image(q, mapping) for quads in quads_of for q in quads]
    sheets, [placements], _per_sheet, size = planes.pack_frames([images], max_sheet=2048, gutter=1)
    cubes = {"f0": [], "test": [], "child": []}
    cursor = 0
    for (bone, _volume, x, lift), quads in zip(parts, quads_of):
        placing = planes.Placing(scale=VOXEL, centre=(PIVOT[0], PIVOT[1]), offset=(x, lift * VOXEL, 0.0))
        for quad in quads:
            cubes[bone].append(planes.quad_cube(quad, placements[cursor], placing, mapping))
            cursor += 1
    bones = [{"name": "f0", "pivot": [0, 0, 0], "cubes": cubes["f0"]},
             {"name": "test", "parent": "f0", "pivot": model_point(PIVOT, TEST_X), "cubes": cubes["test"]}]
    if with_child:
        bones.append({"name": "child", "parent": "test", "pivot": model_point(CHILD_PIVOT, TEST_X),
                      "cubes": cubes["child"]})
    return {"frames": 1, "per_sheet": 1, "sheet_width": size[0], "sheet_height": size[1],
            "sheets": [Image.fromarray(sheets[0], "RGBA")], "bones": bones,
            "quads": sum(len(c) for c in cubes.values()), "size_blocks": (12.0, 12.0, 12.0)}


def animation(identifier: str, video_id: str) -> dict:
    if video_id == CLOCK_ID:
        keys = {f"{t}.0": [0, 90 * t, 0] for t in range(5)}
        body = {"loop": True, "anim_time_update": "math.mod(v.t, 4)", "bones": {"test": {"rotation": keys}}}
    elif video_id in POSITIONS:
        body = {"loop": True, "bones": {"test": {"position": POSITIONS[video_id]}}}
    else:
        body = {"loop": True, "bones": {bone: {"rotation": value} for bone, value in POSES[video_id].items()}}
    return {"format_version": "1.8.0", "animations": {identifier: body}}


def add_animations(files: dict, video_ids: list[str]) -> None:
    """build_glass_files の出力に、ボーンのアニメーションを足す (client_entity の animations と animate)。
    人物の part_visibility は「全部のボーン」にする (コマの切り替えは使わない)。"""
    state = f"q.property('{NAMESPACE}:state') != 0"
    for video_id in video_ids:
        short = f"screen_{video_id}_a"
        identifier = f"animation.{NAMESPACE}.{short}.pose"
        files[f"RP/animations/{short}.animation.json"] = json.dumps(animation(identifier, video_id), indent=2) + "\n"
        entity_path = f"RP/entity/{short}.entity.json"
        entity = json.loads(files[entity_path])
        description = entity["minecraft:client_entity"]["description"]
        description["animations"] = {"pose": identifier}
        description["scripts"]["animate"] = ["pose"]
        files[entity_path] = json.dumps(entity, indent=2, ensure_ascii=False) + "\n"
        controller_path = f"RP/render_controllers/{short}.render_controllers.json"
        controllers = json.loads(files[controller_path])
        figure = controllers["render_controllers"][f"controller.render.{NAMESPACE}.{short}"]
        figure["part_visibility"] = [{"*": state}]
        files[controller_path] = json.dumps(controllers, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    use_utf8_for_pipes()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", help="出力の .mcaddon")
    parser.add_argument("--pack-name", default="BVP rig test", help="パック名")
    args = parser.parse_args(argv)
    ids = list(POSES) + [CLOCK_ID] + list(POSITIONS)
    entries = []
    for video_id in ids:
        figure = build_figure(with_child=video_id == "r_chain")
        entries.append({"id": video_id, "title": f"rig test {video_id}", "figure": figure, "figure_fps": 1,
                        "figure_loop": True, "case": None, "wall_video": None, "audio": None})
    files, _report = convert_image_mode.build_glass_files(entries, namespace=NAMESPACE, pack_name=args.pack_name)
    add_animations(files, ids)
    output = Path(args.output)
    write_file_atomic(output, convert_image_mode.zip_bytes(files))
    print(f"出力: {output}")
    for video_id in ids:
        what = POSES.get(video_id) or (f"position {POSITIONS[video_id]}" if video_id in POSITIONS
                                       else "y の周りに 1 秒 90 度 (v.t)")
        print(f"  {video_id}: {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
