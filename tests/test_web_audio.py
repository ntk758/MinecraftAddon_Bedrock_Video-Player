"""ブラウザ版の、動画から AAC の音声トラックを取り出す処理 (web/mp4audio.js) のテスト。

iPhone の Safari / Chrome は MP4・MOV の動画ファイルを丸ごと decodeAudioData に渡すとデコードに失敗するので、
音声トラックだけを ADTS にして渡す。取り出した ADTS が、ffmpeg が同じ動画から書き出す ADTS とバイト単位で同じことを
確かめる (MOV の QuickTime の音声の記述、モノラル・ステレオ、48 kHz・44.1 kHz)。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("node") is None or shutil.which("ffmpeg") is None,
                                reason="node と ffmpeg が必要")


def make_video(path, audio_args, seconds=3):
    """テスト用の動画 (色の模様と音)。audio_args が None なら音声なし。"""
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=64x48:rate=10:duration={seconds}"]
    if audio_args is not None:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", *audio_args]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(args, check=True)


def extract(tmp_path, video):
    out = tmp_path / "out.aac"
    result = subprocess.run(["node", str(ROOT / "tests" / "js" / "mp4_audio.mjs"), str(ROOT / "web" / "mp4audio.js"),
                             str(video), str(out)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return None if result.stdout == "null" else out.read_bytes()


@pytest.mark.parametrize("name, audio_args", [
    ("stereo.mp4", ["-c:a", "aac", "-ar", "44100", "-ac", "2"]),
    ("mono.mov", ["-c:a", "aac", "-ar", "48000", "-ac", "1"]),
    ("stereo.mov", ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "96k"]),
])
def test_aac_track_becomes_the_same_adts_as_ffmpeg(tmp_path, name, audio_args):
    video = tmp_path / name
    make_video(video, audio_args)
    reference = tmp_path / "ref.aac"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-c:a", "copy", "-f", "adts", str(reference)],
                   check=True)
    adts = extract(tmp_path, video)
    assert adts is not None
    assert adts == reference.read_bytes()


@pytest.mark.parametrize("name, audio_args", [
    ("silent.mp4", None),
    # AAC でない音声 (ADTS にできない) は取り出さない
    ("mp3.mov", ["-c:a", "libmp3lame"]),
])
def test_no_aac_track_gives_null(tmp_path, name, audio_args):
    video = tmp_path / name
    make_video(video, audio_args)
    assert extract(tmp_path, video) is None


def test_not_a_video_gives_null(tmp_path):
    path = tmp_path / "clip.vox"
    path.write_bytes(b"VOX " + bytes(200))
    assert extract(tmp_path, path) is None
