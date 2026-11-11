"""Windows 版の画面 (PackBuilderApp) を実際に作って確かめる: 動画 ID の重複・数字の ID、設定の確認、はじめてガイド。

画面を出せない環境 (CI の Linux など、ディスプレイが無い) では skip する。設定ファイルは読み書きしない。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tk = pytest.importorskip("tkinter")

import pack_builder  # noqa: E402
import video_player_gui as gui  # noqa: E402


@pytest.fixture(scope="module")
def shared_app():
    """画面は 1 つだけ作って使い回す (Tk を何度も作り直すと、環境によって Tcl の初期化に失敗することがある)。"""
    patch = pytest.MonkeyPatch()
    # 利用者の設定 (言語・はじめてガイドを出したか) を読まない・書かない
    patch.setattr(gui, "load_settings", lambda: {"wizard_shown": True})
    patch.setattr(gui, "save_settings", lambda settings: None)
    shown = []
    for name in ("showerror", "showwarning", "showinfo"):
        patch.setattr(gui.messagebox, name, lambda title, text, _name=name, **kw: shown.append((_name, text)))
    patch.setattr(gui.messagebox, "askyesno", lambda *args, **kw: False)
    try:
        application = gui.PackBuilderApp(language="en")
    except tk.TclError as error:
        patch.undo()
        pytest.skip(f"画面を作れない: {error}")
    application.withdraw()
    application.shown = shown
    yield application
    application.destroy()
    patch.undo()


@pytest.fixture
def app(shared_app, tmp_path):
    """テストごとに、動画の一覧・入力・言語・はじめてガイドを最初の状態に戻す。"""
    application = shared_app
    if application.wizard is not None and application.wizard.window.winfo_exists():
        application.wizard.window.destroy()
    application.wizard = None
    application.change_language("en")
    application.video_tree.delete(*application.video_tree.get_children())
    application.building = False
    application.namespace_var.set("badapple")
    application.keyframe_interval_var.set(30)
    application.output_var.set(str(tmp_path / "out.mcaddon"))
    application.shown.clear()
    return application


def test_numeric_file_names_get_distinct_ids(app, tmp_path):
    """数字だけの名前 (123.png) を 2 つ足すと、2 つ目は別の ID になる (以前は同じ ID のままアドオンが壊れていた)。"""
    (tmp_path / "dup").mkdir()
    first = app.add_video_path(tmp_path / "123.png")
    second = app.add_video_path(tmp_path / "dup" / "123.png")
    third = app.add_video_path(tmp_path / "007.png")
    assert [first, second, third] == ["123", "123_2", "007"]
    # 一覧から読み直しても文字のまま ("007" が 7 にならない)
    assert app.video_ids() == ["123", "123_2", "007"]


def test_ids_survive_a_language_switch(app, tmp_path):
    app.add_video_path(tmp_path / "007.png")
    app.add_video_path(tmp_path / "2024_01_05.mp4")
    app.change_language("ja")
    assert app.video_ids() == ["007", "2024_01_05"]


def test_editing_an_id_to_an_existing_one_is_refused(app, tmp_path, monkeypatch):
    from tkinter import simpledialog

    app.add_video_path(tmp_path / "a.mp4")
    app.add_video_path(tmp_path / "b.mp4")
    rows = app.video_tree.get_children()
    app.video_tree.selection_set(rows[1])
    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kw: "a")
    app._edit_video_id()
    assert app.video_ids() == ["a", "b"]
    assert app.shown and "a" in app.shown[-1][1]
    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kw: "c")
    app._edit_video_id()
    assert app.video_ids() == ["a", "c"]


def _ready_to_build(app, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    app.add_video_path(video)


@pytest.mark.parametrize("namespace", ["my-pack", "my.pack", "BAD", "minecraft", ""])
def test_namespaces_that_cannot_be_command_names_are_refused(app, tmp_path, namespace):
    _ready_to_build(app, tmp_path)
    app.namespace_var.set(namespace)
    app._start_build()
    assert not app.building
    assert app.shown[-1][0] == "showerror"


@pytest.mark.parametrize("keyframe", ["", "-5", "abc"])
def test_a_bad_keyframe_interval_is_refused_before_the_build_starts(app, tmp_path, keyframe):
    _ready_to_build(app, tmp_path)
    app.tk.globalsetvar(str(app.keyframe_interval_var), keyframe)
    app._start_build()
    assert not app.building
    assert app.shown[-1][0] == "showerror"


def test_wizard_label_goes_back_when_the_build_does_not_start(app, tmp_path):
    _ready_to_build(app, tmp_path)
    app._open_wizard()
    app.namespace_var.set("BAD NS")
    app.wizard.build()
    assert not app.building
    assert app.wizard.build_label.cget("text") == app.tr("wizard.build_hint")


def test_language_switch_reopens_the_wizard(app):
    app._open_wizard()
    app.change_language("ja")
    assert app.wizard is not None and app.wizard.window.winfo_exists()
    assert app.wizard.window.title() == app.tr("wizard.title")


def test_duplicate_video_ids_are_refused_when_packing():
    """同じ ID が 2 つあると videos.js の import がぶつかってアドオン全体が読み込めないので、組み立てで止める。"""
    video = {"id": "a", "title": "a", "frames_js": 'export const FRAME_DATA = {"frame_count":1,"width":1,"height":1};',
             "thumbnail": None, "sounds": []}
    with pytest.raises(ValueError, match="a"):
        pack_builder.build_addon_bytes([video, dict(video)], "P", "ns")
