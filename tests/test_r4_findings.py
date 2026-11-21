"""v6.41 の後の外部の監査 (R4、GitHub の #210〜#216) の回帰テスト。

.vox の読み込み (#211)、画像モードの範囲の外のボクセル (#212)、文書・ブラウザ版・パックの変更履歴 (#210・#213〜#216)。
"""
import re
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pack_metadata  # noqa: E402
from image_mode import impostor  # noqa: E402
from mvcodec import volume  # noqa: E402


def _chunk(chunk_id, content=b"", children=b""):
    return chunk_id.encode("ascii") + struct.pack("<ii", len(content), len(children)) + content + children


def _vox(*chunks):
    return b"VOX " + struct.pack("<i", 150) + _chunk("MAIN", children=b"".join(chunks))


def _dict(values=None):
    out = struct.pack("<i", len(values or {}))
    for key, value in (values or {}).items():
        out += struct.pack("<i", len(key)) + key.encode() + struct.pack("<i", len(value)) + value.encode()
    return out


SIZE = _chunk("SIZE", struct.pack("<iii", 2, 2, 2))
XYZI = _chunk("XYZI", struct.pack("<i", 1) + bytes([0, 0, 0, 1]))


def _trn(node_id, child, frame=None):
    return _chunk("nTRN", struct.pack("<i", node_id) + _dict() + struct.pack("<iiii", child, -1, 0, 1)
                  + _dict(frame or {}))


def _shp(node_id, model, attrs=None):
    return _chunk("nSHP", struct.pack("<i", node_id) + _dict() + struct.pack("<ii", 1, model) + _dict(attrs or {}))


# --- #211: .vox のチャンクと属性 ---

@pytest.mark.parametrize("data, words", [
    # 20 バイトの細工: MAIN の子に、中身 4 バイトの SIZE (12 バイト要る)
    (_vox(_chunk("SIZE", b"\x02\x00\x00\x00")), "SIZE の中身"),
    (_vox(SIZE, _chunk("XYZI", b"\x01")), "XYZI の中身"),
    (_vox(SIZE, XYZI, _chunk("RGBA", bytes(100))), "RGBA"),
    (_vox(_chunk("SIZE", struct.pack("<iii", 2, -3, 2)), XYZI), "SIZE の大きさ"),
    (_vox(_chunk("SIZE", struct.pack("<iii", 0, 2, 2)), XYZI), "SIZE の大きさ"),
    (_vox(_chunk("SIZE", struct.pack("<iii", 100000, 2, 2)), XYZI), "SIZE の大きさ"),
    # SIZE → sIZE の 1 バイトの違い: XYZI の前に SIZE が無い
    (_vox(_chunk("sIZE", struct.pack("<iii", 2, 2, 2)), XYZI), "SIZE がありません"),
    # 辞書の数・文字列の長さが中身を超える
    (_vox(SIZE, XYZI, _chunk("nTRN", struct.pack("<i", 0) + struct.pack("<i", 50))), "nTRN"),
    (_vox(SIZE, XYZI, _chunk("nTRN", struct.pack("<ii", 0, 1) + struct.pack("<i", 9999))), "nTRN"),
    (_vox(SIZE, XYZI, _chunk("nGRP", struct.pack("<i", 1) + _dict() + struct.pack("<i", -5))), "nGRP"),
    (_vox(SIZE, XYZI, _chunk("META", struct.pack("<i", 1))), "META"),
])
def test_broken_vox_chunks_give_a_reason(data, words):
    with pytest.raises(ValueError, match=words):
        volume.parse_vox(data)


@pytest.mark.parametrize("graph, words", [
    ([_trn(0, 1), _shp(1, 0, {"_f": ""})], "_f"),
    ([_trn(0, 1), _shp(1, 0, {"_f": "abc"})], "_f"),
    ([_trn(0, 1, {"_t": "1 2"}), _shp(1, 0)], "_t"),
    ([_trn(0, 1, {"_t": "a b c"}), _shp(1, 0)], "_t"),
    ([_trn(0, 1, {"_r": "x"}), _shp(1, 0)], "_r"),
])
def test_scene_attributes_that_are_not_numbers_give_a_reason(graph, words):
    with pytest.raises(ValueError, match=words):
        volume.parse_vox(_vox(SIZE, XYZI, *graph))


def test_anim_range_that_is_not_numbers_gives_a_reason():
    meta = _chunk("META", _dict({"_anim_range": "0 x"}))
    with pytest.raises(ValueError, match="_anim_range"):
        volume.parse_vox(_vox(SIZE, XYZI, meta, _trn(0, 1), _shp(1, 0)))


def test_fuzzed_vox_files_only_fail_with_reasons():
    """壊したファイルは、読めるか、理由を書いた ValueError のどちらか (struct.error・TypeError・numpy の生の文が出ない)。"""
    rng = np.random.default_rng(7)
    models = [((4, 4, 3), np.array([[0, 0, 0, 1], [3, 3, 2, 5]], np.uint8)), ((2, 2, 2), np.array([[1, 1, 1, 7]], np.uint8))]
    scene = [{"translations": [(0, (0, 0, 0)), (3, (2, 1, 0))], "models": [(0, 0), (2, 1)]}]
    bases = [volume.write_vox(models), volume.write_vox(models, scene=scene)]
    for i in range(3000):
        data = bytearray(bases[i % 2])
        pos = int(rng.integers(0, len(data)))
        if i % 3 == 0:
            data[pos] = int(rng.integers(0, 256))
        elif i % 3 == 1:
            del data[pos:pos + int(rng.integers(1, 9))]
        else:
            data[pos:pos] = bytes(rng.integers(0, 256, 4).astype(np.uint8))
        try:
            frames, palette = volume.parse_vox(bytes(data))
        except ValueError as exc:
            assert ".vox" in str(exc), str(exc)
            continue
        impostor.dense_frames(frames, [palette] * len(frames))


# --- #212: 画像モードの範囲の外のボクセル ---

def test_image_mode_drops_voxels_outside_size_like_block_mode():
    """z = 102 (SIZE の z は 2) のボクセル。ブロックモードは落として変換でき、画像モードは IndexError で止まっていた。"""
    voxels = np.array([[0, 0, 0, 1], [1, 1, 102, 3]], np.uint8)
    frames, palette = volume.parse_vox(volume.write_vox([((2, 2, 2), voxels)]))
    volumes, _rgbs, shape = impostor.dense_frames(frames, [palette])
    assert shape == (2, 2, 2)
    assert np.count_nonzero(volumes[0]) == 1 and volumes[0][0, 0, 0] == 1
    block = volume.vox_to_volume(frames[0], volume.vox_volume_shape(frames), np.arange(256, dtype=np.int16))
    assert np.count_nonzero(block != volume.AIR_LEVEL) == 1


# --- #210・#213〜#216: 文書・ブラウザ版・パックの変更履歴 ---

def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_known_issues_warn_about_the_v6_41_0_exe_and_point_to_v6_41_1():
    known = _read("docs/KNOWN_ISSUES.md")
    line = next(l for l in known.splitlines() if "v6.41.0 の Windows 版 (EXE)" in l)
    assert "v6.41.1 で修正" in line and "ブラウザ版" in line and "--run-converter" in line
    for store in ("docs/store/curseforge.md", "docs/store/crafterscolony_ja.md"):
        assert "v6.41.1" in _read(store), store


def test_screen_block_verification_is_described_the_same_everywhere():
    known = _read("docs/KNOWN_ISSUES.md")
    assert "スクリーンブロックは実機で確かめていない" not in known
    assert "Next release, not yet checked in the game" not in _read("README.md")
    for text in (known, _read("README.md"), _read("docs/SCREEN_SYSTEM.md")):
        assert "Windows 11" in text


def test_static_howto_text_does_not_ask_for_cheats():
    html = _read("web/index.html")
    howto = re.search(r'<li id="howtoImport"[^>]*>(.*?)</li>', html).group(1)
    assert "cheats are not needed" in howto and "turn cheats on" not in howto
    assert "turn cheats on" not in _read("locales/en.json").split('"web.howto_import"')[1].split("\n")[0]


def test_usage_disclosure_lists_every_event_the_page_sends():
    """数えている出来事を足したら、説明 (10 言語) も直すこと (#215)。"""
    events = set(re.findall(r'Usage\.event\(\s*[`"]([a-z-]+)', _read("web/app.js")))
    assert events == {"build-done", "build-kind-", "build-lang-", "build-error", "build-start"}
    en = __import__("json").loads(_read("locales/en.json"))["web.usage_note"]
    for words in ("page opens", "started", "finished", "failed", "kind of add-on", "display language"):
        assert words in en
    assert en in _read("web/index.html")


def test_pack_changelog_is_a_labelled_summary_of_recent_versions():
    text = pack_metadata.changelog_markdown("Pack")
    assert f"v{pack_metadata.version_text()}" in text and pack_metadata.CHANGELOG_URL in text
    items = [l for l in text.splitlines() if l.startswith("- ")]
    assert items and all(re.match(r"- v\d+\.\d+\.\d+: ", l) for l in items)
    assert items[0].startswith(f"- v{pack_metadata.version_text()}: ")
    assert len(items) <= pack_metadata.CHANGELOG_RECENT_VERSIONS
