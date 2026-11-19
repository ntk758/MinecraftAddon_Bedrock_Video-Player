"""スクリーンの管理 (screen_system.js) の単体テストと、スクリーンブロックのパッケージの検査。

単体テストは tests/js/screen_system_test.mjs (node --test)。パッケージの検査は、pack_builder が作るアドオンの中身
(ブロックの定義・テクスチャ・翻訳・manifest・スクリプト) が互いに合っているかを見る (Minecraft が読み込めるかは実機でしか
分からないので、docs/TESTING.md の 7 章の手順で確かめる)。
"""
import io
import json
import re
import shutil
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest

import legacy_target
import pack_builder

ROOT = Path(__file__).resolve().parent.parent


def test_screen_system_unit_tests():
    if shutil.which("node") is None:
        pytest.skip("node が必要")
    result = subprocess.run(["node", "--test", "tests/js/screen_system_test.mjs"], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=300, check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    # 結果の行は、端末なら「ℹ pass 32」、パイプ (CI の Node 22 など) なら TAP の「# pass 32」
    assert re.search(r"^(?:ℹ|#) pass [1-9]\d*", result.stdout, re.M), result.stdout[-2000:]
    assert re.search(r"^(?:ℹ|#) fail 0$", result.stdout, re.M), result.stdout[-2000:]


def build(namespace="ns", pack_name="P", **kwargs):
    video = {"id": "demo", "title": "demo", "frames_js": 'export const FRAME_DATA = {"frame_count":1,"width":1,"height":1};',
             "thumbnail": None, "sounds": []}
    return zipfile.ZipFile(io.BytesIO(pack_builder.build_addon_bytes([video], pack_name, namespace, **kwargs)))


def read_png(data):
    """PNG の幅・高さ・色の種類を読み、IDAT が展開できることを確かめる。"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        assert struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0] == zlib.crc32(tag + body) & 0xFFFFFFFF
        chunks.setdefault(tag, b"")
        chunks[tag] += body
        pos += 12 + length
    width, height, depth, color = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    raw = zlib.decompress(chunks[b"IDAT"])
    assert len(raw) == height * (1 + width * 4)
    return width, height, depth, color


def test_the_addon_has_a_complete_screen_block():
    z = build()
    names = set(z.namelist())
    block = json.loads(z.read("BP/blocks/video_screen.json"))["minecraft:block"]
    description, components = block["description"], block["components"]
    assert description["identifier"] == "ns:video_screen"
    assert description["traits"]["minecraft:placement_direction"]["enabled_states"] == ["minecraft:cardinal_direction"]
    # カスタムコンポーネントの名前は main.js が登録するものと同じ
    main = z.read("BP/scripts/main.js").decode("utf-8")
    assert "const SCREEN_COMPONENT_ID = `${EVENT_NAMESPACE}:screen_controller`;" in main
    assert "const SCREEN_BLOCK_ID = `${EVENT_NAMESPACE}:video_screen`;" in main
    assert 'const EVENT_NAMESPACE = "ns";' in main
    assert "ns:screen_controller" in components
    # material_instances があるのに geometry が無いと、Minecraft は定義ごと読まない (PC の実機で見つけた)
    assert components["minecraft:geometry"] == "minecraft:geometry.full_block"
    # テクスチャの参照先: material_instances の名前 → terrain_texture.json → PNG
    terrain = json.loads(z.read("RP/textures/terrain_texture.json"))
    assert terrain["texture_name"] == "atlas.terrain"
    for instance in components["minecraft:material_instances"].values():
        path = terrain["texture_data"][instance["texture"]]["textures"]
        assert f"RP/{path}.png" in names
        assert read_png(z.read(f"RP/{path}.png")) == (16, 16, 8, 6)
    # data モジュール (ブロックの定義を読ませる) があり、UUID はどれも別
    bp = json.loads(z.read("BP/manifest.json"))
    rp = json.loads(z.read("RP/manifest.json"))
    assert [m["type"] for m in bp["modules"]] == ["script", "data"]
    uuids = [bp["header"]["uuid"], rp["header"]["uuid"], *[m["uuid"] for m in bp["modules"] + rp["modules"]]]
    assert len(uuids) == len(set(uuids))
    # スクリプト: main.js が import するファイルがすべて入っている
    for imported in re.findall(r'from "\./([a-z_]+\.js)"', main):
        if imported != "videos.js":
            assert f"BP/scripts/{imported}" in names, imported
    for name in pack_builder.ADDON_SCRIPTS:
        assert f"BP/scripts/{name}" in names
    # ブロックの名前: すべての翻訳ファイルに 1 行ずつ
    for name in names:
        if name.startswith("RP/texts/") and name.endswith(".lang"):
            lines = [line for line in z.read(name).decode("utf-8").splitlines() if line.startswith("tile.ns:video_screen.name=")]
            assert len(lines) == 1, name
    assert "tile.ns:video_screen.name=ビデオスクリーン" in z.read("RP/texts/ja_JP.lang").decode("utf-8")


def test_identifiers_do_not_collide_between_two_addons():
    """同じワールドに接頭辞の違う 2 つのアドオンを入れても、ブロック・テクスチャ・コンポーネントの名前がぶつからない。"""
    first, second = build("alpha", "A"), build("beta", "B")
    ids = []
    for z in (first, second):
        block = json.loads(z.read("BP/blocks/video_screen.json"))["minecraft:block"]
        terrain = json.loads(z.read("RP/textures/terrain_texture.json"))
        ids += [block["description"]["identifier"], *[k for k in block["components"] if not k.startswith("minecraft:")],
                *terrain["texture_data"]]
    assert len(ids) == len(set(ids)) == 8


def test_one_language_addon_names_the_block_in_that_language():
    z = build(addon_language="ja")
    assert "tile.ns:video_screen.name=ビデオスクリーン" in z.read("RP/texts/en_US.lang").decode("utf-8")


def test_the_legacy_target_has_no_screen_block():
    z = build(main_text=legacy_target.main_text(pack_builder.MAIN_SCRIPT.read_text(encoding="utf-8")),
              manifest_text=legacy_target.manifest_text(pack_builder.MANIFEST.read_text(encoding="utf-8")),
              lang_transform=legacy_target.lang_text)
    names = set(z.namelist())
    assert not any(n.startswith(("BP/blocks/", "RP/textures/")) for n in names)
    assert "BP/scripts/screen_ui.js" not in names
    # main.js が import する screen_system.js は入れる (旧版の Script API でも動く、Minecraft を使わないモジュール)
    assert "BP/scripts/screen_system.js" in names
    main = z.read("BP/scripts/main.js").decode("utf-8")
    assert "screen_ui.js" not in main and "registerCustomComponent" not in main and "SCREEN_BLOCK_ID" not in main
    assert [m["type"] for m in json.loads(z.read("BP/manifest.json"))["modules"]] == ["script"]
    assert not any("tile." in z.read(n).decode("utf-8") for n in names if n.endswith(".lang"))


def test_screen_system_does_not_import_minecraft():
    """screen_system.js は Minecraft の API に頼らない (Node の単体テストと、旧版の出力のため)。"""
    text = (ROOT / "screen_system.js").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\b", text, re.M) and "@minecraft" not in text


def test_every_screen_text_key_exists():
    """main.js と screen_ui.js が使う screen.* の文と、失敗の理由 (screen.error.<理由>) が翻訳にある。"""
    from test_main_js import lang_keys

    known = {key[len("bvp."):] for key in lang_keys()}
    sources = "".join((ROOT / name).read_text(encoding="utf-8") for name in ("main.js", "screen_ui.js", "screen_system.js"))
    used = set(re.findall(r'"(screen\.[a-z_.]+)"', sources))
    used = {key for key in used if not key.endswith(".")}
    reasons = set(re.findall(r'(?:fail|screenFail)\("([a-z_]+)"', sources))
    reasons |= set(re.findall(r'reason: "([a-z_]+)"', sources))
    reasons |= set(re.findall(r'\{ error: "([a-z_]+)"', sources))
    # ScreenRegistry.register の理由は、ScreenManager が too_many_screens などに言い換えるので、プレイヤーには出ない
    reasons -= {"duplicate_id", "location_taken", "limit"}
    assert used and reasons
    missing = {k for k in used if k not in known} | {f"screen.error.{r}" for r in reasons if f"screen.error.{r}" not in known}
    assert not missing, missing
    states = set(re.findall(r'(\w+): "(idle|playing|paused|stopped)"', sources))
    assert {f"screen.state.{s}" for _, s in states} <= known
