"""画像モード - 動画のコマをテクスチャ (atlas) にして、エンティティで再生するアドオン (.mcaddon) を作る CLI。

ブロックモード (convert.py / main.js) とは別のアドオンで、データ形式も共有しない。取り決めは image_mode/SPEC.md。
処理: ffmpeg でコマを取り出す → シート PNG に並べる → メモリを見積もる → 音声を切り出す → パックに組んで zip にする。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from image_mode.dedup import DEFAULT_NEAR_THRESHOLD, MODES as DEDUP_MODES, FrameDeduper, should_use, slot_intervals
from mvcodec.fileio import work_dir
from mvcodec.limits import MAX_ID_LENGTH, clean_title, pack_name_problem, shorten_id
from mvcodec.resources import use_utf8_for_pipes
from image_mode import atlas, budget, impostor
from gui_i18n import LOCALES, addon_lang_files
from image_mode.rp import LIGHTING_MODES, rp_files
from pack_builder import build_manifest_version, write_file_atomic
from pack_metadata import version_text

APP_DIR = Path(__file__).resolve().parent
IMAGE_MAIN_SCRIPT = APP_DIR / "image_mode" / "image_main.js"

DEFAULT_NAMESPACE = "bvpimg"
# 立体 (.vox) の既定の fps (ブロックモードの立体と同じ 10)
IMPOSTOR_DEFAULT_FPS = 10.0
# 角度で絵を切り替える立体の向きの数の上限 (5 度ごと)。向きの数だけ絵を描いてから予算で断っていた (2026-11-19 の監査 AUD2-07)
MAX_VIEWS = 72
# 骨で動かす人物 (MMD) の再生の時計の fps (コマの数え方。動きはアニメーションの補間でなめらか)。ゲームの 1 秒 20 tick
RIG_CLOCK_FPS = 20.0
MMD_NOTICE = ("MMD のモデル・踊り・曲には作者の利用規約があります。規約で許された範囲で使ってください "
              "(作ったアドオンを人に配れるかも規約しだいです)")
DEFAULT_PACK_NAME = "Block & Image Video Player (Image)"
MIN_ENGINE_VERSION = [1, 26, 10]
ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
NAMESPACE_PATTERN = re.compile(r"^[a-z0-9_]+$")
# 引数の上限 (#165・#182・#191)。1 コマ・シートの一辺 (画素)、1 枚のマスの数、スクリーンの幅 (ブロック)、音声の区切り (秒)
MAX_FRAME_SIDE = 4096
MAX_SHEET_SIDE = 4096
MAX_CELLS_LIMIT = 1024
MAX_SCREEN_BLOCKS = 256
MIN_AUDIO_CHUNK_SECONDS = 0.5
MAX_AUDIO_CHUNK_SECONDS = 60

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
    # 長いファイル名は切る (210 文字のファイル名がそのまま 210 文字の ID になっていた。#186)
    base = shorten_id(re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_") or "video")
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
                # 体 (板) の向きを頭の向きに常に合わせる。無いと setRotation の後、体が約 1 秒遅れて回り、
                # 再生の最初が横向きに映った (format_version 1.21.90 から。実機 2026-11-15)
                "minecraft:body_rotation_always_follows_head": {},
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


# シートの形式: png (既定、劣化なし) / jpeg (実写で小さくなる。v6.37.0 で PC の実機に映ることを確かめた)。
# テクスチャのパスは拡張子なしで書くので、ゲームはどちらの形式でも読む。メモリ (GPU) に置く量は形式で変わらない
SHEET_FORMATS = ("png", "jpeg")
SHEET_EXTENSIONS = {"png": "png", "jpeg": "jpg"}
# 品質 92: 実写 2 本 (Tears of Steel、512×288・24 fps・8 秒) で、PNG より PSNR −0.84 / −0.66 dB、パック −74% / −72%
DEFAULT_JPEG_QUALITY = 92


def sheet_bytes(image: Image.Image, sheet_format: str = "png", png_optimize: bool = False,
                jpeg_quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """シートの画像のバイト列。jpeg は 4:4:4 (色を間引かない。マスの縁で隣のマスの色が混ざりにくい)。"""
    return png_bytes(image, png_optimize) if sheet_format == "png" else _jpeg_bytes(image, jpeg_quality)


def _jpeg_bytes(image: Image.Image, jpeg_quality: int) -> bytes:
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=jpeg_quality, subsampling=0, optimize=True)
    return out.getvalue()


def sheet_file(image: Image.Image, sheet_format: str = "png", png_optimize: bool = False,
               jpeg_quality: int = DEFAULT_JPEG_QUALITY) -> tuple[str, bytes]:
    """シート 1 枚の (拡張子, バイト列)。jpeg を選んでも、PNG のほうが小さいシートは PNG にする: 図形や文字の多い映像は
    PNG のほうが小さく、劣化も無い (デモの映像で JPEG 1.6 MiB、PNG 0.8 MiB)。テクスチャのパスは拡張子なしなので、
    シートごとに形式が違ってよい。"""
    png = png_bytes(image, png_optimize)
    if sheet_format == "png":
        return SHEET_EXTENSIONS["png"], png
    jpeg = _jpeg_bytes(image, jpeg_quality)
    return (SHEET_EXTENSIONS["png"], png) if len(png) <= len(jpeg) else (SHEET_EXTENSIONS["jpeg"], jpeg)


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
                log=print, lighting: str = "bright", png_optimize: bool = False, sheet_format: str = "png",
                jpeg_quality: int = DEFAULT_JPEG_QUALITY, dedup: str = "off",
                near_threshold: float = DEFAULT_NEAR_THRESHOLD) -> tuple[dict, dict]:
    """コマから、アドオンのファイル (zip の中のパス → bytes) とレポートを作る。ffmpeg を使わない (ブラウザ版も使う)。

    sources の 1 つは {"id", "title", "fps", "frames": コマ (高さ×幅×3 の uint8) を出す iterable,
    "audio": (最長の秒数) → [(番号, 拡張子, bytes)] を返す関数 (無ければ音声なし)}。
    予算超過などで作れないときは ConvertError。
    lighting は rp.LIGHTING_MODES ("bright" は周りの光で暗くならない)。png_optimize はシートの PNG の最適化。
    sheet_format はシートの形式 (SHEET_FORMATS)、jpeg_quality は jpeg の品質 (1〜95)。
    dedup は同じ絵のコマを 1 マスにまとめるか (image_mode/dedup.py の MODES。既定 off は今までと同じ出力)。
    """
    if lighting not in LIGHTING_MODES:
        raise ConvertError(f"lighting は {', '.join(LIGHTING_MODES)} のどれか: {lighting}")
    used: set[str] = set()
    for source in sources:
        if not ID_PATTERN.match(source["id"]):
            raise ConvertError(f"動画 ID は小文字の英数字と _ だけにしてください: {source['id']}")
        if len(source["id"]) > MAX_ID_LENGTH:
            raise ConvertError(f"動画 ID は {MAX_ID_LENGTH} 文字以内にしてください: {source['id']}")
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

        views = int(source.get("views") or 1)
        if views > 1 and dedup != "off":
            raise ConvertError(f"[{video_id}] 角度で絵を切り替える立体 (.vox) は、同じ絵のコマをまとめる (--dedup) と一緒に使えません")
        if views > 1 and sheet_format != "png":
            raise ConvertError(f"[{video_id}] 角度で絵を切り替える立体 (.vox) は背景を透かすので、シートは PNG だけです")
        deduper = FrameDeduper(dedup, near_threshold) if dedup != "off" else None
        try:
            sheets, layout = atlas.pack_sheets(deduper(frames()) if deduper else frames(), width, height, max_sheet,
                                               gutter, max_cells)
            frame_count = len(deduper.slots) if deduper else layout["frame_count"]
            slot_map = None
            if deduper:
                intervals = slot_intervals(deduper.slots)
                if should_use(frame_count, layout["frame_count"], intervals):
                    slot_map = [list(interval) for interval in intervals]
                    log(f"[{video_id}] 同じ絵のコマをまとめました ({dedup}): {frame_count} コマ -> {layout['frame_count']} マス "
                        f"(区間 {len(intervals)})")
                else:
                    # まとめる価値が無い (省けるマスが少ない・式が長すぎる): 元のコマの順に並べ直す
                    log(f"[{video_id}] 同じ絵のコマはまとめません ({frame_count} コマ -> {layout['frame_count']} マス、"
                        f"区間 {len(intervals)})")
                    sheets, layout = atlas.pack_sheets(atlas.cells_in_order(sheets, layout, deduper.slots), width, height,
                                                       max_sheet, gutter, max_cells)
        except ValueError as error:
            raise ConvertError(f"[{video_id}] {error}") from None
        if icon is None and first_frame:
            icon = pack_icon(first_frame[0][..., :3])
        if views > 1:
            # マス = コマ × 向き。メタデータの frame_count は時間のコマ数
            frame_count = layout["frame_count"] // views
        sheets_of[video_id] = sheets
        texels = layout["sheet_width"] * layout["sheet_height"] * layout["sheets"]
        videos.append({
            "id": video_id, "title": title, "fps": fps if fps != int(fps) else int(fps),
            "frame_count": frame_count, "width": width, "height": height,
            "sheet_width": layout["sheet_width"], "sheet_height": layout["sheet_height"],
            "cols": layout["cols"], "rows": layout["rows"], "cells_per_sheet": layout["cells_per_sheet"],
            "sheets": layout["sheets"], "gutter": gutter,
            "screen_blocks": source.get("screen_blocks") or screen_blocks(width, height, screen_width),
            "texture_paths": [f"textures/bvp_image/{video_id}/sheet_{n}" for n in range(layout["sheets"])],
            "audio_chunks": 0, "audio_chunk_seconds": audio_chunk_seconds, "methods": methods,
            "lighting": lighting,
            "memory": {"texels": texels, "bytes": texels * 4},
            # まとめたときだけ: マスの数と、コマ → マスの区間 (rp.pre_animation が使う)。無ければコマ = マス
            **({"cells": layout["frame_count"], "slot_map": slot_map} if slot_map else {}),
            # 角度で絵を切り替える立体 (image_mode/impostor.py): 向きの数と、終わりで頭へ戻すこと
            **({"views": views, "loop": True, "cells": layout["frame_count"]} if views > 1 else {}),
        })
        log(f"[{video_id}] {frame_count} コマ -> シート {layout['sheets']} 枚 "
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
    sheet_kinds = {}
    for video in videos:
        for number, sheet in enumerate(sheets_of[video["id"]]):
            extension, data = sheet_file(sheet, sheet_format, png_optimize, jpeg_quality)
            sheet_kinds[extension] = sheet_kinds.get(extension, 0) + 1
            put(f"RP/textures/bvp_image/{video['id']}/sheet_{number}.{extension}", data)
    for relative, content in rp_files(videos, namespace).items():
        put(f"RP/{relative}", content)
    _common_files(put, videos, namespace, pack_name, icon, sound_definitions)

    if sheet_format == "jpeg" and sheet_kinds.get("png"):
        log(f"PNG のほうが小さいシート {sheet_kinds['png']} 枚は PNG にしました (JPEG {sheet_kinds.get('jpg', 0)} 枚)")
    memory_summary = {k: v for k, v in memory.items() if k != "table"}
    report = {
        "namespace": namespace, "pack_name": pack_name, "methods": methods, "videos": videos,
        "memory": {**memory_summary, "per_video": [{"id": v["id"], **v["memory"], "handles": v["sheets"]}
                                                    for v in videos]},
        "warnings": memory["warnings"],
        # sheet_files は jpeg を選んだときの、実際の形式ごとのシートの数 (PNG のほうが小さいシートは PNG にする)
        "sheet_format": sheet_format, **({"jpeg_quality": jpeg_quality, "sheet_files": sheet_kinds} if sheet_format == "jpeg" else {}),
        "lang_keys": {"en": lang_keys("en"), "ja": lang_keys("ja")},
        "files": sorted(files),
    }
    return files, report


def _common_files(put, videos: list[dict], namespace: str, pack_name: str, icon: bytes | None,
                  sound_definitions: dict) -> None:
    """どのアドオンにも入るファイル: BP のエンティティ・音の定義・lang・マニフェスト・スクリプト・アイコン。"""
    for video in videos:
        for method in video["methods"]:
            put(f"BP/entities/screen_{video['id']}_{method}.json",
                json.dumps(entity_json(video, method, namespace), ensure_ascii=False, indent=2) + "\n")
    if sound_definitions:
        put("RP/sounds/sound_definitions.json", json.dumps(
            {"format_version": "1.14.0", "sound_definitions": sound_definitions}, ensure_ascii=False, indent=2) + "\n")
    for relative, content in lang_files().items():
        put(f"RP/{relative}", content)
    bp_manifest, rp_manifest = manifests(namespace, pack_name)
    put("BP/manifest.json", json.dumps(bp_manifest, ensure_ascii=False, indent=2) + "\n")
    put("RP/manifest.json", json.dumps(rp_manifest, ensure_ascii=False, indent=2) + "\n")
    put("BP/scripts/image_videos.js", image_videos_js(videos, namespace))
    # 文字として読んで LF にそろえる (ブロックモードの main.js と同じ)。バイトのまま入れると、Windows の
    # core.autocrlf で取り出した場所では CRLF になり、同じ版でもアドオンの中身が違っていた (2026-11-19 の監査 AUD2-10)
    put("BP/scripts/image_main.js", IMAGE_MAIN_SCRIPT.read_text(encoding="utf-8"))
    if icon is not None:
        put("RP/pack_icon.png", icon)
        put("BP/pack_icon.png", icon)


def _sound_definition(namespace: str, video_id: str, index: int) -> tuple[str, dict]:
    return f"{namespace}.img.{video_id}.chunk_{index}", {
        "category": "ui",
        "sounds": [{"name": f"sounds/bvp_image/{video_id}/chunk_{index}", "stream": True, "load_on_low_memory": True}],
    }


def _figure_icon(sheet: Image.Image, size: int = 256) -> bytes:
    """人物のシートの左上 (最初のコマの板が並ぶ所) を黒地に置いたアイコン。"""
    image = sheet.copy()
    image.thumbnail((size, size), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (size, size))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2), image)
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def build_glass_files(entries: list[dict], *, namespace: str, pack_name: str, max_sheet: int = 2048, gutter: int = 1,
                      max_cells: int = atlas.MAX_CELLS, audio_chunk_seconds: float = 4, allow_over_budget: bool = False,
                      log=print, lighting: str = "bright", png_optimize: bool = False, sheet_format: str = "png",
                      jpeg_quality: int = DEFAULT_JPEG_QUALITY, mapping=None) -> tuple[dict, dict]:
    """ガラスケースの立体 (docs/research/glass_case.md) のアドオンのファイルとレポート。ffmpeg を使わない。

    entries の 1 つは {"id", "title", "figure": glass.figure_from_volumes の戻り値, "figure_fps", "figure_loop",
    "case": glass.CaseOptions か None, "wall_video": {"frames", "width", "height", "fps"} か None,
    "audio": (最長の秒数) → [(番号, 拡張子, bytes)] か None (壁の動画か、浮き彫りにした動画の音声)}。
    主の時計は、壁の動画があればその動画、無ければ人物 (figure_loop なら繰り返し、そうでなければ最後で止まる)。

    骨で動かす人物 (image_mode/rig.py) は、figure に "rig": {"seconds", "animation" (アニメーションの JSON の文字列)}
    がある。人物が主のときは、figure_fps の時計で seconds 秒 (曲があれば曲の長さ) を数える。"""
    from image_mode import glass, glass_rp, planes

    mapping = mapping or planes.DEFAULT_MAPPING
    if lighting not in LIGHTING_MODES:
        raise ConvertError(f"lighting は {', '.join(LIGHTING_MODES)} のどれか: {lighting}")
    files: dict[str, bytes] = {}

    def put(path: str, content) -> None:
        files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)

    videos, layouts, warnings = [], [], []
    sound_definitions: dict = {}
    icon = None
    rp_texts: dict[str, str] = {}
    for entry in entries:
        video_id = entry["id"]
        if not ID_PATTERN.match(video_id):
            raise ConvertError(f"動画 ID は小文字の英数字と _ だけにしてください: {video_id}")
        if len(video_id) > MAX_ID_LENGTH:
            raise ConvertError(f"動画 ID は {MAX_ID_LENGTH} 文字以内にしてください: {video_id}")
        if entry.get("lighting") and entry["lighting"] not in LIGHTING_MODES:
            raise ConvertError(f"lighting は {', '.join(LIGHTING_MODES)} のどれか: {entry['lighting']}")
        figure = entry["figure"]
        first_layout = len(layouts)
        box = glass.case_box(figure["size_blocks"], entry.get("case"))
        log(f"[{video_id}] 人物: {figure['frames']} コマ、板 {figure['quads']} 枚、シート {len(figure['sheets'])} 枚 "
            f"({figure['sheet_width']}x{figure['sheet_height']}、1 枚 {figure['per_sheet']} コマ)、"
            f"大きさ {box[0]:g}x{box[1]:g}x{box[2]:g} ブロック")
        if figure["quads"] > glass.QUAD_ERROR and not allow_over_budget:
            raise ConvertError(f"[{video_id}] 板が {figure['quads']} 枚で多すぎます (上限 {glass.QUAD_ERROR})。"
                               "コマ数・立体の細かさを減らすか、--allow-over-budget で作ってください")
        if figure["quads"] > glass.QUAD_WARNING:
            warnings.append(f"[{video_id}] 板が {figure['quads']} 枚あります。重くなるかもしれません "
                            "(--plane-split rect で減る。コマ数・細かさを減らしても減る)")
        rig_info = figure.get("rig")
        if rig_info:
            log(f"[{video_id}] 骨で動かす人物: geometry のボーン {rig_info['bones']} 本、踊り {rig_info['seconds']:g} 秒、"
                f"キー {rig_info['keys']} 個 (アニメーション {len(rig_info['animation']) / 1024 / 1024:.2f} MiB)")
        fig_paths = [f"textures/bvp_image/{video_id}/fig_{n}" for n in range(len(figure["sheets"]))]
        for number, sheet in enumerate(figure["sheets"]):
            put(f"RP/{fig_paths[number]}.png", png_bytes(sheet, png_optimize))
        layouts.append({"sheet_width": figure["sheet_width"], "sheet_height": figure["sheet_height"],
                        "sheets": len(figure["sheets"])})
        if icon is None and figure["sheets"]:
            icon = _figure_icon(figure["sheets"][0])
        glass_meta = {
            "box": list(box),
            "master": "wall_video" if entry.get("wall_video") else "figure",
            "figure": {"frames": figure["frames"], "fps": _plain(entry["figure_fps"]), "loop": bool(entry["figure_loop"]),
                       "per_sheet": figure["per_sheet"], "sheets": len(figure["sheets"]),
                       "sheet_width": figure["sheet_width"], "sheet_height": figure["sheet_height"],
                       "texture_paths": fig_paths, "quads": figure["quads"],
                       **({"rig": {"animation": glass_rp.animation_identifier(video_id, namespace),
                                   "seconds": rig_info["seconds"], "bones": rig_info["bones"]}} if rig_info else {})},
        }
        case = entry.get("case")
        if case is not None:
            try:
                texture, uv, frame_texel = glass.case_texture(box, case, mapping, gutter)
            except ValueError as error:
                raise ConvertError(f"[{video_id}] {error}") from None
            path = f"textures/bvp_image/{video_id}/case"
            put(f"RP/{path}.png", png_bytes(texture, png_optimize))
            layouts.append({"sheet_width": texture.width, "sheet_height": texture.height, "sheets": 1})
            glass_meta["case"] = {"texture_path": path, "texture_size": [texture.width, texture.height], "uv": uv,
                                  "frame": bool(case.frame), **({"frame_texel": list(frame_texel)} if frame_texel else {})}
        wall = entry.get("wall_video")
        width, height = figure["sheet_width"], figure["sheet_height"]
        if wall is not None:
            if case is None:
                raise ConvertError(f"[{video_id}] 壁の動画 (--case-video) はケース (--case) と一緒に使います")
            try:
                sheets, layout = atlas.pack_sheets(wall["frames"], wall["width"], wall["height"], max_sheet, gutter,
                                                   max_cells)
            except ValueError as error:
                raise ConvertError(f"[{video_id}] {error}") from None
            vid_paths = [f"textures/bvp_image/{video_id}/sheet_{n}" for n in range(layout["sheets"])]
            for number, sheet in enumerate(sheets):
                extension, data = sheet_file(sheet, sheet_format, png_optimize, jpeg_quality)
                put(f"RP/{vid_paths[number]}.{extension}", data)
            layouts.append({"sheet_width": layout["sheet_width"], "sheet_height": layout["sheet_height"],
                            "sheets": layout["sheets"]})
            glass_meta["wall_video"] = {
                "frame_count": layout["frame_count"], "fps": _plain(wall["fps"]), "width": wall["width"],
                "height": wall["height"], "cols": layout["cols"], "rows": layout["rows"],
                "cells_per_sheet": layout["cells_per_sheet"], "sheets": layout["sheets"], "gutter": gutter,
                "sheet_width": layout["sheet_width"], "sheet_height": layout["sheet_height"], "texture_paths": vid_paths,
                "plane": glass.wall_plane(box, wall["width"], wall["height"]),
            }
            width, height = wall["width"], wall["height"]
            fps, frame_count, loop = wall["fps"], layout["frame_count"], False
            log(f"[{video_id}] 壁の動画: {frame_count} コマ -> シート {layout['sheets']} 枚")
        elif rig_info:
            fps, loop = entry["figure_fps"], bool(entry["figure_loop"])
            frame_count = max(1, int(round(rig_info["seconds"] * fps)) + 1)
        else:
            fps, frame_count, loop = entry["figure_fps"], figure["frames"], bool(entry["figure_loop"])
        texels = sum(item["sheet_width"] * item["sheet_height"] * item["sheets"] for item in layouts[first_layout:])
        video = {
            "id": video_id, "title": entry["title"], "fps": _plain(fps), "frame_count": frame_count,
            "width": width, "height": height, "screen_blocks": [box[0], box[2]],
            "audio_chunks": 0, "audio_chunk_seconds": audio_chunk_seconds, "methods": ["a"],
            "lighting": entry.get("lighting") or lighting,
            "memory": {"texels": texels, "bytes": texels * 4},
            **({"loop": True} if loop else {}),
            "glass": glass_meta,
        }
        chunks = entry["audio"](frame_count / fps) if entry.get("audio") else []
        video["audio_chunks"] = len(chunks)
        for index, ext, data in chunks:
            put(f"RP/sounds/bvp_image/{video_id}/chunk_{index}.{ext}", data)
            key, definition = _sound_definition(namespace, video_id, index)
            sound_definitions[key] = definition
        log(f"[{video_id}] 音声 {len(chunks)} チャンク" + ("" if chunks else " (音声なし)"))
        for relative, content in glass_rp.glass_rp_files(video, namespace, figure["bones"], mapping,
                                                         rig_info["animation"] if rig_info else None).items():
            rp_texts[relative] = content
        videos.append(video)

    memory = atlas.memory_report(layouts, allow_over_budget=allow_over_budget)
    log(memory["table"])
    if memory["errors"]:
        raise ConvertError("テクスチャが予算を超えるので作りません (--allow-over-budget で作れます)。"
                           "コマ数・立体の細かさ・壁の動画の大きさを減らしてください")
    for relative, content in rp_texts.items():
        put(f"RP/{relative}", content)
    _common_files(put, videos, namespace, pack_name, icon, sound_definitions)
    memory_summary = {k: v for k, v in memory.items() if k != "table"}
    report = {
        "namespace": namespace, "pack_name": pack_name, "methods": ["a"], "videos": videos,
        "memory": {**memory_summary, "per_video": [{"id": v["id"], **v["memory"]} for v in videos]},
        "warnings": memory["warnings"] + warnings,
        "face_mapping": {"north_u": mapping.north_u, "east_u": mapping.east_u, "up_u": mapping.up_u, "up_v": mapping.up_v},
        "lang_keys": {"en": lang_keys("en"), "ja": lang_keys("ja")},
        "files": sorted(files), "layouts": layouts,
    }
    for warning in warnings:
        log(f"警告: {warning}")
    return files, report


def _plain(value):
    """30.0 のような値を 30 にする (JSON と Molang に余計な小数を出さない)。"""
    value = float(value)
    return int(value) if value == int(value) else value


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
    # 書き終えてから置き換える (output を省くとブロックモードのファイルを上書きするので、途中で失敗してもそれを壊さない)
    write_file_atomic(output, zip_bytes(files))
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


def apply_priority(args: argparse.Namespace, inputs: list[Path], log=print, reserved_bytes: int = 0) -> list:
    """--priority: 予算 (--target-tier) に収まるよう args の大きさ・fps を直し、動画ごとの終わり (秒) を返す。
    reserved_bytes は、同じアドオンに入るほかのもの (ガラスケースの人物) が先に使うテクスチャ。"""
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
                         source_size=source_size, reserved_bytes=reserved_bytes)
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


def build(args: argparse.Namespace, log=print, figures: list[dict] | None = None) -> dict:
    """引数どおりにアドオンを作り、レポート (dict) を返す。予算超過などで作れないときは ConvertError。

    figures は、同じアドオンに入れる骨で動かす MMD の人物 (_build_with_figures。Windows 版の画面が使う)。"""
    inputs = [Path(p) for p in args.inputs]
    if not inputs and not figures:
        raise ConvertError("入力がありません")
    for path in inputs:
        if not path.is_file():
            raise ConvertError(f"入力動画が見つかりません: {path}")
    used: set[str] = set()
    ids = []
    for index, path in enumerate(inputs):
        video_id = args.ids[index] if args.ids else default_id(path, used)
        if not ID_PATTERN.match(video_id):
            raise ConvertError(f"動画 ID は小文字の英数字と _ だけにしてください: {video_id}")
        if len(video_id) > MAX_ID_LENGTH:
            raise ConvertError(f"動画 ID は {MAX_ID_LENGTH} 文字以内にしてください: {video_id}")
        if video_id in used:
            raise ConvertError(f"動画 ID が重なっています: {video_id}")
        used.add(video_id)
        ids.append(video_id)
    titles = [clean_title(title) for title in (args.titles or [p.stem for p in inputs])]
    if figures:
        return _build_with_figures(args, inputs, ids, titles, figures, log)
    if wants_glass(args):
        return _build_glass(args, inputs, ids, titles, log)
    voxes = [path.suffix.lower() == ".vox" for path in inputs]
    if any(voxes):
        if not all(voxes):
            raise ConvertError("立体 (.vox) と動画は同じアドオンに入れられません (試作)。別々に作ってください")
        return _build_impostor(args, inputs, ids, titles, log)
    if args.fps is None:
        args.fps = 30.0
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

    with work_dir("bvp-image-") as temp:
        sources = [{
            "id": video_id, "title": title, "fps": args.fps,
            "frames": atlas.read_frames(path, args.width, args.height, args.fps, args.start, end,
                                        args.fit, ffmpeg, warn=log),
            "audio": (lambda seconds, path=path, video_id=video_id, end=end: atlas.extract_audio_chunks(
                path, Path(temp) / video_id, args.audio_chunk_seconds, args.start, end, ffmpeg,
                max_seconds=seconds, warn=log)),
        } for path, video_id, title, end in zip(inputs, ids, titles, ends)]
        files, report = build_files(
            sources, width=args.width, height=args.height, methods=methods, namespace=args.namespace,
            pack_name=pack_name, max_sheet=args.max_sheet, gutter=args.gutter, max_cells=args.max_cells,
            screen_width=args.screen_width, audio_chunk_seconds=args.audio_chunk_seconds,
            allow_over_budget=args.allow_over_budget, log=log, lighting=args.lighting, png_optimize=args.png_optimize,
            sheet_format=getattr(args, "sheet_format", "png"), jpeg_quality=getattr(args, "jpeg_quality", DEFAULT_JPEG_QUALITY),
            dedup=getattr(args, "dedup", "off"), near_threshold=getattr(args, "near_threshold", DEFAULT_NEAR_THRESHOLD))

    return _write_output(args, files, report, log)


def _build_impostor(args: argparse.Namespace, inputs: list[Path], ids: list[str], titles: list[str], log=print) -> dict:
    """立体 (.vox) から、角度で絵を切り替える立体 (image_mode/impostor.py) のアドオンを作る。音声なし・繰り返し再生。"""
    from mvcodec.volume import load_vox_frames_with_palettes

    if args.priority != "none":
        raise ConvertError("立体 (.vox) では --priority を使えません (試作)")
    views = args.views
    fps = args.fps if args.fps is not None else IMPOSTOR_DEFAULT_FPS
    loaded = []
    for path in inputs:
        try:
            frames, palettes = load_vox_frames_with_palettes(path)
        except (OSError, ValueError) as error:
            raise ConvertError(f"立体を読めませんでした: {path}: {error}") from None
        loaded.append(impostor.dense_frames(frames, palettes))
    # 1 マスの高さは --height。幅は、全部の立体のうち一番広いものに合わせる (どの向きでも入るように)
    sizes = [impostor.cell_size(shape, args.height) for _volumes, _rgbs, shape in loaded]
    width = max(size[0] for size in sizes)
    height = args.height
    log(f"立体を {views} 方向から描きます: 1 マス {width}x{height}、{fps:g} fps、高さ {args.figure_blocks:g} ブロック")
    sources = [{
        "id": video_id, "title": title, "fps": fps, "views": views,
        "screen_blocks": impostor.screen_blocks(width, height, args.figure_blocks),
        "frames": impostor.render_cells(volumes, rgbs, shape, views, width, height, scale),
    } for (volumes, rgbs, shape), (_w, _h, scale), video_id, title in zip(loaded, sizes, ids, titles)]
    files, report = build_files(
        sources, width=width, height=height, methods=["a"], namespace=args.namespace,
        pack_name=args.pack_name or titles[0], max_sheet=args.max_sheet, gutter=args.gutter, max_cells=args.max_cells,
        allow_over_budget=args.allow_over_budget, log=log, lighting=args.lighting, png_optimize=args.png_optimize)
    return _write_output(args, files, report, log)


GLASS_ONLY_OPTIONS = ("case", "case_image", "case_video", "case_frame", "lift", "motion", "audio")


def wants_glass(args: argparse.Namespace) -> bool:
    """ガラスケースの立体 (面の板) を作るか。--solid planes か、ケース・浮き彫り・MMD のオプションか、MMD のモデル
    (.pmx) の入力があるとき。"""
    used = [name for name in GLASS_ONLY_OPTIONS if getattr(args, name, None)]
    mmd_input = any(Path(p).suffix.lower() == ".pmx" for p in getattr(args, "inputs", []))
    solid = getattr(args, "solid", None)
    if solid == "views" and (used or mmd_input):
        raise ConvertError("--case・--case-image・--case-video・--case-frame・--lift・--motion・--audio と MMD のモデル "
                           "(.pmx) は --solid planes (面の板) で使います")
    return solid == "planes" or (solid is None and (bool(used) or mmd_input))


def _read_limited(frames, limit: int, what: str) -> list:
    """コマを limit 枚まで読む (多すぎる長い動画を全部読む前に止める)。"""
    out = []
    for frame in frames:
        out.append(frame)
        if len(out) > limit:
            raise ConvertError(f"{what}のコマが {limit} 枚を超えます (--max-figure-frames)。"
                               "--fps を下げるか、--start / --end で短くしてください")
    return out


def _build_glass(args: argparse.Namespace, inputs: list[Path], ids: list[str], titles: list[str], log=print) -> dict:
    """ガラスケースの立体 (docs/research/glass_case.md) のアドオンを作る。人物は .vox か、動画の浮き彫り (--lift)。"""
    from image_mode import glass, planes
    from mvcodec.volume import load_vox_frames_with_palettes

    if args.priority != "none" and not args.case_video:
        raise ConvertError("ガラスケースの立体で --priority を使えるのは、奥の壁の動画 (--case-video) があるときだけです "
                           "(壁の動画の大きさと fps を予算に合わせる)")
    if getattr(args, "dedup", "off") != "off":
        raise ConvertError("ガラスケースの立体では --dedup を使えません")
    mmd_inputs = [path for path in inputs if path.suffix.lower() == ".pmx"]
    if (args.case_video or args.lift or mmd_inputs or args.audio) and len(inputs) != 1:
        raise ConvertError("--case-video・--lift・--audio と MMD のモデル (.pmx) のときは、入力を 1 つだけにしてください")
    if (args.motion or args.audio) and not mmd_inputs:
        raise ConvertError("--motion・--audio は MMD のモデル (.pmx) を入力にしたときに使います")
    if mmd_inputs and (args.start is not None or args.end is not None) and not args.case_video:
        raise ConvertError("MMD の人物では --start・--end を使えません (踊りと曲は最初から最後まで使います)")
    for extra in (args.motion, args.audio):
        if extra and not Path(extra).is_file():
            raise ConvertError(f"ファイルが見つかりません: {extra}")
    mapping = args.face_mapping or planes.DEFAULT_MAPPING
    try:
        images = glass.parse_case_images(args.case_image)
    except ValueError as error:
        raise ConvertError(str(error)) from None
    case = None
    if args.case or images or args.case_video or args.case_frame:
        case = glass.CaseOptions(margin=args.case_margin, images=images, frame=args.case_frame)
    figure_fps = args.fps if args.fps is not None else IMPOSTOR_DEFAULT_FPS
    wall_path = Path(args.case_video) if args.case_video else None
    if wall_path is not None and not wall_path.is_file():
        raise ConvertError(f"壁の動画が見つかりません: {wall_path}")
    for extra in (args.depth_video, args.mask_video):
        if extra and not Path(extra).is_file():
            raise ConvertError(f"動画が見つかりません: {extra}")
    with work_dir("bvp-glass-") as temp:
        entries = []
        for path, video_id, title in zip(inputs, ids, titles):
            audio = None
            figure = None
            if path.suffix.lower() == ".pmx":
                if args.lift:
                    raise ConvertError("--lift は動画を浮き彫りにするオプションです (MMD のモデルはそのまま人物になります)")
                mmd_entry = mmd_glass_entry(path, video_id, title, args, Path(temp), log, motion=args.motion,
                                            audio=args.audio, case=case)
                figure, audio = mmd_entry["figure"], mmd_entry["audio"]
                figure_fps, loop = mmd_entry["figure_fps"], mmd_entry["figure_loop"]
            elif path.suffix.lower() == ".vox":
                if args.lift:
                    raise ConvertError("--lift は動画を浮き彫りにするオプションです (.vox はそのまま立体になります)")
                try:
                    frames, palettes = load_vox_frames_with_palettes(path)
                except (OSError, ValueError) as error:
                    raise ConvertError(f"立体を読めませんでした: {path}: {error}") from None
                volumes, rgbs, _shape = impostor.dense_frames(frames, palettes)
                if len(volumes) > args.max_figure_frames:
                    raise ConvertError(f"立体のコマが {len(volumes)} 枚で、上限 {args.max_figure_frames} を超えます "
                                       "(--max-figure-frames)")
                loop = True
                log(f"[{video_id}] 立体 {len(volumes)} コマを面の板にします ({args.plane_split})")
            else:
                if not args.lift:
                    raise ConvertError("動画から人物を作るには --lift relief|extrude|inflate を付けてください "
                                       "(普通の動画を奥の壁で流すだけなら --case-video)")
                lift_w, lift_h = args.lift_size
                log(f"[{video_id}] 動画を浮き彫り ({args.lift}、{lift_w}x{lift_h}、奥行き {args.depth}) にします")
                frames = _read_limited(atlas.read_frames(path, lift_w, lift_h, figure_fps, args.start, args.end,
                                                         args.fit, args.ffmpeg), args.max_figure_frames, "浮き彫りにする動画")
                if not frames:
                    raise ConvertError(f"動画からコマを読めませんでした: {path}")
                heights = None
                extra = args.depth_video or args.mask_video
                if extra:
                    heights = glass.luminance_frames(_read_limited(
                        atlas.read_frames(Path(extra), lift_w, lift_h, figure_fps, args.start, args.end, args.fit,
                                          args.ffmpeg), args.max_figure_frames, "深度・切り抜きの動画"))
                volumes, rgbs = glass.lift_frames(frames, args.lift, args.depth, heights, args.lift_threshold,
                                                  args.lift_invert)
                loop = False
                if wall_path is None:
                    audio = (lambda seconds, path=path, video_id=video_id: atlas.extract_audio_chunks(
                        path, Path(temp) / video_id, args.audio_chunk_seconds, args.start, args.end, args.ffmpeg,
                        max_seconds=seconds))
            if figure is None:
                try:
                    figure = glass.figure_from_volumes(volumes, rgbs, figure_blocks=args.figure_blocks,
                                                       split=args.plane_split, shade=args.plane_shade, mapping=mapping,
                                                       max_sheet=args.max_sheet, gutter=args.gutter)
                except ValueError as error:
                    raise ConvertError(f"[{video_id}] {error}") from None
            wall = None
            if wall_path is not None:
                wall_w, wall_h, wall_fps, wall_end = args.width, args.height, args.case_video_fps, args.end
                if args.priority != "none":
                    wall_w, wall_h, wall_fps, wall_end = _plan_wall_video(args, wall_path, figure, case, mapping, log)
                wall = {"frames": atlas.read_frames(wall_path, wall_w, wall_h, wall_fps, args.start,
                                                    wall_end, args.fit, args.ffmpeg),
                        "width": wall_w, "height": wall_h, "fps": wall_fps}
                if args.audio:
                    log(f"[{video_id}] 曲 (--audio) を鳴らします (壁の動画の音声の代わり。長さは壁の動画に合わせます)")
                else:
                    audio = (lambda seconds, video_id=video_id, end=wall_end: atlas.extract_audio_chunks(
                        wall_path, Path(temp) / f"{video_id}_wall", args.audio_chunk_seconds, args.start, end,
                        args.ffmpeg, max_seconds=seconds))
            entries.append({"id": video_id, "title": title, "figure": figure, "figure_fps": figure_fps,
                            "figure_loop": loop, "case": case, "wall_video": wall, "audio": audio})
        files, report = build_glass_files(
            entries, namespace=args.namespace, pack_name=args.pack_name or titles[0], max_sheet=args.max_sheet,
            gutter=args.gutter, max_cells=args.max_cells, audio_chunk_seconds=args.audio_chunk_seconds,
            allow_over_budget=args.allow_over_budget, log=log, lighting=args.lighting, png_optimize=args.png_optimize,
            sheet_format=args.sheet_format, jpeg_quality=args.jpeg_quality, mapping=mapping)
    return _write_output(args, files, report, log)


def mmd_glass_entry(path: Path, video_id: str, title: str, args: argparse.Namespace, temp: Path, log=print, *,
                    motion=None, audio=None, case=None) -> dict:
    """MMD のモデル (.pmx) から、骨で動かす人物の build_glass_files の entry を作る。motion は踊り (.vmd)、audio は曲
    (再生の長さになる)、case は glass.CaseOptions か None。細かさなどは args (--figure-voxels など) のもの。"""
    from image_mode import glass_rp, planes, rig_anim

    log(MMD_NOTICE)
    seconds = None
    chunks = None
    if audio:
        seconds = atlas.probe_duration(audio, args.ffmpeg)
        if seconds is None:
            raise ConvertError(f"曲の長さを読めませんでした: {audio}")
        chunks = (lambda limit: atlas.extract_audio_chunks(
            audio, temp / f"{video_id}_song", args.audio_chunk_seconds, None, None, args.ffmpeg, max_seconds=limit))
    elif not motion:
        log(f"[{video_id}] 踊り (.vmd) が無いので、動かない人物にします")
    log(f"[{video_id}] MMD の人物を骨で動かす板にします (高さ {args.figure_voxels} ボクセル、重ね代 {args.joint_overlap})")
    try:
        figure = rig_anim.mmd_figure(
            path, motion, identifier=glass_rp.animation_identifier(video_id, args.namespace),
            figure_blocks=args.figure_blocks, voxels=args.figure_voxels, overlap=args.joint_overlap, seconds=seconds,
            loop=args.figure_loop, split=args.plane_split, shade=args.plane_shade,
            mapping=args.face_mapping or planes.DEFAULT_MAPPING, max_sheet=args.max_sheet, gutter=args.gutter, log=log)
    except (OSError, ValueError) as error:
        raise ConvertError(f"[{video_id}] MMD の人物を作れませんでした: {error}") from None
    return {"id": video_id, "title": title, "figure": figure, "figure_fps": RIG_CLOCK_FPS,
            "figure_loop": bool(args.figure_loop), "case": case, "wall_video": None, "audio": chunks}


def merge_image_files(parts: list[tuple[dict, dict]], *, namespace: str, allow_over_budget: bool = False,
                      log=print) -> tuple[dict, dict]:
    """build_files と build_glass_files の出力 ((ファイル, レポート) のリスト) を 1 つのアドオンにまとめる。

    名前空間とパック名が同じなので、マニフェスト・スクリプト・lang は同じ中身。動画の一覧 (image_videos.js) と
    音の定義はまとめて書き直し、テクスチャの予算はアドオン全体で数え直す。アイコンは最初の出力のもの。"""
    files: dict[str, bytes] = {}
    videos: list[dict] = []
    sounds: dict = {}
    layouts: list[dict] = []
    warnings: list[str] = []
    for part_files, _report in reversed(parts):  # 最初の出力のファイル (アイコンなど) を最後に書いて残す
        files.update(part_files)
    for part_files, report in parts:
        for video in report["videos"]:
            if any(video["id"] == other["id"] for other in videos):
                raise ConvertError(f"動画 ID が重なっています: {video['id']}")
            videos.append(video)
        layouts += report.get("layouts") or [{"sheet_width": v["sheet_width"], "sheet_height": v["sheet_height"],
                                               "sheets": v["sheets"]} for v in report["videos"]]
        if "RP/sounds/sound_definitions.json" in part_files:
            sounds.update(json.loads(part_files["RP/sounds/sound_definitions.json"])["sound_definitions"])
        warnings += [w for w in report.get("warnings", []) if not w.startswith("テクスチャ")]
    memory = atlas.memory_report(layouts, allow_over_budget=allow_over_budget)
    log("アドオン全体: " + memory["table"])
    if memory["errors"]:
        raise ConvertError("動画とガラスケースの人物を合わせると、テクスチャが予算を超えるので作りません "
                           "(--allow-over-budget で作れます)")
    files["BP/scripts/image_videos.js"] = image_videos_js(videos, namespace).encode("utf-8")
    if sounds:
        files["RP/sounds/sound_definitions.json"] = (json.dumps(
            {"format_version": "1.14.0", "sound_definitions": sounds}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    first = parts[0][1]
    memory_summary = {k: v for k, v in memory.items() if k != "table"}
    report = {**first, "videos": videos, "methods": sorted({m for v in videos for m in v["methods"]}),
              "memory": {**memory_summary, "per_video": [{"id": v["id"], **v["memory"]} for v in videos]},
              "warnings": memory["warnings"] + warnings, "layouts": layouts, "files": sorted(files)}
    return files, report


def _build_with_figures(args: argparse.Namespace, inputs: list[Path], ids: list[str], titles: list[str],
                        figures: list[dict], log=print) -> dict:
    """普通の動画 (inputs) と、骨で動かす MMD の人物 (figures) を 1 つの画像モードのアドオンにする (Windows 版の画面)。

    figures の 1 つは {"id", "title", "pmx", "motion" (.vmd か None), "audio" (曲か None), "case_image" (奥の壁の絵か
    None)}。人物はケース付き。動画の予算 (--priority) は、人物とケースのテクスチャを引いた残りで決める。"""
    from image_mode import glass, planes

    for path in inputs:
        if path.suffix.lower() in (".vox", ".pmx"):
            raise ConvertError("MMD の人物と一緒に入れられるのは普通の動画だけです (.vox・.pmx は入れられません)")
    if getattr(args, "dedup", "off") != "off":
        raise ConvertError("MMD の人物と一緒のときは --dedup を使えません")
    if args.fps is None:
        args.fps = 30.0
    mapping = args.face_mapping or planes.DEFAULT_MAPPING
    used = set(ids)
    for figure in figures:
        if not ID_PATTERN.match(figure["id"]):
            raise ConvertError(f"動画 ID は小文字の英数字と _ だけにしてください: {figure['id']}")
        if figure["id"] in used:
            raise ConvertError(f"動画 ID が重なっています: {figure['id']}")
        used.add(figure["id"])
    pack_name = args.pack_name or (titles[0] if titles else figures[0]["title"])
    common = {"namespace": args.namespace, "pack_name": pack_name, "max_sheet": args.max_sheet, "gutter": args.gutter,
              "max_cells": args.max_cells, "audio_chunk_seconds": args.audio_chunk_seconds,
              "allow_over_budget": args.allow_over_budget, "log": log, "lighting": args.lighting,
              "png_optimize": args.png_optimize}
    with work_dir("bvp-image-") as temp:
        entries = []
        reserved = 0
        for figure in figures:
            images = {"back": figure["case_image"]} if figure.get("case_image") else {}
            case = glass.CaseOptions(margin=args.case_margin, images=images, frame=args.case_frame)
            entry = mmd_glass_entry(Path(figure["pmx"]), figure["id"], figure["title"], args, Path(temp), log,
                                    motion=figure.get("motion"), audio=figure.get("audio"), case=case)
            entries.append(entry)
            sheet = entry["figure"]
            reserved += sheet["sheet_width"] * sheet["sheet_height"] * len(sheet["sheets"]) * 4
            try:
                texture, _uv, _frame = glass.case_texture(glass.case_box(sheet["size_blocks"], case), case, mapping,
                                                          args.gutter)
            except ValueError as error:
                raise ConvertError(f"[{figure['id']}] {error}") from None
            reserved += texture.width * texture.height * 4
        parts = [build_glass_files(entries, mapping=mapping, **common)]
        if inputs:
            ends = [args.end] * len(inputs)
            if args.priority != "none":
                ends = apply_priority(args, inputs, log, reserved_bytes=reserved)
            sources = [{
                "id": video_id, "title": title, "fps": args.fps,
                "frames": atlas.read_frames(path, args.width, args.height, args.fps, args.start, end, args.fit,
                                            args.ffmpeg, warn=log),
                "audio": (lambda seconds, path=path, video_id=video_id, end=end: atlas.extract_audio_chunks(
                    path, Path(temp) / video_id, args.audio_chunk_seconds, args.start, end, args.ffmpeg,
                    max_seconds=seconds, warn=log)),
            } for path, video_id, title, end in zip(inputs, ids, titles, ends)]
            methods = ["a", "c"] if args.method == "both" else [args.method]
            parts.insert(0, build_files(sources, width=args.width, height=args.height, methods=methods,
                                        screen_width=args.screen_width,
                                        sheet_format=getattr(args, "sheet_format", "png"),
                                        jpeg_quality=getattr(args, "jpeg_quality", DEFAULT_JPEG_QUALITY), **common))
        files, report = merge_image_files(parts, namespace=args.namespace, allow_over_budget=args.allow_over_budget,
                                          log=log)
    return _write_output(args, files, report, log)


def _plan_wall_video(args: argparse.Namespace, wall_path: Path, figure: dict, case, mapping, log=print):
    """--priority: 人物とケースのテクスチャを除いた残りの予算 (--target-tier) に、奥の壁の動画が収まる
    (幅, 高さ, fps, 終わりの秒)。docs/research/glass_case_practical.md の 5.1。"""
    from image_mode import glass

    start = args.start or 0.0
    total = atlas.probe_duration(wall_path, args.ffmpeg)
    if total is None:
        raise ConvertError(f"壁の動画の長さを読めませんでした: {wall_path}")
    end = min(total, args.end) if args.end is not None else total
    reserved = figure["sheet_width"] * figure["sheet_height"] * len(figure["sheets"]) * 4
    if case is not None:
        try:
            texture, _uv, _frame = glass.case_texture(glass.case_box(figure["size_blocks"], case), case, mapping,
                                                      args.gutter)
        except ValueError as error:
            raise ConvertError(str(error)) from None
        reserved += texture.width * texture.height * 4
    result = budget.plan([max(0.0, end - start)], args.width, args.height, args.case_video_fps, args.priority,
                         args.target_tier, max_sheet=args.max_sheet, gutter=args.gutter, max_cells=args.max_cells,
                         reserved_bytes=reserved)
    if not result["fits"]:
        raise ConvertError(f"Tier {args.target_tier} の予算に、壁の動画がどうしても収まりません (人物とケースで "
                           f"{reserved / 1024 / 1024:.1f} MiB)")
    if result["changed"]:
        what = "・".join(CHANGED_LABELS[item] for item in result["changed"])
        log(f"{PRIORITY_LABELS[args.priority]}を優先して、人物とケース ({reserved / 1024 / 1024:.1f} MiB) を除いた "
            f"Tier {args.target_tier} の予算に壁の動画が収まるよう {what} を変えました: {result['width']}x{result['height']}・"
            f"{result['fps']:g} fps・{result['seconds'][0]:.1f} 秒")
    return result["width"], result["height"], result["fps"], start + result["seconds"][0]


def _write_output(args: argparse.Namespace, files: dict, report: dict, log=print) -> dict:
    output = Path(args.output)
    write_file_atomic(output, zip_bytes(files))
    report = {"namespace": report["namespace"], "pack_name": report["pack_name"], "output": str(output),
              "size_bytes": output.stat().st_size,
              "plan": getattr(args, "plan", None),
              **{key: value for key, value in report.items() if key not in ("namespace", "pack_name")}}
    report_path = Path(args.report) if args.report else output.with_suffix(".report.json")
    log(f"出力: {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")
    try:
        # アドオンと同じく、書き終えてから置き換える (書きかけのレポートを残さない)
        write_file_atomic(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as error:
        # アドオンはできている。レポートが書けないだけで「失敗」にしない (#192)
        log(f"注意: アドオンはできましたが、レポートを書けませんでした ({report_path}): {error}")
        return report
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


def _finite_float(text: str) -> float:
    """数 (nan・inf は断る。どの比べ方も通ってしまい、後で ffmpeg や予算の分かりにくいエラーになっていた。#190)。"""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("数を指定してください") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("有限の数を指定してください (nan・inf は使えません)")
    return value


def _positive_float(text: str) -> float:
    value = _finite_float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("0 より大きくしてください")
    return value


def _non_negative_float(text: str) -> float:
    value = _finite_float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("0 以上にしてください")
    return value


def _bounded_float(low: float, high: float, low_open: bool = False):
    """low〜high の数 (low_open なら low は含まない)。"""
    def parse(text: str) -> float:
        value = _finite_float(text)
        if value > high or value < low or (low_open and value == low):
            if low_open:
                raise argparse.ArgumentTypeError(f"{low:g} より大きく {high:g} 以下にしてください")
            raise argparse.ArgumentTypeError(f"{low:g}〜{high:g} にしてください")
        return value
    return parse


def _bounded_int(low: int, high: int):
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError("整数を指定してください") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{low}〜{high} にしてください")
        return value
    return parse


def _size(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", text)
    if not match or int(match.group(1)) < 2 or int(match.group(2)) < 2 or int(match.group(1)) > 512 or int(match.group(2)) > 512:
        raise argparse.ArgumentTypeError("幅x高さ (2〜512) で指定してください。例: 128x72")
    return int(match.group(1)), int(match.group(2))


def _face_mapping(text: str):
    from image_mode.planes import FaceMapping

    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4 or any(p not in ("1", "-1", "+1") for p in parts):
        raise argparse.ArgumentTypeError("1 か -1 を 4 つ、カンマで区切って指定してください。例: 1,1,-1,1")
    north_u, east_u, up_u, up_v = (int(p) for p in parts)
    return FaceMapping(north_u=north_u, east_u=east_u, up_u=up_u, up_v=up_v)


def _namespace(text: str) -> str:
    if not NAMESPACE_PATTERN.match(text) or text == "minecraft":
        raise argparse.ArgumentTypeError("名前空間は小文字の英数字と _ だけで、minecraft 以外にしてください")
    return text


def parse_args(argv=None, allow_no_inputs: bool = False) -> argparse.Namespace:
    """コマンドラインの引数。allow_no_inputs は、入力の動画を渡さない呼び出し (画面から MMD の人物だけを作るとき。
    -o が要る) のため。"""
    from image_mode.rig import MAX_VOXELS

    parser = argparse.ArgumentParser(
        description="画像モード: 動画のコマをテクスチャのシートにして、エンティティで再生するアドオン (.mcaddon) を作ります。"
                    "ブロックモード (convert.py) とは別のアドオンです。")
    parser.add_argument("inputs", nargs="*", metavar="VIDEO",
                        help="入力動画ファイル (複数可。1 本が 1 つの動画になる)。立体 (.vox) と MMD のモデル (.pmx) も使える")
    parser.add_argument("--output", "-o", default=None, help="出力の .mcaddon (既定は最初の動画と同じ場所の <名前>_image.mcaddon)")
    parser.add_argument("--ids", nargs="+", default=None, metavar="ID",
                        help="動画 ID ([a-z0-9_]+、動画と同じ数)。既定はファイル名から作る")
    parser.add_argument("--titles", nargs="+", default=None, metavar="TITLE", help="表示名 (動画と同じ数)。既定はファイル名")
    # 上限: 大きすぎる組み合わせで、予算を確かめる前に 3 GB のシートを作ろうとしていた (#182)
    parser.add_argument("--width", type=_bounded_int(1, MAX_FRAME_SIDE), default=256,
                        help=f"1 コマの横の画素数 (既定 256、{MAX_FRAME_SIDE} まで)")
    parser.add_argument("--height", type=_bounded_int(1, MAX_FRAME_SIDE), default=144,
                        help=f"1 コマの縦の画素数 (既定 144、{MAX_FRAME_SIDE} まで)")
    parser.add_argument("--fps", type=_fps, default=None, help="再生の fps (1〜60、既定は動画 30・立体 (.vox) 10)")
    parser.add_argument("--start", type=_non_negative_float, default=None, help="開始位置 (秒)")
    parser.add_argument("--end", type=_positive_float, default=None, help="終了位置 (秒)")
    parser.add_argument("--fit", default="pad", choices=["pad", "cover"],
                        help="縦横比の合わせ方。pad は全体を入れて余白を黒、cover は画面を埋めて中央で切る (既定 pad)")
    parser.add_argument("--max-cells", type=_bounded_int(1, MAX_CELLS_LIMIT), default=atlas.MAX_CELLS,
                        help=f"シート 1 枚のマスの数の上限 (既定 {atlas.MAX_CELLS}。方式 a のボーンの数になる)")
    parser.add_argument("--max-sheet", type=_bounded_int(1, MAX_SHEET_SIDE), default=2048,
                        help=f"シート 1 枚の一辺の最大画素数 (既定 2048、{MAX_SHEET_SIDE} まで)")
    parser.add_argument("--gutter", type=_non_negative_int, default=1,
                        help="マスの間の画素数。端の画素を繰り返して埋め、にじみを防ぐ (既定 1)")
    parser.add_argument("--screen-width", type=_bounded_float(0, MAX_SCREEN_BLOCKS, low_open=True), default=16,
                        help="スクリーンの幅 (ブロック数、既定 16)。高さは縦横比から決まる")
    parser.add_argument("--method", default="a", choices=["a", "c", "both"],
                        help="a=アトラス+ボーンの切り替え (安全)、c=uv_anim の自作マテリアル (軽いが将来壊れうる)、both=両方 (既定は a。実機の確認で a に決めた)")
    parser.add_argument("--namespace", type=_namespace, default=DEFAULT_NAMESPACE,
                        help=f"名前空間 (小文字の英数字と _。既定 {DEFAULT_NAMESPACE})")
    parser.add_argument("--pack-name", default=None, help="パック名 (既定は最初の動画の表示名)")
    # 小さすぎると音声のファイルが何万にもなり、大きすぎるとチャンクに分ける意味が無くなる (1e9 は ffmpeg が読めない。#191)
    parser.add_argument("--audio-chunk-seconds", type=_bounded_float(MIN_AUDIO_CHUNK_SECONDS, MAX_AUDIO_CHUNK_SECONDS), default=4,
                        help=f"音声を区切る秒数 (既定 4、{MIN_AUDIO_CHUNK_SECONDS:g}〜{MAX_AUDIO_CHUNK_SECONDS:g})")
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
    parser.add_argument("--sheet-format", default="png", choices=SHEET_FORMATS,
                        help="シートの形式。png (既定、劣化なし) / jpeg (実写でパックが小さくなる。アニメや文字は縁がにじみやすい。"
                             "スマートフォンで読めるかは未確認)")
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY,
                        help=f"--sheet-format jpeg の品質 (1〜95、既定 {DEFAULT_JPEG_QUALITY})")
    parser.add_argument("--dedup", default="off", choices=DEDUP_MODES,
                        help="同じ絵のコマを 1 マスにまとめてテクスチャを減らす。exact: まったく同じコマ (アニメの 2 コマ打ち・止め絵)、"
                             "near: すぐ前のコマとの差が --near-threshold 以下のものも。off (既定): まとめない "
                             "(Minecraft での確認がまだなので既定は off)")
    parser.add_argument("--near-threshold", type=_bounded_float(0, 255), default=DEFAULT_NEAR_THRESHOLD,
                        help=f"--dedup near で同じとみなす画素ごとの差の平均 (0〜255、既定 {DEFAULT_NEAR_THRESHOLD})")
    parser.add_argument("--views", type=_bounded_int(1, MAX_VIEWS), default=impostor.DEFAULT_VIEWS,
                        help=f"立体 (.vox) を何方向から描くか (試作。1〜{MAX_VIEWS}、既定 8 = 45 度ごと。"
                             "テクスチャは向きの数だけ増える)")
    parser.add_argument("--figure-blocks", type=_bounded_float(0, MAX_SCREEN_BLOCKS, low_open=True), default=6,
                        help="立体 (.vox) の高さ (ブロック数、既定 6)")
    glass_group = parser.add_argument_group(
        "ガラスケースの立体 (試作。docs/research/glass_case.md)",
        "立体を、ボクセルの外に出ている面の板にして置きます。どの向きから見ても本物の立方体と同じに見え、ケースの奥の壁に"
        "背景や動画を出せます。--case・--case-image・--case-video・--lift を付けると --solid planes になります")
    glass_group.add_argument("--solid", default=None, choices=["views", "planes"],
                             help="立体 (.vox) の見せ方。views=角度ごとの絵 (今までの試作)、planes=面の板。既定は views "
                                  "(ケース・浮き彫りのオプションがあれば planes)")
    glass_group.add_argument("--plane-split", default="rect", choices=["rect", "tile8", "tile16"],
                             help="面の板の分け方。rect=平面ごとに 1 枚 (板が少ない)、tile8・tile16=区画に分ける "
                                  "(透明な所が減り、テクスチャも減るが板が増える)。既定 rect")
    glass_group.add_argument("--plane-shade", action=argparse.BooleanOptionalAction, default=True,
                             help="面の向きの明るさ (上 1.0・横 0.8 / 0.6・下 0.5) を色に焼き込む (既定)。--no-plane-shade で付けない")
    glass_group.add_argument("--max-figure-frames", type=_positive_int, default=240,
                             help="人物のコマ数の上限 (既定 240。geometry はコマごとに板を持つ)")
    glass_group.add_argument("--case", action="store_true", help="人物の周りにケース (床と 4 枚の壁) を置く。手前の壁は透け、奥の壁が見える")
    glass_group.add_argument("--case-image", action="append", default=None, metavar="SIDE=PATH",
                             help="ケースの絵。SIDE は back・front・left・right・floor・all (何度でも指定できる)")
    glass_group.add_argument("--case-margin", type=_non_negative_float, default=0.5,
                             help="人物とケースの壁の間 (ブロック、既定 0.5)")
    glass_group.add_argument("--case-frame", action="store_true", help="ケースの 12 本の縁を描く")
    glass_group.add_argument("--case-video", default=None, metavar="VIDEO",
                             help="奥の壁で流す動画 (音声付き。大きさは --width・--height、範囲は --start・--end)")
    glass_group.add_argument("--case-video-fps", type=_fps, default=30.0, help="壁の動画の fps (既定 30)")
    glass_group.add_argument("--lift", default=None, choices=["relief", "extrude", "inflate"],
                             help="入力の動画を浮き彫りの立体にして人物にする (ブロックモードと同じ方法)。正面から斜めまでは立体、"
                                  "後ろからは平らに見える")
    glass_group.add_argument("--lift-size", type=_size, default=(128, 72), metavar="WxH",
                             help="浮き彫りにするときの動画の大きさ (画素 = ボクセル、既定 128x72)")
    glass_group.add_argument("--depth", type=int, default=16, help="浮き彫りの奥行き (ボクセル、2〜128、既定 16)")
    glass_group.add_argument("--depth-video", default=None,
                             help="relief の奥行きに使う深度動画 (明るいほど手前。scripts/make_depth_video.py で作れる)")
    glass_group.add_argument("--mask-video", default=None, help="extrude・inflate で立体にする範囲の動画 (明るい所)")
    glass_group.add_argument("--lift-threshold", type=_finite_float, default=0.5,
                             help="extrude・inflate で立体にする明るさのしきい値 (0〜1、既定 0.5)")
    glass_group.add_argument("--lift-invert", action="store_true", help="extrude・inflate で暗い所を立体にする")
    glass_group.add_argument("--motion", default=None, metavar="VMD",
                             help="MMD のモデル (.pmx) を踊らせるモーション (.vmd)。骨で動かすので、1 曲を通しても軽い")
    glass_group.add_argument("--audio", default=None, metavar="FILE",
                             help="MMD の人物と一緒に鳴らす曲 (wav・mp3 など)。曲の長さだけ再生し、踊りが短ければ最後の姿勢で止まる")
    glass_group.add_argument("--figure-voxels", type=_bounded_int(1, MAX_VOXELS), default=128,
                             help=f"MMD の人物の細かさ (初期姿勢の高さのボクセル数、1〜{MAX_VOXELS}、既定 128)。"
                                  "大きさは --figure-blocks")
    glass_group.add_argument("--joint-overlap", type=_non_negative_int, default=1,
                             help="MMD の人物の関節で、隣の部品に重ねるボクセルの数 (曲げたときのすき間を防ぐ。既定 1)")
    glass_group.add_argument("--figure-loop", action="store_true", help="MMD の人物の踊りを繰り返す (既定は最後で止まる)")
    glass_group.add_argument("--face-mapping", type=_face_mapping, default=None, metavar="N,E,UU,UV",
                             help="面の板の UV の向き (試験用。north の u・east の u・up の u・up の v を 1 か -1 で)。"
                                  "既定は実機で決めた 1,-1,1,-1 (DECISIONS.md の A19)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 実行ファイルのパス")
    args = parser.parse_args(argv)
    if not args.inputs and not allow_no_inputs:
        parser.error("入力の動画を 1 つ以上渡してください")
    if not args.inputs and args.output is None:
        parser.error("入力の動画が無いときは -o で出力を決めてください")
    if not 2 <= args.depth <= 128:
        parser.error("--depth は 2〜128 にしてください")
    if not 0 <= args.lift_threshold <= 1:
        parser.error("--lift-threshold は 0〜1 にしてください")
    if args.depth_video and args.lift != "relief":
        parser.error("--depth-video は --lift relief でだけ使えます")
    if args.mask_video and args.lift not in ("extrude", "inflate"):
        parser.error("--mask-video は --lift extrude / inflate でだけ使えます")
    if not 1 <= args.jpeg_quality <= 95:
        parser.error("--jpeg-quality は 1〜95 にしてください")
    if args.ids is not None and len(args.ids) != len(args.inputs):
        parser.error("--ids の数が動画の数と違います")
    if args.titles is not None and len(args.titles) != len(args.inputs):
        parser.error("--titles の数が動画の数と違います")
    if args.start is not None and args.start < 0:
        parser.error("--start は 0 以上にしてください")
    if args.end is not None and args.end <= (args.start or 0):
        parser.error("--end は --start より後にしてください")
    if args.pack_name is not None and pack_name_problem(args.pack_name):
        # 改行などの制御文字が manifest の名前と UUID の元に入っていた (#187)
        parser.error(f"--pack-name: {pack_name_problem(args.pack_name)}")
    if args.output is None:
        first = Path(args.inputs[0])
        args.output = str(first.with_name(first.stem + "_image.mcaddon"))
    if args.report is not None and Path(args.report).is_dir():
        # アドオンを書いた後でレポートが書けずに止まり、できたアドオンを「失敗」と伝えていた (#192)
        parser.error(f"--report がフォルダです。ファイルのパスにしてください: {args.report}")
    return args


def main(argv=None) -> int:
    # ログは日本語。パイプへ書くとき (CI・ほかのプログラムから起動) は UTF-8 にする。Windows の英語などの環境 (cp1252) では、
    # そのままだと UnicodeEncodeError で止まっていた (CI の Windows のジョブで見つけた)
    use_utf8_for_pipes()
    args = parse_args(argv)
    try:
        build(args)
    except (ConvertError, RuntimeError, ValueError, OSError) as error:
        # OSError (書けない場所・ディスクがいっぱいなど) も、理由を 1 行で出す (Python のトレースバックを出していた。#192)
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
