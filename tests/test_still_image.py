"""画像 1 枚を、1 コマの動画 (止まったブロックアート) にするテスト。

ffmpeg の fps のフィルターは 1 枚の画像を落とすので、convert.py は画像を Pillow で読む。再生が終わってもブロックは
残るので (main.js の stopPlayback はブロックを消さない)、画像は置いたままのブロックアートになる。
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".webp"])
def test_a_picture_becomes_one_frame(tmp_path, suffix):
    arr = np.zeros((40, 80, 3), np.uint8)
    arr[:, :40] = (200, 30, 30)
    arr[:, 40:] = (30, 30, 200)
    picture = tmp_path / f"art{suffix}"
    Image.fromarray(arr).save(picture)
    out = tmp_path / "art.js"
    assert convert.main(["--input-video", str(picture), "--output", str(out), "--width", "32", "--height", "32",
                         "--fps", "10", "--device", "cpu", "--threads", "1"]) == 0
    data = load_frame_data(out)
    assert data["frame_count"] == 1
    (_, rgb), = list(iter_decoded_frames(data))
    # 縦横比を保って収める (上下は黒い帯)、左は赤・右は青
    assert rgb.shape == (32, 32, 3)
    assert rgb[16, 4, 0] > rgb[16, 4, 2] and rgb[16, 28, 2] > rgb[16, 28, 0]
    assert rgb[0, 16].sum() < 60


def test_playback_end_keeps_the_blocks():
    """再生が終わったとき (stopPlayback) にブロックを消さないこと。消すと画像のブロックアートが残らない。"""
    main = (Path(__file__).resolve().parent.parent / "main.js").read_text(encoding="utf-8")
    body = main[main.index("  stopPlayback() {"):main.index("\n  }\n", main.index("  stopPlayback() {"))]
    assert "fillBlocks" not in body and "clearScreen" not in body and "air" not in body
