"""main.js を Minecraft API のモック上で動かす結合テスト (Node.js が必要)。

再生の速さ・最終盤面・音声チャンクに加え、プレイヤーに見せる文字列がすべて翻訳キー (texts/*.lang) 経由で、
そのキーが翻訳ファイルに存在することを確認する。
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui_i18n import addon_lang_files  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from test_codec_v4 import HEIGHT, WIDTH, make_scene_frames, run_convert  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")


def lang_keys():
    content = addon_lang_files()["texts/en_US.lang"]
    return {line.split("=", 1)[0] for line in content.splitlines() if line}


def collect_text(value, keys, literals):
    """RawMessage を辿り、translate キーと、翻訳を通らない生の文字列を集める。"""
    if isinstance(value, str):
        literals.append(value)
    elif isinstance(value, dict):
        if "translate" in value:
            keys.add(value["translate"])
        if "text" in value:
            literals.append(value["text"])
        for child in value.get("rawtext", []):
            collect_text(child, keys, literals)
        with_args = value.get("with")
        if isinstance(with_args, dict):
            for child in with_args.get("rawtext", []):
                collect_text(child, keys, [])  # 引数 (動画 ID や数値) は翻訳対象外
        elif isinstance(with_args, list):
            pass
    elif isinstance(value, list):
        for child in value:
            collect_text(child, keys, literals)


@pytest.fixture(scope="module")
def run_result(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("main_js")
    make_scene_frames(tmp / "frames")
    data = run_convert(tmp / "frames", tmp / "frames_demo.js", "--keyframe-interval", "10")

    work = tmp / "addon"
    work.mkdir()
    shutil.copy(ROOT / "main.js", work / "main.js")
    shutil.copy(ROOT / "codec.js", work / "codec.js")
    shutil.copy(tmp / "frames_demo.js", work / "frames_demo.js")
    shutil.copy(ROOT / "tests" / "js" / "run_main.mjs", work / "run_main.mjs")
    (work / "videos.js").write_text(
        'import { FRAME_DATA as video_demo } from "./frames_demo.js";\n'
        'export const VIDEOS = { "demo": video_demo };\n'
        'export const VIDEO_LIST = [{ id: "demo", title: "Demo", frame_count: video_demo.frame_count, '
        'width: video_demo.width, height: video_demo.height }];\n',
        encoding="utf-8",
    )
    mock_dir = ROOT / "tests" / "js" / "minecraft_mock"
    for module, source in (("server", "server.js"), ("server-ui", "server-ui.js")):
        target = work / "node_modules" / "@minecraft" / module
        target.mkdir(parents=True)
        (target / "package.json").write_text(json.dumps({"name": f"@minecraft/{module}", "type": "module", "main": "index.js"}))
        shutil.copy(mock_dir / source, target / "index.js")
    (work / "package.json").write_text(json.dumps({"type": "module"}))

    result = subprocess.run(["node", "run_main.mjs"], cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stderr
    return data, json.loads(result.stdout)


def test_playback_runs_at_video_fps(run_result):
    data, out = run_result
    assert out["finished"]
    ticks_per_frame = 20 / data["fps"]
    expected = data["frame_count"] * ticks_per_frame
    assert expected <= out["ticks"] <= expected + 10  # 開始待ち (5 tick) 程度の差だけ


def test_final_board_matches_reference_decoder(run_result):
    data, out = run_result
    *_, (index_board, _rgb) = iter_decoded_frames(data)
    blocks = [spec["block"] for spec in data["level_blocks"]]
    expected = [blocks[i] for i in index_board.reshape(-1)]
    assert out["finalBoard"] == expected
    assert len(expected) == WIDTH * HEIGHT


def test_audio_chunks_follow_playback(run_result):
    _data, out = run_result
    assert out["sounds"] and out["sounds"][0] == "badapple.demo.chunk_0"


def test_all_player_facing_text_is_translated(run_result):
    _data, out = run_result
    keys, literals = set(), []
    collect_text(out["messages"], keys, literals)
    for form in out["forms"]:
        for part in [form["title"], form["body"], *form["buttons"], *form["sliders"]]:
            collect_text(part, keys, literals)

    known = lang_keys()
    assert keys, "翻訳キーが 1 つも使われていません"
    assert keys <= known, f"翻訳ファイルに無いキー: {keys - known}"
    # 翻訳を通らずに表示される文字列は、接頭辞・空白・改行・動画タイトル程度に限る
    for text in literals:
        assert not re.search(r"[A-Za-z぀-ヿ一-鿿]{4,}", text.replace("badapple", "").replace("Demo", "")), (
            f"翻訳されていない文字列: {text!r}"
        )
    # リモコン画面が開かれていること
    assert any(form["title"] == {"translate": "bvp.remote.title"} for form in out["forms"])


def test_every_translate_key_in_main_js_exists():
    source = (ROOT / "main.js").read_text(encoding="utf-8")
    used = set(re.findall(r'\b(?:tr|notify)\(\s*"([a-z_.]+)"', source))
    used |= {m for pair in re.findall(r'tr\(running \? "([a-z_.]+)" : "([a-z_.]+)"\)', source) for m in pair}
    known = {key[len("bvp."):] for key in lang_keys()}
    assert used, "main.js から翻訳キーを抽出できませんでした"
    assert used <= known, f"翻訳ファイルに無いキー: {used - known}"
