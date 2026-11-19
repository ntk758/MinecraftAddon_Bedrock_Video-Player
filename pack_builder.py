"""変換したデータ・サムネイル・音声から .mcaddon を組み立てる (GUI とブラウザ版で共通)。

tkinter・ffmpeg を使わないので、ブラウザの中の Python (Pyodide、web/) でも動く。
動画の変換・サムネイル・音声の切り出しは呼び出し側が行い、ここではファイルを並べて zip にするだけ。
"""
from __future__ import annotations

import io
import json
import os
import re
import struct
import time
import uuid
import zipfile
import zlib
from pathlib import Path

from gui_i18n import FALLBACK_LANGUAGE, LOCALES, MINECRAFT_LOCALES, addon_lang_files
from pack_metadata import PACK_VERSION, changelog_markdown, manifest_description, version_text

APP_DIR = Path(__file__).resolve().parent
MANIFEST = APP_DIR / "manifest.json"
MAIN_SCRIPT = APP_DIR / "main.js"
CODEC_SCRIPT = APP_DIR / "codec.js"
# スクリーンの管理 (複数のスクリーン) と、スクリーンブロックの操作画面。main.js が import する
SCREEN_SYSTEM_SCRIPT = APP_DIR / "screen_system.js"
SCREEN_UI_SCRIPT = APP_DIR / "screen_ui.js"
# アドオンの scripts/ に入れるファイル (テストもこの一覧でモックの作業フォルダに並べる)
ADDON_SCRIPTS = ("main.js", "codec.js", "screen_system.js", "screen_ui.js")
# main.js にこの印があれば、スクリーンブロック (BP/blocks・テクスチャ・ブロック名) も入れる。旧版向けの出力
# (legacy_target.py) は印の範囲ごと消すので、ブロックも入れない (カスタムコンポーネント V2 は Script API 2.x から)
SCREEN_BLOCK_MARKER = "// <legacy:screens>"
# カスタムブロックの定義の版 (カスタムコンポーネント V2 を書ける版)。アドオンの min_engine_version (1.26.10) より古い
SCREEN_BLOCK_FORMAT = "1.21.90"

BUILD_STAMP_EPOCH = 1704067200  # 2024-01-01T00:00:00Z
# 再ビルドしたパックをワールドが「新しい版」と認識できるよう、ビルド日時をバージョン番号に使う。
# Bedrock はバージョン番号の各要素を 16bit (0〜65535) で扱う。超えるとマニフェストが不正になりインポートできない
MANIFEST_VERSION_MAX = 65535
# main.js の AUDIO_CHUNK_SECONDS と一致させること
AUDIO_CHUNK_SECONDS = 10


# 動画 ID とコマンドの接頭辞に使える文字。動画 ID は videos.js の名前・zip の中のパス (frames_<ID>.js など) に、接頭辞は
# main.js の文字列とコマンドの名前 (/<接頭辞>:setup) にそのまま入るので、ここで確かめる (呼び出し側の検査が漏れても壊さない)
SAFE_NAME = re.compile(r"[a-z0-9_]+")
# Minecraft のコマンドの名前空間なので、独自のコマンドを登録できない
RESERVED_NAMESPACES = {"minecraft"}
# main.js の中の、接頭辞を差し替える行
NAMESPACE_LINE = 'const EVENT_NAMESPACE = "badapple";'


def check_names(ids, namespace: str) -> None:
    """動画 ID と接頭辞が SAFE_NAME だけでできていて、接頭辞が予約された名前でないことを確かめる (違えば ValueError)。"""
    bad = sorted({video_id for video_id in ids if not SAFE_NAME.fullmatch(str(video_id))})
    if bad:
        raise ValueError(f"動画 ID は小文字の英数字と _ だけにしてください / video IDs may only use a-z, 0-9 and _: "
                         f"{', '.join(map(repr, bad))}")
    if not SAFE_NAME.fullmatch(str(namespace)) or namespace in RESERVED_NAMESPACES:
        raise ValueError(f"コマンドの接頭辞は小文字の英数字と _ だけで、minecraft 以外にしてください / "
                         f"the command prefix may only use a-z, 0-9 and _ and must not be minecraft: {namespace!r}")


def write_file_atomic(path, data: bytes) -> Path:
    """data を path に書く。同じフォルダの一時ファイルに書き終えてから置き換えるので、途中で失敗 (ディスクがいっぱいなど) しても
    前からあるファイルは壊れず、書きかけのファイルも残らない。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with open(temp, "wb") as out:
            out.write(data)
            out.flush()
            try:
                os.fsync(out.fileno())
            except (OSError, AttributeError):  # fsync の無いファイルシステム (ブラウザ版の Pyodide のメモリなど)
                pass
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return path


# 製品名を "Block Video Player" から変えた (v6.36.0)。既定のパック名のまま作り直した人のパックが別のパックに
# ならないよう、新しい既定の名前は前の名前と同じ UUID にする
RENAMED_PACK_NAMES = {"Block & Image Video Player": "Block Video Player"}


def stable_pack_uuid(namespace: str, pack_name: str, role: str) -> str:
    """同じパック名・接頭辞なら毎回同じ UUID にする (再ビルドしても別パック扱いにならない)。"""
    pack_name = RENAMED_PACK_NAMES.get(pack_name, pack_name)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"block-video-player/{namespace}/{pack_name}/{role}"))


def build_manifest_version(now: float | None = None) -> list[int]:
    """[major, 2024-01-01 からの日数, その日の分 (0〜1439)]。

    UUID が同じでも新しいビルドを「新しい版」として読み込ませるため、ビルドごとに必ず大きくなる値にする。
    どの要素も MANIFEST_VERSION_MAX 以下 (日数は約 179 年分まで収まる)。
    """
    now = time.time() if now is None else now
    minutes = max(0, int(now - BUILD_STAMP_EPOCH) // 60)
    days, minute_of_day = divmod(minutes, 24 * 60)
    return [PACK_VERSION[0], min(days, MANIFEST_VERSION_MAX), minute_of_day]


def frame_data_dims(frames_js: str) -> dict:
    """変換結果 (frames_*.js の文字列) から frame_count / width / height / depth を読む。"""
    dims = {}
    for key in ("frame_count", "width", "height", "depth"):
        match = re.search(rf'"{key}":\s*(\d+)', frames_js)
        if match:
            dims[key] = int(match.group(1))
    return dims


def vox_thumbnail_png(vox_path, size: int = 256, palette: str = "auto") -> bytes:
    """MagicaVoxel の最初のフレームを斜め上から見た立体の画像 (PNG の bytes)。.vox のサムネイルに使う。

    ブロックの選び方は変換 (convert.convert_vox) と同じ: 自動のときは全ブロックから、側面の色で一番近いものを選ぶ。
    上面はブロックの上面の色、正面と側面は側面の色で塗る。
    """
    import numpy as np
    from PIL import Image

    from mvcodec.color import PALETTES, side_palette
    from mvcodec.volume import AIR_LEVEL, iso_view, load_vox_frames, nearest_levels, vox_to_volume, vox_volume_shape

    blocks = PALETTES["all" if palette == "auto" else palette]
    top_rgb = np.array([item["rgb"] for item in blocks], dtype=np.float64)
    side_rgb = np.array([item["rgb"] for item in side_palette(blocks)], dtype=np.float64)
    frames, vox_palette = load_vox_frames(vox_path)
    color_to_level = np.full(256, AIR_LEVEL, dtype=np.int16)
    color_to_level[1:] = nearest_levels(vox_palette[:255, :3], side_rgb)
    volume = vox_to_volume(frames[0], vox_volume_shape(frames), color_to_level)
    table = np.vstack([top_rgb, np.zeros((256, 3))])
    side_table = np.vstack([side_rgb, np.zeros((256, 3))])
    image = Image.fromarray(iso_view(volume, rgb=table[volume], side_rgb=side_table[volume], background=(0, 0, 0)))
    if max(image.size) > size:
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
    else:
        scale = size // max(image.size)
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (size, size))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def videos_js(entries: list[dict]) -> str:
    """動画の一覧 (videos.js)。"""
    lines = [f'import {{ FRAME_DATA as video_{e["id"]} }} from "./frames_{e["id"]}.js";' for e in entries]
    lines.append("")
    members = ", ".join(f'"{e["id"]}": video_{e["id"]}' for e in entries)
    lines.append(f"export const VIDEOS = {{ {members} }};")
    lines.append("")
    lines.append(f"export const VIDEO_LIST = {json.dumps(entries, ensure_ascii=False, separators=(',', ':'))};")
    lines.append("")
    return "\n".join(lines)


# 同じ動画の立体版と平面版をセットで入れるとき: 平面版の動画 ID の末尾と、一覧に出す名前の印
FLAT_PAIR_SUFFIX = "_flat"
PAIR_TITLE_3D = "{title} (3D)"
PAIR_TITLE_2D = "{title} (2D)"


def flat_pair_dither(dither_method: str) -> str:
    """平面版のディザ。立体のモードではディザを「なし」にする (粒が立体の表面のノイズになる) ので、そのときは平面の
    既定の Blue Noise にする。ほかのディザを選んでいれば、それを使う。"""
    return "blue_noise" if dither_method == "none" else dither_method


def _png_rgba(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    """RGBA の画素 (行のリスト) を PNG にする (Pillow を使わない。ブラウザ版でも同じバイト列になる)。"""
    height, width = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def screen_block_textures() -> dict[str, bytes]:
    """スクリーンブロックのテクスチャ (16x16)。side は横 4 面 (画面と再生の印)、top は上と下。"""
    frame, screen = (0x3A, 0x3A, 0x3A, 255), (0x10, 0x1A, 0x26, 255)
    play, stand = (0x39, 0xC5, 0xE0, 255), (0x55, 0x55, 0x55, 255)
    side = [[frame] * 16 for _ in range(16)]
    for y in range(2, 13):
        for x in range(2, 14):
            side[y][x] = screen
    for i, y in enumerate(range(4, 11)):
        for x in range(7, 7 + min(i, 6 - i) + 1):
            side[y][x] = play
    for y in (14, 15):
        side[y] = [stand] * 16
    edge, face = (0x4A, 0x4A, 0x4A, 255), (0x2E, 0x2E, 0x2E, 255)
    top = [[edge if x in (0, 15) or y in (0, 15) else face for x in range(16)] for y in range(16)]
    return {"side": _png_rgba(side), "top": _png_rgba(top)}


def screen_block_files(namespace: str, pack_name: str) -> dict:
    """スクリーンブロック (<接頭辞>:video_screen) の定義・テクスチャ。識別子とテクスチャの名前は接頭辞ごとに別にする
    (同じワールドに 2 つのアドオンを入れても、ブロックとテクスチャの名前がぶつからない)。"""
    block_id = f"{namespace}:video_screen"
    side, top = f"{namespace}_video_screen_side", f"{namespace}_video_screen_top"
    block = {
        "format_version": SCREEN_BLOCK_FORMAT,
        "minecraft:block": {
            "description": {
                "identifier": block_id,
                "menu_category": {"category": "items"},
                # minecraft:cardinal_direction = 置いた人が見ていた方向。画面はその方向に (置いた人の方を向いて) 立つ
                "traits": {"minecraft:placement_direction": {"enabled_states": ["minecraft:cardinal_direction"]}},
            },
            "components": {
                # main.js の SCREEN_COMPONENT_ID。使う・置く・壊すと main.js が呼ばれる
                f"{namespace}:screen_controller": {},
                "minecraft:destructible_by_mining": {"seconds_to_destroy": 0.5},
                "minecraft:destructible_by_explosion": {"explosion_resistance": 6},
                "minecraft:map_color": "#1a2633",
                "minecraft:material_instances": {
                    "*": {"texture": side, "render_method": "opaque"},
                    "up": {"texture": top, "render_method": "opaque"},
                    "down": {"texture": top, "render_method": "opaque"},
                },
            },
        },
    }
    terrain = {
        "resource_pack_name": pack_name,
        "texture_name": "atlas.terrain",
        "texture_data": {side: {"textures": f"textures/blocks/{side}"}, top: {"textures": f"textures/blocks/{top}"}},
    }
    textures = screen_block_textures()
    return {
        "BP/blocks/video_screen.json": json.dumps(block, ensure_ascii=False, indent=2) + "\n",
        "RP/textures/terrain_texture.json": json.dumps(terrain, ensure_ascii=False, indent=2) + "\n",
        f"RP/textures/blocks/{side}.png": textures["side"],
        f"RP/textures/blocks/{top}.png": textures["top"],
    }


def screen_block_lang_line(namespace: str, language: str) -> str:
    """ブロックの名前の翻訳の行 (tile.<識別子>.name=...)。"""
    texts = LOCALES.get(language) or LOCALES[FALLBACK_LANGUAGE]
    name = texts.get("addon.screen.block_name") or LOCALES[FALLBACK_LANGUAGE]["addon.screen.block_name"]
    return f"tile.{namespace}:video_screen.name={name}\n"


def _language_of_lang_file(relative_path: str, addon_language) -> str:
    """texts/<コード>.lang がどの言語の文か (言語を固定したときは、その言語)。"""
    if addon_language is not None:
        return addon_language if addon_language in LOCALES else FALLBACK_LANGUAGE
    code = relative_path[len("texts/"):-len(".lang")]
    return next((language for language, codes in MINECRAFT_LOCALES.items() if code in codes), FALLBACK_LANGUAGE)


def build_addon(output, videos: list[dict], pack_name: str, namespace: str, addon_language: str | None = None,
                manifest_text: str | None = None, main_text: str | None = None, codec_text: str | None = None,
                lang_transform=None) -> list[dict]:
    """.mcaddon を output (パスか書き込みできるファイル) に書き、動画の一覧 (videos.js の VIDEO_LIST) を返す。

    videos の各要素:
        id          動画 ID
        title       表示名
        frames_js   convert.py の出力 (frames_*.js の文字列)
        thumbnail   サムネイルの PNG (bytes)。最初の動画のものをパックのアイコンにする。無ければ None
        sounds      [(チャンクの番号, 拡張子 ("ogg" / "wav"), bytes), ...] (AUDIO_CHUNK_SECONDS ごと)。無ければ []
        width / height  変換時の大きさ (frames_js に無いとき用)
        hidden      True なら、ゲーム内の動画の一覧とリモコンの「次の動画」に出さない (だまし絵の背景の映像)
        audio_from  ほかの動画の ID。その動画の音声を鳴らす (同じ動画の立体版と平面版をセットで入れるとき、音声を
                    2 回入れない)。この動画の sounds は使わない

    lang_transform: 翻訳の .lang の文字列を書き換える関数 (ブラウザ版の旧版向けの出力。legacy_target.lang_text)
    """
    manifest_text = manifest_text if manifest_text is not None else MANIFEST.read_text(encoding="utf-8")
    main_text = main_text if main_text is not None else MAIN_SCRIPT.read_text(encoding="utf-8")
    codec_text = codec_text if codec_text is not None else CODEC_SCRIPT.read_text(encoding="utf-8")
    # スクリーンブロックを入れるか (main.js に印があるか。旧版向けの出力は印ごと消してある)
    with_screen_block = SCREEN_BLOCK_MARKER in main_text
    files: dict[str, bytes] = {}

    def put(path: str, content) -> None:
        files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)

    ids = [video["id"] for video in videos]
    check_names(ids, namespace)
    if NAMESPACE_LINE not in main_text:
        # 差し替えられないと、接頭辞が badapple のままのアドオンを黙って作ってしまう
        raise ValueError(f"main.js に接頭辞の行がありません / main.js has no line: {NAMESPACE_LINE}")
    duplicates = sorted({video_id for video_id in ids if ids.count(video_id) > 1})
    if duplicates:
        # 同じ ID が 2 つあると videos.js の import がぶつかり、アドオン全体が読み込めなくなる
        raise ValueError(f"動画 ID が重複しています / duplicate video IDs: {', '.join(duplicates)}")
    entries = []
    sound_definitions = {}
    icon = None
    for video in videos:
        video_id = video["id"]
        dims = frame_data_dims(video["frames_js"])
        entry = {"id": video_id, "title": video.get("title", video_id), "frame_count": dims.get("frame_count", 0),
                 "width": dims.get("width", video.get("width", 0)), "height": dims.get("height", video.get("height", 0))}
        if "depth" in dims:
            entry["depth"] = dims["depth"]
        # 音声の区切りの数。0 ならゲーム内で再生するときに「音声が入っていない」と知らせる
        entry["audio_chunks"] = len(video.get("sounds") or [])
        audio_from = video.get("audio_from")
        if audio_from:
            source = next((v for v in videos if v["id"] == audio_from), None)
            if source is None:
                raise ValueError(f"audio_from の動画がありません: {video_id} -> {audio_from}")
            entry["audio_from"] = audio_from
            entry["audio_chunks"] = len(source.get("sounds") or [])
        if video.get("hidden"):
            # だまし絵の背景の映像など、人物の動画と一緒に再生するので一覧に出さない
            entry["hidden"] = True
        entries.append(entry)
        put(f"BP/scripts/frames_{video_id}.js", video["frames_js"])
        if icon is None and video.get("thumbnail"):
            icon = video["thumbnail"]
        for index, ext, data in sorted([] if audio_from else (video.get("sounds") or []), key=lambda item: item[0]):
            put(f"RP/sounds/music/{video_id}/chunk_{index}.{ext}", data)
            # パスは拡張子なしで書く (.ogg / .wav のどちらでもゲームが探す)
            sound_definitions[f"{namespace}.{video_id}.chunk_{index}"] = {
                "category": "ui",
                # load_on_low_memory: メモリの少ない端末 (スマートフォンなど) でも読み込ませる
                "sounds": [{"name": f"sounds/music/{video_id}/chunk_{index}", "stream": True, "load_on_low_memory": True}],
            }

    if sound_definitions:
        put("RP/sounds/sound_definitions.json", json.dumps(
            {"format_version": "1.14.0", "sound_definitions": sound_definitions}, ensure_ascii=False, indent=2))
    # ゲーム内表示の翻訳 (既定は各プレイヤーのゲーム言語。固定なら GUI の言語だけ)
    for relative_path, content in addon_lang_files(addon_language).items():
        text = content
        if lang_transform is not None and relative_path.endswith(".lang"):
            text = lang_transform(text)
        if with_screen_block and relative_path.endswith(".lang"):
            text += screen_block_lang_line(namespace, _language_of_lang_file(relative_path, addon_language))
        put(f"RP/{relative_path}", text)
    put("BP/scripts/videos.js", videos_js(entries))

    bp_uuid = stable_pack_uuid(namespace, pack_name, "bp")
    rp_uuid = stable_pack_uuid(namespace, pack_name, "rp")
    version = build_manifest_version()
    bp_manifest = json.loads(manifest_text)
    bp_manifest["header"].update({"name": f"{pack_name} v{version_text()}", "description": manifest_description(namespace),
                                  "uuid": bp_uuid, "version": version})
    bp_manifest["modules"][0].update({"uuid": stable_pack_uuid(namespace, pack_name, "bp-script"), "version": version})
    if with_screen_block:
        # ブロックの定義 (BP/blocks) を読ませる data モジュール。前からある UUID (パック・スクリプト) は変えない
        bp_manifest["modules"].append({"description": "Screen block", "type": "data",
                                       "uuid": stable_pack_uuid(namespace, pack_name, "bp-data"), "version": version})
    bp_manifest.setdefault("dependencies", []).append({"uuid": rp_uuid, "version": version})
    put("BP/manifest.json", json.dumps(bp_manifest, ensure_ascii=False, indent=2) + "\n")
    rp_manifest = {
        "format_version": 2,
        "header": {"name": f"{pack_name} Res v{version_text()}", "description": manifest_description(namespace), "uuid": rp_uuid,
                   "version": version, "min_engine_version": bp_manifest["header"]["min_engine_version"]},
        "modules": [{"description": "Resources", "type": "resources",
                     "uuid": stable_pack_uuid(namespace, pack_name, "rp-resources"), "version": version}],
        "dependencies": [{"uuid": bp_uuid, "version": version}],
    }
    put("RP/manifest.json", json.dumps(rp_manifest, ensure_ascii=False, indent=2) + "\n")
    # 再生速度は各動画データの fps から決まるので、main.js は接頭辞だけ差し替える
    put("BP/scripts/main.js", main_text.replace(NAMESPACE_LINE, f'const EVENT_NAMESPACE = "{namespace}";'))
    put("BP/scripts/codec.js", codec_text)
    # main.js が import するので、旧版向けの出力にも入れる (スクリーンブロックを使わなくても読み込みに要る)
    put("BP/scripts/screen_system.js", SCREEN_SYSTEM_SCRIPT.read_text(encoding="utf-8"))
    if with_screen_block:
        put("BP/scripts/screen_ui.js", SCREEN_UI_SCRIPT.read_text(encoding="utf-8"))
        for path, content in screen_block_files(namespace, pack_name).items():
            put(path, content)
    if icon is not None:
        put("RP/pack_icon.png", icon)
        put("BP/pack_icon.png", icon)
    put("BP/CHANGELOG.md", changelog_markdown(pack_name))
    put("RP/CHANGELOG.md", changelog_markdown(pack_name))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.writestr(path, files[path])
    if isinstance(output, (str, Path)):
        # 書き終えてから置き換える (作り直しの途中で失敗しても、前の .mcaddon を壊さない)
        write_file_atomic(output, buffer.getvalue())
    else:
        output.write(buffer.getvalue())
    return entries


def build_addon_bytes(videos: list[dict], pack_name: str, namespace: str, **kwargs) -> bytes:
    """build_addon の結果を bytes で返す (ブラウザ版でダウンロードさせる用)。"""
    buffer = io.BytesIO()
    build_addon(buffer, videos, pack_name, namespace, **kwargs)
    return buffer.getvalue()
