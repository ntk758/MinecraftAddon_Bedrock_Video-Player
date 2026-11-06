"""画像（テクスチャ）再生方式のアドオンを作る（動画ごとに 1 エンティティ）。

ブロック方式 (convert.py / main.js / codec.js / pack_builder.py) は変更しない。別方式として
フレームの PNG を同梱した Behavior Pack + Resource Pack を組み、`.mcaddon` に書き出す。

設計 (docs/IMAGE_MODE.md):
  - 動画ごとに 1 つのエンティティ種別 `<namespace>:<動画ID>` を持つ。画面の位置はワールドの動的なプロパティに保存する
  - フレーム番号はエンティティプロパティ `<namespace>:frame` (client_sync: true) に書く
  - RP の render controller が `Array.frames[q.property('<namespace>:frame')]` でフレームの PNG を選ぶ
  - 再生・停止・フレーム進行は `image_mode/scripts/image_main.js` (独自コマンド `<namespace>:<操作>`)
  - 複数動画は既存のブロック方式と同じ「動画の一覧 + 動画 ID の enum のコマンド引数」の形にする
    (VIDEO_LIST の代わりとして image_videos.js の IMAGE_VIDEO_LIST を作る)

使い方:
  python image_mode/build_image_addon.py --video badapple=badapple.mp4 --video intro=intro.mp4 \
      --width 256 --height 144 --fps 20 --limit 300 -o dist/VideoPlayerImages.mcaddon
  # 動画のファイルが無いときは ffmpeg の testsrc で試作する: --sample 2
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from pack_builder import MANIFEST, build_manifest_version, stable_pack_uuid  # noqa: E402
from pack_metadata import changelog_markdown, manifest_description, version_text  # noqa: E402

try:
    # image_mode をパッケージとして読み込んだとき (Windows 版の EXE・ブラウザ版の Python はこう使う)
    from . import frame_extract, pack_docs  # noqa: F401
except ImportError:
    # `python image_mode/build_image_addon.py` を直接実行したときは、このフォルダの名前で読む
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))
    import frame_extract, pack_docs  # noqa: F401,E402

# 再生のスクリプト。EXE ではバンドルの中、ソースから実行したときはリポジトリの中にある
IMAGE_SCRIPTS = [APP_DIR / "scripts" / "image_main.js", REPO_DIR / "image_mode" / "scripts" / "image_main.js"]
# 動画 ID の形 (Bedrock の identifier は小文字の英数字と _)。エンティティ名・テクスチャのパス・コマンドの enum に使う
VIDEO_ID = re.compile(r"[a-z0-9_]{1,32}")
# テクスチャの枚数の上限 (--limit)。公式に配列の枚数の上限は記載されていないので、自分で上限を設ける
MAX_FRAMES = 2000
# 画像方式のアドオンの、接頭辞と名前と出力ファイルの名前 (Windows 版とブラウザ版が同じ形を使う)
IMAGE_NAMESPACE_SUFFIX = "_img"
IMAGE_PACK_SUFFIX = "_images"
# テクスチャの容量の見積りの目安 (Texture Budgets の Tier の値。ローカル適用での強制は未確認)
RP_TIER_LIMITS = {"Tier1": 300, "Tier2": 450, "Tier3": 600, "Tier4": 1200, "Tier5": 1600}  # MiB


def image_videos_js(entries: list[dict]) -> str:
    """画像方式の動画の一覧 (image_videos.js の IMAGE_VIDEO_LIST)。"""
    return ("export const IMAGE_VIDEO_LIST = "
            f"{json.dumps(entries, ensure_ascii=False, separators=(',', ':'))};\n")


def image_namespace(namespace: str) -> str:
    """画像方式のアドオンの接頭辞。ブロック方式と同じ接頭辞だと、ゲームに登録するコマンドの名前が同じになる
    (両方を入れると衝突する) ので、末尾に `IMAGE_NAMESPACE_SUFFIX` を足す。"""
    return f"{namespace}{IMAGE_NAMESPACE_SUFFIX}"


def image_pack_name(pack_name: str) -> str:
    """画像方式のアドオンの名前 (ブロック方式のパックの名前に足す)。"""
    return f"{pack_name}{IMAGE_PACK_SUFFIX}"


def image_output_path(output, suffix: str = IMAGE_PACK_SUFFIX) -> Path:
    """画像方式の .mcaddon の出力先。ブロック方式の出力と同じフォルダに `<name><suffix>.mcaddon` を置く。"""
    path = Path(output)
    return path.with_name(f"{path.stem}{suffix}.mcaddon")


def image_main_text(namespace: str) -> str:
    """再生のスクリプト。テンプレートの namespace を差し替える (main.js と同じ考え方)。"""
    for candidate in IMAGE_SCRIPTS:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            break
    else:
        raise ValueError("再生のスクリプトがありません: " + " / ".join(str(p) for p in IMAGE_SCRIPTS))
    text = text.replace("__NAMESPACE__", namespace)
    if "__NAMESPACE__" in text:
        raise ValueError("テンプレートの namespace が置き換わりませんでした")
    return text


def build_image_addon(output, videos: list[dict], namespace: str = "bvpimg",
                      pack_name: str = "Block Video Player Images", screen_blocks: float = 4.0,
                      description: str | None = None) -> dict:
    """.mcaddon を output (パスか書き込みできるファイル) に書き、動画の一覧を返す。

    description を渡すと、パックの説明にそれを使う (Windows 版とブラウザ版は画面の言語で説明を書く)。

    videos の各要素:
        id     動画 ID (小文字の英数字と _。エンティティ名・テクスチャのパスに使う)
        title  表示名
        frames フレームの PNG の bytes の一覧 (index 0 が 1 番目のフレーム)
        width / height  フレームの実際の大きさ
        fps    再生速度 (20 を超えたら 20 に clamp して ticks_per_frame を作る)
        hidden True ならゲーム内の動画の一覧と「次の動画」に出さない
    """
    if not videos:
        raise ValueError("動画が 0 個です")
    seen = set()
    for video in videos:
        video_id = video["id"]
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError(f"動画の ID は小文字の英数字と _ (1〜32文字): {video_id}")
        if video_id in seen:
            raise ValueError(f"動画の ID が重複しています: {video_id}")
        seen.add(video_id)
        if len(video["frames"]) == 0:
            raise ValueError(f"フレームの PNG が 0 個です: {video_id}")
        if len(video["frames"]) > MAX_FRAMES:
            raise ValueError(f"フレーム数が大きすぎます: {video_id} に {len(video['frames'])} 枚 (上限 {MAX_FRAMES})")
        if video["width"] <= 0 or video["height"] <= 0:
            raise ValueError(f"フレームの大きさは 0 より大きい値: {video_id} に {video['width']}x{video['height']}")

    files: dict[str, bytes] = {}

    def put(path: str, content) -> None:
        files[path] = content.encode("utf-8") if isinstance(content, str) else bytes(content)

    entries = []
    texels = 0
    icon = None
    for video in videos:
        video_id = video["id"]
        frames = video["frames"]
        width, height = int(video["width"]), int(video["height"])
        fps = min(float(video.get("fps", 20.0)), frame_extract.MAX_FPS)
        ticks = frame_extract.ticks_per_frame(fps)
        entry = {"id": video_id, "title": video.get("title", video_id), "frame_count": len(frames),
                 "width": width, "height": height, "fps": fps, "ticks_per_frame": ticks}
        if video.get("hidden"):
            entry["hidden"] = True
        entries.append(entry)
        for path, data in pack_docs.texture_bytes(frames, namespace, video_id).items():
            put(path, data)
        texels += width * height * len(frames) * 4
        put(f"BP/entities/{video_id}.json", pack_docs.bp_entity_json(namespace, video_id, len(frames)))
        put(f"RP/entity/{video_id}.json", pack_docs.rp_entity_json(namespace, video_id, len(frames)))
        put(f"RP/render_controllers/{video_id}.json", pack_docs.render_controller_json(namespace, video_id, len(frames)))
        put(f"RP/models/{video_id}.geo.json", pack_docs.geometry_json(namespace, video_id, width, height, screen_blocks))
        if icon is None:
            icon = frames[0]

    put("BP/scripts/main.js", image_main_text(namespace))
    put("BP/scripts/image_videos.js", image_videos_js(entries))
    put("BP/CHANGELOG.md", changelog_markdown(pack_name))
    put("RP/CHANGELOG.md", changelog_markdown(pack_name))
    if icon is not None:
        put("BP/pack_icon.png", icon)
        put("RP/pack_icon.png", icon)

    version = build_manifest_version()
    bp_uuid = stable_pack_uuid(namespace, pack_name, "bp")
    rp_uuid = stable_pack_uuid(namespace, pack_name, "rp")
    default_description = "画像（テクスチャ）再生方式のアドオン (ブロック方式とは別方式)"
    bp_description = description or default_description
    rp_description = description or "画像（テクスチャ）再生方式のリソースパック"
    bp_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bp_manifest["header"].update({"name": f"{pack_name} v{version_text()}",
                                  "description": bp_description,
                                  "uuid": bp_uuid, "version": version})
    bp_manifest["modules"][0].update({"uuid": stable_pack_uuid(namespace, pack_name, "bp-script"), "version": version,
                                      "entry": "scripts/main.js"})
    # 再生には @minecraft/server の API だけ使う (UI は使わないので依存を残さない)
    bp_manifest["dependencies"] = [dep for dep in bp_manifest.get("dependencies", [])
                                   if dep.get("module_name") == "@minecraft/server"]
    bp_manifest["dependencies"].append({"uuid": rp_uuid, "version": version})
    put("BP/manifest.json", json.dumps(bp_manifest, ensure_ascii=False, indent=2) + "\n")
    rp_manifest = {
        "format_version": 2,
        "header": {"name": f"{pack_name} Res v{version_text()}", "description": rp_description,
                   "uuid": rp_uuid, "version": version, "min_engine_version": bp_manifest["header"]["min_engine_version"]},
        # サブパックの memory_performance_tier で機種別のテクスチャの容量に分けられるが、manifest V3 の実験的な設定
        # (このアドオンは format_version 2 を使うので入れていない。docs/IMAGE_MODE.md の未確認の事項に書いた)
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
    return {"paths": sorted(files), "videos": entries, "texture_mib": texels / (1024 * 1024)}


def build_image_addon_bytes(videos: list[dict], pack_name: str, namespace: str = "bvpimg",
                            screen_blocks: float = 4.0, **kwargs) -> bytes:
    """build_image_addon の結果を bytes で返す (ブラウザ版でダウンロードさせる用)。"""
    buffer = io.BytesIO()
    build_image_addon(buffer, videos, namespace, pack_name, screen_blocks, **kwargs)
    return buffer.getvalue()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="画像（テクスチャ）再生方式の .mcaddon を作る（複数動画対応）")
    parser.add_argument("--video", action="append", metavar="ID=PATH",
                        help="動画 ID と動画のファイル。繰り返すと複数動画が入る (例: --video badapple=badapple.mp4)")
    parser.add_argument("--sample", type=int, default=0, help="ffmpeg の testsrc で試作する動画の数 (--video が無いとき)")
    parser.add_argument("--width", type=int, default=256, help="フレームの幅 (px, 既定 256)")
    parser.add_argument("--height", type=int, default=144, help="フレームの高さ (px, 既定 144)")
    parser.add_argument("--fps", type=float, default=20.0, help="再生速度。20 が上限 (1 tick に 1 フレーム)")
    parser.add_argument("--limit", type=int, default=600, help=f"1 動画のフレーム数の上限 (既定 600, 最大 {MAX_FRAMES})")
    parser.add_argument("--screen-blocks", type=float, default=4.0, help="画面の幅 (ブロック数, 既定 4)")
    parser.add_argument("--namespace", default="bvpimg", help="エンティティとプロパティの接頭辞 (既定 bvpimg)")
    parser.add_argument("--pack-name", default="Block Video Player Images", help="パックの名前")
    parser.add_argument("-o", "--output", default="dist/VideoPlayerImages.mcaddon", help="出力の .mcaddon")
    args = parser.parse_args(argv)
    if args.width <= 0 or args.height <= 0:
        parser.error("--width と --height は 0 より大きく")
    if args.screen_blocks <= 0:
        parser.error("--screen-blocks は 0 より大きく")
    if args.limit <= 0 or args.limit > MAX_FRAMES:
        parser.error(f"--limit は 1〜{MAX_FRAMES}")
    if args.video and args.sample:
        parser.error("--video と --sample はどちらか一つ")
    if not args.video and args.sample <= 0:
        parser.error("--video か --sample が必要です")
    return args


def video_specs(args: argparse.Namespace) -> list[dict]:
    """引数から動画の指定を作る (抽出は行わない)。"""
    if args.video:
        specs = []
        for item in args.video:
            video_id, _, path = item.partition("=")
            if not path:
                raise ValueError(f"--video は ID=PATH の形: {item}")
            source = Path(path)
            if not source.is_file():
                raise ValueError(f"動画のファイルがありません: {path}")
            specs.append({"id": video_id, "title": video_id, "source": source})
        return specs
    return [{"id": f"sample{i + 1}", "title": f"Sample {i + 1}", "source": None} for i in range(args.sample)]


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    videos = []
    for spec in video_specs(args):
        result = frame_extract.extract_frames(spec["source"], args.width, args.height, args.fps, args.limit)
        videos.append({"id": spec["id"], "title": spec["title"], "frames": result["frames"],
                       "width": result["width"], "height": result["height"], "fps": result["fps"]})
    info = build_image_addon(args.output, videos, args.namespace, args.pack_name, args.screen_blocks)
    print(f"{args.output}: {len(info['videos'])} 動画, "
          + ", ".join(f"{v['id']}: {v['frame_count']} フレーム {v['width']}x{v['height']} {v['fps']}fps" for v in info["videos"]))
    mib = info["texture_mib"]
    print(f"テクスチャの見積容量 {mib:.1f} MiB (Resource Pack の Tier の上限: "
          + ", ".join(f"{tier} {limit} MiB" for tier, limit in RP_TIER_LIMITS.items()) + ")")
    if mib > RP_TIER_LIMITS["Tier3"]:
        print("Tier3 (600 MiB) を超えています。--limit か --width/--height を減らしてください", file=sys.stderr)
    print("ゲームでの確認手順は docs/IMAGE_MODE.md に書いています (実機での動作は未確認)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
