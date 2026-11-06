"""画像（テクスチャ）再生方式のパックの JSON を作る。

動画ごとに 1 つのエンティティ種別を使う (画面のエンティティは `namespace:<entityName>`)。
フレーム番号はエンティティプロパティ `namespace:frame` に書き、render controller が
`Array.frames[q.property('<namespace>:frame')]` でフレームの PNG を選ぶ。

出典 (確認した公式ドキュメント、URL は docs/IMAGE_MODE.md):
- エンティティプロパティの宣言 (type / range / default / client_sync): Introduction to Entity Properties
- テクスチャの配列と index の数式: Render Controllers
- クライアントエンティティの textures / materials / geometry / render_controllers: Client Entity documentation
- ジオメトリの texture_width / texture_height と面ごとの UV: geometry.v1.16.0
- material はビルトインだけ使う: Material Files (カスタム material には「予期しない・不安定な挙動」の注意書きがある)
"""
import json

# 1 エンティティ種別あたりのプロパティの数の上値 (公式: 32 個まで)。この方式は 1 個だけ使う。
ENTITY_PROPERTY_LIMIT = 32


def bp_entity_json(namespace: str, entity_name: str, frame_count: int) -> str:
    """BP 側のエンティティ定義。フレーム番号の int のプロパティを宣言する。"""
    if frame_count <= 0:
        raise ValueError(f"フレームの数 は 0 より大きい値: {frame_count}")
    doc = {
        "format_version": "1.21.0",
        "minecraft:entity": {
            "description": {
                "identifier": f"{namespace}:{entity_name}",
                "is_spawnable": False,
                "is_summonable": True,
                "is_experimental": False,
                "properties": {
                    f"{namespace}:frame": {
                        "type": "int",
                        "range": [0, frame_count - 1],
                        "default": 0,
                        # クライアントに同期しないと Resource Pack の描画 (q.property) はこの値を読めない
                        "client_sync": True,
                    }
                },
            },
            "components": {
                "minecraft:health": {"value": 1024, "max": 1024},
                "minecraft:physics": {},
                "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": False},
            },
        },
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def rp_entity_json(namespace: str, entity_name: str, frame_count: int) -> str:
    """RP 側のクライアントエンティティ定義。フレームの PNG を短い名前で並べる。"""
    textures = {f"f{index}": f"textures/{namespace}/{entity_name}/frame_{index}" for index in range(frame_count)}
    doc = {
        "format_version": "1.10.0",
        "minecraft:client_entity": {
            "description": {
                "identifier": f"{namespace}:{entity_name}",
                # ビルトインの material だけ使う (カスタム material は公式に「不安定な挙動」の注意書きがある)
                "materials": {"default": "entity"},
                "textures": textures,
                "geometry": {"default": f"geometry.{namespace}.{entity_name}"},
                "render_controllers": [f"controller.render.{namespace}.{entity_name}"],
            }
        },
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def render_controller_json(namespace: str, entity_name: str, frame_count: int) -> str:
    """テクスチャの配列をエンティティプロパティの値で選ぶ render controller。"""
    doc = {
        "format_version": "1.8.0",
        "render_controllers": {
            f"controller.render.{namespace}.{entity_name}": {
                "arrays": {
                    "textures": {
                        "Array.frames": [f"Texture.f{index}" for index in range(frame_count)]
                    }
                },
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                # 配列の index を q.property で選ぶ (公式の例は query.variant を使っている)
                "textures": [f"Array.frames[q.property('{namespace}:frame')]"],
            }
        },
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def geometry_json(namespace: str, entity_name: str, width: int, height: int, screen_blocks: float = 2.0) -> str:
    """画面のジオメトリ。1 ブロック = 16 units。aspect ratio はテクスチャと同じにする。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"画面の大きさは 0 より大きい: {width}x{height}")
    if screen_blocks <= 0:
        raise ValueError(f"画面の幅は 0 より大きい: {screen_blocks}")
    units_x = int(round(screen_blocks * 16))
    units_y = int(round(units_x * height / width))
    cube = {
        "origin": [-units_x // 2, -units_y // 2, -1],
        "size": [units_x, max(1, units_y), 2],
        # 前後の面に同じ画像を貼る (どちら側から見ても映る)
        "uv": {
            "north": {"uv": [0, 0], "uv_size": [width, height]},
            "south": {"uv": [0, 0], "uv_size": [width, height]},
        },
    }
    doc = {
        "format_version": "1.16.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": f"geometry.{namespace}.{entity_name}",
                    "texture_width": width,
                    "texture_height": height,
                    "visible_bounds_width": max(2, units_x // 16 + 1),
                    "visible_bounds_height": max(2, units_y // 16 + 1),
                    "visible_bounds_offset": [0, 1, 0],
                },
                "bones": [{"name": "screen", "pivot": [0, 0, 0], "cubes": [cube]}],
            }
        ],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def texture_bytes(frames: list[bytes], namespace: str, entity_name: str) -> dict[str, bytes]:
    """フレームの PNG を RP のテクスチャのパスに並べる。"""
    return {f"RP/textures/{namespace}/{entity_name}/frame_{index}.png": bytes(data)
            for index, data in enumerate(frames)}
