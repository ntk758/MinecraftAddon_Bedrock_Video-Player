"""EXE (PyInstaller) に入れるデータファイルの検査。

v6.41.0 の EXE は screen_system.js・screen_ui.js が入っておらず、GUI では .mcaddon を作れなかった (#195)。
ここでは EXE と同じ並び (Python のモジュールと、build_standalone.bundled_data() のファイルだけ) を作り、
`--self-check` (GUI と同じ組み立て) が通ることを確かめる。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import build_standalone
import pack_builder

ROOT = Path(__file__).resolve().parent.parent


def test_every_addon_script_is_bundled():
    bundled = {source.resolve() for source, _ in build_standalone.bundled_data()}
    for name in pack_builder.ADDON_SCRIPTS:
        assert (ROOT / name).resolve() in bundled, name
    assert pack_builder.MANIFEST.resolve() in bundled


def test_the_bundle_layout_can_build_an_addon(tmp_path):
    app = tmp_path / "_internal"
    app.mkdir()
    for path in ROOT.glob("*.py"):
        shutil.copy2(path, app / path.name)
    for package in ("mvcodec", "image_mode"):
        (app / package).mkdir()
        for path in (ROOT / package).glob("*.py"):
            shutil.copy2(path, app / package / path.name)
    for source, target in build_standalone.bundled_data():
        place = app / target
        place.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, place / source.name if target == "." else place, dirs_exist_ok=True)
        else:
            shutil.copy2(source, place / source.name)
    result = subprocess.run([sys.executable, "video_player_gui.py", "--self-check"], cwd=app, capture_output=True,
                            text=True, encoding="utf-8", timeout=300, check=False)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    assert "self-check ok" in result.stdout


def test_a_missing_script_fails_the_self_check(tmp_path, monkeypatch):
    """足りないファイルがあれば、self-check は終了コード 1 になる (リリースのワークフローが止まる)。"""
    import video_player_gui

    monkeypatch.setattr(pack_builder, "SCREEN_SYSTEM_SCRIPT", tmp_path / "missing.js")
    assert video_player_gui.run_self_check_mode([]) == 1


def test_the_release_workflow_runs_the_self_check():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    exe_job = text[text.index("  exe:\n"):text.index("  ci:\n")]
    assert "'--self-check'" in exe_job
    assert exe_job.index("'--self-check'") < exe_job.index("- name: Zip")
