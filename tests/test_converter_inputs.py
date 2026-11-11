"""変換 (convert.py) の入力まわりの不具合の再現テスト: 壊れた動画・写真の向き・16 bit と透明・連番の並び・
1 マスの大きさ・極端な縦横比・色数の上限・--fit cover の案内の動画・--dither-follow-motion の CPU と GPU。"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert  # noqa: E402
from mvcodec.color import PALETTES  # noqa: E402
from mvcodec.decode import iter_decoded_frames  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg が必要")


def frames_of(path):
    text = Path(path).read_text(encoding="utf-8")
    import json
    return [rgb for _, rgb in iter_decoded_frames(json.loads(text[text.index("{"):text.rindex("}") + 1]))]


def convert_dir(frames_dir, out, *extra, width=8, height=8):
    argv = ["--frames-dir", str(frames_dir), "--output", str(out), "--width", str(width), "--height", str(height),
            "--fps", "10", "--threads", "1", "--device", "cpu", "--palette", "concrete", *extra]
    assert convert.main(argv) == 0
    return frames_of(out)


@needs_ffmpeg
def test_a_damaged_video_fails_quickly_instead_of_hanging(tmp_path):
    """壊れた動画で ffmpeg がエラーを書き続けても、stderr が詰まって止まらない (以前は待ち合って止まっていた)。"""
    video = tmp_path / "bad.ts"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=30",
                    "-c:v", "mpeg2video", str(video)], check=True)
    data = bytearray(video.read_bytes())
    rng = np.random.default_rng(0)
    for i in rng.choice(len(data), len(data) // 300, replace=False):
        data[i] = rng.integers(0, 256)
    video.write_bytes(bytes(data))
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import convert\n"
        "n = sum(1 for _ in convert.ffmpeg_frame_generator(sys.argv[2], 32, 18, 30))\n"
        "print(n)\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(Path(convert.__file__).parent), str(video)],
                            capture_output=True, text=True, timeout=60)
    # 壊れ方によっては ffmpeg が失敗で終わる。どちらでも、止まらずに 60 秒以内に終わればよい
    assert result.returncode == 0 or "ffmpeg" in result.stderr


def test_photo_orientation_follows_exif(tmp_path):
    """EXIF の向き 6 (右に 90 度) の写真は、縦向きとして変換する (以前は横向きのままだった)。"""
    img = Image.new("RGB", (40, 20))
    img.paste((0, 0, 255), (0, 0, 20, 20))
    img.paste((255, 0, 0), (20, 0, 40, 20))
    exif = Image.Exif()
    exif[0x0112] = 6
    (tmp_path / "in").mkdir()
    img.save(tmp_path / "in" / "photo.jpg", exif=exif.tobytes(), quality=95)
    rgb = np.asarray(convert.letterbox_image(Image.open(tmp_path / "in" / "photo.jpg"), 20, 40))
    # 右に 90 度回すと、左の青が上、右の赤が下になる
    assert rgb[5, 10, 2] > 150 and rgb[35, 10, 0] > 150


def test_16bit_and_transparent_images(tmp_path):
    ramp = (np.linspace(0, 65535, 64)[None, :].repeat(8, 0)).astype(np.uint16)
    gray = convert.to_rgb(Image.fromarray(ramp))
    assert 100 < np.asarray(gray).mean() < 155  # 以前は 254 (白く飛ぶ)
    rgba = np.zeros((8, 8, 4), np.uint8)
    rgba[..., 1] = 255  # 隠れた色は緑
    rgba[4:, ..., 3] = 255
    out = np.asarray(convert.to_rgb(Image.fromarray(rgba, "RGBA")))
    assert out[:4].max() == 0 and out[4:, :, 1].min() == 255


def test_frames_dir_is_sorted_by_number_and_obeys_duration(tmp_path):
    folder = tmp_path / "seq"
    folder.mkdir()
    for n in (1, 2, 10, 11):
        Image.new("RGB", (8, 8), (n * 20, n * 20, n * 20)).save(folder / f"{n}.png")
    names = [p.name for p in sorted(folder.iterdir(), key=convert.natural_key)]
    assert names == ["1.png", "2.png", "10.png", "11.png"]
    frames = convert_dir(folder, tmp_path / "all.js", "--palette", "full")
    brightness = [int(f.mean()) for f in frames]
    assert brightness == sorted(brightness)
    assert len(convert_dir(folder, tmp_path / "two.js", "--duration", "0.2")) == 2


@pytest.mark.parametrize("size", [(1, 16), (16, 1), (1, 1)])
def test_one_cell_wide_or_high(tmp_path, size):
    folder = tmp_path / "f"
    folder.mkdir()
    for n in range(3):
        Image.new("RGB", (16, 16), (n * 80, 0, 0)).save(folder / f"{n}.png")
    assert len(convert_dir(folder, tmp_path / "o.js", width=size[0], height=size[1])) == 3


@needs_ffmpeg
def test_extreme_aspect_ratio_with_pad(tmp_path):
    video = tmp_path / "wide.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=2000x60:r=10:d=0.5",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    frames = list(convert.ffmpeg_frame_generator(video, 8, 8, 10))
    assert frames and frames[0].shape == (8, 8, 3)


@pytest.mark.parametrize("mode,colors", [("screen", 129), ("relief", 128)])
def test_too_many_palette_colors_is_refused_up_front(tmp_path, mode, colors):
    with pytest.raises(SystemExit):
        convert.parse_args(["--frames-dir", str(tmp_path), "--output", str(tmp_path / "o.js"), "--palette", "auto",
                            "--palette-colors", str(colors), "--mode", mode])


@needs_ffmpeg
def test_fit_cover_also_crops_the_mask_video(tmp_path, monkeypatch):
    """--fit cover のとき、マスクの動画も色と同じように切る (以前は余白を付けて縮めていて、形と色がずれた)。"""
    src = tmp_path / "src.mp4"
    mask = tmp_path / "mask.mp4"
    for path, source in ((src, "testsrc2=s=320x180:r=10:d=0.5"), (mask, "color=white:s=320x180:r=10:d=0.5")):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", str(path)],
                       check=True)
    calls = []
    original = convert.ffmpeg_frame_generator

    def recording(video, *args, **kwargs):
        calls.append((Path(video).name, kwargs.get("fit", args[5] if len(args) > 5 else "pad")))
        return original(video, *args, **kwargs)

    monkeypatch.setattr(convert, "ffmpeg_frame_generator", recording)
    out = tmp_path / "o.js"
    assert convert.main(["--input-video", str(src), "--mask-video", str(mask), "--mode", "extrude", "--fit", "cover",
                         "--width", "32", "--height", "32", "--fps", "10", "--output", str(out), "--device", "cpu",
                         "--threads", "1", "--palette", "concrete", "--depth", "4"]) == 0
    assert ("mask.mp4", "cover") in calls and ("mask.mp4", "pad") not in calls


def test_follow_motion_gives_the_same_output_on_cpu_and_gpu_code():
    """--dither-follow-motion で、CPU の条件付き更新も前のフレームの番号をずらしてから比べる (GPU と同じ)。"""
    torch = pytest.importorskip("torch")  # noqa: F841
    rng = np.random.default_rng(3)
    base = rng.integers(0, 256, size=(40, 80, 3), dtype=np.uint8)
    base = np.asarray(Image.fromarray(base).resize((160, 40), Image.Resampling.BILINEAR))
    frames = [np.ascontiguousarray(base[:, 2 * t:2 * t + 64]) for t in range(12)]
    palette = PALETTES["full"]
    pal_rgb = np.array([item["rgb"] for item in palette], dtype=np.float64)
    cpu = list(convert.quantize_frames_cpu(frames, [palette], [0, 12], 64, 40, "blue_noise", False, 1,
                                           dither_follow_motion=True, temporal_hysteresis=24.0))
    gpu = list(convert.quantize_frames_gpu(frames, [pal_rgb], [0, 12], 64, 40, "blue_noise", False, gpu=None,
                                           dither_follow_motion=True, temporal_hysteresis=24.0))
    differing = np.mean([np.mean(a != b) for a, b in zip(cpu, gpu)])
    assert differing < 0.01
