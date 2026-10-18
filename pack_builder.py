"""変換したデータ・サムネイル・音声から .mcaddon を組み立てる (GUI とブラウザ版で共通)。

tkinter・ffmpeg を使わないので、ブラウザの中の Python (Pyodide、web/) でも動く。
動画の変換・サムネイル・音声の切り出しは呼び出し側が行い、ここではファイルを並べて zip にするだけ。
"""
from __future__ import annotations

import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path

from gui_i18n import addon_lang_files
from pack_metadata import PACK_VERSION, changelog_markdown, manifest_description, version_text

APP_DIR = Path(__file__).resolve().parent
MANIFEST = APP_DIR / "manifest.json"
MAIN_SCRIPT = APP_DIR / "main.js"
CODEC_SCRIPT = APP_DIR / "codec.js"

BUILD_STAMP_EPOCH = 1704067200  # 2024-01-01T00:00:00Z
# 再ビルドしたパックをワールドが「新しい版」と認識できるよう、ビルド日時をバージョン番号に使う。
# Bedrock はバージョン番号の各要素を 16bit (0〜65535) で扱う。超えるとマニフェストが不正になりインポートできない
MANIFEST_VERSION_MAX = 65535
# main.js の AUDIO_CHUNK_SECONDS と一致させること
AUDIO_CHUNK_SECONDS = 10


def stable_pack_uuid(namespace: str, pack_name: str, role: str) -> str:
    """同じパック名・接頭辞なら毎回同じ UUID にする (再ビルドしても別パック扱いにならない)。"""
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


def build_addon(output, videos: list[dict], pack_name: str, namespace: str, addon_language: str | None = None,
                manifest_text: str | None = None, main_text: str | None = None, codec_text: str | None = None) -> list[dict]:
    """.mcaddon を output (パスか書き込みできるファイル) に書き、動画の一覧 (videos.js の VIDEO_LIST) を返す。

    videos の各要素:
        id          動画 ID
        title       表示名
        frames_js   convert.py の出力 (frames_*.js の文字列)
        thumbnail   サムネイルの PNG (bytes)。最初の動画のものをパックのアイコンにする。無ければ None
        sounds      [(チャンクの番号, 拡張子 ("ogg" / "wav"), bytes), ...] (AUDIO_CHUNK_SECONDS ごと)。無ければ []
        width / height  変換時の大きさ (frames_js に無いとき用)
    """
    manifest_text = manifest_text if manifest_text is not None else MANIFEST.read_text(encoding="utf-8")
    main_text = main_text if main_text is not None else MAIN_SCRIPT.read_text(encoding="utf-8")
    codec_text = codec_text if codec_text is not None else CODEC_SCRIPT.read_text(encoding="utf-8")
    files: dict[str, bytes] = {}

    def put(path: str, content) -> None:
        files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)

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
        entries.append(entry)
        put(f"BP/scripts/frames_{video_id}.js", video["frames_js"])
        if icon is None and video.get("thumbnail"):
            icon = video["thumbnail"]
        for index, ext, data in sorted(video.get("sounds") or [], key=lambda item: item[0]):
            put(f"RP/sounds/music/{video_id}/chunk_{index}.{ext}", data)
            # パスは拡張子なしで書く (.ogg / .wav のどちらでもゲームが探す)
            sound_definitions[f"{namespace}.{video_id}.chunk_{index}"] = {
                "category": "ui",
                "sounds": [{"name": f"sounds/music/{video_id}/chunk_{index}", "stream": True}],
            }

    if sound_definitions:
        put("RP/sounds/sound_definitions.json", json.dumps(
            {"format_version": "1.14.0", "sound_definitions": sound_definitions}, ensure_ascii=False, indent=2))
    # ゲーム内表示の翻訳 (既定は各プレイヤーのゲーム言語。固定なら GUI の言語だけ)
    for relative_path, content in addon_lang_files(addon_language).items():
        put(f"RP/{relative_path}", content)
    put("BP/scripts/videos.js", videos_js(entries))

    bp_uuid = stable_pack_uuid(namespace, pack_name, "bp")
    rp_uuid = stable_pack_uuid(namespace, pack_name, "rp")
    version = build_manifest_version()
    bp_manifest = json.loads(manifest_text)
    bp_manifest["header"].update({"name": f"{pack_name} v{version_text()}", "description": manifest_description(),
                                  "uuid": bp_uuid, "version": version})
    bp_manifest["modules"][0].update({"uuid": stable_pack_uuid(namespace, pack_name, "bp-script"), "version": version})
    bp_manifest.setdefault("dependencies", []).append({"uuid": rp_uuid, "version": version})
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
    # 再生速度は各動画データの fps から決まるので、main.js は接頭辞だけ差し替える
    put("BP/scripts/main.js", main_text.replace('const EVENT_NAMESPACE = "badapple";',
                                                 f'const EVENT_NAMESPACE = "{namespace}";'))
    put("BP/scripts/codec.js", codec_text)
    if icon is not None:
        put("RP/pack_icon.png", icon)
        put("BP/pack_icon.png", icon)
    put("BP/CHANGELOG.md", changelog_markdown(pack_name))
    put("RP/CHANGELOG.md", changelog_markdown(pack_name))

    target = output
    if isinstance(output, (str, Path)):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.writestr(path, files[path])
    return entries


def build_addon_bytes(videos: list[dict], pack_name: str, namespace: str, **kwargs) -> bytes:
    """build_addon の結果を bytes で返す (ブラウザ版でダウンロードさせる用)。"""
    buffer = io.BytesIO()
    build_addon(buffer, videos, pack_name, namespace, **kwargs)
    return buffer.getvalue()
