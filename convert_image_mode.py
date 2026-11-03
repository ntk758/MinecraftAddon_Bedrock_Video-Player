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

from image_mode import atlas
from image_mode.rp import rp_files
from pack_builder import build_manifest_version
from pack_metadata import version_text

APP_DIR = Path(__file__).resolve().parent
IMAGE_MAIN_SCRIPT = APP_DIR / "image_mode" / "image_main.js"

DEFAULT_NAMESPACE = "bvpimg"
DEFAULT_PACK_NAME = "Block Video Player Image"
MIN_ENGINE_VERSION = [1, 26, 10]
ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
NAMESPACE_PATTERN = re.compile(r"^[a-z0-9_]+$")

# ゲーム内のメッセージ (RP/texts/*.lang)。スクリプトは rawtext の translate で "bvpimg.<名前>" を使う。
# %1 〜 は translate の with の引数。名前空間 (--namespace) を変えてもキーは bvpimg. のまま
LANG_KEYS = {
    "setup_done": {"en_US": "Screen set up: %1 (method %2)", "ja_JP": "スクリーンを置きました: %1 (方式 %2)"},
    "not_setup": {"en_US": "No screen is set up yet. Run the setup command first.",
                  "ja_JP": "スクリーンがまだありません。先に setup を実行してください。"},
    "playing": {"en_US": "Playing: %1", "ja_JP": "再生します: %1"},
    "paused": {"en_US": "Paused", "ja_JP": "一時停止しました"},
    "resumed": {"en_US": "Resumed", "ja_JP": "再開しました"},
    "stopped": {"en_US": "Stopped", "ja_JP": "停止しました"},
    "method_set": {"en_US": "Method changed to %1", "ja_JP": "方式を %1 に変えました"},
    "list_item": {"en_US": "%1: %2 (%3 frames, %4 fps)", "ja_JP": "%1: %2 (%3 コマ, %4 fps)"},
    "video_not_found": {"en_US": "Video not found: %1", "ja_JP": "動画が見つかりません: %1"},
    "seek_done": {"en_US": "Moved to %1 s", "ja_JP": "%1 秒へ移動しました"},
    "reload_resumed": {"en_US": "Restored the screen after reloading the world: %1",
                       "ja_JP": "ワールドを開き直したので、スクリーンを元に戻しました: %1"},
}
LANG_CODES = ("en_US", "ja_JP")


class ConvertError(Exception):
    """入力や予算の問題で作れないとき (メッセージをそのまま表示する)。"""


def pack_uuid(namespace: str, pack_name: str, role: str) -> str:
    """名前空間・パック名・役割から決まる UUID (再ビルドしても同じ。ブロックモードのパックとは別の種)。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"block-video-player-image/{namespace}/{pack_name}/{role}"))


def lang_files() -> dict[str, str]:
    """RP/texts/ に置くファイル (en_US.lang・ja_JP.lang・languages.json)。"""
    files = {}
    for code in LANG_CODES:
        lines = [f"bvpimg.{key}={texts[code]}" for key, texts in LANG_KEYS.items()]
        files[f"texts/{code}.lang"] = "\n".join(lines) + "\n"
    files["texts/languages.json"] = json.dumps(list(LANG_CODES), indent=2) + "\n"
    return files


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


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
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
    methods = ["a", "c"] if args.method == "both" else [args.method]
    namespace = args.namespace
    pack_name = args.pack_name or titles[0]
    ffmpeg = args.ffmpeg

    # 1. コマ → シート
    videos: list[dict] = []
    sheets_of: dict[str, list[Image.Image]] = {}
    icon: bytes | None = None
    for path, video_id, title in zip(inputs, ids, titles):
        log(f"[{video_id}] コマを取り出してシートに並べます: {path.name}")
        first_frame: list[np.ndarray] = []

        def frames():
            for frame in atlas.read_frames(path, args.width, args.height, args.fps, args.start, args.end,
                                           args.fit, ffmpeg):
                if not first_frame:
                    first_frame.append(frame.copy())
                yield frame

        try:
            sheets, layout = atlas.pack_sheets(frames(), args.width, args.height, args.max_sheet, args.gutter, args.max_cells)
        except ValueError as error:
            raise ConvertError(f"[{video_id}] {error}") from None
        if icon is None:
            icon = pack_icon(first_frame[0])
        sheets_of[video_id] = sheets
        texels = layout["sheet_width"] * layout["sheet_height"] * layout["sheets"]
        videos.append({
            "id": video_id, "title": title, "fps": args.fps if args.fps != int(args.fps) else int(args.fps),
            "frame_count": layout["frame_count"], "width": args.width, "height": args.height,
            "sheet_width": layout["sheet_width"], "sheet_height": layout["sheet_height"],
            "cols": layout["cols"], "rows": layout["rows"], "cells_per_sheet": layout["cells_per_sheet"],
            "sheets": layout["sheets"], "gutter": args.gutter,
            "screen_blocks": screen_blocks(args.width, args.height, args.screen_width),
            "texture_paths": [f"textures/bvp_image/{video_id}/sheet_{n}" for n in range(layout["sheets"])],
            "audio_chunks": 0, "audio_chunk_seconds": args.audio_chunk_seconds, "methods": methods,
            "memory": {"texels": texels, "bytes": texels * 4},
        })
        log(f"[{video_id}] {layout['frame_count']} コマ -> シート {layout['sheets']} 枚 "
            f"({layout['sheet_width']}x{layout['sheet_height']}, {layout['cols']}x{layout['rows']} マス)")

    # 2. メモリの見積もり (全動画の合計。予算は 1 つのアドオンに対する値)
    layouts = [{"sheet_width": v["sheet_width"], "sheet_height": v["sheet_height"], "sheets": v["sheets"]}
               for v in videos]
    memory = atlas.memory_report(layouts, allow_over_budget=args.allow_over_budget)
    log(memory["table"])
    if memory["errors"]:
        raise ConvertError("テクスチャが予算を超えるので作りません (--allow-over-budget で作れます)。"
                         "コマの大きさ・fps・長さを減らしてください")

    # 3. 音声
    files: dict[str, bytes] = {}

    def put(path: str, content) -> None:
        files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)

    sound_definitions = {}
    with tempfile.TemporaryDirectory(prefix="bvp-image-") as temp:
        for video in videos:
            video_id = video["id"]
            path = inputs[ids.index(video_id)]
            chunks = atlas.extract_audio_chunks(path, Path(temp) / video_id, args.audio_chunk_seconds,
                                                args.start, args.end, ffmpeg)
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
            put(f"RP/textures/bvp_image/{video['id']}/sheet_{number}.png", png_bytes(sheet))
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

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.writestr(path, files[path])

    memory_summary = {k: v for k, v in memory.items() if k != "table"}
    report = {
        "namespace": namespace, "pack_name": pack_name, "output": str(output), "size_bytes": output.stat().st_size,
        "methods": methods, "videos": videos,
        "memory": {**memory_summary, "per_video": [{"id": v["id"], **v["memory"], "handles": v["sheets"]}
                                                    for v in videos]},
        "warnings": memory["warnings"],
        "lang_keys": {f"bvpimg.{key}": texts for key, texts in LANG_KEYS.items()},
        "files": sorted(files),
    }
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
    parser.add_argument("--method", default="both", choices=["a", "c", "both"],
                        help="a=アトラス+ボーンの切り替え (安全)、c=uv_anim の自作マテリアル (軽いが将来壊れうる)、both=両方 (既定)")
    parser.add_argument("--namespace", type=_namespace, default=DEFAULT_NAMESPACE,
                        help=f"名前空間 (小文字の英数字と _。既定 {DEFAULT_NAMESPACE})")
    parser.add_argument("--pack-name", default=None, help="パック名 (既定は最初の動画の表示名)")
    parser.add_argument("--audio-chunk-seconds", type=_positive_float, default=4, help="音声を区切る秒数 (既定 4)")
    parser.add_argument("--allow-over-budget", action="store_true",
                        help="テクスチャが Tier 5 の予算 (800 MB) を超えても作る")
    parser.add_argument("--report", default=None, help="レポートの JSON のパス (既定は出力の隣)")
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
