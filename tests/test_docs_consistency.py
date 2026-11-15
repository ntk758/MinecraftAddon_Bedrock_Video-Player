"""ドキュメントと設定に書いた版と色数が、実装 (唯一の管理場所) とずれていないこと (TODO.md の P0-1)。

文章の言い回しには頼らず、決まった目印 (見出し・キー・表の 1 行・ファイル名) だけを見る。
版の唯一の管理場所は pack_metadata.PACK_VERSION、色数は mvcodec.color の定義。
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.color import (AUTO_PALETTE_CANDIDATES, EVERY_BLOCK_PALETTE, MAX_PALETTE_BLOCKS,  # noqa: E402
                           OPAQUE_BLOCKS)
from pack_metadata import PACK_VERSION  # noqa: E402

VERSION = "v" + ".".join(map(str, PACK_VERSION))
LOCALES = sorted((ROOT / "locales").glob("*.json"))


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _numbers(text):
    return {int(n) for n in re.findall(r"\d+", text)}


# --- 版 ---

def test_manifest_matches_pack_version():
    manifest = json.loads(_read("manifest.json"))
    assert manifest["header"]["version"] == list(PACK_VERSION)
    assert [module["version"] for module in manifest["modules"]] == [list(PACK_VERSION)] * len(manifest["modules"])
    assert re.findall(r"v\d+\.\d+\.\d+", manifest["header"]["name"]) == [VERSION]
    assert re.findall(r"v\d+\.\d+\.\d+", manifest["header"]["description"]) == [VERSION]


def test_changelog_starts_with_the_pack_version():
    headings = re.findall(r"^## (v\d+\.\d+\.\d+)", _read("CHANGELOG.md"), re.MULTILINE)
    assert headings and headings[0] == VERSION


def test_project_status_names_the_pack_version():
    found = re.findall(r"^- \*\*最新バージョン\*\*: `(v\d+\.\d+\.\d+)`", _read("PROJECT_STATUS.md"), re.MULTILINE)
    assert found == [VERSION]


def test_readme_checksum_examples_name_the_released_zip():
    names = re.findall(r"BlockVideoPlayer-(v\d+\.\d+\.\d+)-windows-x64\.zip", _read("README.md"))
    assert len(names) >= 2   # Get-FileHash と sha256sum
    assert set(names) == {VERSION}


# --- 色数 ---

@pytest.mark.parametrize("path", LOCALES, ids=[p.name for p in LOCALES])
def test_locale_palette_labels_match_the_palettes(path):
    texts = json.loads(path.read_text(encoding="utf-8"))
    assert _numbers(texts["palette.auto"]) == {MAX_PALETTE_BLOCKS, len(AUTO_PALETTE_CANDIDATES)}
    assert _numbers(texts["palette.all"]) == {MAX_PALETTE_BLOCKS}


def test_web_palette_labels_match_the_palettes():
    html = _read("web/index.html")
    auto = re.search(r'data-i18n="palette\.auto">([^<]*)<', html).group(1)
    every = re.search(r'data-i18n="palette\.all">([^<]*)<', html).group(1)
    assert _numbers(auto) == {MAX_PALETTE_BLOCKS, len(AUTO_PALETTE_CANDIDATES)}
    assert _numbers(every) == {MAX_PALETTE_BLOCKS}


@pytest.mark.parametrize("pattern,expected", [
    (r"マイクラ実在ブロック (\d+) 色", len(OPAQUE_BLOCKS)),
    (r"(\d+) real block colors", len(OPAQUE_BLOCKS)),
    (r"(\d+) 色の候補からシーンごとに最大", len(AUTO_PALETTE_CANDIDATES)),
    (r"色の候補からシーンごとに最大 (\d+) 色", MAX_PALETTE_BLOCKS),   # 更新履歴の古い色数には当てない
    (r"光るブロックを含む (\d+) 色から", len(EVERY_BLOCK_PALETTE)),
    (r"`auto` \((\d+) 色から", len(AUTO_PALETTE_CANDIDATES)),
    (r"`all` \(全ブロックから色の離れた (\d+) 色\)", MAX_PALETTE_BLOCKS),
], ids=["opaque_ja", "opaque_en", "auto_candidates", "auto_max", "all_pool", "cli_auto", "cli_all"])
def test_readme_palette_counts_match_the_palettes(pattern, expected):
    found = re.findall(pattern, _read("README.md"))
    assert found, f"README.md に目印 {pattern!r} が見つからない (書き方を変えたら、このテストも直す)"
    assert {int(n) for n in found} == {expected}


def test_project_status_palette_counts_match_the_palettes():
    row = re.search(r"^\| 自動パレット \|.*$", _read("PROJECT_STATUS.md"), re.MULTILINE).group(0)
    assert re.search(r"(\d+) 色の候補", row).group(1) == str(len(AUTO_PALETTE_CANDIDATES))
    assert re.search(r"〜(\d+) 色", row).group(1) == str(MAX_PALETTE_BLOCKS)
    assert re.search(r"全ブロックは (\d+) 色", row).group(1) == str(len(EVERY_BLOCK_PALETTE))
