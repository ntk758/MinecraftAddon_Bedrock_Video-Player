"""2026-11-20〜21 の外部の監査 (muse、GitHub の #155〜#206) で見つかった変換器の問題の回帰テスト。

切れた・壊れた動画 (#155〜#157・#160・#162・#169・#176〜#178)、.vox (#159・#161)、PMX (#184)、
アナモルフィック (#166)、作れない fps (#168)。
"""
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
import mmd_fixture  # noqa: E402
from mvcodec import mmd, truncation, volume  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")


def _finishes(function, seconds=30.0):
    """function() を別のスレッドで呼び、(終わったか, 戻り値か例外)。直す前は終わらなかった呼び出しに使う。"""
    result = {}

    def run():
        try:
            result["value"] = function()
        except BaseException as error:  # noqa: BLE001
            result["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive(), result


# --- 判定 (ffmpeg を使わない) ---

def test_a_cut_file_without_a_length_is_truncation():
    """長さの情報が無くても、ファイルの終わりが欠けた文があれば止める (#156)。"""
    with pytest.raises(truncation.TruncatedVideoError, match="the end of the file is missing"):
        truncation.check_complete("v.mp4", 29, 10, None, "[mov] stream 0, offset 0x1418f: partial file")
    # 長さが分からず、壊れた文も無いなら止めない
    truncation.check_complete("v.mp4", 29, 10, None, "")


def test_a_cut_file_with_an_understated_length_is_truncation():
    """長さの情報 (6 秒) より長く読めた (8.1 秒) うえで、終わりが欠けた文があれば止める (#176)。"""
    with pytest.raises(truncation.TruncatedVideoError):
        truncation.check_complete("v.mkv", 81, 10, 6.0, "[matroska,webm] File ended prematurely")
    # 終わりの欠けた文が無ければ、長さの情報が短いだけ (止めない)
    truncation.check_complete("v.mkv", 81, 10, 6.0, "")


def test_webm_cut_message_is_recognised():
    """Matroska / WebM は「File ended prematurely」だけを書く (#157)。"""
    with pytest.raises(truncation.TruncatedVideoError):
        truncation.check_complete("v.webm", 29, 10, 5.0, "[in#0/matroska,webm @ 0x1] File ended prematurely")


def test_the_tolerance_is_capped_for_long_videos():
    """5% の許しは 2 秒まで: 10 分の動画で 30 秒欠けても通っていた (#169)。"""
    error = "[mov] stream 0, offset 0x1: partial file"
    with pytest.raises(truncation.TruncatedVideoError):
        truncation.check_complete("v.mp4", 5700, 10, 600.0, error)  # 30 秒足りない
    truncation.check_complete("v.mp4", 5985, 10, 600.0, error)  # 1.5 秒 (許しの中)


def test_a_short_video_without_errors_is_reported():
    """エラーの文の無い短さは止めないが、知らせる (#155: 映像 2.9 秒・音声 5 秒)。"""
    notes = []
    truncation.check_complete("v.mp4", 29, 10, 5.0, "", warn=notes.append)
    assert len(notes) == 1 and "2.9" in notes[0] and "5.0" in notes[0]
    notes.clear()
    truncation.check_complete("v.mp4", 50, 10, 5.0, "", warn=notes.append)
    assert notes == []


def test_damaged_parts_are_reported():
    """長さは足りていても、デコードのエラーがあれば知らせる (#162: 壊れたコマは前のコマで埋まる)。"""
    notes = []
    truncation.check_complete("v.mp4", 50, 10, 5.0, "[h264 @ 0x1] Invalid NAL unit size (2991 > 895).",
                              warn=notes.append)
    assert len(notes) == 1 and "Invalid NAL unit size" in notes[0]


def test_audio_shorter_than_the_video_is_reported():
    notes = []
    truncation.check_audio_length(2.9, 5.0, notes.append)
    assert len(notes) == 1 and "2.9" in notes[0]
    notes.clear()
    truncation.check_audio_length(4.9, 5.0, notes.append)
    truncation.check_audio_length(None, 5.0, notes.append)
    assert notes == []


class _Pipe:
    def __init__(self, data, size=4096):
        self.data, self.size, self.pos = data, size, 0

    def read(self, n):
        chunk = self.data[self.pos:self.pos + min(n, self.size)]
        self.pos += len(chunk)
        return chunk


def test_early_errors_survive_a_long_stderr():
    """エラーの文の後に 8 KB を超える別の文が続いても、判定には残る (#177)。"""
    noise = b"".join(b"[h264 @ 0x1] non-matching noise line %d\n" % n for n in range(600))
    stream = b"[mov] stream 1, offset 0x2: partial file\n" + noise
    collector = truncation.StderrCollector(_Pipe(stream)).start()
    collector.join()
    assert len(collector.text.encode()) <= 8192 and "partial file" not in collector.text
    with pytest.raises(truncation.TruncatedVideoError):
        truncation.check_complete("v.mp4", 29, 10, 5.0, collector.for_check)


def _fake_ffmpeg(monkeypatch, module, frames, width, height, stderr_bytes):
    """module.subprocess.Popen を、stderr に stderr_bytes を書いてから frames コマを出す Python に差し替える。"""
    script = (f"import sys; sys.stderr.buffer.write(b'Invalid data found when processing input\\n' * {stderr_bytes // 42}); "
              f"sys.stderr.flush(); sys.stdout.buffer.write(bytes({width * height * 3 * frames}))")
    real = subprocess.Popen

    def popen(cmd, *args, **kwargs):
        return real([sys.executable, "-c", script], *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(truncation, "probe_duration", lambda *a, **k: frames / 10)


def test_image_mode_does_not_deadlock_on_heavy_stderr(monkeypatch):
    """stderr を最後にまとめて読んでいたので、64 KB を超えるエラーで ffmpeg とこちらが待ち合って止まった (#160)。"""
    from image_mode import atlas

    _fake_ffmpeg(monkeypatch, atlas, 5, 8, 6, 200_000)
    monkeypatch.setattr(atlas, "probe_size", lambda *a, **k: (8, 6))
    notes = []
    done, result = _finishes(lambda: len(list(atlas.read_frames("v.mp4", 8, 6, 10, warn=notes.append))))
    assert done, "read_frames が終わらない (stderr が詰まっている)"
    assert result.get("value") == 5, result
    assert notes and "壊れた所" in notes[0]


def test_block_mode_keeps_early_errors(monkeypatch):
    """ブロックモードでも、前の方のエラーの文が判定に残る (#177)。"""
    real = subprocess.Popen
    noise = "x" * 120
    script = ("import sys; sys.stderr.write('[mov] stream 0, offset 0x1: partial file\\n'); "
              f"[sys.stderr.write('{noise}\\n') for _ in range(200)]; sys.stderr.flush(); "
              f"sys.stdout.buffer.write(bytes({8 * 6 * 3 * 29}))")
    monkeypatch.setattr(convert.subprocess, "Popen", lambda cmd, *a, **k: real([sys.executable, "-c", script], *a, **k))
    monkeypatch.setattr(truncation, "probe_duration", lambda *a, **k: 5.0)
    with pytest.raises(truncation.TruncatedVideoError):
        list(convert.ffmpeg_frame_generator("v.mp4", 8, 6, 10))


# --- 実際の動画 (ffmpeg) ---

@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    folder = tmp_path_factory.mktemp("muse_clips")
    src = ["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=5", "-f", "lavfi", "-i", "sine=d=5", "-shortest"]
    webm = folder / "full.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-c:v", "libvpx", "-b:v", "200k", "-c:a", "libvorbis", str(webm)],
                   check=True)
    data = webm.read_bytes()
    cut_webm = folder / "cut.webm"
    cut_webm.write_bytes(data[:int(len(data) * 0.6)])
    short_video = folder / "short_video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=5",
                    "-f", "lavfi", "-i", "sine=d=5", "-filter_complex", "[0:v]trim=0:2.9,setpts=PTS-STARTPTS[v]",
                    "-map", "[v]", "-map", "1:a", "-pix_fmt", "yuv420p", str(short_video)], check=True)
    squeezed = folder / "sar2.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:size=40x80:rate=10:duration=1",
                    "-vf", "setsar=2/1", "-pix_fmt", "yuv420p", str(squeezed)], check=True)
    return {"folder": folder, "cut_webm": cut_webm, "webm": webm, "short_video": short_video, "sar2": squeezed}


@needs_ffmpeg
def test_a_cut_webm_is_refused(clips):
    with pytest.raises(truncation.TruncatedVideoError):
        list(convert.ffmpeg_frame_generator(clips["cut_webm"], 16, 9, 10))
    assert len(list(convert.ffmpeg_frame_generator(clips["webm"], 16, 9, 10))) == 50


@needs_ffmpeg
def test_a_video_shorter_than_its_audio_is_reported(clips, capsys):
    convert._WARNED.clear()
    assert len(list(convert.ffmpeg_frame_generator(clips["short_video"], 16, 9, 10))) == 29
    out = capsys.readouterr().out
    assert "2.9" in out and "5.0" in out


@needs_ffmpeg
def test_the_display_aspect_is_used(clips):
    """SAR 2:1 の 40x80 は、表示では 80x80 の正方形。80x40 の画面に pad すると、左右に黒い帯が出る (#166)。"""
    from image_mode import atlas
    from mvcodec import person

    frame = next(convert.ffmpeg_frame_generator(clips["sar2"], 80, 40, 10))
    # 表示どおりなら 40x40 の赤い正方形が真ん中 (左右 20 ずつ黒)。SAR を無視すると 20x40 で、左右 30 ずつ黒
    red = (frame[20, :, 0] > 128) & (frame[20, :, 1] < 80)
    assert 36 <= int(red.sum()) <= 44, int(red.sum())
    image = next(atlas.read_frames(clips["sar2"], 80, 40, 10))
    assert 36 <= int(((image[20, :, 0] > 128) & (image[20, :, 1] < 80)).sum()) <= 44
    assert atlas.probe_size(clips["sar2"]) == (80, 80)
    person_frame = next(person.read_frames(clips["sar2"], 10, long_side=80))
    assert person_frame.shape[:2] == (80, 80)


@needs_ffmpeg
def test_an_fps_that_makes_no_frame_is_explained(clips):
    with pytest.raises(ValueError, match="1 コマも作れません"):
        list(convert.ffmpeg_frame_generator(clips["sar2"], 16, 9, 0.1))


# --- .vox ---

def _chunk(chunk_id, content=b"", children=b""):
    return chunk_id.encode("ascii") + struct.pack("<ii", len(content), len(children)) + content + children


def _vox(*chunks):
    return b"VOX " + struct.pack("<i", 150) + _chunk("MAIN", children=b"".join(chunks))


def _dict(values=None):
    values = values or {}
    out = struct.pack("<i", len(values))
    for key, value in values.items():
        out += struct.pack("<i", len(key)) + key.encode() + struct.pack("<i", len(value)) + value.encode()
    return out


SIZE = _chunk("SIZE", struct.pack("<iii", 2, 2, 2))
XYZI = _chunk("XYZI", struct.pack("<i", 1) + bytes([0, 0, 0, 1]))


def _trn(node_id, child):
    return _chunk("nTRN", struct.pack("<i", node_id) + _dict() + struct.pack("<iiii", child, -1, 0, 1) + _dict())


def _grp(node_id, children):
    return _chunk("nGRP", struct.pack("<i", node_id) + _dict() + struct.pack("<i", len(children))
                  + b"".join(struct.pack("<i", c) for c in children))


def _shp(node_id, model):
    return _chunk("nSHP", struct.pack("<i", node_id) + _dict() + struct.pack("<ii", 1, model) + _dict())


def test_a_negative_chunk_size_is_refused_quickly():
    """content + children が負の .vox で、読む位置が進まずに止まらなかった (#159)。"""
    bad = b"VOX " + struct.pack("<i", 150) + b"MAIN" + struct.pack("<ii", 0, 92) + b"SIZE" + struct.pack("<ii", -12, 0)
    bad += bytes(80)
    done, result = _finishes(lambda: volume.parse_vox(bad))
    assert done, "parse_vox が終わらない"
    assert isinstance(result.get("error"), ValueError) and "大きさ" in str(result["error"])


@pytest.mark.parametrize("graph", [
    [_trn(0, 1), _grp(1, [1])],                 # 自分自身
    [_trn(0, 1), _grp(1, [2]), _trn(2, 1)],     # 互いに
    [_trn(0, 1), _grp(1, [0])],                 # 根へ戻る
])
def test_scene_graph_cycles_are_refused(graph):
    with pytest.raises(ValueError, match="循環"):
        volume.parse_vox(_vox(SIZE, XYZI, *graph))


@pytest.mark.parametrize("graph, words", [
    ([_trn(0, 1), _shp(1, 999)], "モデルの番号"),
    ([_trn(0, 1), _shp(1, -1)], "モデルの番号"),   # 負の番号で最後のモデルを黙って使っていた
    ([_trn(0, 999)], "子 999"),
])
def test_scene_graph_references_are_checked(graph, words):
    with pytest.raises(ValueError, match=words):
        volume.parse_vox(_vox(SIZE, XYZI, *graph))


@pytest.mark.parametrize("count", [1, -1, 1000])
def test_voxel_counts_must_match_the_chunk(count):
    """XYZI の数が中身 (8 個) と違えば壊れている (少ない数で 7 個を黙って落としていた。#161)。"""
    voxels = bytes(sum(([x, y, z, 1] for x in (0, 1) for y in (0, 1) for z in (0, 1)), []))
    with pytest.raises(ValueError, match="XYZI"):
        volume.parse_vox(_vox(SIZE, _chunk("XYZI", struct.pack("<i", count) + voxels)))


def test_a_normal_scene_still_loads():
    frames, _palette = volume.parse_vox(_vox(SIZE, XYZI, _trn(0, 1), _grp(1, [2]), _trn(2, 3), _shp(3, 0)))
    assert len(frames) == 1 and len(frames[0][1]) == 1


# --- PMX の材質の面の数 (#184) ---

def _pmx_with_face_count(tmp_path, count):
    data = bytearray(mmd_fixture.pmx_bytes())
    start = bytes(data).index(mmd_fixture._text("m0"))
    struct.pack_into("<i", data, start + 92, count)
    path = tmp_path / "doll.pmx"
    path.write_bytes(bytes(data))
    return path


def test_the_fixture_face_count_offset_is_right(tmp_path):
    original = mmd.read_pmx(_pmx_with_face_count(tmp_path, 36))
    assert original.materials[0]["face_count"] == 36


@pytest.mark.parametrize("count", [39, 34, 37, -3])
def test_material_face_counts_are_checked(tmp_path, count):
    with pytest.raises(ValueError, match="壊れ"):
        mmd.read_pmx(_pmx_with_face_count(tmp_path, count))


def test_unused_faces_are_allowed(tmp_path):
    """材質に入らない面がある (合計が少ない) だけなら読む (描かれない面)。"""
    model = mmd.read_pmx(_pmx_with_face_count(tmp_path, 30))
    assert model.materials[0]["face_count"] == 30
