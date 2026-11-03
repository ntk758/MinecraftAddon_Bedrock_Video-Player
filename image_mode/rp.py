"""画像モードのリソースパック JSON (geometry・render controller・client entity・material) を作る。

取り決めは image_mode/SPEC.md。標準ライブラリだけで動く純粋な関数なので、ブラウザの中の Python (Pyodide) でも使える。
テクスチャ・音声・lang・マニフェストは扱わない (呼び出し側が置く)。

方式
- a: アトラス + textures 配列 + 1 マス 1 ボーン + part_visibility。公開されている機能だけを使い、マテリアルを変えない
- c: USE_UV_ANIM のマテリアル + uv_anim。板は 1 枚で済み、軽い。ただし material_json の注意を読むこと

板の向き: どちらの方式も板は geometry の north の面だけに UV を貼る (south は貼らない)。
north の面はエンティティの正面 (向いている方向) から見える。ヨー 0 のエンティティなら南側から北を向いて見たとき。
裏側 (背面) からは見えない。画像が左右反転しないよう、正面から見て左上がマスの左上になる。
"""
from __future__ import annotations

import json
import math

# SPEC.md の「動画のメタデータ」の既定値
DEFAULT_FPS = 30
DEFAULT_GUTTER = 1
DEFAULT_SCREEN_BLOCKS_WIDTH = 16

# 方式 c が使う自作マテリアルの名前。client_entity の materials から参照する
UV_ANIM_MATERIAL = "bvp_uv_anim"
UV_ANIM_MATERIAL_KEY = "bvp_uv_anim:entity_alphatest"

METHODS = ("a", "c")


def _num(value):
    """整数で表せる数は int にする (JSON と Molang に 30.0 のような余計な小数を出さない)。"""
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def _fps(video: dict):
    return _num(video.get("fps") or DEFAULT_FPS)


def _gutter(video: dict) -> int:
    gutter = video.get("gutter")
    return DEFAULT_GUTTER if gutter is None else int(gutter)


def _screen_blocks(video: dict) -> tuple:
    """スクリーンの [幅, 高さ] (ブロック数)。無ければ幅 16、高さは 1 コマの縦横比から。"""
    blocks = video.get("screen_blocks")
    if blocks:
        return _num(blocks[0]), _num(blocks[1])
    width = DEFAULT_SCREEN_BLOCKS_WIDTH
    return width, _num(width * video["height"] / video["width"])


def _check_method(method: str) -> None:
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}: {method!r}")


def _short_name(video: dict, method: str) -> str:
    return f"screen_{video['id']}_{method}"


def entity_name(video: dict, method: str, namespace: str) -> str:
    return f"{namespace}:{_short_name(video, method)}"


def cell_origin(video: dict, cell: int) -> tuple:
    """シート上のマス cell の左上 (画素)。SPEC.md: x = (i % cols) * (width + gutter)。"""
    step_x = video["width"] + _gutter(video)
    step_y = video["height"] + _gutter(video)
    return (cell % video["cols"]) * step_x, (cell // video["cols"]) * step_y


def _plane_cube(video: dict, cell: int) -> dict:
    """スクリーン 1 枚分の板 (厚み 0 の直方体)。north の面だけにマス cell の UV を貼る。"""
    blocks_w, blocks_h = _screen_blocks(video)
    plane_w = _num(blocks_w * 16)  # geometry の単位は 1/16 ブロック
    plane_h = _num(blocks_h * 16)
    u, v = cell_origin(video, cell)
    return {
        # 幅方向は x = 0 を中心、下端は y = 0、板は z = 0 に置く
        "origin": [_num(-plane_w / 2), 0, 0],
        "size": [plane_w, plane_h, 0],
        "uv": {
            "north": {"uv": [u, v], "uv_size": [video["width"], video["height"]]},
        },
    }


def geometry_json(video: dict, method: str, namespace: str) -> dict:
    """geometry.<ns>.screen_<id>_<m>。方式 a はマスの数だけボーン、方式 c はボーン 1 本。"""
    _check_method(method)
    blocks_w, blocks_h = _screen_blocks(video)
    # カリング (画面外と判定して描かない) されないよう、スクリーン全体を覆う大きさにする。単位はブロック
    bounds = math.ceil(math.hypot(blocks_w, blocks_h)) + 2
    cells = video["cells_per_sheet"] if method == "a" else 1
    bones = [
        {"name": f"c{i}", "pivot": [0, 0, 0], "cubes": [_plane_cube(video, i)]}
        for i in range(cells)
    ]
    return {
        "format_version": "1.12.0",
        "minecraft:geometry": [{
            "description": {
                "identifier": f"geometry.{namespace}.{_short_name(video, method)}",
                "texture_width": video["sheet_width"],
                "texture_height": video["sheet_height"],
                "visible_bounds_width": bounds,
                "visible_bounds_height": bounds,
                "visible_bounds_offset": [0, _num(blocks_h / 2), 0],
            },
            "bones": bones,
        }],
    }


def render_controller_json(video: dict, method: str, namespace: str) -> dict:
    """controller.render.<ns>.screen_<id>_<m>。シートの選択と、コマの表示 (a: ボーン、c: uv_anim)。"""
    _check_method(method)
    state = f"q.property('{namespace}:state')"
    sheets = [f"Texture.sheet_{n}" for n in range(video["sheets"])]
    controller = {
        "arrays": {"textures": {"Array.sheets": sheets}},
        "geometry": "Geometry.default",
        "materials": [{"*": "Material.default"}],
        "textures": ["Array.sheets[v.sheet]"],
    }
    if method == "a":
        # 1 要素 1 キー。先に全部隠してから、今のマスのボーンだけ出す (後ろの要素が勝つ)。
        # 状態 0 (停止) のときは条件が偽になり全部隠れる
        visibility = [{"*": False}]
        for i in range(video["cells_per_sheet"]):
            visibility.append({f"c{i}": f"v.cell == {i} && {state} != 0"})
        controller["part_visibility"] = visibility
    else:
        # 方式 c: 板 1 枚をマスの分だけ UV をずらして見せる。
        # 注意: uv_anim は USE_UV_ANIM のカスタムマテリアルが前提 (material_json を参照)。
        # Microsoft Learn は自作マテリアルが将来のリリースで非推奨・削除されうると書いているので、
        # 将来の Minecraft の更新でこの方式が壊れる可能性がある (方式 a は公開機能のみで、影響を受けない)
        cols = video["cols"]
        step_x = video["width"] + _gutter(video)
        step_y = video["height"] + _gutter(video)
        controller["uv_anim"] = {
            "offset": [
                f"math.mod(v.cell, {cols}) * {step_x} / {video['sheet_width']}",
                f"math.floor(v.cell / {cols}) * {step_y} / {video['sheet_height']}",
            ],
            "scale": [1, 1],
        }
        controller["part_visibility"] = [{"*": f"{state} != 0"}]
    return {
        "format_version": "1.10.0",
        "render_controllers": {
            f"controller.render.{namespace}.{_short_name(video, method)}": controller,
        },
    }


def pre_animation(video: dict, namespace: str) -> list:
    """クライアントのコマの計算 (SPEC.md の scripts.pre_animation に数を入れたもの)。

    seq が変わった時刻を覚え、そこから自分の時計で進める。時計は vanilla の render controller (breeze・wind_charge など) と
    同じ (math.floor(q.life_time * 20) + q.frame_alpha) / 20。q.life_time が tick (1/20 秒) ごとにしか進まなくても、
    tick の間の割合 (q.frame_alpha) を足すので 30 fps のコマが描画ごとに変わる。
    q.life_time が 0 に戻ったとき (チャンクの読み直し) は変数も消えるので、次の回で今の frame から進め直す。
    配列の添字の折り返しには頼らず、math.min / math.floor で範囲を明示する。
    """
    ns = namespace
    last = video["frame_count"] - 1
    per_sheet = video["cells_per_sheet"]
    return [
        f"v.seq_now = q.property('{ns}:seq');",
        "v.changed = (v.seq_seen ?? -1) != v.seq_now;",
        "v.t = (math.floor(q.life_time * 20) + q.frame_alpha) / 20;",
        "v.base_time = v.changed ? v.t : v.base_time;",
        f"v.base_frame = v.changed ? q.property('{ns}:frame') : v.base_frame;",
        "v.seq_seen = v.seq_now;",
        f"v.frame = q.property('{ns}:state') == 1 ? math.min(v.base_frame + math.floor((v.t - v.base_time) * {_fps(video)}), {last}) : v.base_frame;",
        f"v.sheet = math.floor(v.frame / {per_sheet});",
        f"v.cell = v.frame - v.sheet * {per_sheet};",
    ]


def client_entity_json(video: dict, method: str, namespace: str) -> dict:
    """<ns>:screen_<id>_<m> のクライアント側の定義。スポーンエッグは作らない。"""
    _check_method(method)
    short = _short_name(video, method)
    # 方式 c だけ自作マテリアル (USE_UV_ANIM) を使う。方式 a は標準の entity_alphatest のまま
    material = UV_ANIM_MATERIAL if method == "c" else "entity_alphatest"
    return {
        "format_version": "1.10.0",
        "minecraft:client_entity": {
            "description": {
                "identifier": entity_name(video, method, namespace),
                "materials": {"default": material},
                "textures": {f"sheet_{n}": path for n, path in enumerate(video["texture_paths"])},
                "geometry": {"default": f"geometry.{namespace}.{short}"},
                "render_controllers": [f"controller.render.{namespace}.{short}"],
                "scripts": {"pre_animation": pre_animation(video, namespace)},
            },
        },
    }


def material_json() -> dict:
    """RP/materials/entity.material。方式 c 用のマテリアル (USE_UV_ANIM を足した entity_alphatest)。

    注意: これは Microsoft Learn「Improving performance and resource usage」
    (https://learn.microsoft.com/en-us/minecraft/creator/documents/practices/improvingperformanceandresourceusage) の手順にある
    自作マテリアルで、Learn の「Materials」のページ (https://learn.microsoft.com/en-us/minecraft/creator/documents/materials) が
    「自作マテリアルは将来のリリースで非推奨になるか削除されるかもしれない」と書いている。
    つまり方式 c は将来の Minecraft の更新で壊れる可能性がある。
    方式 a はマテリアルを変えず、公開されている機能だけを使うので、その心配がない。
    """
    return {
        "materials": {
            "version": "1.0.0",
            UV_ANIM_MATERIAL_KEY: {"+defines": ["USE_UV_ANIM"]},
        },
    }


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def rp_files(videos: list, namespace: str) -> dict:
    """リソースパック内の相対パス (RP/ の下) -> JSON の文字列。"""
    files = {}
    uses_c = False
    for video in videos:
        for method in video.get("methods") or ["a"]:
            _check_method(method)
            short = _short_name(video, method)
            files[f"entity/{short}.entity.json"] = _dump(client_entity_json(video, method, namespace))
            files[f"models/entity/{short}.geo.json"] = _dump(geometry_json(video, method, namespace))
            files[f"render_controllers/{short}.render_controllers.json"] = _dump(
                render_controller_json(video, method, namespace))
            uses_c = uses_c or method == "c"
    if uses_c:
        # 方式 c を使う動画があるときだけ自作マテリアルを置く (material_json の注意を参照)
        files["materials/entity.material"] = _dump(material_json())
    return files
