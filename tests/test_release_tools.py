"""リリースの道具 (scripts/repack_addon.py・play_addon_mock.py・release_notes.py と .github/workflows/release.yml) のテスト。"""
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack_builder  # noqa: E402
import play_addon_mock  # noqa: E402
import release_notes  # noqa: E402
import repack_addon  # noqa: E402
from pack_metadata import version_text  # noqa: E402
from test_main_js import make_scene_frames, run_convert  # noqa: E402


@pytest.fixture(scope="module")
def old_addon(tmp_path_factory):
    """前の版のアドオンの代わり: 古い main.js (印を入れたもの) で作った、音声・一覧に出さない動画・audio_from 付きのアドオン。"""
    tmp = tmp_path_factory.mktemp("old")
    make_scene_frames(tmp / "frames")
    run_convert(tmp / "frames", tmp / "a.js", "--keyframe-interval", "10")
    frames_js = (tmp / "a.js").read_text(encoding="utf-8")
    old_main = (ROOT / "main.js").read_text(encoding="utf-8") + "\n// OLD-VERSION-MARK\n"
    path = tmp / "Old Sample.mcaddon"
    pack_builder.build_addon(path, [
        {"id": "clip", "title": "Clip", "frames_js": frames_js, "thumbnail": b"\x89PNG-icon",
         "sounds": [(0, "ogg", b"OggS-a"), (1, "ogg", b"OggS-b")]},
        {"id": "clip_flat", "title": "Clip (2D)", "frames_js": frames_js, "sounds": [], "audio_from": "clip"},
        {"id": "clip_bg", "title": "bg", "frames_js": frames_js, "sounds": [], "hidden": True},
    ], "Block Video Player Sample", "badapple", main_text=old_main)
    return path


def test_repack_keeps_the_data_and_replaces_the_addon_code(old_addon, tmp_path):
    new = tmp_path / "new.mcaddon"
    entries = repack_addon.repack(old_addon, new)
    assert [e["id"] for e in entries] == ["clip", "clip_flat", "clip_bg"]
    with zipfile.ZipFile(old_addon) as a, zipfile.ZipFile(new) as b:
        assert set(a.namelist()) == set(b.namelist())
        changed = sorted(n for n in a.namelist() if a.read(n) != b.read(n))
        assert set(changed) <= {"BP/manifest.json", "RP/manifest.json", "BP/scripts/main.js",
                                "BP/CHANGELOG.md", "RP/CHANGELOG.md"}
        assert "BP/scripts/main.js" in changed and b"OLD-VERSION-MARK" not in b.read("BP/scripts/main.js")
        for name in ("BP/manifest.json", "RP/manifest.json"):
            old, now = json.loads(a.read(name))["header"], json.loads(b.read(name))["header"]
            assert old["uuid"] == now["uuid"]  # 同じパック名なので上書きで更新できる
        assert json.loads(b.read("BP/manifest.json"))["header"]["name"] == f"Block Video Player Sample v{version_text()}"
        listing = repack_addon.read_listing(b)
    assert listing[1]["audio_from"] == "clip" and listing[2]["hidden"] is True


def test_pack_name_strips_the_version():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("BP/manifest.json", json.dumps({"header": {"name": "Block Video Player 3D Sample v6.30.1"}}))
    with zipfile.ZipFile(data) as z:
        assert repack_addon.pack_name_of(z) == "Block Video Player 3D Sample"


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")
def test_play_addon_mock_plays_every_listed_video(old_addon):
    results = dict(play_addon_mock.check(old_addon))
    assert set(results) == {"clip", "clip_flat"}  # 一覧に出さない背景の映像は再生しない
    assert results == {"clip": [], "clip_flat": []}


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")
def test_play_addon_mock_reports_a_broken_addon(old_addon, tmp_path):
    broken = tmp_path / "broken.mcaddon"
    with zipfile.ZipFile(old_addon) as src, zipfile.ZipFile(broken, "w") as out:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "BP/scripts/main.js":
                data += b"\nthrow new Error('broken on purpose');\n"
            out.writestr(info, data)
    results = dict(play_addon_mock.check(broken))
    assert all(problems for problems in results.values())


def test_release_notes_use_the_changelog_section(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## v9.9.9\n\n- new thing\n\n## v9.9.8\n\n- old thing\n", encoding="utf-8")
    text = release_notes.notes("v9.9.9", "- checked", changelog)
    assert "- new thing" in text and "old thing" not in text
    assert "BlockVideoPlayer-v9.9.9-windows-x64.zip" in text and "## 確認したこと\n- checked" in text
    with pytest.raises(ValueError):
        release_notes.changelog_section("v1.0.0", changelog)


def test_current_version_has_a_changelog_section():
    assert release_notes.changelog_section(f"v{version_text()}")


def test_release_workflow_runs_the_tools():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)  # YAML 1.1 では on が True になる
    assert triggers["push"]["tags"] == ["v*"]
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for needed in ("build_standalone.py", "scripts/repack_addon.py", "scripts/play_addon_mock.py",
                   "scripts/release_notes.py", "SHA256SUMS.txt", "--run-converter"):
        assert needed in text, needed
    assert set(workflow["jobs"]["release"]["needs"]) == {"exe", "samples"}
