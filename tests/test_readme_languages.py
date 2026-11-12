"""README の各言語 (README.md = 日本語、README.<言語>.md) の上の、言語の切り替えのテスト。

どのページにも同じ 10 言語が同じ順で並び、自分の言語だけが太字 (リンク無し)、ほかはあるファイルへのリンクであること。
各ページから相対パスで参照しているファイル (docs/... の画像や文書) があることも確かめる。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["README.en.md", "README.zh-CN.md", "README.zh-TW.md", "README.md", "README.ko.md", "README.de.md",
         "README.fr.md", "README.es.md", "README.pt-BR.md", "README.ru.md"]
BAR = re.compile(r'<p align="center">\s*((?:(?:<b>[^<]+</b>|<a href="[^"]+">[^<]+</a>)\s*·?\s*)+)</p>')
ITEM = re.compile(r'<b>([^<]+)</b>|<a href="([^"]+)">([^<]+)</a>')


def language_bar(page: str) -> list[tuple[str, str | None]]:
    """(表示名, リンク先。自分のページなら None) の一覧。"""
    text = (ROOT / page).read_text(encoding="utf-8")
    match = BAR.search(text[:3000])
    assert match, f"{page}: 上に言語の切り替えが無い"
    return [(bold, None) if bold else (name, href) for bold, href, name in ITEM.findall(match.group(1))]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_lists_all_languages_in_the_same_order(page):
    bar = language_bar(page)
    names = [name for name, _ in bar]
    assert names == [name for name, _ in language_bar("README.md")]
    assert len(bar) == len(PAGES)
    targets = [href for _, href in bar]
    # 自分の言語だけ太字、ほかは自分以外のページへ
    assert targets.count(None) == 1
    assert targets.index(None) == PAGES.index(page)
    for index, href in enumerate(targets):
        if href is not None:
            assert href == PAGES[index] and (ROOT / href).is_file()


@pytest.mark.parametrize("page", PAGES)
def test_relative_links_point_to_existing_files(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    links = re.findall(r'\]\(([^)#\s]+)(?:#[^)]*)?\)|src="([^"]+)"|href="([^"#]+)"', text)
    for groups in links:
        target = next(g for g in groups if g)
        if re.match(r"^[a-z]+:", target):
            continue  # https: など
        assert (ROOT / target).exists(), f"{page}: {target} が無い"
