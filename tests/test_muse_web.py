"""外部の監査 (muse、2026-11) で見つかったブラウザ版の問題の回帰テスト (ネットワークもブラウザも使わない所)。

切れた動画・中止・1 本の失敗・設定の保存などの動きは、組み立てたサイトをブラウザで動かして確かめた (PR の説明)。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_pyodide  # noqa: E402

WEB = ROOT / "web"


def test_pyodide_files_are_pinned():
    """サイトに置く Pyodide の本体は SHA-256 を決めてある (#175)。版は worker.js と同じ。"""
    assert set(fetch_pyodide.CORE_FILES) == {"pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip",
                                             "pyodide-lock.json"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in fetch_pyodide.CORE_FILES.values())
    worker = (WEB / "worker.js").read_text(encoding="utf-8")
    assert f'const PYODIDE_VERSION = "{fetch_pyodide.PYODIDE_VERSION}";' in worker
    assert 'loadPackage(["numpy", "pillow"])' in worker and set(fetch_pyodide.PACKAGES) == {"numpy", "pillow"}


def test_packages_follow_their_dependencies():
    lock = {"packages": {"numpy": {"file_name": "numpy.whl", "sha256": "a" * 64, "depends": []},
                         "pillow": {"file_name": "pillow.whl", "sha256": "b" * 64, "depends": ["zlib"]},
                         "zlib": {"file_name": "zlib.whl", "sha256": "c" * 64, "depends": []}}}
    assert fetch_pyodide.package_files(lock) == {"numpy.whl": "a" * 64, "pillow.whl": "b" * 64, "zlib.whl": "c" * 64}


def test_a_changed_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_pyodide, "fetch", lambda name, cache: b"tampered")
    assert fetch_pyodide.main(["--out", str(tmp_path / "pyodide")]) == 1


def test_the_site_gets_pyodide_and_is_rebuilt_for_the_screen_scripts():
    pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "python scripts/fetch_pyodide.py --out web_dist/pyodide" in pages
    assert pages.index("scripts/build_web.py --out web_dist") < pages.index("scripts/fetch_pyodide.py --out web_dist/pyodide")
    for path in ("'screen_system.js'", "'screen_ui.js'", "'image_mode/**'", "'scripts/fetch_pyodide.py'"):
        assert path in pages, path


def test_the_worker_clears_old_frames_before_writing():
    """失敗した前の作成のコマが残り、次の作成に混ざっていた (#171・#203)。書く前と、1 本を作り終えた後に消す。"""
    worker = (WEB / "worker.js").read_text(encoding="utf-8")
    job = worker[worker.index("for (const [n, job] of jobs.entries())"):]
    assert job.index("clearWorkDir(pyodide, dir);") < job.index("pyodide.FS.writeFile(`${dir}/frame_")
    assert "} finally {\n        clearWorkDir(pyodide, dir);" in job.replace("\r\n", "\n")


def test_app_limits_match_the_converter():
    from mvcodec import limits

    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert f"const MAX_ID_LENGTH = {limits.MAX_ID_LENGTH};" in app
    assert f"const DEFAULT_ID_LENGTH = {limits.DEFAULT_ID_LENGTH};" in app
