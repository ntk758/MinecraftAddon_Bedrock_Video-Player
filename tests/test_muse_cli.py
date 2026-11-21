"""外部の監査 (muse、2026-11) で見つかった、引数・名前・ファイル・Windows 版の画面の問題の回帰テスト。

引数 (#158・#163・#165・#167・#181・#182・#183・#189・#190・#191・#193)、名前 (#186・#187・#194)、
ファイル (#164・#180・#192)、Windows 版の画面 (#185・#197)。
"""
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402
import convert_image_mode as cim  # noqa: E402
import pack_builder  # noqa: E402
from mvcodec import fileio, limits  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
BASE = ["--input-video", "in.mp4", "--output", "out.js"]


# --- ブロックモードの引数 ---

@pytest.mark.parametrize("extra", [
    ["--duration", "inf"], ["--duration", "nan"], ["--duration", "1e308"], ["--fps", "nan"],
    ["--mode", "extrude", "--thickness", "0"], ["--mode", "extrude", "--thickness", "9999"],
    ["--scene-threshold", "1"], ["--scene-threshold", "-0.1"], ["--temporal-hysteresis", "999"],
    ["--mode", "relief", "--depth-hysteresis", "999"], ["--rdo-block-cap", "-1"],
    ["--palette-weight", "1000000"], ["--palette-weight", "nan"], ["--palette-weight", "0.5"],
    ["--stage-background", "Not-An-Id"], ["--stage-background", "x" * 65],
])
def test_block_mode_refuses_bad_values(extra, capsys):
    with pytest.raises(SystemExit) as error:
        convert.parse_args(BASE + extra)
    assert error.value.code == 2
    assert capsys.readouterr().err.strip()


def test_block_mode_defaults_still_parse():
    args = convert.parse_args(BASE + ["--mode", "extrude", "--thickness", "4", "--palette-weight", "8",
                                      "--stage-background", "dance_bg"])
    assert args.thickness == 4 and args.palette_weight == 8


def test_output_must_not_be_the_input(tmp_path, capsys):
    """--output が入力と同じだと、変換の後に元の動画を上書きして消していた (#158)。"""
    video = tmp_path / "X.mp4"
    video.write_bytes(b"video")
    with pytest.raises(SystemExit):
        convert.parse_args(["--input-video", str(video), "--output", str(tmp_path / "." / "X.mp4")])
    assert "--output" in capsys.readouterr().err
    assert video.read_bytes() == b"video"


def test_the_frames_file_is_written_atomically(tmp_path):
    out = tmp_path / "frames.js"
    out.write_text("old", encoding="utf-8")
    args = convert.parse_args(["--input-video", "in.mp4", "--output", str(out)])
    convert.write_frame_data(args, {"frame_count": 1})
    assert out.read_text(encoding="utf-8").startswith("export const FRAME_DATA = ")
    assert not list(tmp_path.glob("*.part"))


# --- 画像モードの引数 ---

@pytest.mark.parametrize("extra", [
    ["--start", "inf"], ["--start", "nan"], ["--end", "inf"], ["--end", "nan"],
    ["--near-threshold", "-1"], ["--near-threshold", "256"], ["--screen-width", "99999"], ["--screen-width", "0"],
    ["--audio-chunk-seconds", "0.001"], ["--audio-chunk-seconds", "100000"], ["--audio-chunk-seconds", "1e9"],
    ["--width", "16000"], ["--max-sheet", "32768"], ["--max-cells", "1000000"],
    ["--pack-name", "Evil\nPack"], ["--pack-name", "x" * 101],
])
def test_image_mode_refuses_bad_values(extra):
    with pytest.raises(SystemExit) as error:
        cim.parse_args(["in.mp4", *extra])
    assert error.value.code == 2


def test_image_mode_refuses_a_folder_as_the_report(tmp_path):
    with pytest.raises(SystemExit):
        cim.parse_args(["in.mp4", "--report", str(tmp_path)])


def test_a_failed_report_does_not_turn_a_built_addon_into_a_failure(tmp_path, monkeypatch):
    """アドオンを書いた後でレポートが書けないと、トレースバックで止まり「失敗」と伝えていた (#192)。"""
    args = cim.parse_args(["in.mp4", "-o", str(tmp_path / "a.mcaddon")])
    real = cim.write_file_atomic

    def write(path, data):
        if str(path).endswith(".report.json"):
            raise PermissionError("denied")
        return real(path, data)

    monkeypatch.setattr(cim, "write_file_atomic", write)
    logs = []
    report = cim._write_output(args, {"BP/x.txt": b"x"}, {"namespace": "ns", "pack_name": "P"}, log=logs.append)
    assert (tmp_path / "a.mcaddon").is_file() and report["namespace"] == "ns"
    assert any("レポートを書けません" in line for line in logs)


def test_long_file_names_make_short_ids():
    video_id = cim.default_id(Path("a" * 210 + ".mp4"), set())
    assert len(video_id) <= limits.DEFAULT_ID_LENGTH


# --- 人物の切り抜きの引数 (#183) ---

@pytest.mark.parametrize("extra", [["--fps", "120"], ["--fps", "0"], ["--fps", "-1"], ["--fps", "nan"],
                                   ["--long-side", "0"], ["--long-side", "-1"], ["--long-side", "99999"]])
def test_person_split_refuses_bad_values(extra):
    from mvcodec import person

    with pytest.raises(SystemExit) as error:
        person.main(["in.mp4", "mask.mp4", "bg.mp4", *extra])
    assert error.value.code == 2


# --- 名前 (#186・#187・#193・#194) ---

def _video(video_id="demo", frames_js='export const FRAME_DATA = {"frame_count":1,"width":1,"height":1};', **extra):
    return {"id": video_id, "title": video_id, "frames_js": frames_js, "thumbnail": None, "sounds": [], **extra}


def test_ids_and_prefixes_have_a_length_limit():
    pack_builder.check_names(["a" * limits.MAX_ID_LENGTH], "b" * limits.MAX_NAMESPACE_LENGTH)
    with pytest.raises(ValueError, match="64"):
        pack_builder.check_names(["a" * (limits.MAX_ID_LENGTH + 1)], "ns")
    with pytest.raises(ValueError, match="32"):
        pack_builder.check_names(["a"], "b" * (limits.MAX_NAMESPACE_LENGTH + 1))


@pytest.mark.parametrize("name", ["Evil\nName", "Evil\rPack\x01", "", "x" * 101])
def test_pack_names_with_control_characters_are_refused(name):
    with pytest.raises(ValueError):
        pack_builder.build_addon_bytes([_video()], name, "ns")


def test_titles_lose_their_control_characters():
    data = pack_builder.build_addon_bytes([_video(title="line\none")], "P", "ns")
    videos = zipfile.ZipFile(io.BytesIO(data)).read("BP/scripts/videos.js").decode("utf-8")
    assert "line one" in videos and "line\\none" not in videos


def test_a_missing_stage_background_is_refused():
    figure = 'export const FRAME_DATA = {"frame_count":1,"width":1,"height":1,"stage_background": "nope"};'
    with pytest.raises(ValueError, match="nope"):
        pack_builder.build_addon_bytes([_video(frames_js=figure)], "P", "ns")
    background = _video("nope", hidden=True)
    pack_builder.build_addon_bytes([_video(frames_js=figure), background], "P", "ns")


# --- 止まった変換の後片付け (#180) ---

def _dead_pid():
    """もう終わったプロセスの番号。"""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_stale_part_files_of_killed_runs_are_removed(tmp_path):
    output = tmp_path / "big.mcaddon"
    stale = tmp_path / f".big.mcaddon.{_dead_pid()}.part"
    stale.write_bytes(b"x" * 1000)
    mine = tmp_path / f".big.mcaddon.{os.getpid()}.other"
    mine.write_bytes(b"keep")
    fileio.write_file_atomic(output, b"new")
    assert output.read_bytes() == b"new"
    assert not stale.exists() and mine.exists()


def test_work_folders_of_killed_runs_are_removed(tmp_path):
    dead = tmp_path / f"bvp-image-{_dead_pid()}-abcd"
    alive = tmp_path / f"bvp-image-{os.getpid()}-efgh"
    old = tmp_path / "bvp-image-zz9x"
    new = tmp_path / "bvp-image-yy8w"
    for folder in (dead, alive, old, new):
        (folder / "audio").mkdir(parents=True)
    past = time.time() - fileio.OLD_TEMP_SECONDS - 60
    os.utime(old, (past, past))
    removed = fileio.sweep_temp_dirs("bvp-image-", tmp_path)
    assert set(removed) == {dead, old}
    assert alive.exists() and new.exists()


# --- Windows 版の画面 (#185・#197) ---

def test_derived_ids_are_checked_before_converting():
    """GUI が足す <ID>_flat・<ID>_bg と、利用者の動画 ID の重なりを、作り始める前に見つける (#185)。"""
    import video_player_gui as gui

    entries = [{"video_id": "a", "file_path": "a.mp4"}, {"video_id": "a_flat", "file_path": "b.mp4"}]
    ids = gui.planned_video_ids(entries, "relief", False, None, True)
    assert ids.count("a_flat") == 2
    assert gui.id_problems(ids)[0] == ("error.id_conflict", "a_flat")
    entries = [{"video_id": "a", "file_path": "a.mp4"}, {"video_id": "a_bg", "file_path": "b.mp4"}]
    assert ("error.id_conflict", "a_bg") in gui.id_problems(gui.planned_video_ids(entries, "relief", True, "bg.mp4", False))
    # 平面では _flat も _bg も足さない
    assert gui.id_problems(gui.planned_video_ids(entries, "screen", True, "bg.mp4", True)) == []
    assert gui.id_problems(["x" * 65])[0][0] == "error.id_too_long"


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _headless(cancel=False):
    import video_player_gui as gui
    from gui_i18n import Translator

    class Headless:
        def __init__(self):
            self.tr = Translator("en")
            self.messages = queue.Queue()
            self.keyframe_interval_var = _Var(30)
            self.perceptual_var = _Var(True)
            self.cancel_event = threading.Event()
            if cancel:
                self.cancel_event.set()
            self.active_processes = set()
            self.process_lock = threading.Lock()

        def _run(self, command):
            gui.PackBuilderApp._run(self, command)

        def _extract_audio(self, *args):
            return gui.PackBuilderApp._extract_audio(self, *args)

        def _write_previews(self, *args):
            return gui.PackBuilderApp._write_previews(self, *args)

    return Headless()


def _messages(app):
    out = []
    while not app.messages.empty():
        out.append(app.messages.get())
    return out


@needs_ffmpeg
def test_one_broken_file_does_not_stop_the_others(tmp_path):
    """1 本が作れなくても、ほかの動画でアドオンを作り、作れなかった動画を知らせる (#197)。"""
    import video_player_gui as gui

    good = tmp_path / "good.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=10:duration=1",
                    "-pix_fmt", "yuv420p", str(good)], check=True)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video" * 200)
    app = _headless()
    output = tmp_path / "out.mcaddon"
    entries = [{"file_path": str(good), "video_id": "good", "thumb_sec": 0},
               {"file_path": str(broken), "video_id": "broken", "thumb_sec": 0}]
    gui.PackBuilderApp._build_pack(app, entries, output, "P", "badapple", 16, 9, 10, None, "full", "none", "cpu")
    log = _messages(app)
    result = [m for m in log if isinstance(m, tuple)]
    assert result and result[-1][0] == "success", result
    assert "broken" in result[-1][1] and output.is_file()
    names = zipfile.ZipFile(output).namelist()
    assert "BP/scripts/frames_good.js" in names and "BP/scripts/frames_broken.js" not in names


@needs_ffmpeg
def test_all_files_broken_reports_the_failure(tmp_path):
    import video_player_gui as gui

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video" * 200)
    app = _headless()
    gui.PackBuilderApp._build_pack(app, [{"file_path": str(broken), "video_id": "broken", "thumb_sec": 0}],
                                   tmp_path / "out.mcaddon", "P", "badapple", 16, 9, 10, None, "full", "none", "cpu")
    result = [m for m in _messages(app) if isinstance(m, tuple)]
    assert result[-1][0] == "error"
    assert not (tmp_path / "out.mcaddon").exists()


@needs_ffmpeg
def test_a_cancelled_build_stops_and_says_so(tmp_path):
    """中止したら、作らずに「中止しました」(#196)。"""
    import video_player_gui as gui

    app = _headless(cancel=True)
    gui.PackBuilderApp._build_pack(app, [{"file_path": "missing.mp4", "video_id": "a", "thumb_sec": 0}],
                                   tmp_path / "out.mcaddon", "P", "badapple", 16, 9, 10, None, "full", "none", "cpu")
    result = [m for m in _messages(app) if isinstance(m, tuple)]
    assert result == [("cancelled", app.tr("build.cancelled"))]
