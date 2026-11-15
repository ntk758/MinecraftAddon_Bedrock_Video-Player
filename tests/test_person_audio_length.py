"""人物の切り抜き (make_person_videos) で、音声と映像の長さが違う動画を処理するテスト。

背景の動画を書く ffmpeg (person._writer) は、duration が無いとき `-shortest` で映像の終わりに合わせる (音声が映像より長い動画で、
音声だけが長く残らないように)。以前は `-shortest` だけだったので、音声が映像より短いと ffmpeg が音声の終わりで出力を閉じ、
Python が残りのフレームを書くところで BrokenPipeError になっていた (切り抜き自体は動いていた)。今は `-af apad` で音声の後ろを
無音で延ばしてから `-shortest` で切るので、背景の動画は常に映像の長さになる。docs/research/roadmap_review_2026-11.md の 2 章。

許容誤差: 映像は 10 fps なので 1 フレーム (0.1 秒)。音声は AAC の 1 フレーム (1024 サンプル、44.1 kHz で約 0.023 秒) の単位で
切れるので、映像の長さとの差は 0.05 秒まで (ffmpeg の版で数十 ms 前後する)。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mvcodec import person  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg と ffprobe が必要")

FPS = 10
VIDEO_SECONDS = 4
FRAMES = FPS * VIDEO_SECONDS
FRAME_SECONDS = 1 / FPS
AUDIO_TOLERANCE = 0.05

# (名前, 元の動画の音声の秒数。None は音声なし)
CASES = [
    ("audio_shorter_than_video", 1),
    ("audio_longer_than_video", 6),
    ("no_audio", None),
    ("very_short_audio", 0.1),
    ("same_length", VIDEO_SECONDS),
]


def _source(path, audio_seconds):
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=160x90:r={FPS}:d={VIDEO_SECONDS}"]
    if audio_seconds is not None:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={audio_seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _streams(path):
    """ストリームごとの長さ (秒) と、映像を最後までデコードして数えたフレームの数。"""
    result = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_entries",
                             "stream=codec_type,duration,nb_read_frames", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True)
    streams = {}
    for s in json.loads(result.stdout)["streams"]:
        streams[s["codec_type"]] = {"duration": float(s["duration"]), "frames": int(s.get("nb_read_frames") or 0)}
    return streams


def _assert_decodes_cleanly(path):
    """映像と音声を最後までデコードして、エラーが 1 つも出ない。"""
    result = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stderr.strip() == ""


@pytest.mark.parametrize("name,audio_seconds", CASES, ids=[c[0] for c in CASES])
def test_person_videos_cover_the_whole_video(tmp_path, name, audio_seconds):
    """切り抜きの全体: BrokenPipeError を出さず、人物の範囲と背景の動画が元の映像と同じフレーム数・長さになる。"""
    source = _source(tmp_path / "in.mp4", audio_seconds)
    mask, background = tmp_path / "mask.mp4", tmp_path / "background.mp4"
    count = person.make_person_videos(source, mask, background, FPS,
                                      segmenter=lambda frame: np.zeros(frame.shape[:2], np.float32))
    assert count == FRAMES

    for path in (mask, background):
        _assert_decodes_cleanly(path)
        video = _streams(path)["video"]
        assert video["frames"] == FRAMES
        assert video["duration"] == pytest.approx(VIDEO_SECONDS, abs=FRAME_SECONDS)

    streams = _streams(background)
    if audio_seconds is None:
        assert "audio" not in streams
    else:
        # 短い音声は無音で延ばし、長い音声は切る: どちらも映像の長さになる
        assert streams["audio"]["duration"] == pytest.approx(VIDEO_SECONDS, abs=AUDIO_TOLERANCE)


def _write_background(tmp_path, source, frames):
    out = tmp_path / "background.mp4"
    writer = person._writer(out, 160, 90, FPS, FFMPEG, audio_from=source)
    frame = np.full((90, 160, 3), 90, np.uint8).tobytes()
    for _ in range(frames):
        writer.stdin.write(frame)   # 以前は音声の終わりで ffmpeg が閉じ、ここで BrokenPipeError
    writer.stdin.close()
    return out, writer.wait()


@pytest.mark.parametrize("name,audio_seconds", CASES, ids=[c[0] for c in CASES])
def test_background_writer_exits_cleanly(tmp_path, name, audio_seconds):
    """背景の動画を書く ffmpeg そのもの: 全フレームを書き終えるまで標準入力を読み、終了コード 0 で終わる。一時ファイルは残らない。"""
    source = _source(tmp_path / "in.mp4", audio_seconds)
    out, code = _write_background(tmp_path, source, FRAMES)
    assert code == 0
    _assert_decodes_cleanly(out)
    assert _streams(out)["video"]["frames"] == FRAMES
    assert sorted(p.name for p in tmp_path.iterdir()) == ["background.mp4", "in.mp4"]


@pytest.mark.parametrize("audio_seconds", [6, 0.1], ids=["longer_audio", "shorter_audio"])
@pytest.mark.parametrize("frames", [1, 3, 5, 8])
def test_background_shorter_than_one_second_keeps_the_video_length(tmp_path, frames, audio_seconds):
    """`-af apad -shortest` だけだと、映像が 1 秒より短いとき音声が約 1 秒まで残ることがあった (実行ごとに変わる)。
    書き終えた後にフレームの数で切るので、音声は映像の長さちょうど。"""
    source = _source(tmp_path / "in.mp4", audio_seconds)
    out, code = _write_background(tmp_path, source, frames)
    assert code == 0
    streams = _streams(out)
    assert streams["video"]["frames"] == frames
    assert streams["audio"]["duration"] == pytest.approx(frames / FPS, abs=AUDIO_TOLERANCE)


# --- 失敗したとき: 終了コードを呼び出し側が知り、途中のファイルと一時ファイルを残さない ---

def _leftovers(folder):
    return sorted(p.name for p in Path(folder).iterdir() if ".video-only" in p.name or ".muxing" in p.name)


def _not_a_media_file(tmp_path):
    bad = tmp_path / "not_media.mp4"
    bad.write_text("this is not a video", encoding="utf-8")
    return bad


def test_failed_audio_mux_is_reported_and_leaves_nothing_behind(tmp_path):
    """2 段目 (音声を足す ffmpeg) が失敗すると、wait() は 0 以外を返し、stderr を error に残す。背景の動画も一時ファイルも残らない。"""
    out, code = _write_background(tmp_path, _not_a_media_file(tmp_path), FRAMES)
    assert code != 0
    assert not out.exists()
    assert _leftovers(tmp_path) == []


def test_failed_audio_mux_is_an_input_error_for_the_caller(tmp_path, monkeypatch):
    """make_person_videos は、書き出しの ffmpeg の失敗を成功と取り違えず、InputError (やり直しても直らない) にする。"""
    source = _source(tmp_path / "in.mp4", 1)
    bad = _not_a_media_file(tmp_path)
    original = person._writer

    def broken_audio(path, width, height, fps, ffmpeg, audio_from=None, duration=None):
        return original(path, width, height, fps, ffmpeg, audio_from=bad if audio_from else None, duration=duration)

    monkeypatch.setattr(person, "_writer", broken_audio)
    background = tmp_path / "background.mp4"
    with pytest.raises(person.InputError, match="background.mp4.*exit code"):
        person.make_person_videos(source, tmp_path / "mask.mp4", background, FPS,
                                  segmenter=lambda frame: np.zeros(frame.shape[:2], np.float32))
    assert not background.exists()
    assert _leftovers(tmp_path) == []


def test_an_error_while_cutting_out_removes_the_temporary_video(tmp_path):
    """切り抜きの途中の例外はそのまま上へ伝わり、音声は足さず、一時ファイルも背景の動画も残らない。"""
    source = _source(tmp_path / "in.mp4", 1)
    seen = []

    def breaks(frame):
        seen.append(1)
        if len(seen) == 5:
            raise RuntimeError("segmenter broke")
        return np.zeros(frame.shape[:2], np.float32)

    background = tmp_path / "background.mp4"
    with pytest.raises(RuntimeError, match="segmenter broke"):
        person.make_person_videos(source, tmp_path / "mask.mp4", background, FPS, segmenter=breaks)
    assert not background.exists()
    assert _leftovers(tmp_path) == []


def test_ffmpeg_that_cannot_start_creates_no_temporary_file(tmp_path):
    with pytest.raises(person.InputError, match="could not start ffmpeg"):
        person._writer(tmp_path / "background.mp4", 160, 90, FPS, str(tmp_path / "no-such-ffmpeg"),
                       audio_from=tmp_path / "in.mp4")
    assert list(tmp_path.iterdir()) == []


def test_a_leftover_from_a_killed_run_is_replaced_and_removed(tmp_path):
    """前の処理が強制終了されて一時ファイルが残っていても、次の同じ出力で上書きされ、終わったら消える。"""
    source = _source(tmp_path / "in.mp4", 1)
    (tmp_path / "background.video-only.mp4").write_bytes(b"stale")
    (tmp_path / "background.muxing.mp4").write_bytes(b"stale")
    out, code = _write_background(tmp_path, source, FRAMES)
    assert code == 0
    assert _streams(out)["video"]["frames"] == FRAMES
    assert _leftovers(tmp_path) == []


def test_an_output_that_cannot_be_replaced_is_an_input_error(tmp_path, monkeypatch):
    """出来上がった背景の動画を本来の名前に置き換えられない (Windows で前の動画を別のアプリが開いているなど) ときも InputError
    (やり直しても直らない)。前からあった背景の動画はそのまま残り、一時ファイルは残らない。"""
    source = _source(tmp_path / "in.mp4", 1)
    background = tmp_path / "background.mp4"
    background.write_bytes(b"previous")

    def locked(src, dst):
        raise PermissionError(13, "The process cannot access the file", str(dst))

    monkeypatch.setattr(person.os, "replace", locked)
    with pytest.raises(person.InputError, match="could not write .*background.mp4"):
        person.make_person_videos(source, tmp_path / "mask.mp4", background, FPS,
                                  segmenter=lambda frame: np.zeros(frame.shape[:2], np.float32))
    assert background.read_bytes() == b"previous"
    assert _leftovers(tmp_path) == []
