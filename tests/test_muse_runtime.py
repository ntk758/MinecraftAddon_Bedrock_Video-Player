"""外部の監査 (muse、2026-11) で見つかったアドオンのスクリプトの問題の回帰テスト (codec.js の #200)。

スクリーンブロックの権限 (#198)・保存 (#199)・音 (#201) は tests/test_screen_block.py と tests/js/screen_system_test.mjs。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_codec_refuses_corrupt_counts():
    if shutil.which("node") is None:
        pytest.skip("node が必要")
    result = subprocess.run(["node", "--test", "tests/js/codec_bounds_test.mjs"], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=120, check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert re.search(r"^(?:ℹ|#) pass 3$", result.stdout, re.M), result.stdout[-2000:]


def test_screen_audio_is_not_stopped_for_everyone():
    """スクリーンの音は、鳴らした人にだけ stopsound する (@a では同じ動画のほかのスクリーンの音も消えた。#201)。"""
    main = (ROOT / "main.js").read_text(encoding="utf-8")
    renderer = main[main.index("class BlockScreenRenderer"):main.index("// </legacy:screens>")]
    assert "stopsound @a" not in renderer
    assert "stopsound @s" in renderer


def test_the_remotes_check_the_permission_level():
    """リモコン (コンパス・時計) はコマンドと同じ操作なので、ほかの人がいるときはオペレーターだけ (#198)。"""
    for path in ("main.js", "image_mode/image_main.js"):
        text = (ROOT / path).read_text(encoding="utf-8")
        handler = text[text.index("itemUse"):]
        handler = handler[:handler.index("});")]
        assert "isOperator(event.source)" in handler, path
        # enum を import しない (無い版の Script API で読み込めなくなる)
        imports = " ".join(re.findall(r"^import [^;]+;", text, re.M | re.S))
        assert "PlayerPermissionLevel" not in imports, path
