"""フォームのボタンの絵 (Minecraft に入っているテクスチャ) の名前が、本当にある名前か。

無い名前を書くと、ボタンの横に紫と黒の格子 (テクスチャが無いときの絵) が出る。リモコン・スクリーンの操作画面のシークの絵が
"textures/items/clock" (本当は clock_item) で、紫と黒になっていた (スクリーンブロックを PC の実機で確かめたときに見つけた)。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [ROOT / "main.js", ROOT / "screen_ui.js", ROOT / "image_mode" / "image_main.js"]

# Minecraft v26.65 の vanilla のリソースパック (data/resource_packs/vanilla/contents.json) にあると確かめた名前。
# 新しい絵を使うときは、contents.json で名前を確かめてからここに足す
VANILLA_TEXTURES = {
    "textures/blocks/barrier",
    "textures/blocks/redstone_block",
    "textures/items/book_portfolio",
    "textures/items/clock_item",
    "textures/items/emerald",
    "textures/items/item_frame",
    "textures/items/paper",
    "textures/items/repeater",
}


def test_every_form_icon_is_a_vanilla_texture():
    used = set()
    for script in SCRIPTS:
        used |= set(re.findall(r'"(textures/(?:items|blocks)/[a-z0-9_]+)"', script.read_text(encoding="utf-8")))
    assert used, "フォームの絵が 1 つも見つからない (探し方が古い)"
    assert used <= VANILLA_TEXTURES, sorted(used - VANILLA_TEXTURES)
