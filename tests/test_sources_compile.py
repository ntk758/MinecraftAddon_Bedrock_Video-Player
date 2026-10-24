"""リポジトリの Python のファイルが、すべて文法どおりに読めること。

テストが import しない所 (コマンドラインの main など) の書き間違いは、EXE に入れるときに黙って抜け落ちる
(PyInstaller は読めないモジュールを入れない。人物の切り抜きのモジュールが EXE から抜けていたことがある)。
"""
import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(p for folder in ("", "mvcodec", "scripts", "tests", "benchmark")
                 for p in (ROOT / folder).glob("*.py"))


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_source_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)
