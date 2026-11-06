"""画像（テクスチャ）再生方式の PoC を作るスクリプト。

ブロック方式 (convert.py / main.js / codec.js / pack_builder.py) は変更しない。このファイルだけで
Behavior Pack と Resource Pack の最小構成を組み、`.mcaddon` に書き出す。

方式 (フェーズA で承認された 方式1a):
  - BP のエンティティ定義に `properties` を宣言し、`client_sync: true` の int プロパティでフレーム番号を持つ
  - Script API の `Entity.setProperty` でフレーム番号を書く (main.js とは別のスクリプト)
  - RP の render controller が `arrays.textures` の配列から `q.property('<ns>:frame')` の値で PNG を選ぶ
出典は docs/IMAGE_MODE.md に書いた。実機での動作は未確認。

使い方:
  python image_mode/image_poc.py --frames 12 --width 192 --height 108 --fps 20 -o dist/ImagePoC.mcaddon
  (動画からフレームを出すとき: --input sample.mp4)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from pack_builder import MANIFEST, build_manifest_version, stable_pack_uuid  # noqa: E402
from pack_metadata import changelog_markdown, manifest_description, version_text  # noqa: E402

POC_SCRIPT = APP_DIR / "scripts" / "image_poc_main.js"
TICKS_PER_SECOND = 20
MAX_FPS = TICKS_PER_SECOND  # 1 tick に 1 フレームが上限 (main.js と同じ前提)

# エンティティプロパティは 1 種別あたり 32 個まで (公式の上限)。この PoC は 1 個だけ使う。
ENTITY_PROPERTY_LIMIT = 32


def ticks_per_frame(fps: float) -> int:
    """1 フレームを何 tick 進めるか。fps > 20 は 20 に clamp する (1 tick = 1 フレームが上限)。"""
    limited = min(float(fps), MAX_FPS)
    if limited <= 0:
        raise ValueError(f"fps は 0 より大きく: {fps}")
    return max(1, int(round(TICKS_PER_SECOND / limited)))


def bp_entity_json(namespace: str, frame_count: int) -> str:
    """BP 側のエンティティ定義。フレーム番号の int プロパティだけを宣言する。"""
    if frame_count < 1:
        raise ValueError(f"フレーム数は 1 以上: {frame_count}")
    if frame_count > ENTITY_PROPERTY_LIMIT * 1024:
        raise ValueError(f"フレーム数が大きすぎます: {frame_count}")
    doc = {
        "format_version": "1.21.0",
        "minecraft:entity": {
            "description": {
                "identifier": f"{namespace}:screen",
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


def rp_entity_json(namespace: str, frame_count: int) -> str:
    """RP 側のクライアントエンティティ定義。フレームの PNG を短い名前で並べる。"""
    textures = {f"f{index}": f"textures/{namespace}/frame_{index}" for index in range(frame_count)}
    doc = {
        "format_version": "1.10.0",
        "minecraft:client_entity": {
            "description": {
                "identifier": f"{namespace}:screen",
                # ビルトインの material だけ使う (カスタム material は公式に「不安定な挙動」の注意書きがある)
                "materials": {"default": "entity"},
                "textures": textures,
                "geometry": {"default": f"geometry.{namespace}.screen"},
                "render_controllers": [f"controller.render.{namespace}.screen"],
            }
        },
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def render_controller_json(namespace: str, frame_count: int) -> str:
    """テクスチャの配列をエンティティプロパティの値で選ぶ render controller。"""
    doc = {
        "format_version": "1.8.0",
        "render_controllers": {
            f"controller.render.{namespace}.screen": {
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


def geometry_json(namespace: str, width: int, height: int) -> str:
    """画面のジオメトリ。1 ブロック = 16 units。aspect ratio はテクスチャと同じにする。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"画面の大きさは 0 より大きい: {width}x{height}")
    units_x = 32
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
                    "identifier": f"geometry.{namespace}.screen",
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


def poc_main_text(namespace: str, frame_count: int, fps: float) -> str:
    """PoC のスクリプト。テンプレートの定数を差し替える (main.js の namespace 差し替えと同じ考え方)。"""
    text = POC_SCRIPT.read_text(encoding="utf-8")
    text = text.replace("__NAMESPACE__", namespace)
    text = text.replace("__FRAME_COUNT__", str(frame_count))
    text = text.replace("__TICKS_PER_FRAME__", str(ticks_per_frame(fps)))
    if "__NAMESPACE__" in text or "__FRAME_COUNT__" in text or "__TICKS_PER_FRAME__" in text:
        raise ValueError("テンプレートの定数が置き換わりませんでした")
    return text


def build_poc_addon(output, frames: list[bytes], width: int, height: int, fps: float = 20.0,
                    namespace: str = "bvpimg", pack_name: str = "Block Video Player ImagePoC") -> dict:
    """.mcaddon を output (パスか書き込みできるファイル) に書き、作ったファイルの一覧を返す。

    frames はフレームの PNG の bytes の一覧 (index 0 が 1 番目のフレーム)。
    """
    if len(frames) == 0:
        raise ValueError("フレームの PNG が 0 個です")
    if len(frames) > ENTITY_PROPERTY_LIMIT * 1024:
        raise ValueError(f"フレーム数が大きすぎます: {len(frames)}")
    files: dict[str, bytes] = {}

    def put(path: str, content) -> None:
        files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)

    frame_count = len(frames)
    for index, data in enumerate(frames):
        put(f"RP/textures/{namespace}/frame_{index}.png", data)
    put("BP/entities/screen.json", bp_entity_json(namespace, frame_count))
    put("RP/entity/screen.json", rp_entity_json(namespace, frame_count))
    put("RP/render_controllers/screen.json", render_controller_json(namespace, frame_count))
    put("RP/models/screen.geo.json", geometry_json(namespace, width, height))
    put("BP/scripts/main.js", poc_main_text(namespace, frame_count, fps))
    put("BP/CHANGELOG.md", changelog_markdown(pack_name))
    put("RP/CHANGELOG.md", changelog_markdown(pack_name))
    put("BP/pack_icon.png", frames[0])
    put("RP/pack_icon.png", frames[0])

    version = build_manifest_version()
    bp_uuid = stable_pack_uuid(namespace, pack_name, "bp")
    rp_uuid = stable_pack_uuid(namespace, pack_name, "rp")
    bp_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bp_manifest["header"].update({"name": f"{pack_name} v{version_text()}", "description": manifest_description(),
                                  "uuid": bp_uuid, "version": version})
    bp_manifest["modules"][0].update({"uuid": stable_pack_uuid(namespace, pack_name, "bp-script"), "version": version,
                                      "entry": "scripts/main.js"})
    # この PoC は @minecraft/server だけ使う (UI は使わないので依存を残さない)
    bp_manifest["dependencies"] = [dep for dep in bp_manifest.get("dependencies", [])
                                   if dep.get("module_name") == "@minecraft/server"]
    bp_manifest["dependencies"].append({"uuid": rp_uuid, "version": version})
    put("BP/manifest.json", json.dumps(bp_manifest, ensure_ascii=False, indent=2) + "\n")
    rp_manifest = {
        "format_version": 2,
        "header": {"name": f"{pack_name} Res v{version_text()}", "description": manifest_description(), "uuid": rp_uuid,
                   "version": version, "min_engine_version": bp_manifest["header"]["min_engine_version"]},
        "modules": [{"description": "Resources", "type": "resources",
                     "uuid": stable_pack_uuid(namespace, pack_name, "rp-resources"), "version": version}],
        "dependencies": [{"uuid": bp_uuid, "version": version}],
    }
    put("RP/manifest.json", json.dumps(rp_manifest, ensure_ascii=False, indent=2) + "\n")

    target = output
    if isinstance(output, (str, Path)):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.writestr(path, files[path])
    return {"paths": sorted(files), "frame_count": frame_count, "ticks_per_frame": ticks_per_frame(fps)}


def png_size(data: bytes) -> tuple[int, int]:
    """PNG のヘッダーから幅と高さを読む (画像のライブラリを使わない)。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG の形式ではありません")
    offset = 8
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            return int.from_bytes(body[0:4], "big"), int.from_bytes(body[4:8], "big")
        offset += 8 + length + 4
    raise ValueError("PNG に IHDR がありません")


def generate_frames(width: int, height: int, fps: float, count: int, source: Path | None = None) -> list[bytes]:
    """ffmpeg でフレームの PNG を書き出す。source が無いときは ffmpeg の testsrc (動くテスト映像)。"""
    if count < 1:
        raise ValueError(f"フレーム数は 1 以上: {count}")
    work = Path(tempfile.mkdtemp(prefix="bvpimg-"))
    try:
        pattern = work / "frame_%03d.png"
        command = ["ffmpeg", "-y", "-loglevel", "error"]
        if source is None:
            command += ["-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={max(1, int(round(fps)))}"]
        else:
            command += ["-i", str(source), "-vf", f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease"]
        command += ["-frames:v", str(count), str(pattern)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg が失敗しました ({result.returncode}): {result.stderr.strip()[:400]}")
        paths = sorted(work.glob("frame_*.png"))
        if len(paths) != count:
            raise RuntimeError(f"expected {count} frame PNGs, got {len(paths)}")
        frames = [path.read_bytes() for path in paths]
        sizes = {png_size(data) for data in frames}
        if sizes and sizes != {(width, height)}:
            print(f"[{width}x{height}] を作れません (実際: {sorted(sizes)})。testsrc のサイズ指定か --input を確認してください",
                  file=sys.stderr)
        return frames
    finally:
        shutil.rmtree(work, ignore_errors=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="画像（テクスチャ）再生方式の PoC の .mcaddon を作る")
    parser.add_argument("--frames", type=int, default=12, help="書き出すフレームの数 (既定 12)")
    parser.add_argument("--width", type=int, default=192, help="フレームの幅 (px, 既定 192)")
    parser.add_argument("--height", type=int, default=108, help="フレームの高さ (px, 既定 108)")
    parser.add_argument("--fps", type=float, default=20.0, help="再生速度。20 が上限 (1 tick に 1 フレーム)")
    parser.add_argument("--namespace", default="bvpimg", help="エンティティとプロパティの接頭辞 (既定 bvpimg)")
    parser.add_argument("--input", default=None, help="動画のファイル。無いときは ffmpeg の testsrc を使う")
    parser.add_argument("-o", "--output", default="dist/ImagePoC.mcaddon", help="出力の .mcaddon")
    parser.add_argument("--keep-frames", default=None, help="フレームの PNG をこのフォルダにも残す")
    args = parser.parse_args(argv)
    if args.frames > 4096:
        parser.error("--frames は 4096 まで (テクスチャの枚数と容量の上限を見積るため)")
    if args.width <= 0 or args.height <= 0:
        parser.error("--width と --height は 0 より大きく")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    frames = generate_frames(args.width, args.height, args.fps, args.frames,
                            Path(args.input) if args.input else None)
    if args.keep_frames:
        keep = Path(args.keep_frames)
        keep.mkdir(parents=True, exist_ok=True)
        for index, data in enumerate(frames):
            (keep / f"frame_{index}.png").write_bytes(data)
        print(f"フレームを {keep} に残しました")
    info = build_poc_addon(args.output, frames, args.width, args.height, args.fps, args.namespace)
    texels = args.width * args.height * len(frames) * 4
    print(f"{args.output}: {info['frame_count']} フレーム, 1 フレーム {info['ticks_per_frame']} tick, "
          f"テクスチャの見積容量 {texels / (1024 * 1024):.1f} MiB")
    print("ゲームでの確認手順は docs/IMAGE_MODE.md に書いています (実機での動作は未確認)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
