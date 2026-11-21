"""外部の網羅テスト (v6.40.0 の EXE、2026-11-19) で残っていた NG の回帰テスト。

- NG1: ウィンドウ版の EXE をコマンドから起動すると `--run-converter --help` が何も出さない
  (標準出力が無いと捨てていた → 親のコンソールにつなぐ)
- NG2: 変換の失敗で PyInstaller の「Unhandled exception」ダイアログが出て、閉じるまで終わらない
  (例外は理由を書いて終了コード 1)
- NG4: `--threads 0` を受け付ける / NG5: `--width 9999` を受け付ける (Windows 版・ブラウザ版の上限 512)
- F-1: 末尾の欠けた動画が、ブロックモード・画像モード・人物の切り抜きのどれでも黙って短く変換される
"""
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402
from mvcodec import truncation  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")


# --- 判定 (ffmpeg を使わない) ---

def test_short_reads_with_decode_errors_are_truncation():
    with pytest.raises(truncation.TruncatedVideoError, match="2.9 秒 / 5.0 秒"):
        truncation.check_complete("v.mp4", 29, 10, 5.0, "[mov] stream 0, offset 0x1418f: partial file\n")


def test_short_reads_without_errors_are_not_truncation():
    """音声の方が長い動画などは、映像が短くてもエラーの文が出ないので止めない。"""
    truncation.check_complete("v.mp4", 30, 10, 5.0, "")


def test_rounding_at_the_end_is_not_truncation():
    truncation.check_complete("v.mp4", 48, 10, 5.0, "Invalid data found when processing input")
    with pytest.raises(truncation.TruncatedVideoError):
        truncation.check_complete("v.mp4", 46, 10, 5.0, "Invalid data found when processing input")


def test_expected_seconds_follow_the_requested_range():
    assert truncation.expected_seconds(10.0) == 10.0
    assert truncation.expected_seconds(10.0, duration=3) == 3.0
    assert truncation.expected_seconds(10.0, start=2, end=6) == 4.0
    assert truncation.expected_seconds(10.0, start=8, end=20) == 2.0
    assert truncation.expected_seconds(None) is None
    # 長さが分からなくても、ファイルの終わりが欠けた文があれば止める (#156)
    with pytest.raises(truncation.TruncatedVideoError):
        truncation.check_complete("v.mp4", 0, 10, None, "partial file")
    truncation.check_complete("v.mp4", 0, 10, None, "")


# --- 実際の動画 (ffmpeg) ---

@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    """完全な動画 (5 秒・10 fps・音声つき)、その先頭 60% で切ったもの、映像 3 秒・音声 5 秒の動画。"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    folder = tmp_path_factory.mktemp("clips")
    full = folder / "full.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=5",
                    "-f", "lavfi", "-i", "sine=d=5", "-shortest", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(full)], check=True)
    data = full.read_bytes()
    truncated = folder / "truncated.mp4"
    truncated.write_bytes(data[:int(len(data) * 0.6)])
    long_audio = folder / "long_audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=d=5", "-pix_fmt", "yuv420p", str(long_audio)], check=True)
    return {"full": full, "truncated": truncated, "long_audio": long_audio, "folder": folder}


def _convert(video, out, *extra):
    return convert.main(["--input-video", str(video), "--output", str(out), "--width", "24", "--height", "16", "--fps", "10",
                         "--device", "cpu", "--threads", "1", *extra])


@needs_ffmpeg
def test_block_mode_stops_on_a_truncated_video(clips):
    with pytest.raises(truncation.TruncatedVideoError):
        _convert(clips["truncated"], clips["folder"] / "t.js")
    assert not (clips["folder"] / "t.js").exists()
    assert _convert(clips["full"], clips["folder"] / "f.js") == 0
    assert _convert(clips["long_audio"], clips["folder"] / "a.js") == 0
    # 切れた所より前までしか使わないなら、そのまま変換できる
    assert _convert(clips["truncated"], clips["folder"] / "head.js", "--duration", "2") == 0


@needs_ffmpeg
def test_image_mode_stops_on_a_truncated_video(clips):
    from image_mode import atlas

    with pytest.raises(truncation.TruncatedVideoError):
        list(atlas.read_frames(clips["truncated"], 32, 18, 10))
    assert len(list(atlas.read_frames(clips["full"], 32, 18, 10))) == 50
    assert len(list(atlas.read_frames(clips["truncated"], 32, 18, 10, start=0, end=2))) == 20


@needs_ffmpeg
def test_person_split_stops_on_a_truncated_video(clips):
    from mvcodec import person

    with pytest.raises(person.InputError, match="truncated"):
        list(person.read_frames(clips["truncated"], 10, long_side=64))
    assert len(list(person.read_frames(clips["full"], 10, long_side=64))) == 50
    assert len(list(person.read_frames(clips["long_audio"], 10, long_side=64))) == 30


def _no_person(frame):
    import numpy as np

    return np.zeros(frame.shape[:2], np.float32)


@needs_ffmpeg
def test_person_split_leaves_no_partial_videos(clips, tmp_path):
    """途中で切れた動画で止まったとき、途中まで書いたマスクの動画を残さない (EXE の確認で見つけた)。"""
    from mvcodec import person

    mask, background = tmp_path / "mask.mp4", tmp_path / "background.mp4"
    with pytest.raises(person.InputError, match="truncated"):
        person.make_person_videos(clips["truncated"], mask, background, 10, segmenter=_no_person, long_side=64)
    assert not mask.exists() and not background.exists()
    assert person.make_person_videos(clips["full"], mask, background, 10, segmenter=_no_person, long_side=64) == 50
    assert mask.exists() and background.exists()


def test_person_split_reports_a_failing_ffmpeg(tmp_path):
    """読めない入力で ffmpeg がエラー終了したら、以前のように黙って 0 コマにせず InputError。"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    from mvcodec import person

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video" * 100)
    with pytest.raises(person.InputError):
        list(person.read_frames(broken, 10, long_side=64))


# --- 引数 ---

@pytest.mark.parametrize("extra", [["--threads", "0"], ["--width", "513"], ["--height", "9999"]])
def test_out_of_range_arguments_are_refused(tmp_path, extra):
    with pytest.raises(SystemExit) as raised:
        convert.main(["--input-video", "x.mp4", "--output", str(tmp_path / "o.js"), *extra])
    assert raised.value.code == 2


def test_the_largest_screen_is_accepted(tmp_path):
    from PIL import Image

    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (64, 2), (200, 30, 30)).save(frames / "frame_0001.png")
    assert convert.main(["--frames-dir", str(frames), "--output", str(tmp_path / "o.js"), "--width", "512", "--height", "2",
                         "--device", "cpu", "--threads", "1"]) == 0


# --- EXE の入口 (video_player_gui.py) ---

def test_converter_mode_returns_1_instead_of_a_crash_dialog(monkeypatch, capsys):
    import video_player_gui

    def broken(argv):
        raise RuntimeError("ffmpeg がエラー終了しました (code 1): moov atom not found")

    monkeypatch.setattr(convert, "main", broken)
    assert video_player_gui.run_converter_mode(["--input-video", "broken.mp4"]) == 1
    err = capsys.readouterr().err
    assert "moov atom not found" in err and "Traceback" in err


def test_person_mode_returns_1_instead_of_a_crash_dialog(monkeypatch, capsys):
    import video_player_gui
    from mvcodec import person

    monkeypatch.setattr(person, "main", lambda argv: 1 / 0)
    assert video_player_gui.run_person_mode([]) == 1
    assert "ZeroDivisionError" in capsys.readouterr().err


def test_converter_help_is_printed(capsys):
    import video_player_gui

    with pytest.raises(SystemExit) as raised:
        video_player_gui.run_converter_mode(["--help"])
    assert raised.value.code == 0
    assert "--input-video" in capsys.readouterr().out


def test_missing_streams_get_a_place_to_write(monkeypatch):
    """ウィンドウ版の EXE のように標準出力が無くても、書き込める先 (親のコンソールか捨てる先) につなぐ。"""
    import video_player_gui

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(video_player_gui, "_attach_parent_console", lambda: False)
    video_player_gui._prepare_output_streams()
    assert sys.stdout is not None and sys.stderr is sys.stdout
    print("written", file=sys.stdout)
    sys.stdout.close()

    console = io.StringIO()

    def attach():
        sys.stdout = console
        sys.stderr = console
        return True

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(video_player_gui, "_attach_parent_console", attach)
    video_player_gui._prepare_output_streams()
    assert sys.stdout is console and sys.stderr is console
