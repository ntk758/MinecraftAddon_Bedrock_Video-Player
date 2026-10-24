"""テスト用に、PMX (2.0) と VMD を一から書き出す (mvcodec/mmd.py のテスト用。実在のモデルは使わない)。

箱を組み合わせた簡単な人型: センター・下半身・上半身・頭・左右の腕とひじ・左右の足とひざと足首、両足の IK。
頭の前 (-Z) には目印の赤い箱 (顔) がある。「左ひざD」は左ひざの回転を付与で受け、左のすねの頂点はこれに付く。
"""
import struct

import numpy as np

# (名前, 位置, 親, 変形階層)
BONES = [
    ("センター", (0, 8, 0), -1, 0),
    ("下半身", (0, 10, 0), 0, 0),
    ("上半身", (0, 10, 0), 0, 0),
    ("頭", (0, 15, 0), 2, 0),
    ("左腕", (2, 14, 0), 2, 0),
    ("左ひじ", (5, 14, 0), 4, 0),
    ("右腕", (-2, 14, 0), 2, 0),
    ("右ひじ", (-5, 14, 0), 6, 0),
    ("左足", (1, 10, 0), 1, 0),
    ("左ひざ", (1, 5, 0), 8, 0),
    ("左足首", (1, 0.5, 0), 9, 0),
    ("右足", (-1, 10, 0), 1, 0),
    ("右ひざ", (-1, 5, 0), 11, 0),
    ("右足首", (-1, 0.5, 0), 12, 0),
    ("左足ＩＫ", (1, 0.5, 0), -1, 0),
    ("右足ＩＫ", (-1, 0.5, 0), -1, 0),
    ("左ひざD", (1, 5, 0), 8, 1),
]
INDEX = {name: i for i, (name, *_rest) in enumerate(BONES)}
# 膝は X 軸まわりにだけ、前へ (負の角度) 曲がる
KNEE_LIMIT = ((-3.14159, 0.0, 0.0), (-0.0087, 0.0, 0.0))

# (中心, 半分の大きさ, ボーン, 色)
BOXES = [
    ((0, 12, 0), (1.5, 2, 0.8), "上半身", (0.9, 0.9, 0.9)),
    ((0, 9, 0), (1.4, 1, 0.8), "下半身", (0.2, 0.3, 0.7)),
    ((0, 15.5, 0), (1, 1, 1), "頭", (1.0, 0.85, 0.7)),
    ((0, 15.6, -1.05), (0.6, 0.3, 0.1), "頭", (0.9, 0.1, 0.1)),  # 顔の目印 (正面 = -Z)
    ((3.5, 14, 0), (1.5, 0.4, 0.4), "左腕", (0.9, 0.9, 0.9)),
    ((6, 14, 0), (1, 0.35, 0.35), "左ひじ", (1.0, 0.85, 0.7)),
    ((-3.5, 14, 0), (1.5, 0.4, 0.4), "右腕", (0.9, 0.9, 0.9)),
    ((-6, 14, 0), (1, 0.35, 0.35), "右ひじ", (1.0, 0.85, 0.7)),
    ((1, 7.5, 0), (0.5, 2.5, 0.5), "左足", (0.1, 0.1, 0.1)),
    ((1, 2.75, 0), (0.45, 2.25, 0.45), "左ひざD", (0.1, 0.1, 0.1)),
    ((-1, 7.5, 0), (0.5, 2.5, 0.5), "右足", (0.1, 0.1, 0.1)),
    ((-1, 2.75, 0), (0.45, 2.25, 0.45), "右ひざ", (0.1, 0.1, 0.1)),
]


def _text(s):
    data = s.encode("utf-16-le")
    return struct.pack("<i", len(data)) + data


def pmx_bytes():
    out = bytearray(b"PMX " + struct.pack("<f", 2.0) + bytes([8, 0, 0, 4, 4, 4, 4, 4, 4]))
    for s in ("テスト人形", "test doll", "", ""):
        out += _text(s)
    vertices, faces, materials = [], [], []
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], np.float32)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    for centre, half, bone, colour in BOXES:
        base = len(vertices)
        for c in corners:
            vertices.append((np.array(centre) + c * np.array(half), INDEX[bone]))
        tris = []
        for a, b, c, d in quads:
            tris += [base + a, base + b, base + c, base + a, base + c, base + d]
        faces += tris
        materials.append((colour, len(tris)))
    out += struct.pack("<i", len(vertices))
    for position, bone in vertices:
        out += struct.pack("<8f", *position, 0, 1, 0, 0.5, 0.5)
        out += bytes([0]) + struct.pack("<i", bone) + struct.pack("<f", 1.0)
    out += struct.pack("<i", len(faces)) + struct.pack(f"<{len(faces)}i", *faces)
    out += struct.pack("<i", 0)  # テクスチャ
    out += struct.pack("<i", len(materials))
    for n, (colour, count) in enumerate(materials):
        out += _text(f"m{n}") + _text("")
        out += struct.pack("<4f", *colour, 1.0) + struct.pack("<3f", 0, 0, 0) + struct.pack("<f", 5)
        out += struct.pack("<3f", 0.3, 0.3, 0.3) + bytes([0]) + struct.pack("<4f", 0, 0, 0, 1) + struct.pack("<f", 1)
        out += struct.pack("<i", -1) + struct.pack("<i", -1) + bytes([0]) + bytes([1, 0]) + _text("")
        out += struct.pack("<i", count)
    out += struct.pack("<i", len(BONES))
    for name, position, parent, layer in BONES:
        flags = 0x0002 | 0x0008 | 0x0010  # 回転・表示・操作
        if name.endswith("ＩＫ"):
            flags |= 0x0004 | 0x0020  # 移動・IK
        if name in ("センター",):
            flags |= 0x0004
        if name == "左ひざD":
            flags |= 0x0100  # 回転の付与
        out += _text(name) + _text("")
        out += struct.pack("<3f", *position) + struct.pack("<i", parent) + struct.pack("<i", layer)
        out += struct.pack("<H", flags) + struct.pack("<3f", 0, 0, 0)
        if name == "左ひざD":
            out += struct.pack("<i", INDEX["左ひざ"]) + struct.pack("<f", 1.0)
        if name.endswith("ＩＫ"):
            side = name[0]
            out += struct.pack("<i", INDEX[f"{side}足首"]) + struct.pack("<i", 40) + struct.pack("<f", 2.0)
            out += struct.pack("<i", 2)
            lower, upper = KNEE_LIMIT
            out += struct.pack("<i", INDEX[f"{side}ひざ"]) + bytes([1]) + struct.pack("<3f", *lower) + struct.pack("<3f", *upper)
            out += struct.pack("<i", INDEX[f"{side}足"]) + bytes([0])
    out += struct.pack("<i", 0)  # モーフ
    out += struct.pack("<i", 0)  # 表示枠
    out += struct.pack("<i", 0)  # 剛体
    out += struct.pack("<i", 0)  # ジョイント
    return bytes(out)


LINEAR = bytes([20, 20, 20, 20, 20, 20, 20, 20, 107, 107, 107, 107, 107, 107, 107, 107] * 4)


def vmd_bytes(keys):
    """keys: [(ボーン名, フレーム, 位置 (3), 回転 (x, y, z, w)), ...]"""
    out = bytearray(b"Vocaloid Motion Data 0002".ljust(30, b"\0") + "テスト".encode("shift_jis").ljust(20, b"\0"))
    out += struct.pack("<I", len(keys))
    for name, frame, position, rotation in keys:
        out += name.encode("shift_jis").ljust(15, b"\0")
        out += struct.pack("<I", frame) + struct.pack("<3f", *position) + struct.pack("<4f", *rotation) + LINEAR
    out += struct.pack("<I", 0) * 4  # モーフ・カメラ・照明・セルフ影
    return bytes(out)


def write_doll(folder, keys):
    pmx = folder / "doll.pmx"
    vmd = folder / "dance.vmd"
    pmx.write_bytes(pmx_bytes())
    vmd.write_bytes(vmd_bytes(keys))
    return pmx, vmd
