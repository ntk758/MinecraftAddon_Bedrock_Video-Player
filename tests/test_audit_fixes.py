"""2026-11 の全体の監査 (AUDIT_PROGRESS.md) で見つけて直した問題の回帰テスト。

- AUD-01 / AUD-12: ブラウザ版も接頭辞 minecraft を断る (Windows 版・pack_builder と同じ一覧)。完成の表示は作り始めたときの設定
- AUD-02: build_addon が動画 ID と接頭辞を自分でも確かめる (videos.js・main.js・zip の中のパスを壊さない)
- AUD-03: convert.py が --duration 0 以下と --extrude-threshold の 0〜1 の外を断る
- AUD-09: .vox のアニメーションのフレームの範囲が壊れていても、固まらずにすぐ断る
- AUD-10: .mcaddon を書く途中で失敗しても、前からあるファイルを壊さず、書きかけのファイルも残さない
"""
import io
import os
import re
import struct
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402
import convert_image_mode  # noqa: E402
import pack_builder  # noqa: E402
from mvcodec import volume  # noqa: E402

FRAMES_JS = 'export const FRAME_DATA = {"width": 2, "height": 2, "frame_count": 1};\n'


def video(video_id="clip"):
    return {"id": video_id, "title": "Clip", "frames_js": FRAMES_JS, "sounds": []}


# --- AUD-02: 動画 ID と接頭辞 ---

@pytest.mark.parametrize("video_id", ['a"b', "../evil", "Clip", "", "a b", "a-b", "a/b", "é"])
def test_build_addon_rejects_unsafe_video_ids(video_id):
    with pytest.raises(ValueError, match="video IDs"):
        pack_builder.build_addon_bytes([video(video_id)], "P", "ns")


@pytest.mark.parametrize("namespace", ["minecraft", 'x";globalThis.hack=1;"', "My-Pack", "", "ns:x"])
def test_build_addon_rejects_unsafe_or_reserved_namespaces(namespace):
    with pytest.raises(ValueError, match="command prefix"):
        pack_builder.build_addon_bytes([video()], "P", namespace)


def test_build_addon_needs_the_namespace_line_in_main_js():
    # 差し替える行が無いと、接頭辞が badapple のままのアドオンになっていた
    with pytest.raises(ValueError, match="EVENT_NAMESPACE"):
        pack_builder.build_addon_bytes([video()], "P", "movie", main_text="// main.js without the line\n")


def test_safe_names_still_build():
    data = pack_builder.build_addon_bytes([video("clip_2"), video("v_10")], "P", "my_pack_2")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "BP/scripts/frames_clip_2.js" in names and "BP/scripts/frames_v_10.js" in names
    assert all(".." not in name for name in names)
    main = zipfile.ZipFile(io.BytesIO(data)).read("BP/scripts/main.js").decode("utf-8")
    assert 'const EVENT_NAMESPACE = "my_pack_2";' in main


# --- AUD-10: 書き終えてから置き換える ---

def test_failed_write_keeps_the_previous_addon(tmp_path, monkeypatch):
    out = tmp_path / "pack.mcaddon"
    pack_builder.build_addon(out, [video()], "P", "movie")
    before = out.read_bytes()

    def broken_replace(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        pack_builder.build_addon(out, [video("other")], "P", "movie")
    assert out.read_bytes() == before  # 前のアドオンはそのまま
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pack.mcaddon"]  # 書きかけ (.part) も残らない


def test_write_file_atomic_replaces_and_cleans_up(tmp_path):
    target = tmp_path / "sub" / "a.bin"
    pack_builder.write_file_atomic(target, b"one")
    pack_builder.write_file_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["a.bin"]


def test_combining_that_fails_keeps_the_block_addon(tmp_path, monkeypatch):
    """GUI は失敗したら「別のファイルのまま残す」と知らせる。そのときブロックモードのファイルが壊れていないこと。"""
    block = tmp_path / "pack.mcaddon"
    pack_builder.build_addon(block, [video()], "P", "movie")
    image = tmp_path / "pack_image.mcaddon"
    with zipfile.ZipFile(image, "w") as archive:
        archive.writestr("BP/manifest.json", "{}")
    before = block.read_bytes()
    monkeypatch.setattr(os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(OSError, match="locked"):
        convert_image_mode.combine_addons(block, image)
    assert block.read_bytes() == before
    assert zipfile.ZipFile(block).testzip() is None


# --- AUD-03: convert.py の引数 ---

@pytest.mark.parametrize("extra", [["--duration", "0"], ["--duration", "-1"],
                                   ["--mode", "extrude", "--extrude-threshold", "1.5"],
                                   ["--mode", "extrude", "--extrude-threshold", "-0.1"]])
def test_convert_rejects_out_of_range_options(extra, capsys):
    with pytest.raises(SystemExit) as stop:
        convert.parse_args(["--input-video", "x.mp4", *extra])
    assert stop.value.code == 2
    assert "--duration" in capsys.readouterr().err or "--extrude-threshold" in " ".join(extra)


def test_convert_accepts_the_edges():
    assert convert.parse_args(["--input-video", "x.mp4", "--duration", "0.1"]).duration == 0.1
    for value in ("0", "1"):
        args = convert.parse_args(["--input-video", "x.mp4", "--mode", "extrude", "--extrude-threshold", value])
        assert args.extrude_threshold == float(value)


# --- AUD-09: .vox のアニメーションの範囲 ---

def _vox_with_anim_range(text):
    """モデル 1 つのシーンに、META の _anim_range を付けた .vox のバイト列。"""
    data = volume.write_vox([((2, 2, 2), [(0, 0, 0, 1)])], scene=[{"translations": [(0, (0, 0, 0))], "models": [(0, 0)]}])
    raw = text.encode("ascii")
    meta_body = struct.pack("<i", 1) + struct.pack("<i", len(b"_anim_range")) + b"_anim_range" + struct.pack("<i", len(raw)) + raw
    meta = b"META" + struct.pack("<ii", len(meta_body), 0) + meta_body
    # MAIN の子の大きさを直してから、最後に META を足す
    main_children = struct.unpack_from("<i", data, 16)[0] + len(meta)
    return data[:16] + struct.pack("<i", main_children) + data[20:] + meta


@pytest.mark.parametrize("anim_range", ["0 2000000000", "5 2"])
def test_vox_with_a_broken_animation_range_fails_fast(anim_range):
    with pytest.raises(ValueError, match="フレームの範囲"):
        volume.parse_vox(_vox_with_anim_range(anim_range))


def test_vox_with_a_normal_animation_range_still_loads():
    frames, _palette = volume.parse_vox(_vox_with_anim_range("0 3"))
    assert len(frames) == 4


# --- AUD-01 / AUD-12: ブラウザ版 ---

def test_web_rejects_the_same_reserved_namespaces():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    listed = re.search(r"const RESERVED_NAMESPACES = \[(.*?)\];", app).group(1)
    assert set(re.findall(r'"([a-z0-9_]+)"', listed)) == pack_builder.RESERVED_NAMESPACES
    build = app[app.index("async function build()"):]
    # 作り始める前に断る (worker に渡してから Python の ValueError になるのではなく、Windows 版と同じ文で知らせる)
    assert build.index('failed(t("error.namespace"))') < build.index("startWorker()")


def test_web_finish_uses_the_settings_the_build_started_with():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    finish = app[app.index("function finish(msg)"):app.index("function failed(text)")]
    assert "buildSettings" in finish
