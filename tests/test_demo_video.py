"""お試し用アドオンの見本の映像 (scripts/make_demo_video.py) のテスト: 絵と音を作れて、場面が移り変わる。"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_demo_video import DURATION, SAMPLE_RATE, Scene, music  # noqa: E402


def test_scenes_change_from_sunset_to_night_to_day():
    scene = Scene(96, 54)
    sunset, night, day = (scene.frame(t).astype(np.float32) for t in (1.0, 7.0, 13.0))
    assert sunset.shape == (54, 96, 3)
    top = lambda img: img[:10, :8].mean(axis=(0, 1))  # noqa: E731 (左上の空の色)
    assert top(night).mean() < top(sunset).mean() * 0.5  # 夜は暗い
    assert top(day)[2] > top(day)[0] + 40  # 昼は青空
    # 猫 (オレンジ) が画面にいる
    orange = (np.abs(day[..., 0] - 250) < 20) & (np.abs(day[..., 1] - 150) < 30) & (day[..., 2] < 90)
    assert orange.sum() > 50


def test_music_has_the_video_length_and_fades():
    audio = music()
    assert len(audio) == int(DURATION * SAMPLE_RATE)
    assert np.abs(audio).max() <= 1
    assert np.abs(audio[: SAMPLE_RATE // 100]).max() < 0.05  # 最初はフェードイン
    assert np.abs(audio[SAMPLE_RATE: 2 * SAMPLE_RATE]).max() > 0.1  # 途中は鳴っている
