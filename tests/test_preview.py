"""できあがりのプレビュー (mvcodec/preview.py) のテスト。

プレビューの絵が、ゲームと同じ規則の参照デコーダで復元した盤面とそろっていること、間引きの幅が合計の上限を
守ること、GIF に保存できることを確かめる。
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from mvcodec.preview import choose_step, render_preview, save_gif, volume_unit  # noqa: E402
from mvcodec.volume import iso_view  # noqa: E402

WIDTH, HEIGHT, FRAMES = 32, 18, 24


@pytest.fixture(scope="module")
def frames_dir(tmp_path_factory):
    """動く四角と、色が途中で変わる背景。"""
    folder = tmp_path_factory.mktemp("frames")
    for t in range(FRAMES):
        arr = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        arr[:] = (30, 60, 200) if t < FRAMES // 2 else (220, 180, 40)
        arr[4:12, t % (WIDTH - 8):t % (WIDTH - 8) + 8] = (240, 240, 240)
        Image.fromarray(arr).save(folder / f"frame_{t:04d}.png")
    return folder


def run(frames_dir, out, *extra):
    argv = ["--frames-dir", str(frames_dir), "--output", str(out), "--width", str(WIDTH), "--height", str(HEIGHT),
            "--fps", "10", "--threads", "1", "--device", "cpu", *extra]
    assert convert.main(argv) == 0
    return load_frame_data(out)


def test_flat_preview_is_the_decoded_board(frames_dir, tmp_path):
    data = run(frames_dir, tmp_path / "flat.js")
    preview = render_preview(data)
    expected = [rgb for _, rgb in iter_decoded_frames(data)]
    assert (preview.width, preview.height, preview.step, preview.fps) == (WIDTH, HEIGHT, 1, 10.0)
    assert len(preview.frames) == preview.total_frames == len(expected) == FRAMES
    for got, want in zip(preview.frames, expected):
        assert np.array_equal(got, want)
    assert len(preview.packed()) == FRAMES * WIDTH * HEIGHT * 3


def test_volume_preview_matches_iso_view(frames_dir, tmp_path):
    data = run(frames_dir, tmp_path / "relief.js", "--mode", "relief", "--depth", "6")
    preview = render_preview(data)
    assert preview.volume and len(preview.frames) == FRAMES
    sides = iter_decoded_frames(data, face="side")
    for i, (index, rgb) in enumerate(iter_decoded_frames(data)):
        want = iso_view(index, rgb=rgb, unit=volume_unit(data), side_rgb=next(sides)[1])
        if i in (0, FRAMES - 1):
            assert np.array_equal(preview.frames[i], want)


def test_long_videos_are_thinned_to_the_budget(frames_dir, tmp_path):
    data = run(frames_dir, tmp_path / "flat.js")
    frame_bytes = WIDTH * HEIGHT * 3
    preview = render_preview(data, max_bytes=frame_bytes * 5)
    assert preview.step == choose_step(FRAMES, frame_bytes, False, frame_bytes * 5) == 5
    assert len(preview.frames) <= 5 and preview.fps == 2.0
    expected = [rgb for _, rgb in iter_decoded_frames(data)][::5]
    assert all(np.array_equal(a, b) for a, b in zip(preview.frames, expected))


def test_choose_step_limits():
    assert choose_step(100, 1000, False, max_bytes=10_000) == 10
    assert choose_step(10, 1000, False, max_bytes=10_000) == 1
    assert choose_step(1000, 10, True, max_bytes=10**9, max_volume_frames=100) == 10
    assert choose_step(0, 10, False) == 1


def test_save_gif(frames_dir, tmp_path):
    data = run(frames_dir, tmp_path / "flat.js")
    preview = render_preview(data)
    path = tmp_path / "preview.gif"
    save_gif(preview, path)
    with Image.open(path) as gif:
        assert gif.n_frames == FRAMES
        assert gif.size == (WIDTH * 15, HEIGHT * 15)  # 長い辺が 480 に収まる整数倍
        assert gif.info["duration"] == 100


def test_write_preview_gifs_next_to_the_addon(frames_dir, tmp_path):
    from mvcodec.preview import preview_gif_paths, write_preview_gifs

    run(frames_dir, tmp_path / "a.js")
    text = (tmp_path / "a.js").read_text(encoding="utf-8")
    output = tmp_path / "My Pack.mcaddon"
    assert preview_gif_paths(output, ["a"]) == {"a": tmp_path / "My Pack_preview.gif"}
    errors = []
    written = write_preview_gifs(output, [
        {"id": "a", "frames_js": text},
        {"id": "b", "frames_js": "export const FRAME_DATA = {};"},  # 壊れたデータは飛ばす
        {"id": "a_bg", "frames_js": text, "hidden": True},  # 背景の映像は作らない
    ], log=lambda vid, error: errors.append(vid))
    assert written == [tmp_path / "My Pack_preview_a.gif"]
    assert errors == ["b"]
    assert not (tmp_path / "My Pack_preview_a_bg.gif").exists()


def test_windows_app_writes_previews_and_logs_them(frames_dir, tmp_path):
    """Windows 版 (video_player_gui) は、作り終えたアドオンの隣にプレビューの GIF を書き、処理ログに場所を出す。"""
    import queue
    from types import SimpleNamespace

    pytest.importorskip("tkinter")
    import video_player_gui
    from gui_i18n import Translator

    run(frames_dir, tmp_path / "a.js")
    app = SimpleNamespace(messages=queue.Queue(), tr=Translator("en"))
    output = tmp_path / "pack.mcaddon"
    written = video_player_gui.PackBuilderApp._write_previews(
        app, output, [{"id": "a", "frames_js": (tmp_path / "a.js").read_text(encoding="utf-8")}])
    assert written == [tmp_path / "pack_preview.gif"] and written[0].stat().st_size > 0
    assert str(written[0]) in app.messages.get_nowait()
