"""アニメ・イラスト向けの人物の切り抜き (mvcodec/person.py の AnimeSegmenter とモデルのダウンロード)。

本物のモデル (176 MB) は使わない。onnxruntime の代わりに、入力の画像をそのまま「人物の確からしさ」として返す
偽物のセッションで、余白を付けて 1024 × 1024 に置き、出力を元の範囲・大きさに戻す計算を確かめる。
"""
import hashlib
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mvcodec.person as person  # noqa: E402


class _FakeSession:
    """入力 (1, 3, 1024, 1024) の赤の成分を、そのまま出力 (1, 1, 1024, 1024) にする。"""
    seen = {}

    def __init__(self, path, providers):
        _FakeSession.seen = {"path": path, "providers": providers}

    def get_inputs(self):
        return [types.SimpleNamespace(name="img")]

    def run(self, _outputs, feeds):
        image = feeds["img"]
        assert image.shape == (1, 3, person.ANIME_INPUT_SIZE, person.ANIME_INPUT_SIZE) and image.dtype == np.float32
        return [image[:, :1]]


@pytest.fixture
def fake_onnxruntime(monkeypatch):
    module = types.SimpleNamespace(InferenceSession=_FakeSession,
                                   get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    return module


def test_mask_comes_back_at_the_original_place_and_size(tmp_path, fake_onnxruntime):
    model = tmp_path / "isnetis.onnx"
    model.write_bytes(b"x")
    segmenter = person.AnimeSegmenter(model_path=model)
    # GPU (DirectML) があれば先に使う
    assert _FakeSession.seen["providers"] == ["DmlExecutionProvider", "CPUExecutionProvider"]
    # 横長の画像の右半分だけ赤い: 確からしさも右半分だけ高く、大きさは元のまま
    rgb = np.zeros((90, 160, 3), np.uint8)
    rgb[:, 80:, 0] = 255
    mask = segmenter(rgb)
    assert mask.shape == (90, 160)
    assert mask[:, 85:].min() > 0.9 and mask[:, :75].max() < 0.1


def test_model_is_downloaded_once_and_checked(tmp_path, monkeypatch, fake_onnxruntime):
    source = tmp_path / "source.onnx"
    source.write_bytes(b"model bytes")
    monkeypatch.setenv("BVP_MODEL_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(person, "ANIME_MODEL_URL", source.as_uri())
    monkeypatch.setattr(person, "ANIME_MODEL_SHA256", hashlib.sha256(b"model bytes").hexdigest())
    progress = []
    person.AnimeSegmenter(download_progress=lambda done, total: progress.append(done))
    cached = tmp_path / "cache" / person.ANIME_MODEL_FILE
    assert cached.read_bytes() == b"model bytes" and progress[-1] == len(b"model bytes")
    # 2 回目はダウンロードしない
    source.unlink()
    person.AnimeSegmenter()
    assert _FakeSession.seen["path"] == str(cached)


def test_broken_download_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source.onnx"
    source.write_bytes(b"truncated")
    target = tmp_path / "cache" / "model.onnx"
    with pytest.raises(RuntimeError):
        person.download_model(source.as_uri(), target, hashlib.sha256(b"the real model").hexdigest())
    assert not target.exists() and not list(target.parent.glob("*.part"))


def test_command_line_offers_both_models():
    assert set(person.SEGMENTERS) == {"photo", "anime"}
    assert person.SEGMENTERS["anime"] is person.AnimeSegmenter


def test_frames_without_an_anime_person_use_the_photo_model(tmp_path, fake_onnxruntime):
    """アニメ向けのモデルが何も見つけないフレーム (3DCG の強い照明など) だけ、実写向きのモデルの結果を使う。"""
    model = tmp_path / "isnetis.onnx"
    model.write_bytes(b"x")
    calls = []

    def photo(rgb):
        calls.append(rgb.shape)
        return np.full(rgb.shape[:2], 0.75, np.float32)

    segmenter = person.AnimeSegmenter(model_path=model, fallback=photo)
    red = np.zeros((90, 160, 3), np.uint8)
    red[:, 80:, 0] = 255
    assert segmenter(red)[:, 85:].min() > 0.9 and not calls
    dark = np.zeros((90, 160, 3), np.uint8)
    assert np.allclose(segmenter(dark), 0.75) and calls == [(90, 160, 3)]
    # 使わない設定なら、何も見つけなくてもそのまま
    assert person.AnimeSegmenter(model_path=model, fallback=None)(dark).max() < 0.1


def test_fast_motion_leaves_no_ghost_but_small_noise_is_smoothed():
    """人物の範囲は前のフレームとならすが、速く動いた所には前の位置の残像 (二重の影) を残さない。
    小さな揺れ (確からしさの差が小さい所) は今まで通りならす。"""
    frames = []

    def segmenter(rgb):
        return frames.pop(0)

    splitter = person.PersonSplitter(segmenter)
    rgb = np.full((40, 60, 3), 30, np.uint8)
    left = np.zeros((40, 60), np.float32)
    left[10:30, 5:20] = 1.0
    right = np.zeros((40, 60), np.float32)
    right[10:30, 35:50] = 1.0
    frames += [left, right]
    splitter(rgb)
    mask, _ = splitter(rgb)
    assert mask[10:30, 5:20].max() < 0.5  # 前の位置に残像が無い
    assert mask[10:30, 35:50].min() > 0.9  # 今の位置はそのまま
    # 小さな揺れはならす: 0.6 → 0.5 は、そのままより前の値に寄る
    steady = np.full((40, 60), 0.6, np.float32)
    wobble = np.full((40, 60), 0.5, np.float32)
    splitter = person.PersonSplitter(segmenter)
    frames += [steady, wobble]
    splitter(rgb)
    mask, _ = splitter(rgb)
    assert 0.5 < mask.mean() < 0.6


class _SalientSession:
    """目立つ物の切り抜きの偽物: 赤の強い所と、青の強い所 (字幕や光の筋のつもり) の両方を「目立つ」とする。"""

    def __init__(self, path, options=None, providers=None):
        pass

    def get_inputs(self):
        return [types.SimpleNamespace(name="input_image")]

    def run(self, _outputs, feeds):
        image = feeds["input_image"]
        assert image.shape == (1, 3, person.SALIENT_INPUT_SIZE, person.SALIENT_INPUT_SIZE)
        strong = (image[:, :1] > 1.0) | (image[:, 2:3] > 1.0)
        return [np.where(strong, 8.0, -8.0).astype(np.float32)]


def test_salient_blobs_are_kept_only_where_the_photo_model_sees_a_person(tmp_path, monkeypatch):
    module = types.SimpleNamespace(InferenceSession=_SalientSession, SessionOptions=lambda: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    model = tmp_path / "salient.onnx"
    model.write_bytes(b"x")
    rgb = np.zeros((90, 160, 3), np.uint8)
    rgb[20:70, 20:50, 0] = 255   # 人物 (赤)
    rgb[20:70, 100:150, 2] = 255  # 光の筋 (青)
    photo = lambda image: (image[..., 0] > 128).astype(np.float32) * 0.8  # 写真向けのモデルは赤だけを人物と見る
    segmenter = person.SalientPersonSegmenter(photo, model_path=model)
    mask = segmenter(rgb)
    assert mask[25:65, 25:45].min() > 0.9      # 人物の塊は残る (縁まで)
    assert mask[:, 95:].max() == 0              # 光の筋の塊は消える


def test_fallback_uses_salient_cutout_only_with_a_gpu(tmp_path, monkeypatch, fake_onnxruntime):
    model = tmp_path / "isnetis.onnx"
    model.write_bytes(b"x")
    made = []
    monkeypatch.setattr(person, "PersonSegmenter", lambda: "photo")
    monkeypatch.setattr(person, "SalientPersonSegmenter",
                        lambda photo, providers=None, download_progress=None: made.append(photo) or "salient")
    gpu = person.AnimeSegmenter(model_path=model, providers=["DmlExecutionProvider", "CPUExecutionProvider"])
    assert gpu._make_fallback() == "salient" and made == ["photo"]
    cpu = person.AnimeSegmenter(model_path=model, providers=["CPUExecutionProvider"])
    assert cpu._make_fallback() == "photo"


def test_components_are_labelled_separately():
    mask = np.zeros((40, 60), bool)
    mask[5:15, 5:15] = True
    mask[25:35, 40:55] = True
    labels = person._label(mask)
    assert labels.max() == 2 and labels[10, 10] != labels[30, 45] and labels[0, 0] == 0
