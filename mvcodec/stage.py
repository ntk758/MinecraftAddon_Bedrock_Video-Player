"""だまし絵 (--view anamorphic) の舞台の大きさ。codec.js の stageGeometry と同じ規則 (tests/test_anamorphic.py で比べる)。

後ろの壁に背景の映像を流すとき、その映像を後ろの壁と同じ大きさ (幅 stage_width × 高さ wall_height) に変換するのに使う。
"""
import math


def stage_geometry(width, height, depth):
    """人物の立体 (幅 × 高さ × 奥行き) から、舞台の大きさを返す (キーは codec.js と同じ名前)。"""
    side = math.ceil(max(width / 2, height * 0.75))
    gap = max(4, math.ceil(depth / 2))
    front = max(4, depth)
    stage_width = width + 2 * side
    wall_height = math.ceil(height * 1.5)
    floor_rows = gap + depth + front
    return {"side": side, "gap": gap, "front": front, "stageWidth": stage_width, "wallHeight": wall_height,
            "floorRows": floor_rows}
