"""convert_image_mode.py (画像モードのアドオンを作る CLI) のテスト。ffmpeg の合成動画で作る。"""
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert_image_mode  # noqa: E402
from image_mode import atlas  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")


def make_video(path, duration=3, audio=True, size="320x240", rate=30):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("src") / "clip.mp4")


@pytest.fixture(scope="module")
def silent_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("src") / "silent.mp4", audio=False)


def run(tmp_path, *extra, inputs=None, name="out.mcaddon"):
    output = tmp_path / name
    argv = [str(p) for p in inputs] + ["--output", str(output), *map(str, extra)]
    code = convert_image_mode.main(argv)
    return code, output


def load(output):
    archive = zipfile.ZipFile(output)
    return archive, set(archive.namelist())


def videos_of(archive):
    text = archive.read("BP/scripts/image_videos.js").decode("utf-8")
    body = text.split("export const IMAGE_VIDEOS = ", 1)[1].rsplit(";", 1)[0]
    return json.loads(body)


def test_read_frames_trim_and_fps(video):
    frames = list(atlas.read_frames(video, 64, 36, 10, start=0.5, end=2.5))
    assert len(frames) == 20
    assert frames[0].shape == (36, 64, 3) and frames[0].dtype == np.uint8
    full = list(atlas.read_frames(video, 64, 36, 10))
    assert len(full) == 30
    # 4:3 の元を 16:9 に pad すると左右が黒、cover なら黒い余白がない
    assert frames[0][:, :4].max() == 0
    covered = next(atlas.read_frames(video, 64, 36, 10, fit="cover"))
    assert covered[:, :4].max() > 0


def test_read_frames_failure_raises(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(RuntimeError):
        list(atlas.read_frames(bad, 64, 36, 10))


def test_audio_chunks(video, silent_video, tmp_path):
    chunks = atlas.extract_audio_chunks(video, tmp_path / "a", chunk_seconds=1, ffmpeg="ffmpeg", max_seconds=3)
    assert [c[0] for c in chunks] == [0, 1, 2]
    assert all(kind == "ogg" and data[:4] == b"OggS" for _, kind, data in chunks)
    trimmed = atlas.extract_audio_chunks(video, tmp_path / "b", chunk_seconds=1, start=0.5, end=2.5)
    assert len(trimmed) == 2
    assert atlas.extract_audio_chunks(silent_video, tmp_path / "c") == []


def test_trim_and_frame_count(video, tmp_path):
    code, output = run(tmp_path, "--start", 0.5, "--end", 2.5, "--fps", 10, "--width", 64, "--height", 36,
                       inputs=[video])
    assert code == 0
    archive, names = load(output)
    [info] = videos_of(archive)
    assert info["frame_count"] == 20
    assert info["fps"] == 10 and (info["width"], info["height"]) == (64, 36)
    # 音声は 2 秒なので既定の 4 秒チャンクが 1 つ
    assert info["audio_chunks"] == 1 and info["audio_chunk_seconds"] == 4
    assert info["screen_blocks"] == [16, 9]
    assert info["memory"]["bytes"] == info["memory"]["texels"] * 4


def test_all_files_present(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--audio-chunk-seconds", 1,
                       "--method", "both", inputs=[video])
    assert code == 0
    archive, names = load(output)
    [info] = videos_of(archive)
    assert info["id"] == "clip" and info["frame_count"] == 30 and info["audio_chunks"] == 3
    expected = {
        "BP/manifest.json", "RP/manifest.json", "BP/pack_icon.png", "RP/pack_icon.png",
        "BP/scripts/image_main.js", "BP/scripts/image_videos.js",
        "BP/entities/screen_clip_a.json", "BP/entities/screen_clip_c.json",
        "RP/entity/screen_clip_a.entity.json", "RP/entity/screen_clip_c.entity.json",
        "RP/models/entity/screen_clip_a.geo.json", "RP/models/entity/screen_clip_c.geo.json",
        "RP/render_controllers/screen_clip_a.render_controllers.json",
        "RP/render_controllers/screen_clip_c.render_controllers.json",
        "RP/materials/entity.material", "RP/sounds/sound_definitions.json",
        "RP/texts/en_US.lang", "RP/texts/ja_JP.lang", "RP/texts/languages.json",
        "RP/textures/bvp_image/clip/sheet_0.png",
        "RP/sounds/bvp_image/clip/chunk_0.ogg", "RP/sounds/bvp_image/clip/chunk_1.ogg",
        "RP/sounds/bvp_image/clip/chunk_2.ogg",
    }
    assert expected <= names
    # ブロックモードのファイルは入らない
    assert not {n for n in names if n.endswith(("main.js", "codec.js", "videos.js")) and "image" not in n}
    assert archive.read("BP/scripts/image_main.js") == (Path(convert_image_mode.IMAGE_MAIN_SCRIPT)).read_bytes()
    definitions = json.loads(archive.read("RP/sounds/sound_definitions.json"))
    assert definitions["format_version"] == "1.14.0"
    sound = definitions["sound_definitions"]["bvpimg.img.clip.chunk_0"]
    assert sound["category"] == "ui" and sound["sounds"][0]["stream"] is True
    assert sound["sounds"][0]["name"] == "sounds/bvp_image/clip/chunk_0"
    sheet = archive.open("RP/textures/bvp_image/clip/sheet_0.png")
    from PIL import Image
    assert Image.open(sheet).size == (info["sheet_width"], info["sheet_height"])
    assert Image.open(archive.open("RP/pack_icon.png")).size == (256, 256)


def test_entity_properties_match_frame_count(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--method", "both", inputs=[video])
    archive, _ = load(output)
    [info] = videos_of(archive)
    for method in ("a", "c"):
        entity = json.loads(archive.read(f"BP/entities/screen_clip_{method}.json"))
        assert entity["format_version"] == "1.26.0"
        description = entity["minecraft:entity"]["description"]
        assert description["identifier"] == f"bvpimg:screen_clip_{method}"
        assert description["is_spawnable"] is False and description["is_summonable"] is True
        properties = description["properties"]
        assert properties["bvpimg:state"]["range"] == [0, 2]
        assert properties["bvpimg:frame"]["range"] == [0, info["frame_count"] - 1]
        assert properties["bvpimg:seq"]["range"] == [0, 1023]
        assert all(p["type"] == "int" and p["client_sync"] is True for p in properties.values())
        components = entity["minecraft:entity"]["components"]
        assert components["minecraft:physics"] == {"has_gravity": False, "has_collision": False}
        assert components["minecraft:pushable"]["is_pushable"] is False
        assert "minecraft:persistent" in components


def test_both_methods_share_textures(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--method", "both", inputs=[video])
    archive, names = load(output)
    [info] = videos_of(archive)
    assert info["methods"] == ["a", "c"]
    textures = []
    for method in ("a", "c"):
        client = json.loads(archive.read(f"RP/entity/screen_clip_{method}.entity.json"))
        description = client["minecraft:client_entity"]["description"]
        assert description["identifier"] == f"bvpimg:screen_clip_{method}"
        textures.append(description["textures"])
    assert textures[0] == textures[1]
    assert sorted(textures[0].values()) == info["texture_paths"]
    for path in info["texture_paths"]:
        assert f"RP/{path}.png" in names
    assert "RP/materials/entity.material" in names


def test_method_a_only_has_no_material(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--method", "a", inputs=[video])
    archive, names = load(output)
    assert "RP/materials/entity.material" not in names
    assert "BP/entities/screen_clip_a.json" in names and "BP/entities/screen_clip_c.json" not in names
    assert videos_of(archive)[0]["methods"] == ["a"]


def test_method_c_only(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--method", "c", inputs=[video])
    archive, names = load(output)
    assert "RP/materials/entity.material" in names
    assert "BP/entities/screen_clip_a.json" not in names and "BP/entities/screen_clip_c.json" in names


def test_manifests(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--pack-name", "Test Pack",
                       inputs=[video])
    archive, _ = load(output)
    bp = json.loads(archive.read("BP/manifest.json"))
    rp = json.loads(archive.read("RP/manifest.json"))
    assert bp["format_version"] == 2 and rp["format_version"] == 2
    assert bp["header"]["min_engine_version"] == [1, 26, 10]
    assert bp["modules"][0]["entry"] == "scripts/image_main.js"
    dependencies = {d.get("module_name"): d["version"] for d in bp["dependencies"] if "module_name" in d}
    # リモコン (フォーム) に server-ui を使う (ブロックモードと同じ版)
    assert dependencies == {"@minecraft/server": "2.6.0", "@minecraft/server-ui": "2.0.0"}
    assert {"uuid": rp["header"]["uuid"], "version": rp["header"]["version"]} in bp["dependencies"]
    assert rp["modules"][0]["type"] == "resources"
    assert rp["dependencies"] == [{"uuid": bp["header"]["uuid"], "version": bp["header"]["version"]}]
    # 作るたびに版が上がる (ブロックモードと同じ build_manifest_version。同じ UUID でも新しい版として読み込ませる)
    from pack_builder import build_manifest_version
    assert bp["header"]["version"][0] == build_manifest_version()[0] and len(bp["header"]["version"]) == 3
    # 同じ入力なら UUID は同じ。ブロックモードのパックとは別の UUID
    from pack_builder import stable_pack_uuid
    assert bp["header"]["uuid"] == convert_image_mode.pack_uuid("bvpimg", "Test Pack", "bp")
    assert bp["header"]["uuid"] != stable_pack_uuid("bvpimg", "Test Pack", "bp")


def test_report_and_lang_keys(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, inputs=[video])
    report = json.loads((tmp_path / "out.report.json").read_text(encoding="utf-8"))
    assert report["videos"][0]["id"] == "clip"
    assert report["memory"]["bytes"] == report["videos"][0]["memory"]["bytes"]
    assert report["memory"]["handles"] == 1
    assert set(report["lang_keys"]) == {"en", "ja"}
    assert report["lang_keys"]["en"]["bvpimg.paused"] == "Paused"
    assert report["lang_keys"]["ja"]["bvpimg.paused"] == "一時停止しました"
    # 既定は方式 a だけ (実機の確認で a に決めた)
    assert report["videos"][0]["methods"] == ["a"]
    archive, names = load(output)
    assert sorted(report["files"]) == sorted(names)
    en = archive.read("RP/texts/en_US.lang").decode("utf-8")
    assert "bvpimg.paused=Paused" in en
    languages = json.loads(archive.read("RP/texts/languages.json"))
    # ブロックモードと同じ 10 言語 (地域違いを含めて 14 のゲームの言語)
    assert {"en_US", "ja_JP", "zh_CN", "zh_TW", "ko_KR", "es_ES", "pt_BR", "fr_FR", "de_DE", "ru_RU"} <= set(languages)
    # スクリプトが使うメッセージは、どの言語のファイルにもある
    script = Path(convert_image_mode.IMAGE_MAIN_SCRIPT).read_text(encoding="utf-8")
    used = set(re.findall(r'(?:notify|tr)\("([a-z_.]+)"', script)) | set(re.findall(r"bvpimg\.([a-z_.]*[a-z])", script))
    used |= set(re.findall(r'"(state_[a-z]+|screen_[a-z]+)"', script))
    assert {"stats", "seek_usage", "volume_set", "wall_not_found"} <= used
    for code in languages:
        text = archive.read(f"RP/texts/{code}.lang").decode("utf-8")
        missing = [key for key in used if f"bvpimg.{key}=" not in text]
        assert not missing, (code, missing)


def test_report_path_option(video, tmp_path):
    report_path = tmp_path / "sub" / "r.json"
    code, _ = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--report", report_path, inputs=[video])
    assert code == 0 and report_path.is_file()


def test_silent_video_has_no_sounds(silent_video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, inputs=[silent_video])
    archive, names = load(output)
    assert videos_of(archive)[0]["audio_chunks"] == 0
    assert "RP/sounds/sound_definitions.json" not in names
    assert not any(n.startswith("RP/sounds/") for n in names)


def test_multiple_videos_with_ids_and_titles(video, silent_video, tmp_path):
    code, output = run(tmp_path, "--fps", 5, "--width", 64, "--height", 36, "--method", "a",
                       "--ids", "one", "two", "--titles", "First", "Second", inputs=[video, silent_video])
    assert code == 0
    archive, names = load(output)
    infos = videos_of(archive)
    assert [(v["id"], v["title"]) for v in infos] == [("one", "First"), ("two", "Second")]
    assert "BP/entities/screen_one_a.json" in names and "BP/entities/screen_two_a.json" in names
    report = json.loads((tmp_path / "out.report.json").read_text(encoding="utf-8"))
    assert report["memory"]["handles"] == 2
    assert report["memory"]["texels"] == sum(v["memory"]["texels"] for v in infos)


def test_multiple_sheets(video, tmp_path):
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--max-sheet", 140, inputs=[video])
    archive, names = load(output)
    [info] = videos_of(archive)
    # 64x36 + gutter 1 → 140 四方に 2 x 3 = 6 コマ。30 コマで 5 枚
    assert (info["cols"], info["rows"], info["cells_per_sheet"]) == (2, 3, 6)
    assert info["sheets"] == 5 and len(info["texture_paths"]) == 5
    for path in info["texture_paths"]:
        assert f"RP/{path}.png" in names
    client = json.loads(archive.read("RP/entity/screen_clip_a.entity.json"))
    assert len(client["minecraft:client_entity"]["description"]["textures"]) == 5


def test_over_budget_is_an_error_unless_allowed(video, tmp_path, monkeypatch, capsys):
    # 予算を小さくして、超過の扱いだけを調べる (実際に 800 MiB のテクスチャは作らない)
    monkeypatch.setattr(atlas, "TIER_BUDGETS_MB", {1: 150, 2: 225, 3: 300, 4: 600, 5: 0.1})
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, inputs=[video])
    assert code == 1 and not output.exists()
    captured = capsys.readouterr()
    assert "Tier 5" in captured.out and "--allow-over-budget" in captured.err
    code, output = run(tmp_path, "--fps", 10, "--width", 64, "--height", 36, "--allow-over-budget", inputs=[video])
    assert code == 0 and output.exists()
    report = json.loads((tmp_path / "out.report.json").read_text(encoding="utf-8"))
    assert any("Tier 5" in w for w in report["warnings"])


@pytest.mark.parametrize("extra", [
    ["--fps", 0], ["--fps", 61], ["--namespace", "minecraft"], ["--namespace", "Bad-NS"], ["--gutter", -1],
    ["--ids", "BAD"], ["--start", 2, "--end", 1],
])
def test_invalid_arguments(video, tmp_path, extra):
    with pytest.raises(SystemExit) as raised:
        argv = [str(video), "--output", str(tmp_path / "x.mcaddon"), *map(str, extra)]
        code = convert_image_mode.main(argv)
        raise SystemExit(code)
    assert raised.value.code != 0


def test_frame_larger_than_sheet_is_an_error(video, tmp_path, capsys):
    code, output = run(tmp_path, "--width", 64, "--height", 36, "--max-sheet", 32, inputs=[video])
    assert code == 1 and not output.exists()
    assert "max_sheet" in capsys.readouterr().err


def test_missing_input(tmp_path, capsys):
    code, _ = run(tmp_path, inputs=[tmp_path / "nothing.mp4"])
    assert code == 1


def test_default_id_from_file_name(tmp_path):
    used = set()
    assert convert_image_mode.default_id(Path("My Clip (1).mp4"), used) == "my_clip_1"
    used.add("my_clip_1")
    assert convert_image_mode.default_id(Path("my_clip_1.mov"), used) == "my_clip_1_2"
    assert convert_image_mode.default_id(Path("動画.mp4"), set()) == "video"
