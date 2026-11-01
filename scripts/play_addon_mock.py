"""アドオン (.mcaddon) の動画を、モックの Minecraft (tests/js/minecraft_mock) で 1 本ずつ最後まで再生して確かめる。
リリースの手順 (docs/RELEASING.md の 4) 用。node が要る。

各動画 (一覧に出すもの) について、setup → play <動画ID> → 再生の終わりまで進め、次を確かめる:
- 再生が最後まで進んだ (playback_finished のメッセージ)
- スクリプトのエラーが無い
- 最後の盤面に置いたブロックの数が、参照デコーダ (mvcodec.decode) の最後のフレームと同じ (だまし絵は投影が目の位置で
  変わるので、置いたことだけを確かめる)

    python scripts/play_addon_mock.py BlockVideoPlayer-sample-v6.31.0.mcaddon [...]
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.decode import iter_decoded_frames  # noqa: E402

FINISHED = "bvp.playback_finished"


def prepare(archive, work):
    """アドオンの BP/scripts とモックの @minecraft/server を work に置く。戻り値は (接頭辞, 一覧)。"""
    for name in archive.namelist():
        if name.startswith("BP/scripts/") and name.endswith(".js"):
            (work / Path(name).name).write_bytes(archive.read(name))
    shutil.copy(ROOT / "tests" / "js" / "drive_main.mjs", work / "drive_main.mjs")
    for module, source in (("server", "server.js"), ("server-ui", "server-ui.js")):
        target = work / "node_modules" / "@minecraft" / module
        target.mkdir(parents=True)
        (target / "package.json").write_text(json.dumps({"name": f"@minecraft/{module}", "type": "module", "main": "index.js"}))
        shutil.copy(ROOT / "tests" / "js" / "minecraft_mock" / source, target / "index.js")
    (work / "package.json").write_text(json.dumps({"type": "module"}))
    main = (work / "main.js").read_text(encoding="utf-8")
    namespace = re.search(r'const EVENT_NAMESPACE = "([a-z0-9_]+)";', main).group(1)
    videos_js = (work / "videos.js").read_text(encoding="utf-8")
    listing = json.loads(videos_js[videos_js.index("VIDEO_LIST = ") + 13:videos_js.rindex("]") + 1])
    return namespace, listing


def expected_placed(data):
    """参照デコーダの最後のフレームで、ブロックが置かれるマスの数 (平面は全マス、立体は空気でないマス)。"""
    *_, (index_board, _rgb) = iter_decoded_frames(data)
    if data.get("layout") in ("volume", "heightfield"):
        return int(np.count_nonzero(index_board != data.get("air_level", 127)))
    return int(np.count_nonzero(index_board >= 0))


def frame_data(work, video_id):
    text = (work / f"frames_{video_id}.js").read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def play_one(work, namespace, video_id):
    steps = [{"event": f"{namespace}:play", "message": video_id}, {"event": f"{namespace}:setup"},
             {"event": f"{namespace}:play", "message": video_id}, {"until": FINISHED}, {"settle": 3}]
    # drive_main の snap は "badapple:anchor" の位置を使うので、接頭辞が badapple のときだけ盤面を比べる
    if namespace == "badapple":
        steps.append({"snap": video_id, "video": video_id})
    result = subprocess.run(["node", "drive_main.mjs", json.dumps(steps)], cwd=work, capture_output=True, text=True,
                            encoding="utf-8", timeout=900)
    if result.returncode != 0:
        return [f"node が失敗しました: {result.stderr[-2000:]}"]
    out = json.loads(result.stdout)
    problems = [f"エラー: {e.splitlines()[0]}" for e in out["errors"]]
    if not any(FINISHED in m for m in out["messages"]):
        problems.append("最後まで再生されませんでした")
    data = frame_data(work, video_id)
    if out["snaps"] and data.get("view") != "anamorphic":
        placed = sum(1 for cell in out["snaps"][0]["board"] if cell not in (None, "minecraft:air"))
        want = expected_placed(data)
        if placed != want:
            problems.append(f"置いたブロックの数 {placed} が参照デコーダ ({want}) と違います")
    return problems


def check(path):
    """アドオン 1 つを確かめ、[(動画 ID, 問題の一覧)] を返す。"""
    results = []
    with zipfile.ZipFile(path) as archive:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            base.mkdir()
            namespace, listing = prepare(archive, base)
            listing_ids = [e["id"] for e in listing if not e.get("hidden")]
            for video_id in listing_ids:
                work = Path(tmp) / f"play_{video_id}"
                shutil.copytree(base, work, symlinks=True)
                results.append((video_id, play_one(work, namespace, video_id)))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("addons", nargs="+")
    args = parser.parse_args(argv)
    if shutil.which("node") is None:
        parser.error("node が見つかりません")
    failed = 0
    for path in args.addons:
        for video_id, problems in check(path):
            status = "OK" if not problems else "NG: " + " / ".join(problems)
            print(f"{Path(path).name} {video_id}: {status}")
            failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
