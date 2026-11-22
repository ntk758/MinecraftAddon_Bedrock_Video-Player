"""テスト用に、PMX (2.0) と VMD を一から書き出す (mvcodec/mmd.py のテスト用。実在のモデルは使わない)。

箱を組み合わせた簡単な人型: センター・下半身・上半身・頭・左右の腕とひじ・左右の足とひざと足首、両足の IK。
頭の前 (-Z) には目印の赤い箱 (顔) がある。「左ひざD」は左ひざの回転を付与で受け、左のすねの頂点はこれに付く。

extras=True (表情と物理のテスト用) のときは、さらに次を足す。
- 頭の後ろに垂れる髪: ボーン 髪1・髪2・髪3 (と先の 髪先)。物理で動く剛体 3 つが、頭の剛体からジョイントでつながる
- 体の当たり: 頭 (球)・上半身 (カプセル) の、ボーンに付いて動く剛体
- 表情: 頂点モーフ「あ」(顔の目印の下の辺を下げる = 口を開ける)・グループモーフ「にやり」(あ × 0.5)・
  ボーンモーフ「うなずき」(頭を前へ 30 度)
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
# extras のときだけ足すボーン (頭の後ろ +Z に垂れる髪)
HAIR_BONES = [
    ("髪1", (0, 15.5, 1.4), "頭", 0),
    ("髪2", (0, 13.5, 1.4), "髪1", 0),
    ("髪3", (0, 11.5, 1.4), "髪2", 0),
    ("髪先", (0, 9.5, 1.4), "髪3", 0),
]
INDEX = {name: i for i, (name, *_rest) in enumerate(BONES)}
INDEX_ALL = {**INDEX, **{name: len(BONES) + i for i, (name, *_rest) in enumerate(HAIR_BONES)}}
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
HAIR_BOXES = [
    ((0, 14.5, 1.4), (0.5, 1, 0.2), "髪1", (0.3, 0.2, 0.1)),
    ((0, 12.5, 1.4), (0.5, 1, 0.2), "髪2", (0.3, 0.2, 0.1)),
    ((0, 10.5, 1.4), (0.5, 1, 0.2), "髪3", (0.3, 0.2, 0.1)),
]
FACE_BOX = 3            # BOXES の顔の目印
MOUTH_OPEN = 0.4        # 「あ」で顔の目印の下の辺を下げる量
# 剛体: (名前, ボーン, グループ, 形 (0 球・1 箱・2 カプセル), 大きさ, 位置, 種類 (0 ボーン・1 物理))
RIGID_BODIES = [
    ("頭", "頭", 0, 0, (1.1, 0, 0), (0, 15.5, 0), 0),
    ("上半身", "上半身", 0, 2, (1.4, 2.5, 0), (0, 12, 0), 0),
    ("髪1", "髪1", 1, 0, (0.3, 0, 0), (0, 14.5, 1.4), 1),
    ("髪2", "髪2", 1, 0, (0.3, 0, 0), (0, 12.5, 1.4), 1),
    ("髪3", "髪3", 1, 0, (0.3, 0, 0), (0, 10.5, 1.4), 1),
]
# ジョイント: (名前, 剛体 A, 剛体 B, 位置, 回転の下限, 上限)
JOINTS = [
    ("頭-髪1", 0, 2, (0, 15.5, 1.4), (-1.5, -0.3, -1.0), (1.5, 0.3, 1.0)),
    ("髪1-髪2", 2, 3, (0, 13.5, 1.4), (-1.0, -0.3, -1.0), (1.0, 0.3, 1.0)),
    ("髪2-髪3", 3, 4, (0, 11.5, 1.4), (-1.0, -0.3, -1.0), (1.0, 0.3, 1.0)),
]


def _text(s):
    data = s.encode("utf-16-le")
    return struct.pack("<i", len(data)) + data


def pmx_bytes(extras=False):
    bones = BONES + ([(name, position, INDEX_ALL[parent], layer) for name, position, parent, layer in HAIR_BONES]
                     if extras else [])
    index = {name: i for i, (name, *_rest) in enumerate(bones)}
    out = bytearray(b"PMX " + struct.pack("<f", 2.0) + bytes([8, 0, 0, 4, 4, 4, 4, 4, 4]))
    for s in ("テスト人形", "test doll", "", ""):
        out += _text(s)
    vertices, faces, materials = [], [], []
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], np.float32)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    for centre, half, bone, colour in BOXES + (HAIR_BOXES if extras else []):
        base = len(vertices)
        for c in corners:
            vertices.append((np.array(centre) + c * np.array(half), index[bone]))
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
    out += struct.pack("<i", len(bones))
    for name, position, parent, layer in bones:
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
    if not extras:
        out += struct.pack("<i", 0)  # モーフ
        out += struct.pack("<i", 0)  # 表示枠
        out += struct.pack("<i", 0)  # 剛体
        out += struct.pack("<i", 0)  # ジョイント
        return bytes(out)
    # モーフ: あ (頂点)・にやり (グループ)・うなずき (ボーン)
    out += struct.pack("<i", 3)
    mouth = [FACE_BOX * 8 + c for c in (0, 1, 4, 5)]  # 顔の目印の下の辺 (y = -1 の角)
    out += _text("あ") + _text("a") + bytes([3, 1]) + struct.pack("<i", len(mouth))
    for v in mouth:
        out += struct.pack("<i", v) + struct.pack("<3f", 0, -MOUTH_OPEN, 0)
    out += _text("にやり") + _text("grin") + bytes([3, 0]) + struct.pack("<i", 1) + struct.pack("<i", 0) + struct.pack("<f", 0.5)
    angle = np.radians(30) / 2
    out += _text("うなずき") + _text("nod") + bytes([4, 2]) + struct.pack("<i", 1)
    out += struct.pack("<i", index["頭"]) + struct.pack("<3f", 0, 0, 0) + struct.pack("<4f", np.sin(angle), 0, 0, np.cos(angle))
    out += struct.pack("<i", 1)  # 表示枠 1 つ (読み飛ばせるか)
    out += _text("表情") + _text("") + bytes([1]) + struct.pack("<i", 1) + bytes([1]) + struct.pack("<i", 0)
    out += struct.pack("<i", len(RIGID_BODIES))
    for name, bone, group, shape, size, position, mode in RIGID_BODIES:
        out += _text(name) + _text("") + struct.pack("<i", index[bone]) + bytes([group]) + struct.pack("<H", 0xFFFF)
        out += bytes([shape]) + struct.pack("<3f", *size) + struct.pack("<3f", *position) + struct.pack("<3f", 0, 0, 0)
        out += struct.pack("<5f", 1.0, 0.5, 0.5, 0.0, 0.5) + bytes([mode])
    out += struct.pack("<i", len(JOINTS))
    for name, a, b, position, lower, upper in JOINTS:
        out += _text(name) + _text("") + bytes([0]) + struct.pack("<i", a) + struct.pack("<i", b)
        out += struct.pack("<3f", *position) + struct.pack("<3f", 0, 0, 0)
        out += struct.pack("<6f", 0, 0, 0, 0, 0, 0) + struct.pack("<3f", *lower) + struct.pack("<3f", *upper)
        out += struct.pack("<6f", 0, 0, 0, 0, 0, 0)
    return bytes(out)


LINEAR = bytes([20, 20, 20, 20, 20, 20, 20, 20, 107, 107, 107, 107, 107, 107, 107, 107] * 4)


def vmd_bytes(keys, morphs=()):
    """keys: [(ボーン名, フレーム, 位置 (3), 回転 (x, y, z, w)), ...]、morphs: [(モーフ名, フレーム, 重み), ...]"""
    out = bytearray(b"Vocaloid Motion Data 0002".ljust(30, b"\0") + "テスト".encode("shift_jis").ljust(20, b"\0"))
    out += struct.pack("<I", len(keys))
    for name, frame, position, rotation in keys:
        out += name.encode("shift_jis").ljust(15, b"\0")
        out += struct.pack("<I", frame) + struct.pack("<3f", *position) + struct.pack("<4f", *rotation) + LINEAR
    out += struct.pack("<I", len(morphs))
    for name, frame, weight in morphs:
        out += name.encode("shift_jis").ljust(15, b"\0") + struct.pack("<I", frame) + struct.pack("<f", weight)
    out += struct.pack("<I", 0) * 3  # カメラ・照明・セルフ影
    return bytes(out)


def write_doll(folder, keys, morphs=(), extras=False):
    pmx = folder / "doll.pmx"
    vmd = folder / "dance.vmd"
    pmx.write_bytes(pmx_bytes(extras or bool(morphs)))
    vmd.write_bytes(vmd_bytes(keys, morphs))
    return pmx, vmd
