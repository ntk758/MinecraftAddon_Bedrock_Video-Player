"""画像モード - 動画のコマをテクスチャ (atlas) にして、エンティティで再生するアドオン (.mcaddon) を作る CLI。

ブロックモード (convert.py / main.js) とは別のアドオンで、データ形式も共有しない。取り決めは image_mode/SPEC.md。
処理: ffmpeg でコマを取り出す → シート PNG に並べる → メモリを見積もる → 音声を切り出す → パックに組んで zip にする。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from image_mode import atlas, budget
from gui_i18n import LOCALES, addon_lang_files
from image_mode.rp import LIGHTING_MODES, rp_files
from pack_builder import build_manifest_version
from pack_metadata import version_text

APP_DIR = Path(__file__).resolve().parent
IMAGE_MAIN_SCRIPT = APP_DIR / "image_mode" / "image_main.js"

DEFAULT_NAMESPACE = "bvpimg"
DEFAULT_PACK_NAME = "Block & Image Video Player (Image)"
MIN_ENGINE_VERSION = [1, 26, 10]
ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
NAMESPACE_PATTERN = re.compile(r"^[a-z0-9_]+$")

# ゲーム内のメッセージ (RP/texts/*.lang) は locales/*.json の "imgaddon.*" (10 言語)。スクリプトは rawtext の translate で
# "bvpimg.<名前>" を使う。%1 〜 は translate の with の引数。名前空間 (--namespace) を変えてもキーは bvpimg. のまま
IMAGE_KEY_PREFIX = "imgaddon."
IMAGE_LANG_PREFIX = "bvpimg."



class ConvertError(Exception):
    """入力や予算の問題で作れないとき (メッセージをそのまま表示する)。"""


def pack_uuid(namespace: str, pack_name: str, role: str) -> str:
    """名前空間・パック名・役割から決まる UUID (再ビルドしても同じ。ブロックモードのパックとは別の種)。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"block-video-player-image/{namespace}/{pack_name}/{role}"))


def lang_files() -> dict[str, str]:
    """RP/texts/ に置くファイル (10 言語の .lang と languages.json。ブロックモードと同じ言語の対応)。"""
    return addon_lang_files(key_prefix=IMAGE_KEY_PREFIX, lang_prefix=IMAGE_LANG_PREFIX)


def lang_keys(language: str) -> dict[str, str]:
    """1 言語のメッセージ ({"bvpimg.<名前>": 文})。レポートとテスト用。"""
    return {IMAGE_LANG_PREFIX + key[len(IMAGE_KEY_PREFIX):]: text
            for key, text in LOCALES[language].items() if key.startswith(IMAGE_KEY_PREFIX)}


def screen_blocks(width: int, height: int, screen_width: float) -> list:
    """スクリーンの [幅, 高さ] (ブロック数)。高さは 1 コマの縦横比から (小数 2 桁、整数で表せれば整数)。"""
    def num(value: float):
        value = round(value, 2)
        return int(value) if value == int(value) else value
    return [num(screen_width), num(screen_width * height / width)]


def default_id(path: Path, used: set[str]) -> str:
    """ファイル名から動画 ID ([a-z0-9_]+) を作る。使えない文字は _ にし、重なったら番号を付ける。"""
    base = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_") or "video"
    candidate, number = base, 2
    while candidate in used:
        candidate = f"{base}_{number}"
        number += 1
    return candidate


def entity_json(video: dict, method: str, namespace: str) -> dict:
    """BP のエンティティ (画面)。動かない・落ちない・ぶつからない・押されない・ダメージを受けない・消えない。

    state (0 停止・1 再生・2 一時停止)・frame (同期したときのコマ)・seq (通し番号) の 3 つの int プロパティを
    client_sync でクライアントへ送り、リソースパック側の pre_animation がコマを計算する。
    """
    ns = namespace
    return {
        "format_version": "1.26.0",
        "minecraft:entity": {
            "description": {
                "identifier": f"{ns}:screen_{video['id']}_{method}",
                "is_spawnable": False,
                "is_summonable": True,
                "is_experimental": False,
                "properties": {
                    f"{ns}:state": {"type": "int", "range": [0, 2], "default": 0, "client_sync": True},
                    # コマが 1 枚だけの動画は range が [0, 0] になるのを避ける
                    f"{ns}:frame": {"type": "int", "range": [0, max(video["frame_count"] - 1, 1)], "default": 0,
                                    "client_sync": True},
                    f"{ns}:seq": {"type": "int", "range": [0, 1023], "default": 0, "client_sync": True},
                },
            },
            "components": {
                "minecraft:type_family": {"family": ["bvp_screen"]},
                "minecraft:physics": {"has_gravity": False, "has_collision": False},
                "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": False},
                "minecraft:damage_sensor": {"triggers": [{"cause": "all", "deals_damage": "no"}]},
                "minecraft:collision_box": {"width": 0.1, "height": 0.1},
                "minecraft:persistent": {},
                "minecraft:knockback_resistance": {"value": 1},
                "minecraft:health": {"value": 1000, "max": 1000},
            },
        },
    }


def pack_icon(frame: np.ndarray, size: int = 256) -> bytes:
    """最初のコマ全体を size 四方に収めた (黒い余白付きの) PNG。パックのアイコン。"""
    image = Image.fromarray(frame, "RGB")
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (size, size))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def png_bytes(image: Image.Image, optimize: bool = False) -> bytes:
    """シートの PNG。optimize なら Pillow の最適化 (compress_level 9) で 4% ほど小さくなるが、5 倍ほど遅い。"""
    out = io.BytesIO()
    if optimize:
        image.save(out, format="PNG", optimize=True, compress_level=9)
    else:
        image.save(out, format="PNG")
    return out.getvalue()


def manifests(namespace: str, pack_name: str) -> tuple[dict, dict]:
    # 作るたびに版を上げる (ブロックモードと同じ)。同じ名前で作り直すと UUID が同じなので、版が同じだと古いパックが使われる
    version = build_manifest_version()
    bp_uuid, rp_uuid = pack_uuid(namespace, pack_name, "bp"), pack_uuid(namespace, pack_name, "rp")
    description = f"Image mode (experimental): a video on a screen entity at 30 fps. Run /{namespace}:setup, then /{namespace}:start"
    bp = {
        "format_version": 2,
        "header": {"name": f"{pack_name} v{version_text()}", "description": description, "uuid": bp_uuid,
                   "version": version, "min_engine_version": MIN_ENGINE_VERSION},
        "modules": [{"description": "Script module", "type": "script", "language": "javascript",
                     "uuid": pack_uuid(namespace, pack_name, "bp-script"), "version": version,
                     "entry": "scripts/image_main.js"}],
        "dependencies": [{"module_name": "@minecraft/server", "version": "2.6.0"},
                         {"module_name": "@minecraft/server-ui", "version": "2.0.0"},
                         {"uuid": rp_uuid, "version": version}],
    }
    rp = {
        "format_version": 2,
        "header": {"name": f"{pack_name} Res v{version_text()}", "description": description, "uuid": rp_uuid,
                   "version": version, "min_engine_version": MIN_ENGINE_VERSION},
        "modules": [{"description": "Resources", "type": "resources",
                     "uuid": pack_uuid(namespace, pack_name, "rp-resources"), "version": version}],
        "dependencies": [{"uuid": bp_uuid, "version": version}],
    }
    return bp, rp


def image_videos_js(videos: list[dict], namespace: str) -> str:
    """BP/scripts/image_videos.js (スクリプトが読む動画の一覧)。"""
    lines = [f"export const NAMESPACE = {json.dumps(namespace)};", "", "export const IMAGE_VIDEOS = ["]
    lines += [("  " + json.dumps(video, ensure_ascii=False, separators=(", ", ": "))
               + ("," if i < len(videos) - 1 else "")) for i, video in enumerate(videos)]
    lines += ["];", ""]
    return "\n".join(lines)


def build_files(sources: list[dict], *, width: int, height: int, methods: list[str], namespace: str,
                pack_name: str, max_sheet: int = 2048, gutter: int = 1, max_cells: int = atlas.MAX_CELLS,
                screen_width: float = 16, audio_chunk_seconds: float = 4, allow_over_budget: bool = False,
                log=print, lighting: str = "bright", png_optimize: bool = False) -> tuple[dict, dict]:
    """コマから、アドオンのファイル (zip の中のパス → bytes) とレポートを作る。ffmpeg を使わない (ブラウザ版も使う)。

    sources の 1 つは {"id", "title", "fps", "frames": コマ (高さ×幅×3 の uint8) を出す iterable,
    "audio": (最長の秒数) → [(番号, 拡張子, bytes)] を返す関数 (無ければ音声なし)}。
    予算超過などで作れないときは ConvertError。
    lighting は rp.LIGHTING_MODES ("bright" は周りの光で暗くならない)。png_optimize はシートの PNG の最適化。
    """
    if lighting not in LIGHTING_MODES:
        raise ConvertError(f"lighting は {', '.join(LIGHTING_MODES)} のどれか: {lighting}")
    used: set[str] = set()
    for source in sources:
        if not ID_PATTERN.match(source["id"]):
            raise ConvertError(f"動画 ID は小文字の英数字と _ だけにしてください: {source['id']}")
        if source["id"] in used:
            raise ConvertError(f"動画 ID が重なっています: {source['id']}")
        used.add(source["id"])

    # 1. コマ → シート
    videos: list[dict] = []
    sheets_of: dict[str, list[Image.Image]] = {}
    icon: bytes | None = None
    for source in sources:
        video_id, title, fps = source["id"], source["title"], source["fps"]
        log(f"[{video_id}] コマをシートに並べます: {title}")
        first_frame: list[np.ndarray] = []

        def frames(source=source, first_frame=first_frame):
            for frame in source["frames"]:
                if not first_frame:
                    first_frame.append(np.array(frame, copy=True))
                yield frame

        try:
            sheets, layout = atlas.pack_sheets(frames(), width, height, max_sheet, gutter, max_cells)
        except ValueError as error:
            raise ConvertError(f"[{video_id}] {error}") from None
        if icon is None and first_frame:
            icon = pack_icon(first_frame[0])
        sheets_of[video_id] = sheets
        texels = layout["sheet_width"] * layout["sheet_height"] * layout["sheets"]
        videos.append({
            "id": video_id, "title": title, "fps": fps if fps != int(fps) else int(fps),
            "frame_count": layout["frame_count"], "width": width, "height": height,
            "sheet_width": layout["sheet_width"], "sheet_height": layout["sheet_height"],
            "cols": layout["cols"], "rows": layout["rows"], "cells_per_sheet": layout["cells_per_sheet"],
            "sheets": layout["sheets"], "gutter": gutter,
            "screen_blocks": screen_blocks(width, height, screen_width),
            "texture_paths": [f"textures/bvp_image/{video_id}/sheet_{n}" for n in range(layout["sheets"])],
            "audio_chunks": 0, "audio_chunk_seconds": audio_chunk_seconds, "methods": methods,
            "lighting": lighting,
            "memory": {"texels": texels, "bytes": texels * 4},
        })
        log(f"[{video_id}] {layout['frame_count']} コマ -> シート {layout['sheets']} 枚 "
            f"({layout['sheet_width']}x{layout['sheet_height']}, {layout['cols']}x{layout['rows']} マス)")

    # 2. メモリの見積もり (全動画の合計。予算は 1 つのアドオンに対する値)
    layouts = [{"sheet_width": v["sheet_width"], "sheet_height": v["sheet_height"], "sheets": v["sheets"]}
               for v in videos]
    memory = atlas.memory_report(layouts, allow_over_budget=allow_over_budget)
    log(memory["table"])
    if memory["errors"]:
        raise ConvertError("テクスチャが予算を超えるので作りません (--allow-over-budget で作れます)。"
                         "コマの大きさ・fps・長さを減らしてください")

    # 3. 音声
    files: dict[str, bytes] = {}

    def put(path: str, content) -> None:
        files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)

    sound_definitions = {}
    for source, video in zip(sources, videos):
        video_id = video["id"]
        chunks = source["audio"](video["frame_count"] / video["fps"]) if source.get("audio") else []
        video["audio_chunks"] = len(chunks)
        log(f"[{video_id}] 音声 {len(chunks)} チャンク" + ("" if chunks else " (音声なし)"))
        for index, ext, data in chunks:
            put(f"RP/sounds/bvp_image/{video_id}/chunk_{index}.{ext}", data)
            # パスは拡張子なしで書く
            sound_definitions[f"{namespace}.img.{video_id}.chunk_{index}"] = {
                "category": "ui",
                "sounds": [{"name": f"sounds/bvp_image/{video_id}/chunk_{index}", "stream": True,
                            "load_on_low_memory": True}],
            }

    # 4. パックを組む
    for video in videos:
        for number, sheet in enumerate(sheets_of[video["id"]]):
            put(f"RP/textures/bvp_image/{video['id']}/sheet_{number}.png", png_bytes(sheet, png_optimize))
        for method in methods:
            put(f"BP/entities/screen_{video['id']}_{method}.json",
                json.dumps(entity_json(video, method, namespace), ensure_ascii=False, indent=2) + "\n")
    if sound_definitions:
        put("RP/sounds/sound_definitions.json", json.dumps(
            {"format_version": "1.14.0", "sound_definitions": sound_definitions}, ensure_ascii=False, indent=2) + "\n")
    for relative, content in rp_files(videos, namespace).items():
        put(f"RP/{relative}", content)
    for relative, content in lang_files().items():
        put(f"RP/{relative}", content)
    bp_manifest, rp_manifest = manifests(namespace, pack_name)
    put("BP/manifest.json", json.dumps(bp_manifest, ensure_ascii=False, indent=2) + "\n")
    put("RP/manifest.json", json.dumps(rp_manifest, ensure_ascii=False, indent=2) + "\n")
    put("BP/scripts/image_videos.js", image_videos_js(videos, namespace))
    put("BP/scripts/image_main.js", IMAGE_MAIN_SCRIPT.read_bytes())
    if icon is not None:
        put("RP/pack_icon.png", icon)
        put("BP/pack_icon.png", icon)

    memory_summary = {k: v for k, v in memory.items() if k != "table"}
    report = {
        "namespace": namespace, "pack_name": pack_name, "methods": methods, "videos": videos,
        "memory": {**memory_summary, "per_video": [{"id": v["id"], **v["memory"], "handles": v["sheets"]}
                                                    for v in videos]},
        "warnings": memory["warnings"],
        "lang_keys": {"en": lang_keys("en"), "ja": lang_keys("ja")},
        "files": sorted(files),
    }
    return files, report


COMBINED_FOLDERS = {"BP/": "BP_image/", "RP/": "RP_image/"}


def combine_addons(block_addon: Path, image_addon: Path, output: Path | None = None) -> Path:
    """ブロックモードと画像モードの .mcaddon を 1 つのファイルにまとめる (中は 4 つのパック。画像モードは BP_image/・RP_image/)。

    パックは別々のまま (UUID・スクリプト・コマンドの接頭辞も別) なので、読み込むとどちらも入り、どちらのコマンドも使える。
    output を省くと block_addon を上書きする。画像モードのファイルは消さない (呼び出し側が決める)。
    """
    output = Path(output) if output else Path(block_addon)
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(block_addon) as block:
        for name in block.namelist():
            if not name.endswith("/"):
                files[name] = block.read(name)
    with zipfile.ZipFile(image_addon) as image:
        for name in image.namelist():
            if name.endswith("/"):
                continue
            prefix = next((p for p in COMBINED_FOLDERS if name.startswith(p)), None)
            target = COMBINED_FOLDERS[prefix] + name[len(prefix):] if prefix else f"image/{name}"
            if target in files:
                raise ConvertError(f"まとめるファイルの名前が重なりました: {target}")
            files[target] = image.read(name)
    output.write_bytes(zip_bytes(files))
    return output


def zip_bytes(files: dict) -> bytes:
    """build_files のファイルを .mcaddon (zip) の bytes にする。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.writestr(path, files[path])
    return buffer.getvalue()


PRIORITY_LABELS = {"quality": "画質", "smooth": "なめらかさ", "length": "長さ"}
CHANGED_LABELS = {"fps": "fps", "size": "コマの大きさ", "length": "長さ"}


def apply_priority(args: argparse.Namespace, inputs: list[Path], log=print) -> list:
    """--priority: 予算 (--target-tier) に収まるよう args の大きさ・fps を直し、動画ごとの終わり (秒) を返す。"""
    start = args.start or 0.0
    durations = []
    for path in inputs:
        total = atlas.probe_duration(path, args.ffmpeg)
        if total is None:
            raise ConvertError(f"動画の長さを読めませんでした: {path}")
        end = min(total, args.end) if args.end is not None else total
        durations.append(max(0.0, end - start))
    source_size = atlas.probe_size(inputs[0], args.ffmpeg) if args.snap else None
    result = budget.plan(durations, args.width, args.height, args.fps, args.priority, args.target_tier,
                         max_sheet=args.max_sheet, gutter=args.gutter, max_cells=args.max_cells, snap=args.snap,
                         source_size=source_size)
    if not result["fits"]:
        raise ConvertError(f"Tier {args.target_tier} の予算にどうしても収まりません (1 コマも入りません)")
    if result["changed"]:
        what = "・".join(CHANGED_LABELS[item] for item in result["changed"])
        log(f"{PRIORITY_LABELS[args.priority]}を優先して、Tier {args.target_tier} の予算 "
            f"({budget.TIER_BUDGETS_MB[args.target_tier]} MB) に収まるよう {what} を変えました: "
            f"{args.width}x{args.height} {args.fps:g} fps → {result['width']}x{result['height']} {result['fps']} fps")
        for path, before, after in zip(inputs, durations, result["seconds"]):
            if after < before - 1e-6:
                log(f"  {path.name}: {before:.1f} 秒 → {after:.1f} 秒 (後ろを切りました)")
    args.width, args.height, args.fps = result["width"], result["height"], float(result["fps"])
    args.plan = result
    return [start + seconds if seconds < duration - 1e-6 else args.end
            for seconds, duration in zip(result["seconds"], durations)]


def build(args: argparse.Namespace, log=print) -> dict:
    """引数どおりにアドオンを作り、レポート (dict) を返す。予算超過などで作れないときは ConvertError。"""
    inputs = [Path(p) for p in args.inputs]
    for path in inputs:
        if not path.is_file():
            raise ConvertError(f"入力動画が見つかりません: {path}")
    used: set[str] = set()
    ids = []
    for index, path in enumerate(inputs):
        video_id = args.ids[index] if args.ids else default_id(path, used)
        if not ID_PATTERN.match(video_id):
            raise ConvertError(f"動画 ID は小文字の英数字と _ だけにしてください: {video_id}")
        if video_id in used:
            raise ConvertError(f"動画 ID が重なっています: {video_id}")
        used.add(video_id)
        ids.append(video_id)
    titles = args.titles or [p.stem for p in inputs]
    ends = [args.end] * len(inputs)
    if args.priority != "none":
        ends = apply_priority(args, inputs, log)
    elif args.snap:
        snapped = budget.snap_size(args.width, args.height, args.max_sheet, args.gutter, args.max_cells,
                                   atlas.probe_size(inputs[0], args.ffmpeg))
        if snapped != (args.width, args.height):
            log(f"1 コマを {args.width}x{args.height} → {snapped[0]}x{snapped[1]} にして、シート 1 枚に入るコマを増やします "
                "(--no-snap で今の大きさのまま)")
            args.width, args.height = snapped
    methods = ["a", "c"] if args.method == "both" else [args.method]
    pack_name = args.pack_name or titles[0]
    ffmpeg = args.ffmpeg

    with tempfile.TemporaryDirectory(prefix="bvp-image-") as temp:
        sources = [{
            "id": video_id, "title": title, "fps": args.fps,
            "frames": atlas.read_frames(path, args.width, args.height, args.fps, args.start, end,
                                        args.fit, ffmpeg),
            "audio": (lambda seconds, path=path, video_id=video_id, end=end: atlas.extract_audio_chunks(
                path, Path(temp) / video_id, args.audio_chunk_seconds, args.start, end, ffmpeg,
                max_seconds=seconds)),
        } for path, video_id, title, end in zip(inputs, ids, titles, ends)]
        files, report = build_files(
            sources, width=args.width, height=args.height, methods=methods, namespace=args.namespace,
            pack_name=pack_name, max_sheet=args.max_sheet, gutter=args.gutter, max_cells=args.max_cells,
            screen_width=args.screen_width, audio_chunk_seconds=args.audio_chunk_seconds,
            allow_over_budget=args.allow_over_budget, log=log, lighting=args.lighting, png_optimize=args.png_optimize)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(zip_bytes(files))
    report = {"namespace": report["namespace"], "pack_name": report["pack_name"], "output": str(output),
              "size_bytes": output.stat().st_size,
              "plan": getattr(args, "plan", None),
              **{key: value for key, value in report.items() if key not in ("namespace", "pack_name")}}
    report_path = Path(args.report) if args.report else output.with_suffix(".report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"出力: {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")
    log(f"レポート: {report_path}")
    return report


def _fps(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("数を指定してください") from None
    if not 1 <= value <= 60:
        raise argparse.ArgumentTypeError("fps は 1〜60 の範囲で指定してください")
    return value


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("整数を指定してください") from None
    if value < 1:
        raise argparse.ArgumentTypeError("1 以上にしてください")
    return value


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("整数を指定してください") from None
    if value < 0:
        raise argparse.ArgumentTypeError("0 以上にしてください")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("数を指定してください") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("0 より大きくしてください")
    return value


def _namespace(text: str) -> str:
    if not NAMESPACE_PATTERN.match(text) or text == "minecraft":
        raise argparse.ArgumentTypeError("名前空間は小文字の英数字と _ だけで、minecraft 以外にしてください")
    return text


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="画像モード: 動画のコマをテクスチャのシートにして、エンティティで再生するアドオン (.mcaddon) を作ります。"
                    "ブロックモード (convert.py) とは別のアドオンです。")
    parser.add_argument("inputs", nargs="+", metavar="VIDEO", help="入力動画ファイル (複数可。1 本が 1 つの動画になる)")
    parser.add_argument("--output", "-o", default=None, help="出力の .mcaddon (既定は最初の動画と同じ場所の <名前>_image.mcaddon)")
    parser.add_argument("--ids", nargs="+", default=None, metavar="ID",
                        help="動画 ID ([a-z0-9_]+、動画と同じ数)。既定はファイル名から作る")
    parser.add_argument("--titles", nargs="+", default=None, metavar="TITLE", help="表示名 (動画と同じ数)。既定はファイル名")
    parser.add_argument("--width", type=_positive_int, default=256, help="1 コマの横の画素数 (既定 256)")
    parser.add_argument("--height", type=_positive_int, default=144, help="1 コマの縦の画素数 (既定 144)")
    parser.add_argument("--fps", type=_fps, default=30, help="再生の fps (1〜60、既定 30)")
    parser.add_argument("--start", type=float, default=None, help="開始位置 (秒)")
    parser.add_argument("--end", type=float, default=None, help="終了位置 (秒)")
    parser.add_argument("--fit", default="pad", choices=["pad", "cover"],
                        help="縦横比の合わせ方。pad は全体を入れて余白を黒、cover は画面を埋めて中央で切る (既定 pad)")
    parser.add_argument("--max-cells", type=_positive_int, default=atlas.MAX_CELLS,
                        help=f"シート 1 枚のマスの数の上限 (既定 {atlas.MAX_CELLS}。方式 a のボーンの数になる)")
    parser.add_argument("--max-sheet", type=_positive_int, default=2048, help="シート 1 枚の一辺の最大画素数 (既定 2048)")
    parser.add_argument("--gutter", type=_non_negative_int, default=1,
                        help="マスの間の画素数。端の画素を繰り返して埋め、にじみを防ぐ (既定 1)")
    parser.add_argument("--screen-width", type=_positive_float, default=16,
                        help="スクリーンの幅 (ブロック数、既定 16)。高さは縦横比から決まる")
    parser.add_argument("--method", default="a", choices=["a", "c", "both"],
                        help="a=アトラス+ボーンの切り替え (安全)、c=uv_anim の自作マテリアル (軽いが将来壊れうる)、both=両方 (既定は a。実機の確認で a に決めた)")
    parser.add_argument("--namespace", type=_namespace, default=DEFAULT_NAMESPACE,
                        help=f"名前空間 (小文字の英数字と _。既定 {DEFAULT_NAMESPACE})")
    parser.add_argument("--pack-name", default=None, help="パック名 (既定は最初の動画の表示名)")
    parser.add_argument("--audio-chunk-seconds", type=_positive_float, default=4, help="音声を区切る秒数 (既定 4)")
    parser.add_argument("--priority", default="none", choices=["none", *budget.PRIORITIES],
                        help="テクスチャが予算 (--target-tier) を超えるとき何を守るか: quality=画質 (fps を下げる)、"
                             "smooth=なめらかさ (コマを小さくする)、length=長さ (両方を少しずつ下げる)。"
                             "それでも入らなければ後ろを切る。none (既定) は何も変えない")
    parser.add_argument("--target-tier", type=int, default=2, choices=sorted(budget.TIER_BUDGETS_MB),
                        help="--priority で収める予算の Tier (既定 2: 多くのスマートフォン・PS4・Xbox One。PC だけなら 5)")
    parser.add_argument("--allow-over-budget", action="store_true",
                        help="テクスチャが Tier 5 の予算 (800 MB) を超えても作る")
    parser.add_argument("--report", default=None, help="レポートの JSON のパス (既定は出力の隣)")
    parser.add_argument("--lighting", default="bright", choices=LIGHTING_MODES,
                        help="明るさ。bright は周りの光で暗くならない (既定。夜や暗い部屋でも元の明るさ)、"
                             "world は v6.36.0 までと同じく周りの光と陰を受ける")
    parser.add_argument("--snap", action=argparse.BooleanOptionalAction, default=True,
                        help="シート 1 枚に入るコマを増やすため、頼んだ大きさの 9 割以上で元の動画をちょうど割り切れる大きさにする "
                             "(1920×1080 で 512×288 → 480×270、テクスチャ −15%%。画質は同等: docs/IMAGE_MODE.md)。"
                             "--no-snap で頼んだ大きさのまま")
    parser.add_argument("--png-optimize", action="store_true",
                        help="シートの PNG を最適化して 4%% ほど小さくする (変換が 5 倍ほど遅くなる)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 実行ファイルのパス")
    args = parser.parse_args(argv)
    if args.ids is not None and len(args.ids) != len(args.inputs):
        parser.error("--ids の数が動画の数と違います")
    if args.titles is not None and len(args.titles) != len(args.inputs):
        parser.error("--titles の数が動画の数と違います")
    if args.start is not None and args.start < 0:
        parser.error("--start は 0 以上にしてください")
    if args.end is not None and args.end <= (args.start or 0):
        parser.error("--end は --start より後にしてください")
    if args.output is None:
        first = Path(args.inputs[0])
        args.output = str(first.with_name(first.stem + "_image.mcaddon"))
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        build(args)
    except (ConvertError, RuntimeError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
