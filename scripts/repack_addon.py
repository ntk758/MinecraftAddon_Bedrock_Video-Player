"""前の版のアドオン (.mcaddon) の中身 (変換したデータ・音声・サムネイル) をそのまま使い、アドオンの部分 (main.js・
codec.js・manifest・翻訳・版) だけを今の版にして組み立て直す。リリースのサンプル (sample・sample-3d・showcase) 用。

データの形式が変わっていない版どうしで使う (変換をやり直さないので、前の版と同じ映像・音声になる)。パックの UUID は
パック名と接頭辞から決まる (pack_builder.stable_pack_uuid) ので、前の版と同じパック名にすれば上書きで更新できる。

    python scripts/repack_addon.py old.mcaddon new.mcaddon [--name "Block & Image Video Player Sample"]
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pack_builder  # noqa: E402


def read_listing(archive):
    """BP/scripts/videos.js の VIDEO_LIST。"""
    text = archive.read("BP/scripts/videos.js").decode("utf-8")
    return json.loads(text[text.index("VIDEO_LIST = ") + len("VIDEO_LIST = "):text.rindex("]") + 1])


def namespace_of(archive):
    match = re.search(r'const EVENT_NAMESPACE = "([a-z0-9_]+)";', archive.read("BP/scripts/main.js").decode("utf-8"))
    if not match:
        raise ValueError("main.js にコマンドの接頭辞 (EVENT_NAMESPACE) がありません")
    return match.group(1)


def videos_of(archive):
    """アドオンから pack_builder.build_addon に渡す動画の一覧を取り出す。"""
    names = archive.namelist()
    icon = archive.read("RP/pack_icon.png") if "RP/pack_icon.png" in names else None
    videos = []
    for n, entry in enumerate(read_listing(archive)):
        vid = entry["id"]
        sounds = []
        for name in names:
            match = re.fullmatch(rf"RP/sounds/music/{re.escape(vid)}/chunk_(\d+)\.(ogg|wav)", name)
            if match:
                sounds.append((int(match.group(1)), match.group(2), archive.read(name)))
        video = {"id": vid, "title": entry.get("title", vid),
                 "frames_js": archive.read(f"BP/scripts/frames_{vid}.js").decode("utf-8"),
                 "thumbnail": icon if n == 0 else None, "sounds": sorted(sounds),
                 "width": entry.get("width", 0), "height": entry.get("height", 0)}
        for key in ("hidden", "audio_from"):
            if entry.get(key):
                video[key] = entry[key]
        videos.append(video)
    return videos


def pack_name_of(archive):
    """前の版のパック名 (BP の manifest の名前から末尾の「 vX.Y.Z」を除いたもの)。"""
    name = json.loads(archive.read("BP/manifest.json"))["header"]["name"]
    return re.sub(r"\s+v\d+\.\d+\.\d+$", "", name)


def repack(source, output, pack_name=None):
    with zipfile.ZipFile(source) as archive:
        videos = videos_of(archive)
        namespace = namespace_of(archive)
        pack_name = pack_name or pack_name_of(archive)
    # ゲーム内の表示は全言語 (各プレイヤーのゲームの言語)。リリースのサンプルはすべてこれ
    return pack_builder.build_addon(output, videos, pack_name, namespace)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--name", default=None,
                        help="パック名 (既定は前の版と同じ。同じなら UUID が同じになり、上書きで更新できる)")
    args = parser.parse_args(argv)
    entries = repack(args.source, args.output, args.name)
    print(f"{args.output}: {len(entries)} videos ({', '.join(e['id'] for e in entries)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
