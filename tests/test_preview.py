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


# ---- ブラウザ版: 絵を先に作らず、再生しながら codec.js で 1 コマずつ描く (web/block_preview.js) ----

def render_in_browser_code(frames_js, data, order):
    """web/block_preview.js (Node) で order の順にフレームを描いた絵の一覧。"""
    import base64
    import json
    import shutil
    import subprocess

    from mvcodec.preview import preview_spec

    if shutil.which("node") is None:
        pytest.skip("node が必要")
    spec = preview_spec(data)
    spec["top"] = base64.b64encode(spec["top"]).decode()
    if spec.get("side"):
        spec["side"] = base64.b64encode(spec["side"]).decode()
    spec_path = Path(frames_js).with_suffix(".spec.json")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    result = subprocess.run(["node", str(Path(__file__).parent / "js" / "block_preview.mjs"), str(frames_js),
                             str(spec_path), ",".join(map(str, order))],
                            capture_output=True, text=True, check=True, timeout=300)
    lines = result.stdout.splitlines()
    info = json.loads(lines[0])
    frames = [np.frombuffer(base64.b64decode(line), np.uint8).reshape(info["height"], info["width"], 3)
              for line in lines[1:]]
    return info, frames


BROWSER_CASES = {
    "flat": [],
    "flat_short_gops": ["--keyframe-interval", "5"],
    "relief_heightfield": ["--mode", "relief", "--depth", "6", "--keyframe-interval", "7"],
    "relief_voxels": ["--mode", "relief", "--depth", "6", "--volume-encoding", "voxels", "--keyframe-interval", "7"],
    "extrude": ["--mode", "extrude", "--depth", "4"],
    "anamorphic": ["--mode", "inflate", "--depth", "8", "--view", "anamorphic", "--keyframe-interval", "6"],
    # 背景の色が変わる所で GOP を分け、GOP ごとに別のパレットにする (番号が変わらないマスは前の GOP の色のまま)
    "flat_palette_per_gop": ["--palette", "auto"],
    "relief_palette_per_gop": ["--palette", "auto", "--mode", "relief", "--depth", "6"],
}


@pytest.mark.parametrize("case", list(BROWSER_CASES))
def test_browser_preview_matches_render_preview_at_every_frame(frames_dir, tmp_path, monkeypatch, case):
    """ブラウザ版のプレビューは全部のコマを描き (間引かない)、Windows 版 (render_preview) と画素まで同じ。
    シーク (前に戻る・飛ばす) しても、頭から順に描いたときと同じ絵になる。"""
    if case.endswith("palette_per_gop"):
        monkeypatch.setattr(convert, "MIN_GOP_SIZE", 2)
    out = tmp_path / f"{case}.js"
    data = run(frames_dir, out, *BROWSER_CASES[case])
    if case.endswith("palette_per_gop"):
        assert len(data["chunks"]) >= 2 and data["adaptive_palette"]
    want = render_preview(data, max_bytes=1 << 40, max_volume_frames=10**9)
    assert want.step == 1 and len(want.frames) == FRAMES
    order = list(range(FRAMES)) + [FRAMES - 1, 3, 0, FRAMES // 2, FRAMES // 2 + 1, 2, FRAMES - 2]
    info, got = render_in_browser_code(out, data, order)
    assert (info["width"], info["height"], info["count"], info["fps"]) == (want.width, want.height, FRAMES, 10.0)
    for frame, image in zip(order, got):
        assert np.array_equal(image, want.frames[frame]), f"{case}: frame {frame}"


def test_browser_preview_repaints_at_a_keyframe_with_a_new_palette(tmp_path):
    """GOP の先頭 (キーフレーム) では、番号が同じマスもその GOP のパレットで塗り直す (ゲームは全部置き直す)。
    番号が 0 のままで、パレットの 0 番が白から黒に変わる 2 つの GOP。"""
    import json
    from types import SimpleNamespace

    from mvcodec.stream import StreamEncoder

    width, rows = 8, 4
    encoder = StreamEncoder(width=width, rows=rows, gop_boundaries=[0, 3, 6], adaptive_fps=False, entropy=True)
    for i in range(6):
        encoder.add(i, 0 if i < 3 else 1, np.zeros(width * rows, np.int32))
    chunks, index = encoder.finish()
    palette = convert.PALETTES["concrete"]
    white = next(p for p in palette if p["name"] == "white")
    black = next(p for p in palette if p["name"] == "black")
    data = convert.build_frame_data(SimpleNamespace(fps=10, keyframe_interval=30), width, rows, [0, 3, 6],
                                    [[white, black], [black, white]], True, encoder, chunks, index)
    out = tmp_path / "palettes.js"
    out.write_text("export const FRAME_DATA = " + json.dumps(data) + ";\n", encoding="utf-8")
    want = render_preview(load_frame_data(out))
    assert tuple(want.frames[2][0, 0]) == white["rgb"] and tuple(want.frames[3][0, 0]) == black["rgb"]
    order = [0, 1, 2, 3, 4, 5, 2, 4, 0, 5]
    _, got = render_in_browser_code(out, load_frame_data(out), order)
    for frame, image in zip(order, got):
        assert np.array_equal(image, want.frames[frame]), frame
