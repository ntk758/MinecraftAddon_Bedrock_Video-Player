"""変換の異常系 (TODO.md の P0-7): ffmpeg で作れる、ふつうでない動画を読んで、止まらず・正しいフレーム数で終わること。

音声なし・音声だけが長い・1 時間の低解像度・4K の数フレーム・縦長・回転のメタデータ付き・可変フレームレート。
フレームの取り出し (convert.ffmpeg_frame_generator) と、音声のチャンクの切り出し (GUI の _extract_audio) を確かめる。
"""
import json
import queue
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg と ffprobe が必要")


def make(path, *args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *args, str(path)], check=True)
    return path


def frames(path, width=32, height=18, fps=10, duration=None, fit="pad"):
    return list(convert.ffmpeg_frame_generator(path, width, height, fps, duration, FFMPEG, fit))


def extract_audio(video, out_dir):
    """GUI (video_player_gui.PackBuilderApp._extract_audio) の音声の切り出しを、画面を作らずに動かす。"""
    import video_player_gui

    class Fake:
        messages = queue.Queue()

        @staticmethod
        def tr(key, **kwargs):
            return key

        @staticmethod
        def _run(command):
            subprocess.run([*command[:1], "-v", "error", *command[1:]], check=True, capture_output=True)

    return video_player_gui.PackBuilderApp._extract_audio(Fake(), FFMPEG, video, out_dir)


def seconds_of(path):
    result = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def test_a_video_without_audio_converts_and_has_no_sound(tmp_path):
    video = make(tmp_path / "silent.mp4", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=3", "-pix_fmt", "yuv420p")
    assert len(frames(video)) == 30
    assert extract_audio(video, tmp_path / "sounds") == []


def test_audio_longer_than_the_video_keeps_the_video_length(tmp_path):
    """映像 2 秒・音声 7 秒: フレームは映像の長さだけ。音声のチャンクは、映像の長さで切らずに音声の長さまで切り出す
    (今の動き。映像が終わった後に音声が鳴り続けるかは、実機で未確認。TODO.md の P0-7)。"""
    video = make(tmp_path / "long_audio.mp4", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=2",
                 "-f", "lavfi", "-i", "sine=d=7", "-pix_fmt", "yuv420p", "-c:a", "aac")
    assert len(frames(video)) == 20
    chunks = extract_audio(video, tmp_path / "sounds")
    total = sum(seconds_of(path) for path in (tmp_path / "sounds").glob("chunk_*.ogg"))
    assert [index for index, _, _ in sorted(chunks)] == [0] and total == pytest.approx(7, abs=0.1)


def test_an_hour_long_low_resolution_video_is_read_to_the_end(tmp_path):
    """1 時間 (1 fps の 16×9)。止まらずに、最後のフレームまで読む。"""
    video = make(tmp_path / "hour.mp4", "-f", "lavfi", "-i", "testsrc2=s=16x9:r=1:d=3600", "-pix_fmt", "yuv420p",
                 "-g", "600")
    count = sum(1 for _ in convert.ffmpeg_frame_generator(video, 16, 9, 1, None, FFMPEG))
    assert count == 3600


def test_a_4k_video_with_a_few_frames(tmp_path):
    video = make(tmp_path / "uhd.mp4", "-f", "lavfi", "-i", "testsrc2=s=3840x2160:r=30:d=0.1", "-pix_fmt", "yuv420p")
    out = frames(video, fps=30)
    assert len(out) == 3
    assert out[0].shape == (18, 32, 3)


def test_a_vertical_video_is_padded_at_the_sides(tmp_path):
    """縦長 (9:16) を 16:9 の画面に入れると、左右が黒で、真ん中に映像が入る。"""
    video = make(tmp_path / "vertical.mp4", "-f", "lavfi", "-i", "color=c=white:s=180x320:r=10:d=1", "-pix_fmt", "yuv420p")
    first = frames(video)[0].astype(int)
    assert first[:, :4].max() <= 16 and first[:, -4:].max() <= 16          # 左右の帯は黒
    assert first[:, 14:18].min() >= 200                                     # 真ん中は白


def test_rotation_metadata_turns_the_picture(tmp_path):
    """横長で撮って回転のメタデータ (90 度) が付いた動画は、縦長として扱う (スマートフォンの縦の動画)。"""
    landscape = make(tmp_path / "landscape.mp4", "-f", "lavfi", "-i", "color=c=white:s=320x180:r=10:d=1",
                     "-pix_fmt", "yuv420p")
    rotated = tmp_path / "rotated.mp4"
    result = subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", "90", "-i", str(landscape), "-c", "copy",
                             str(rotated)], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip("この ffmpeg は -display_rotation に対応していない")
    first = frames(rotated)[0].astype(int)
    # 縦長になったので、16:9 の画面では左右が黒
    assert first[:, :4].max() <= 16 and first[:, 14:18].min() >= 200


def test_variable_frame_rate_follows_the_timestamps(tmp_path):
    """可変フレームレート: 最初の 10 コマは 20 fps (0.5 秒)、残りの 20 コマは 5 fps (4 秒)。10 fps で取り出すと、
    コマの数 (30) ではなく時刻で数える。最後のコマは 4.3 秒にあるので、43〜45 フレーム。"""
    video = make(tmp_path / "vfr.mkv", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=1",
                 "-vf", "setpts='if(lt(N,10),N*0.05,0.5+(N-10)*0.2)/TB'", "-fps_mode", "vfr", "-pix_fmt", "yuv420p")
    count = len(frames(video))
    assert 43 <= count <= 45


def test_duration_cuts_a_long_video_without_reading_it_all(tmp_path):
    """長い動画でも、--duration の分だけで止まる。"""
    video = make(tmp_path / "ten_minutes.mp4", "-f", "lavfi", "-i", "testsrc2=s=16x9:r=10:d=600", "-pix_fmt", "yuv420p")
    assert len(frames(video, 16, 9, 10, duration=3)) == 30


def test_a_still_image_inside_a_video_container(tmp_path):
    """1 コマだけの動画 (1 fps で 1 秒) は、10 fps で取り出すと同じコマが 10 フレーム。"""
    video = make(tmp_path / "one.mp4", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=1:d=1", "-frames:v", "1",
                 "-pix_fmt", "yuv420p")
    out = frames(video)
    assert len(out) == 10
    assert all(np.array_equal(out[0], frame) for frame in out) and out[0].any()
