"""ブラウザ版の音声を Ogg Vorbis にするエンコーダー (web/vendor/wasm-media-encoders) のテスト。

- リポジトリに写したファイルが、npm の wasm-media-encoders 0.7.0 の配布物と同じであること (SHA-256)
- web/app.js と同じ設定で作った OGG を ffmpeg が読め、44.1 kHz ステレオの Vorbis で、10 秒ごとに区切られること
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "web" / "vendor" / "wasm-media-encoders"
EXPECTED_SHA256 = {
    "WasmMediaEncoder.min.js": "dd4e17abf5377dfecc726d6ec5e7b72dab01cf3522974278e5347f4e68480fb3",
    "ogg.wasm": "cd1f50349e58e650e33e3f0b850a785ed612c4abaa4756e520e50b2583cf07ed",
}


def test_vendored_files_are_the_published_ones():
    for name, digest in EXPECTED_SHA256.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name
        assert digest in (VENDOR / "README.md").read_text(encoding="utf-8")
    assert "MIT License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")


def test_encoder_runs_in_its_own_worker_from_the_vendored_files():
    """UMD は大域に var e, t を宣言するので、画面 (app.js の t) とぶつからないよう Worker の中だけで読む。"""
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    worker = (ROOT / "web" / "ogg-worker.js").read_text(encoding="utf-8")
    assert "WasmMediaEncoder" not in html
    assert 'new Worker("ogg-worker.js")' in app
    assert 'importScripts("vendor/wasm-media-encoders/WasmMediaEncoder.min.js");' in worker
    assert 'fetch("vendor/wasm-media-encoders/ogg.wasm")' in worker
    assert "unpkg.com" not in app + worker


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("ffprobe") is None, reason="node と ffprobe が必要")
def test_encoded_chunks_are_vorbis_that_ffmpeg_reads(tmp_path):
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "ogg_encode.mjs"), str(VENDOR), str(tmp_path), "25"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    sizes = json.loads(result.stdout)
    assert len(sizes) == 3  # 10 + 10 + 5 秒
    durations = []
    for index in range(3):
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "stream=codec_name,sample_rate,channels:format=duration", "-of", "json",
                                str(tmp_path / f"chunk_{index}.ogg")], capture_output=True, text=True, check=True)
        info = json.loads(probe.stdout)
        stream = info["streams"][0]
        assert (stream["codec_name"], stream["sample_rate"], stream["channels"]) == ("vorbis", "44100", 2)
        durations.append(float(info["format"]["duration"]))
    assert durations == pytest.approx([10.0, 10.0, 5.0], abs=0.05)
    # WAV (22.05 kHz モノラル、10 秒で 441 KB) より十分小さい
    assert max(sizes) < 441_000 / 5
