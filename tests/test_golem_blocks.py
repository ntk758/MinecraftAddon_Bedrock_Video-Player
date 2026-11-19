"""ゴーレムの頭になるブロック (くり抜いたカボチャなど) を、動画のブロックとして置かない。

雪のブロック・銅のブロック・鉄のブロックの上にくり抜いたカボチャがあると、ゲームがゴーレムを作り、ブロックが消える
(銅のブロックは銅のチェストになる)。立てた画面 (スクリーンブロック・setup wall) と立体では上下に並ぶので、PC の実機で
画面からコッパーゴーレムが出て、画面が崩れた。main.js は置くときに別のブロックに置き換える (BLOCK_ID_ALIASES)。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))
from mvcodec.block_colors import BLOCK_COLORS  # noqa: E402

# ゴーレムの頭 (Bedrock: 雪だるま・アイアンゴーレム・コッパーゴーレム)
GOLEM_HEADS = {"minecraft:carved_pumpkin", "minecraft:lit_pumpkin"}


def aliases():
    text = (ROOT / "main.js").read_text(encoding="utf-8")
    body = text[text.index("const BLOCK_ID_ALIASES = {"):]
    body = body[:body.index("};")]
    return dict(re.findall(r'"(minecraft:[a-z_]+)":\s*"(minecraft:[a-z_]+)"', body))


def test_golem_heads_are_replaced_when_placed():
    table = {entry["block"] for entry in BLOCK_COLORS}
    used = table & GOLEM_HEADS
    assert used, "色の表にゴーレムの頭が無くなったら、このテストは要らない"
    mapping = aliases()
    for block in used:
        assert block in mapping, block
        assert mapping[block] not in GOLEM_HEADS


def test_the_replacement_looks_the_same():
    """置き換えた先のブロックは、上面・側面の色が同じ (顔は 1 面だけなので、平均の色は変わらない)。"""
    assert json.dumps(aliases()["minecraft:carved_pumpkin"]) == '"minecraft:pumpkin"'
