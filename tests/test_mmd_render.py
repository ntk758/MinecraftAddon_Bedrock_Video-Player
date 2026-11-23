"""MMD を描く所 (mvcodec/mmd_render.py) と、動画にする所 (mvcodec/mmd_video.py) のテスト。

モデルはテスト用の箱の人形 (tests/mmd_fixture.py)。顔の前 (-Z) に赤い目印の箱がある。
"""
import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mmd_fixture as fixture  # noqa: E402
from mvcodec import mmd, mmd_render, mmd_video  # noqa: E402

STILL = [("頭", 0, (0, 0, 0), (0, 0, 0, 1)), ("頭", 30, (0, 0, 0), (0, 0, 0, 1))]
BACKGROUND = (10, 20, 30, 255)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="ffmpeg と ffprobe が必要")


def doll(tmp_path, extras=False, keys=STILL, cameras=()):
    pmx, vmd = fixture.write_doll(tmp_path, keys, extras=extras, cameras=cameras)
    model = mmd.read_pmx(pmx)
    return pmx, vmd, model, mmd_render.Scene(model, tmp_path)


def front_camera(scene, skin=None, aspect=4 / 3):
    v, _n = scene.posed(skin if skin is not None else mmd.Poser(scene.model).pose({}, 0))
    return mmd_render.framing_camera(v, aspect)


def draw(scene, camera, skin=None, weights=None, size=(160, 120), **options):
    skin = skin if skin is not None else mmd.Poser(scene.model).pose({}, 0)
    v, n = scene.posed(skin, weights)
    settings = mmd_render.RenderSettings(size[0], size[1], background=BACKGROUND, **options)
    return mmd_render.render([(scene, v, n)], camera, settings)


def red(image):
    """顔の目印の赤 (光とトゥーンで明るさは変わるので、赤が緑・青よりはっきり強い画素)。"""
    rgb = image[..., :3].astype(int)
    return (rgb[..., 0] > 120) & (rgb[..., 0] > rgb[..., 1] * 1.8) & (rgb[..., 0] > rgb[..., 2] * 1.8)


def background(image):
    return np.all(image[..., :3] == BACKGROUND[:3], axis=-1)


def test_pmx_keeps_what_the_renderer_needs(tmp_path):
    _pmx, _vmd, model, _scene = doll(tmp_path)
    assert model.normals.shape == model.positions.shape and model.edge_scales.shape == (len(model.positions),)
    material = model.materials[0]
    assert material["shininess"] == 5 and np.allclose(material["ambient"], (0.3, 0.3, 0.3))
    assert material["flags"] == 0 and material["shared_toon"] == 0 and material["toon"] == -1
    assert material["sphere"] == -1 and material["edge_size"] == 1


def test_front_view_shows_the_face_marker_and_the_back_view_hides_it(tmp_path):
    """正面 (-Z) から見ると顔の赤い目印が見え、後ろ (+Z) から見ると頭に隠れる (深さ)。"""
    _pmx, _vmd, _model, scene = doll(tmp_path)
    camera = front_camera(scene)
    image = draw(scene, camera)
    assert image.shape == (120, 160, 4) and image.dtype == np.uint8
    assert background(image)[0, 0] and background(image)[-1, -1]
    assert red(image).sum() > 10
    ys, xs = np.nonzero(red(image))
    assert ys.mean() < 120 * 0.4 and abs(xs.mean() - 80) < 10   # 上のほう、真ん中
    behind = mmd_render.Camera(camera.target + (camera.target - camera.eye) * np.array([1, 1, 1]), camera.target)
    assert red(draw(scene, behind)).sum() == 0
    assert (~background(draw(scene, behind))).sum() > 100       # 人形は見える


def test_model_left_is_on_the_viewers_right(tmp_path):
    """MMD のモデルの左 (+X、左腕) は、正面から見ると画面の右。左腕を上げたら、頭より上の画素は右にだけある。"""
    _pmx, _vmd, model, scene = doll(tmp_path)
    poser = mmd.Poser(model)
    best = None
    for sign in (1, -1):  # 腕が上がる向きの回転を選ぶ (回転の符号はこのテストの関心ではない)
        q = (0, 0, sign * math.sin(math.radians(40)), math.cos(math.radians(40)))
        skin = poser.pose({"左腕": [(0, (0, 0, 0), q)]}, 0)
        v, _ = scene.posed(skin)
        arm = v[model.bone_indices[:, 0] == fixture.INDEX["左ひじ"]]
        if best is None or arm[:, 1].max() > best[1]:
            best = (skin, arm[:, 1].max())
    skin = best[0]
    camera = front_camera(scene, skin)
    image = draw(scene, camera, skin)
    body = ~background(image)
    rest_image = draw(scene, camera)
    rest_rows = np.nonzero((~background(rest_image)).any(axis=1))[0]
    head_row = rest_rows.min()   # 腕を下げた人形の一番上 (頭)
    above = body[:head_row]
    assert above.any()
    xs = np.nonzero(above)[1]
    assert xs.min() > 80


def test_transparent_material_blends_with_what_is_behind(tmp_path):
    _pmx, _vmd, _model, scene = doll(tmp_path)
    camera = front_camera(scene)
    solid = draw(scene, camera)
    scene.diffuse[:, 3] = 0.5
    scene.may_be_transparent[:] = True
    half = draw(scene, camera)
    mask = ~background(solid)
    # 半透明: 背景と人形の色の間。全部透明でも、全部不透明でもない
    assert (half[mask][:, :3].astype(int) != solid[mask][:, :3].astype(int)).any()
    scene.diffuse[:, 3] = 0.0
    assert background(draw(scene, camera)).all()


def test_edges_draw_an_outline_in_the_edge_colour(tmp_path):
    _pmx, _vmd, _model, scene = doll(tmp_path)
    camera = front_camera(scene)
    scene.flags[:] |= mmd_render.FLAG_EDGE
    scene.edge_size[:] = 3.0
    scene.edge_colour[:] = (1.0, 0.0, 1.0, 1.0)
    with_edges = draw(scene, camera, size=(320, 240))
    magenta = (with_edges[..., 0] > 200) & (with_edges[..., 1] < 60) & (with_edges[..., 2] > 200)
    assert magenta.sum() > 10   # 人形の箱の法線は全部上向きなので、殻は上下にだけ広がる
    without = draw(scene, camera, size=(320, 240), edges=False)
    magenta = (without[..., 0] > 200) & (without[..., 1] < 60) & (without[..., 2] > 200)
    assert magenta.sum() == 0


def test_vertex_morph_changes_the_face(tmp_path):
    """表情「あ」(顔の目印の下の辺を下げる) で、赤い目印が縦に長くなる。"""
    _pmx, _vmd, _model, scene = doll(tmp_path, extras=True)
    camera = front_camera(scene)
    closed = red(draw(scene, camera, size=(320, 240))).sum()
    opened = red(draw(scene, camera, size=(320, 240), weights={"あ": 1.0})).sum()
    assert opened > closed * 1.2


def test_supersampling_keeps_the_size_and_smooths_edges(tmp_path):
    _pmx, _vmd, _model, scene = doll(tmp_path)
    camera = front_camera(scene)
    sharp = draw(scene, camera, supersample=1)
    smooth = draw(scene, camera, supersample=3)
    assert sharp.shape == smooth.shape
    colours = lambda image: len(np.unique(image[..., :3].reshape(-1, 3), axis=0))  # noqa: E731
    assert colours(smooth) > colours(sharp)


def test_camera_from_vmd_follows_the_keys():
    cameras = [(0, -45.0, (0, 10, 0), (0, 0, 0), 30), (30, -45.0, (0, 10, 0), (0, math.pi / 2, 0), 30)]
    keys = mmd.read_vmd_camera(_vmd_file(cameras))
    start = mmd_render.camera_from_vmd(keys, 0)
    assert np.allclose(start.eye, (0, 10, -45)) and np.allclose(start.target, (0, 10, 0)) and start.fov == 30
    side = mmd_render.camera_from_vmd(keys, 30)
    assert abs(side.eye[0]) == pytest.approx(45) and np.allclose(side.up, (0, 1, 0))


def _vmd_file(cameras):
    import tempfile
    path = Path(tempfile.mkdtemp()) / "camera.vmd"
    path.write_bytes(fixture.vmd_bytes([], cameras=cameras))
    return path


def test_stage_and_model_share_the_depth(tmp_path):
    """2 つのモデル (人形とステージ) を同じ深さで描く: 手前の人形が奥の人形を隠す。"""
    _pmx, _vmd, model, scene = doll(tmp_path)
    camera = front_camera(scene)
    v, n = scene.posed(mmd.Poser(model).pose({}, 0))
    behind = v + np.array([0.0, 0.0, 6.0])
    settings = mmd_render.RenderSettings(160, 120, background=BACKGROUND)
    both = mmd_render.render([(scene, behind, n), (scene, v, n)], camera, settings)
    front_only = mmd_render.render([(scene, v, n)], camera, settings)
    assert red(both).sum() == red(front_only).sum()


# --- 動画 (mmd_video) ---

def _wav(path, seconds, rate=44100):
    t = np.arange(int(seconds * rate)) / rate
    data = (np.sin(2 * np.pi * 440 * t) * 0.2 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data.tobytes())


def _job(tmp_path, **options):
    pmx, vmd = fixture.write_doll(tmp_path, STILL)
    values = {"pmx": str(pmx), "motion": str(vmd), "output": str(tmp_path / "doll.mp4"), "width": 96, "height": 64,
              "fps": 10, "physics": "off", "jobs": 1, "encoder": "libx264", "supersample": 1}
    values.update(options)
    return mmd_video.VideoJob(**values)


@needs_ffmpeg
def test_render_video_makes_a_checked_mp4_and_removes_the_frames(tmp_path):
    job = _job(tmp_path, seconds=1.0)
    report = mmd_video.render_video(job, log=lambda *_: None)
    assert report["frames"] == 10 and report["probe"]["frames"] == 10
    assert (report["probe"]["width"], report["probe"]["height"]) == (96, 64)
    assert Path(job.output).is_file() and not (tmp_path / "doll.frames").exists()
    assert not list(tmp_path.glob("*.part.mp4"))


@needs_ffmpeg
def test_audio_sets_the_length_and_stays_in_sync(tmp_path):
    song = tmp_path / "song.wav"
    _wav(song, 1.25)
    job = _job(tmp_path, audio=str(song), fps=24)
    report = mmd_video.render_video(job, log=lambda *_: None)
    probe = report["probe"]
    assert probe["frames"] == 30                      # 1.25 秒 × 24 fps
    assert abs(probe["video_duration"] - 1.25) < 1 / 24
    assert abs(probe["audio_duration"] - 1.25) < 0.05
    assert report["av_offset_s"] < 1 / 24


@needs_ffmpeg
def test_resume_reuses_good_frames_and_redraws_broken_ones(tmp_path):
    work = tmp_path / "work"
    job = _job(tmp_path, seconds=1.0, work_dir=str(work), keep_frames=True)
    mmd_video.render_video(job, log=lambda *_: None)
    frames = sorted(work.glob("frame_*.png"))
    assert len(frames) == 10
    frames[2].unlink()
    frames[5].write_bytes(frames[5].read_bytes()[:40])      # 途中で切れた PNG
    (work / "frame_000007.png.tmp").write_bytes(b"half")   # 書きかけ
    report = mmd_video.render_video(job, log=lambda *_: None)
    assert report["frames_reused"] == 8 and report["probe"]["frames"] == 10
    assert not list(work.glob("*.tmp"))
    # 設定 (大きさ) が変わったら、古いコマは使わない
    job.width, job.height = 64, 48
    report = mmd_video.render_video(job, log=lambda *_: None)
    assert report["frames_reused"] == 0 and report["probe"]["width"] == 64


@needs_ffmpeg
def test_ffmpeg_failure_is_an_error_and_leaves_no_output(tmp_path, monkeypatch):
    monkeypatch.setattr(mmd_video, "_encode_args", lambda encoder, crf: ["-c:v", "no_such_encoder_xyz"])
    job = _job(tmp_path, seconds=0.5, keep_frames=True)
    with pytest.raises(mmd_video.RenderError):
        mmd_video.render_video(job, log=lambda *_: None)
    assert not Path(job.output).exists() and not list(tmp_path.glob("*.part.mp4"))
    assert len(list((tmp_path / "doll.frames").glob("frame_*.png"))) == 5   # 描いたコマは再開のために残る


def test_bad_settings_are_refused(tmp_path):
    with pytest.raises(mmd_video.RenderError):
        mmd_video.render_video(_job(tmp_path, width=95), log=lambda *_: None)
    with pytest.raises(mmd_video.RenderError):
        mmd_video.render_video(_job(tmp_path, motion=str(tmp_path / "none.vmd")), log=lambda *_: None)
    with pytest.raises(mmd_video.RenderError):
        mmd_video.render_video(_job(tmp_path, preset="nope"), log=lambda *_: None)


def test_presets_and_supersample_defaults():
    assert mmd_video.RENDER_PRESETS["standard"] == (1280, 720, 30)
    assert mmd_video.RENDER_PRESETS["mobile"] == (854, 480, 24)
    assert mmd_video.VideoJob(pmx="x", preset="experimental").resolved_supersample() == 1
    assert mmd_video.VideoJob(pmx="x", preset="high").resolved_supersample() == 2
    assert all(w % 2 == 0 and h % 2 == 0 for w, h, _f in mmd_video.RENDER_PRESETS.values())


@needs_ffmpeg
def test_render_mmd_cli_builds_an_addon_with_subpacks(tmp_path):
    import json
    import zipfile
    sys.path.insert(0, str(ROOT / "scripts"))
    import render_mmd
    pmx, vmd = fixture.write_doll(tmp_path, STILL)
    song = tmp_path / "song.wav"
    _wav(song, 2.0)   # 720p 30 fps 2 秒 = 約 211 MiB: Tier 1 (150 MB) には縮めないと入らない
    out = tmp_path / "doll.mp4"
    addon = tmp_path / "doll.mcaddon"
    code = render_mmd.main(["--pmx", str(pmx), "--motion", str(vmd), "--audio", str(song), "-o", str(out),
                            "--width", "1280", "--height", "720", "--fps", "30", "--supersample", "1",
                            "--physics", "off", "--jobs", "2",
                            "--encoder", "libx264", "--addon", str(addon), "--subpack-tiers", "1,5",
                            "--report", str(tmp_path / "r.json")])
    assert code == 0
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["addon"]["exit_code"] == 0
    with zipfile.ZipFile(addon) as z:
        names = z.namelist()
        manifest = json.loads(z.read("RP/manifest.json"))
    assert [s["folder_name"] for s in manifest["subpacks"]] == ["tier1", "tier5"]
    assert manifest["subpacks"][0]["memory_tier"] == 0 and manifest["subpacks"][1]["memory_tier"] == 32
    for tier in ("tier1", "tier5"):
        assert any(n.startswith(f"RP/subpacks/{tier}/textures/") for n in names)
        assert any(n.startswith(f"RP/subpacks/{tier}/entity/") for n in names)
    assert not any(n.startswith("RP/textures/") for n in names)       # シートはサブパックにだけ
    assert any(n.startswith("RP/sounds/bvp_image/") for n in names)  # 音声は本体に 1 つ
    assert "BP/scripts/image_videos.js" in names


@needs_ffmpeg
def test_subpacks_need_a_priority(tmp_path):
    import convert_image_mode
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    assert convert_image_mode.main([str(video), "-o", str(tmp_path / "a.mcaddon"), "--subpack-tiers", "2,5"]) != 0
    assert convert_image_mode.main([str(video), "-o", str(tmp_path / "a.mcaddon"), "--subpack-tiers", "2,5",
                                    "--priority", "smooth", "--width", "320", "--height", "180"]) == 0


def test_figure_presets_and_precedence():
    import convert_image_mode as c
    args = c.parse_args(["x.pmx", "--figure-preset", "mobile"], allow_no_inputs=True)
    assert c.figure_resolution(args) == (64, 2)
    args = c.parse_args(["x.pmx", "--figure-preset", "extreme", "--figure-texels", "2"], allow_no_inputs=True)
    assert c.figure_resolution(args) == (512, 2)
    args = c.parse_args(["x.pmx", "--figure-preset", "high", "--figure-voxels", "100"], allow_no_inputs=True)
    assert c.figure_resolution(args) == (100, 2)
    # Windows 版が人物ごとに渡す細かさ (standard・high・max) は今までどおり
    args = c.parse_args(["x.pmx", "--figure-preset", "high"], allow_no_inputs=True)
    assert c.figure_resolution(args, "standard") == (256, 1)
    assert c.figure_resolution(c.parse_args(["x.pmx"], allow_no_inputs=True)) == (256, 2)
    assert set(c.FIGURE_PRESETS) == {"mobile", "standard", "high", "ultra", "experimental", "extreme"}
    assert [v for v, _t in c.FIGURE_PRESETS.values()] == [64, 128, 192, 256, 384, 512]


def test_split_subpacks_puts_only_differences_in_subpacks():
    import json
    import convert_image_mode as c
    manifest = json.dumps({"format_version": 2, "header": {"name": "x"}}).encode()

    def build(tier, size, sheet):
        files = {"BP/manifest.json": b"bp", "BP/scripts/image_videos.js": f"{tier}".encode(),
                 "RP/manifest.json": manifest, "RP/texts/en_US.lang": b"same", "RP/pack_icon.png": f"i{tier}".encode(),
                 "RP/sounds/bvp_image/v/chunk_0.ogg": f"ogg{tier}".encode(),
                 "RP/textures/bvp_image/v/sheet_0.png": sheet, "RP/entity/screen_v_a.entity.json": f"e{size}".encode()}
        if tier == 5:
            files["RP/textures/bvp_image/v/sheet_1.png"] = b"extra"
        report = {"videos": [{"width": size, "height": size // 2, "fps": 30}], "memory": {"mib": 1.0, "handles": 1}}
        return tier, files, report

    files, subpacks = c.split_subpacks([build(2, 400, b"small"), build(5, 800, b"big")])
    assert files["RP/texts/en_US.lang"] == b"same"
    assert files["BP/scripts/image_videos.js"] == b"5" and files["RP/pack_icon.png"] == b"i5"
    assert files["RP/sounds/bvp_image/v/chunk_0.ogg"] == b"ogg5"       # 音声は本体に 1 つ
    assert "RP/textures/bvp_image/v/sheet_0.png" not in files
    assert files["RP/subpacks/tier2/textures/bvp_image/v/sheet_0.png"] == b"small"
    assert files["RP/subpacks/tier5/textures/bvp_image/v/sheet_1.png"] == b"extra"
    assert "RP/subpacks/tier2/textures/bvp_image/v/sheet_1.png" not in files
    entries = json.loads(files["RP/manifest.json"])["subpacks"]
    assert entries == [{"folder_name": "tier2", "name": "Tier 2: 400x200", "memory_tier": 0},
                       {"folder_name": "tier5", "name": "Tier 5: 800x400", "memory_tier": 32}]
    assert [e["tier"] for e in subpacks] == [2, 5]
