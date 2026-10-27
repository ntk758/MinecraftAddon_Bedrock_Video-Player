"""アニメ・イラスト向けの人物の切り抜き (mvcodec/person.py の AnimeSegmenter とモデルのダウンロード)。

本物のモデル (176 MB) は使わない。onnxruntime の代わりに、入力の画像をそのまま「人物の確からしさ」として返す
偽物のセッションで、余白を付けて 1024 × 1024 に置き、出力を元の範囲・大きさに戻す計算を確かめる。
後半は TemporalGuard (人物を見つけないフレームを、前後 6 フレームを見て直す): フレームごとに決めた確からしさを返す
偽の切り抜きで、前後で埋める・面積と位置を合わせる・場面の切り替わりをまたがない、を確かめる。
"""
import hashlib
import json
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mvcodec.person as person  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_gpu_probe(monkeypatch):
    """person.main は始めたときの空きの VRAM を測る (PowerShell を起動して約 2 秒)。テストでは測らない。"""
    from mvcodec import resources

    monkeypatch.setattr(resources, "gpu_memory", lambda: None)


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

    class Salient:
        def __init__(self, photo, providers=None, download_progress=None, slow_ms=None):
            made.append(photo)

        def warm_up(self):
            pass

    monkeypatch.setattr(person, "PersonSegmenter", lambda: "photo")
    monkeypatch.setattr(person, "SalientPersonSegmenter", Salient)
    gpu = person.AnimeSegmenter(model_path=model, providers=["DmlExecutionProvider", "CPUExecutionProvider"])
    assert isinstance(gpu._make_fallback(), Salient) and made == ["photo"]
    cpu = person.AnimeSegmenter(model_path=model, providers=["CPUExecutionProvider"])
    assert cpu._make_fallback() == "photo"


def test_components_are_labelled_separately():
    mask = np.zeros((40, 60), bool)
    mask[5:15, 5:15] = True
    mask[25:35, 40:55] = True
    labels = person._label(mask)
    assert labels.max() == 2 and labels[10, 10] != labels[30, 45] and labels[0, 0] == 0


def test_gui_retries_the_cutout_with_lighter_profiles(tmp_path, monkeypatch):
    """GUI は人物の切り抜きを別のプロセスで行い、メモリ不足 (終了コード 3) や落ちたときは軽い段階でやり直す。"""
    import queue

    import video_player_gui as gui
    from gui_i18n import Translator
    from mvcodec import resources

    gib = 1 << 30
    monkeypatch.setattr(resources, "system_memory", lambda: (32 * gib, 16 * gib))
    monkeypatch.setattr(resources, "gpu_memory", lambda: (16 * gib, 12 * gib))

    class Headless:
        def __init__(self, codes):
            self.tr = Translator("ja")
            self.messages = queue.Queue()
            self.codes = list(codes)
            self.profiles = []

        def _person_process(self, argv):
            self.profiles.append(argv[argv.index("--profile") + 1])
            return self.codes.pop(0), "out of memory: E_OUTOFMEMORY"

    app = Headless([person.EXIT_OUT_OF_MEMORY, -1073741819, 0])
    mask, background = gui.PackBuilderApp._split_person(app, tmp_path / "in.mp4", tmp_path / "work", 80, 45, 10, None,
                                                        "ffmpeg", "anime")
    # 空きメモリが多いので一番重い段階から始め、メモリ不足・落ちたときは 1 段ずつ軽くする
    assert app.profiles == ["full", "gpu", "cpu"]
    assert mask.name == "mask.mp4" and background.name == "background.mp4"
    log = []
    while not app.messages.empty():
        log.append(str(app.messages.get()))
    assert any("16.0" in line for line in log)  # 空きの RAM を知らせる

    # 全部の段階でだめならエラー
    app = Headless([person.EXIT_OUT_OF_MEMORY] * 4)
    with pytest.raises(RuntimeError):
        gui.PackBuilderApp._split_person(app, tmp_path / "in.mp4", tmp_path / "work", 80, 45, 10, None, "ffmpeg", "anime")
    assert app.profiles == ["full", "gpu", "cpu", "photo"]


def test_cutout_stops_when_ram_runs_low(monkeypatch):
    from mvcodec import resources

    monkeypatch.setattr(resources, "system_memory", lambda: (8 << 30, 100 << 20))
    with pytest.raises(MemoryError):
        person._check_free_ram()
    assert person.is_out_of_memory(MemoryError())
    assert person.is_out_of_memory(RuntimeError("[ONNXRuntimeError] DmlExecutionProvider 887A0005"))
    assert not person.is_out_of_memory(ValueError("bad input"))


def test_profiles_follow_free_memory():
    gib = 1 << 30
    assert person.profile_ladder("anime", 16 * gib, 14 * gib) == ["full", "gpu", "cpu", "photo"]
    # 目立つ物の切り抜き (BiRefNet) は最初の推論で VRAM を約 11.3 GB、確保済みメモリを約 12.4 GB 使う (RX 9070 XT の実測):
    # 空きの VRAM が 8 GiB なら gpu から、14 GiB なら full から
    assert person.PROFILE_NEEDS["full"] == (12 * gib, 12 * gib)
    assert person.profile_ladder("anime", 16 * gib, 8 * gib) == ["gpu", "cpu", "photo"]
    assert person.profile_ladder("anime", 16 * gib, 12 * gib)[0] == "full"
    assert person.profile_ladder("anime", 16 * gib, 12 * gib - 1)[0] == "gpu"
    # 目立つ物の切り抜きは RAM を 5〜10 GB 使うので、空きが少なければ使わない
    assert person.profile_ladder("anime", 6 * gib, 12 * gib)[0] == "gpu"
    assert person.profile_ladder("anime", 6 * gib, gib // 2)[0] == "cpu"
    assert person.profile_ladder("anime", None, None)[0] == "full"
    assert person.profile_ladder("photo", 16 * gib, 12 * gib) == ["photo"]


# ---------------------------------------------------------------------------
# 時間方向の安定化 (TemporalGuard): 偽の切り抜きで、アニメ向けが人物を見つけないフレームの扱いを確かめる
# ---------------------------------------------------------------------------

SHAPE = (90, 160)


def _scene_images(layout, shape=SHAPE, seed=3):
    """layout の数ずつ、場面ごとに違う大きな模様の画像を作る (場面の中は ±2 の細かい揺れだけ。場面の境目で構図が変わる)。"""
    rng = np.random.default_rng(seed)
    frames = []
    for count in layout:
        base = np.repeat(np.repeat(rng.integers(30, 220, (6, 8, 3)), shape[0] // 6, axis=0), shape[1] // 8, axis=1)
        for _ in range(count):
            frames.append(np.clip(base + rng.integers(-2, 3, base.shape), 0, 255).astype(np.uint8))
    return frames


def _rect(top, left, height, width, shape=SHAPE):
    mask = np.zeros(shape, np.float32)
    mask[top:top + height, left:left + width] = 1.0
    return mask


def _cone(center, radius, peak=1.0, shape=SHAPE):
    """中心から外へ直線的に下がる確からしさ (本物のモデルの、縁がぼやけた出力のつもり)。"""
    y, x = np.mgrid[:shape[0], :shape[1]]
    return np.clip(peak * (1 - np.hypot(y - center[0], x - center[1]) / radius), 0, None).astype(np.float32)


class _Scripted:
    """フレームごとに決めた確からしさを返す偽の切り抜き。primary がアニメ向け、fallback_prob が予備 (呼んだフレームの番号を覚える)。"""

    def __init__(self, frames, primary, fallback=None):
        self.index = {id(frame): i for i, frame in enumerate(frames)}
        self.primary_probs, self.fallback_probs = primary, fallback
        self.fallback_calls = []

    def primary(self, rgb):
        return self.primary_probs[self.index[id(rgb)]]

    def fallback_prob(self, rgb, frame=None):
        index = self.index[id(rgb)]
        assert frame in (None, index)   # TemporalGuard は今のフレームの番号を渡す
        self.fallback_calls.append(index)
        return None if self.fallback_probs is None else self.fallback_probs[index]


def _settle_all(segmenter, frames, lookahead=person.PERSON_LOOKAHEAD):
    guard = person.TemporalGuard(segmenter, lookahead)
    settled = []
    for frame in frames:
        settled += guard.push(frame)
    settled += guard.flush()
    return guard, settled


def _fraction(prob):
    return float((prob >= 0.5).mean())


def test_scene_images_make_cuts_only_between_scenes():
    frames = _scene_images([4, 3, 5])
    features = [person.scene_features(frame) for frame in frames]
    cuts = [i for i in range(1, len(frames)) if person.is_scene_cut(features[i - 1], features[i])]
    assert cuts == [4, 7]


def test_scene_cut_rule_uses_the_histogram_only_when_the_structure_is_somewhat_different():
    def features(correlation, histogram_distance):
        luma = (np.array([1.0, 0.0]), np.array([correlation, np.sqrt(1 - correlation ** 2)]))
        histogram = (np.array([1.0, 0.0]), np.array([1.0 - histogram_distance, histogram_distance]))
        return (luma[0], histogram[0]), (luma[1], histogram[1])

    assert person.is_scene_cut(*features(0.4, 0.0))        # 構図が大きく違えば、色が同じでも切り替わり
    assert not person.is_scene_cut(*features(0.9, 1.0))    # 構図が近ければ、色が全く違っても切り替わりでない
    assert person.is_scene_cut(*features(0.6, 0.9))        # 中間: 色のヒストグラムが大きく違えば切り替わり
    assert not person.is_scene_cut(*features(0.6, 0.2))    # 中間でも、色が近ければ切り替わりでない
    # ヒストグラムは各色 3 ビット (512 ビン)。赤 224 と赤 96 (上位 3 ビットが 7 と 3) は別のビン
    dark_red, light_red = np.zeros((16, 16, 3), np.uint8), np.zeros((16, 16, 3), np.uint8)
    dark_red[..., 0], light_red[..., 0] = 96, 224
    histograms = [person.scene_features(image)[1] for image in (dark_red, light_red)]
    assert histograms[0].shape == (512,) and np.isclose(histograms[0].sum(), 1) and np.isclose(histograms[1].sum(), 1)
    assert np.isclose(0.5 * np.abs(histograms[0] - histograms[1]).sum(), 1.0)


def test_a_short_gap_inside_a_scene_is_filled_from_the_neighbours_without_the_fallback_model():
    frames = _scene_images([12])
    people = [_rect(30, 20 + 4 * t, 36, 20) for t in range(12)]
    empty = np.zeros(SHAPE, np.float32)
    people[4], people[5], people[9] = empty, empty, empty
    segmenter = _Scripted(frames, people, fallback=[_cone((50, 80), 30)] * 12)
    guard, settled = _settle_all(segmenter, frames)
    assert segmenter.fallback_calls == []
    # 2 フレームの欠け: それぞれ近い方 (4 は 3、5 は 6)。1 フレームの欠けは距離が同じなので前 (8)
    assert np.array_equal(settled[4][1], people[3]) and np.array_equal(settled[5][1], people[6])
    assert np.array_equal(settled[9][1], people[8])
    assert guard.counts == {"anime": 9, "filled": 3, "matched": 0, "fallback": 0, "rejected": 0, "flagged": 3}


def test_a_gap_of_three_frames_is_not_filled():
    frames = _scene_images([12])
    people = [_rect(30, 20 + 4 * t, 36, 20) for t in range(12)]
    for t in (4, 5, 6):
        people[t] = np.zeros(SHAPE, np.float32)
    segmenter = _Scripted(frames, people, fallback=[_cone((50, 80), 30)] * 12)
    guard, _ = _settle_all(segmenter, frames)
    assert segmenter.fallback_calls == [4, 5, 6]
    assert guard.counts["filled"] == 0 and guard.counts["matched"] == 3


def test_a_long_gap_takes_the_size_of_the_nearby_people_and_drops_distant_blobs():
    frames = _scene_images([16])
    person_box = lambda t: _rect(30, 20 + 4 * t, 36, 20)   # 36 × 20 = 720 画素 (画面の 5%)
    people = [person_box(t) for t in range(16)]
    # 5 フレーム (5〜9) は何も見つからず、予備は 3 倍の大きさの塊 (縁がなだらかに下がる) と、離れた所の塊 (字幕や光の筋のつもり)
    for t in range(5, 10):
        people[t] = np.zeros(SHAPE, np.float32)
    fallback = [np.maximum(_cone((48, 30 + 4 * t), 52.4), _cone((5, 150), 8, 0.9)) for t in range(16)]
    target = _fraction(person_box(0))
    assert 2.5 * target < _fraction(fallback[5]) < 3.5 * target   # そのままでは人物の 3 倍の面積
    segmenter = _Scripted(frames, people, fallback)
    guard, settled = _settle_all(segmenter, frames)
    assert segmenter.fallback_calls == [5, 6, 7, 8, 9]
    assert guard.counts["matched"] == 5 and guard.counts["fallback"] == 0
    for t in range(5, 10):
        prob = settled[t][1]
        assert abs(_fraction(prob) - target) <= 0.2 * target, t
        assert prob[:25, 120:].max() == 0                             # 離れた塊は消える
        assert (prob[44:52, 26 + 4 * t:34 + 4 * t] >= 0.5).all()       # 人物の位置は残る


def test_nothing_is_filled_or_matched_across_a_scene_cut():
    # 場面 A (4 フレーム、左に小さい人物) → 場面 B (6 フレーム、右に大きい人物)。
    frames = _scene_images([4, 6])
    left, right = _rect(30, 10, 30, 16), _rect(10, 100, 60, 40)   # 面積は 480 と 2400 画素
    people = [left] * 4 + [right] * 6
    empty = np.zeros(SHAPE, np.float32)
    # 予備は、場面 A では左の人物のまわり、場面 B では右の人物のまわりに、どちらも人物より大きな塊を返す
    fallback = [_cone((45, 18), 50)] * 4 + [_cone((40, 120), 50)] * 6
    # 場面 A の最後 (3) が見つからない: 場面 B の最初のフレーム (4) で埋めない。A の小さい人物に合わせる
    people[3] = empty
    segmenter = _Scripted(frames, people, fallback)
    guard, settled = _settle_all(segmenter, frames)
    assert segmenter.fallback_calls == [3] and guard.counts["matched"] == 1 and guard.counts["filled"] == 0
    assert abs(_fraction(settled[3][1]) - _fraction(left)) <= 0.2 * _fraction(left)
    assert [cut for _, _, cut in settled] == [False, False, False, False, True, False, False, False, False, False]
    # 場面 B の最初 (4) が見つからない: 前の場面 A の人物では埋めず、B の大きい人物に合わせる
    people[3], people[4] = left, empty
    segmenter = _Scripted(frames, people, fallback)
    _, settled = _settle_all(segmenter, frames)
    assert segmenter.fallback_calls == [4]
    assert settled[4][1][:, :80].max() == 0
    assert abs(_fraction(settled[4][1]) - _fraction(right)) <= 0.2 * _fraction(right)
    # 場面 B の 2 フレーム (4, 5) が見つからない: すぐ前に A の正常なフレームがあっても埋めない
    people[4], people[5] = empty, empty
    segmenter = _Scripted(frames, people, fallback)
    guard, settled = _settle_all(segmenter, frames)
    assert segmenter.fallback_calls == [4, 5] and guard.counts["filled"] == 0
    # 場面 B が全部見つからない: 合わせる相手がいないので、予備の結果をそのまま使う (A に合わせない)
    people = [left] * 4 + [empty] * 6
    segmenter = _Scripted(frames, people, fallback)
    guard, settled = _settle_all(segmenter, frames)
    assert all(np.array_equal(settled[t][1], fallback[t]) for t in range(4, 10))
    assert guard.counts["fallback"] == 6 and guard.counts["matched"] == 0


def test_a_brightness_change_is_not_a_scene_cut_for_the_new_rule():
    rng = np.random.default_rng(5)
    dim = np.clip(np.repeat(np.repeat(rng.integers(40, 160, (6, 8, 1)), 15, axis=0), 20, axis=1)
                  + rng.integers(-3, 4, (90, 160, 3)), 0, 255).astype(np.uint8)
    bright = np.clip(dim.astype(np.float32) * 1.5, 0, 255).astype(np.uint8)
    # 前のフレームとの平均の色の差が大きいので、今までの規則 (SCENE_CUT_DIFF) なら場面の切り替わりになる
    assert np.abs(bright.astype(np.float32) - dim).mean() > person.SCENE_CUT_DIFF
    assert not person.is_scene_cut(person.scene_features(dim), person.scene_features(bright))
    other = _scene_images([1], seed=9)[0]
    assert person.is_scene_cut(person.scene_features(dim), person.scene_features(other))
    # ならしと背景の記憶を消すかどうかは、渡された cut に従う (人物は左上の四角。確からしさ 0.6 → 0.5 の小さな揺れ)
    inside = _rect(10, 10, 30, 30) > 0
    steady, wobble = np.where(inside, 0.6, 0.0).astype(np.float32), np.where(inside, 0.5, 0.0).astype(np.float32)
    splitter = person.PersonSplitter(None)
    splitter(dim, steady, False)
    mask, _ = splitter(bright, wobble, False)
    assert 0.5 < mask[inside].mean() < 0.6                        # 切り替わりではないので、前とならす
    assert not np.array_equal(splitter.background[80, 150], bright[80, 150])   # 背景の記憶も残る (前の色と混ざる)
    splitter = person.PersonSplitter(None)
    splitter(dim, steady, False)
    mask, _ = splitter(bright, wobble, True)
    assert np.allclose(mask[inside], 0.5)                         # 切り替わりなら、今のフレームそのまま
    assert np.array_equal(splitter.background[80, 150], bright[80, 150].astype(np.float32))   # 背景の記憶も捨てる


def test_photo_path_is_unchanged_by_the_new_splitter_arguments():
    """切り抜きが写真向け (primary が無い) なら今まで通り: PersonSplitter は平均の色の差で場面の切り替わりを決める。
    元の実装 (版 6.28.0) をそのまま写した基準と、マスクも背景もバイト単位で同じ。"""

    class OldSplitter:
        def __init__(self, segmenter):
            self.segmenter = segmenter
            self.mask = None
            self.previous = None
            self.background = None
            self.known = None

        def __call__(self, rgb):
            from PIL import Image, ImageFilter
            prob = self.segmenter(rgb)
            frame = rgb.astype(np.float32)
            cut = self.previous is None or np.abs(frame - self.previous).mean() > person.SCENE_CUT_DIFF
            if cut or self.mask is None:
                self.mask = prob
                self.background = frame.copy()
                self.known = np.zeros(prob.shape, bool)
            else:
                keep = person.MASK_SMOOTHING * np.clip(1 - np.abs(prob - self.mask) / person.MASK_FOLLOW, 0, 1)
                self.mask = keep * self.mask + (1 - keep) * prob
            self.previous = frame
            hole = np.asarray(Image.fromarray((self.mask >= 0.5).astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0
            visible = ~hole
            self.background[visible] = ((1 - person.BACKGROUND_LEARNING) * self.background[visible]
                                        + person.BACKGROUND_LEARNING * frame[visible])
            self.background[visible & ~self.known] = frame[visible & ~self.known]
            self.known |= visible
            filled = person.fill_masked(rgb, hole, max(2, min(rgb.shape[:2]) // 8))
            background = np.where((hole & self.known)[..., None], self.background, filled).astype(np.uint8)
            return self.mask, background

    frames = _scene_images([5, 5], seed=11)
    # 平均の色の差で切り替わりになるのは、5 → 6 枚目 (番号 4 → 5) だけ
    assert [i for i in range(1, 10) if np.abs(frames[i].astype(np.float32) - frames[i - 1]).mean() > person.SCENE_CUT_DIFF] == [5]
    rng = np.random.default_rng(2)
    probs = [np.clip(_cone((45, 20 + 10 * i), 25) + rng.normal(0, 0.05, SHAPE), 0, 1).astype(np.float32) for i in range(10)]
    expected = []
    old = OldSplitter(lambda rgb, it=iter(probs): next(it))
    for frame in frames:
        expected.append(old(frame))
    new = person.PersonSplitter(lambda rgb, it=iter(probs): next(it))
    for frame, (mask, background) in zip(frames, expected):
        got_mask, got_background = new(frame)
        assert got_mask.tobytes() == mask.tobytes() and got_background.tobytes() == background.tobytes()


def test_only_the_anime_model_gets_the_temporal_guard(monkeypatch, capsys):
    """TemporalGuard を通すのは primary と fallback_prob を持つ切り抜き (AnimeSegmenter) だけ。写真向けは今まで通り 1 枚ずつ。"""
    import shutil
    import subprocess
    import tempfile

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "in.mp4"
        # 傾きのある暗い背景 (平らだと構図の相関が出ない) の前を、白い四角が動く 2 秒 (10 fps)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x203060:size=160x90:rate=10:duration=2",
                        "-vf", "format=rgb24,geq=r='20+X':g='40+Y':b='96',drawbox=x='40+20*sin(t*3)':y=20:w=30:h=60:color=white:t=fill",
                        "-c:v", "libx264", "-pix_fmt", "yuv444p", "-crf", "0", str(video)], check=True)
        bright = lambda rgb: (rgb.mean(axis=2) > 200).astype(np.float32)

        def photo(rgb):
            return bright(rgb)

        class Anime:
            """アニメ向けの偽物: 10 枚目と 11 枚目 (番号 10, 11) だけ、四角があっても何も見つけない。"""

            def __init__(self):
                self.reads = 0
                self.fallback_calls = 0

            def primary(self, rgb):
                self.reads += 1
                return np.zeros(rgb.shape[:2], np.float32) if self.reads in (11, 12) else bright(rgb)

            def fallback_prob(self, rgb, frame=None):
                self.fallback_calls += 1
                return np.full(rgb.shape[:2], 0.1, np.float32)

        made = []
        real_guard = person.TemporalGuard
        monkeypatch.setattr(person, "TemporalGuard", lambda *args, **kwargs: made.append(real_guard(*args, **kwargs)) or made[-1])
        stats = {}
        count = person.make_person_videos(video, Path(folder) / "m1.mp4", Path(folder) / "b1.mp4", 10, segmenter=photo,
                                          stats=stats)
        assert count == 20 and made == [] and "flagged" not in stats and stats["sources"] == {"photo": 20}
        stats.clear()
        seen = []
        anime = Anime()
        count = person.make_person_videos(video, Path(folder) / "m2.mp4", Path(folder) / "b2.mp4", 10, segmenter=anime,
                                          progress=seen.append, stats=stats)
        # 6 フレーム遅れるが、動画の終わりで残りを全部書き出す: フレームの数は同じ。2 フレームの欠けは前後で埋めるので、予備は呼ばない
        assert count == 20 and len(made) == 1 and seen == list(range(1, 21))
        assert stats["sources"] == {"anime": 18, "filled": 2, "matched": 0, "fallback": 0, "rejected": 0} and stats["flagged"] == 2
        assert anime.fallback_calls == 0
        masks = [frame[..., 0].mean() / 255 for frame in person.read_frames(Path(folder) / "m2.mp4", 10, None, "ffmpeg", 640)]
        assert len(masks) == 20 and min(masks) > 0.1   # 見つけなかった 2 枚にも、前後と同じ四角 (面積 12.5%) が入っている

        # コマンドラインは、出所ごとのフレーム数を 1 行で知らせる (写真向けのときは知らせない)
        monkeypatch.setattr(person, "make_segmenter", lambda model, profile, download_progress=None, **_: Anime())
        argv = [str(video), str(Path(folder) / "m3.mp4"), str(Path(folder) / "b3.mp4"), "--fps", "10", "--model", "anime",
                "--profile", "gpu"]
        assert person.main(argv) == 0
        lines = capsys.readouterr().out.splitlines()
        assert "sources anime=18 filled=2 matched=0 fallback=0 rejected=0 flagged=2" in lines \
            and lines[-1].startswith("done 20 frames")
        monkeypatch.setattr(person, "make_segmenter", lambda model, profile, download_progress=None, **_: photo)
        assert person.main(argv[:-4] + ["--model", "photo", "--profile", "photo"]) == 0
        assert not [line for line in capsys.readouterr().out.splitlines() if line.startswith("sources")]


def test_guard_output_equals_a_direct_reading_of_the_rules():
    """乱数で作った見つけない連なり・場面の切り替わりで、窓を持って 1 枚ずつ確定する TemporalGuard が、動画全体を見て
    規則どおりに決めた結果 (先行の試作と同じ式) と同じになる。始まり・終わり・場面の境目をまたぐ連なりも含む。"""
    shape = (48, 64)
    min_area = person.ANIME_MIN_AREA

    def rescale(prob, tau):
        return np.where(prob >= tau, 0.5 + 0.5 * (prob - tau) / (1 - tau), 0.5 * prob / tau).astype(np.float32)

    def expanded_box(mask):
        ys, xs = np.nonzero(mask)
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        dy, dx = int((y1 - y0 + 1) * 0.3) + 8, int((x1 - x0 + 1) * 0.3) + 8
        out = np.zeros(mask.shape, bool)
        out[max(0, y0 - dy):y1 + dy + 1, max(0, x0 - dx):x1 + dx + 1] = True
        return out

    rejected_total = 0
    for seed in range(8):
        rng = np.random.default_rng(seed)
        layout = [int(n) for n in rng.integers(3, 16, rng.integers(2, 5))]
        frames = _scene_images(layout, shape, seed=seed)
        n = len(frames)
        scene = np.repeat(np.arange(len(layout)), layout)
        primary = []
        for t in range(n):
            top, left = rng.integers(2, 20), rng.integers(2, 40)
            primary.append(_rect(top, left, rng.integers(10, 26), rng.integers(8, 20), shape))
        for _ in range(int(rng.integers(2, 6))):    # 見つけない連なり (長さ 1〜8)。何も無いか、しきい値未満の小さな点だけ
            start = int(rng.integers(0, n))
            for t in range(start, min(n, start + int(rng.integers(1, 9)))):
                primary[t] = _rect(5, 5, 3, 3, shape) if rng.random() < 0.4 else np.zeros(shape, np.float32)
        primary[int(rng.integers(0, n))] = _rect(5, 5, 4, 4, shape)   # しきい値のすぐ上 (16 / 3072 = 0.0052) は、見つけたことになる
        fallback = [np.maximum(_cone((rng.integers(15, 35), rng.integers(10, 54)), float(rng.integers(8, 30)), 1.0, shape),
                               _cone((rng.integers(0, 48), rng.integers(0, 64)), 6, 0.9, shape)) for _ in range(n)]
        segmenter = _Scripted(frames, primary, fallback)
        guard, settled = _settle_all(segmenter, frames)
        assert len(settled) == n
        assert [cut for _, _, cut in settled] == [t > 0 and scene[t] != scene[t - 1] for t in range(n)]
        area = np.array([_fraction(p) for p in primary])
        flag = area < min_area
        rejected = 0
        for t in range(n):
            expected = primary[t]
            if flag[t]:
                first = last = t
                while first > 0 and flag[first - 1] and scene[first - 1] == scene[t]:
                    first -= 1
                while last + 1 < n and flag[last + 1] and scene[last + 1] == scene[t]:
                    last += 1
                before = first - 1 if first > 0 and scene[first - 1] == scene[t] else None
                after = last + 1 if last + 1 < n and scene[last + 1] == scene[t] else None
                if last - first + 1 <= 2 and before is not None and after is not None:
                    expected = primary[before if t - before <= after - t else after]
                else:
                    expected = fallback[t]
                    near = [u for u in range(max(0, t - 6), min(n, t + 7)) if not flag[u] and scene[u] == scene[t]]
                    if near:
                        box = expanded_box(primary[near[0]] >= 0.5) | expanded_box(primary[near[-1]] >= 0.5)
                    else:   # 窓に無いときは、同じ場面の直前の正常なフレームの箱 (30 フレーム以内)
                        last = [u for u in range(t) if not flag[u]]
                        box = expanded_box(primary[last[-1]] >= 0.5) \
                            if last and scene[last[-1]] == scene[t] and t - last[-1] <= 30 else None
                    mask = expected >= 0.5
                    if mask.mean() >= min_area and (mask.mean() > 0.6 or (box is not None and not (mask & box).any())):
                        expected = np.zeros(shape, np.float32)      # 予備の結果を採らない (rejected)
                        rejected += 1
                    elif near:
                        tau = float(np.clip(np.quantile(expected, 1 - float(np.median(area[near]))), 0.3, 0.8))
                        expected = np.where(box, rescale(expected, tau), 0).astype(np.float32)
            assert np.array_equal(settled[t][1], expected), (seed, t)
            assert settled[t][0] is frames[t]
        assert sum(guard.counts[k] for k in ("anime", "filled", "matched", "fallback", "rejected")) == n
        assert guard.counts["rejected"] == rejected
        assert guard.counts["flagged"] == int(flag.sum())
        rejected_total += rejected
    assert rejected_total > 0   # 乱数の例の中に、予備の結果を採らない場合も入っている


def test_guard_keeps_a_small_window_and_delays_by_the_lookahead():
    frames = _scene_images([40])
    people = [_rect(30, 20 + t, 36, 20) for t in range(40)]
    segmenter = _Scripted(frames, people)
    guard = person.TemporalGuard(segmenter)
    outputs = []
    for i, frame in enumerate(frames):
        settled = guard.push(frame)
        outputs.append(len(settled))
        assert len(guard.window) <= 2 * person.PERSON_LOOKAHEAD + 1
    assert outputs == [0] * person.PERSON_LOOKAHEAD + [1] * (40 - person.PERSON_LOOKAHEAD)
    rest = guard.flush()
    assert len(rest) == person.PERSON_LOOKAHEAD and guard.flush() == []
    # 1 フレームだけの動画でも、終わりで確定する
    guard = person.TemporalGuard(_Scripted(frames[:1], people[:1]))
    assert guard.push(frames[0]) == [] and len(guard.flush()) == 1


def test_anime_segmenter_primary_does_not_use_the_fallback(tmp_path, fake_onnxruntime):
    model = tmp_path / "isnetis.onnx"
    model.write_bytes(b"x")
    calls = []

    def photo(rgb):
        calls.append(1)
        return np.full(rgb.shape[:2], 0.75, np.float32)

    segmenter = person.AnimeSegmenter(model_path=model, fallback=photo)
    dark = np.zeros((90, 160, 3), np.uint8)
    assert segmenter.primary(dark).max() < 0.1 and not calls
    assert np.allclose(segmenter.fallback_prob(dark), 0.75) and len(calls) == 1
    assert np.allclose(segmenter(dark), 0.75) and len(calls) == 2
    # 予備を使わない設定なら None。予備を作れなければ (PyTorch が無いなど) 最初の 1 回で None になり、primary のまま
    assert person.AnimeSegmenter(model_path=model, fallback=None).fallback_prob(dark) is None
    segmenter = person.AnimeSegmenter(model_path=model)
    segmenter._make_fallback = lambda: None
    assert segmenter.fallback_prob(dark) is None and segmenter.fallback is None
    assert segmenter(dark).max() < 0.1


def test_salient_cutout_without_an_approved_blob_returns_the_photo_probability(tmp_path, monkeypatch):
    module = types.SimpleNamespace(InferenceSession=_SalientSession, SessionOptions=lambda: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    model = tmp_path / "salient.onnx"
    model.write_bytes(b"x")
    rgb = np.zeros((90, 160, 3), np.uint8)
    rgb[20:70, 100:150, 2] = 255   # 光の筋 (青): 目立つ物だが、写真向けのモデルは人物と見ない
    rgb[10:40, 10:30, 1] = 200     # 目立つ物には入らないが、写真向けのモデルは人物と見る所 (緑)
    photo = lambda image: np.where(image[..., 1] > 128, 0.8, 0.0).astype(np.float32)
    mask = person.SalientPersonSegmenter(photo, model_path=model)(rgb)
    # 承認された塊が 1 つも無い: 空ではなく写真向けの結果
    assert mask.dtype == np.float32 and np.array_equal(mask, photo(rgb))


# ---------------------------------------------------------------------------
# 失敗の扱い: UTF-8 の出力、原因ごとの終了コード、メモリが足りないときに BiRefNet をその場で外す
# ---------------------------------------------------------------------------

# 子プロセスの代わりに person.main を動かす小さなスクリプト: onnxruntime を偽物にして、本物のモデルもダウンロードも使わない
_DRIVER = '''
import os, sys, types
sys.path.insert(0, os.environ["DRIVER_ROOT"])


class Session:
    def __init__(self, path, options=None, providers=None):
        pass

    def get_inputs(self):
        return [types.SimpleNamespace(name="img")]

    def run(self, outputs, feeds):
        return [feeds["img"][:, :1]]


sys.modules["onnxruntime"] = types.SimpleNamespace(
    InferenceSession=Session, SessionOptions=lambda: types.SimpleNamespace(), get_available_providers=lambda: ["CPUExecutionProvider"])
from mvcodec import person, resources
resources.gpu_memory = lambda: None
if os.environ.get("DRIVER_MODEL_URL"):
    person.ANIME_MODEL_URL = os.environ["DRIVER_MODEL_URL"]
sys.exit(person.main(sys.argv[1:]))
'''


def _run_person_cli(tmp_path, args, model_url=None, model_dir=None):
    """GUI と同じく、標準出力をパイプにして person.main を別のプロセスで動かす。(終了コード, utf-8 で読んだ出力)。"""
    import os
    import subprocess

    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    model_dir = model_dir or tmp_path / "models"
    model_dir.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(DRIVER_ROOT=str(ROOT), BVP_MODEL_DIR=str(model_dir))
    if model_url:
        env["DRIVER_MODEL_URL"] = model_url
    result = subprocess.run([sys.executable, str(driver), *map(str, args)], capture_output=True, env=env, timeout=120)
    return result.returncode, (result.stdout + result.stderr).decode("utf-8", errors="replace")


def _japanese(text):
    """ひらがな・カタカナ・漢字を含むか。"""
    import re

    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text))


def _without_os_text(line):
    """知らせの行の終わりの「(元の例外の文)」を除く (OS の例外の文は、日本語の Windows では日本語になる)。"""
    import re

    return re.sub(r" \(.*\)$", "", line)


def _model_dir_with_isnet(tmp_path):
    folder = tmp_path / "models"
    folder.mkdir(exist_ok=True)
    (folder / person.ANIME_MODEL_FILE).write_bytes(b"x")
    return folder


def test_pipe_output_is_utf8_and_an_unreadable_input_exits_with_4(tmp_path):
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    text = tmp_path / "not_a_video.txt"
    text.write_text("this is not a video", encoding="utf-8")
    code, output = _run_person_cli(tmp_path, [text, tmp_path / "m.mp4", tmp_path / "b.mp4", "--model", "anime", "--profile", "gpu"],
                                   model_dir=_model_dir_with_isnet(tmp_path))
    assert code == person.EXIT_INPUT_ERROR == 4
    # 1 行の知らせで終わる (トレースバックは出さない)。知らせは対象と元の例外の文だけで、日本語の説明は入らない (F6)
    assert "Traceback" not in output and "�" not in output
    lines = [line for line in output.splitlines() if line.startswith("input error: ")]
    assert len(lines) == 1 and lines[0].startswith("input error: could not read the video: ") and not _japanese(lines[0])
    # 日本語のパスは、パイプ越しにそのまま届く (cp932 で書くと GUI の読み方 (utf-8) では文字化けする)
    japanese = tmp_path / "動画.txt"
    japanese.write_text("this is not a video", encoding="utf-8")
    code, output = _run_person_cli(tmp_path, [japanese, tmp_path / "m.mp4", tmp_path / "b.mp4", "--model", "anime", "--profile", "gpu"],
                                   model_dir=_model_dir_with_isnet(tmp_path))
    assert code == person.EXIT_INPUT_ERROR and "�" not in output and "動画.txt" in output


def test_a_missing_ffmpeg_exits_with_4(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"x")
    code, output = _run_person_cli(
        tmp_path, [video, tmp_path / "m.mp4", tmp_path / "b.mp4", "--model", "anime", "--profile", "gpu",
                   "--ffmpeg", tmp_path / "no-such-ffmpeg"], model_dir=_model_dir_with_isnet(tmp_path))
    assert code == person.EXIT_INPUT_ERROR and "�" not in output
    assert "input error: could not start ffmpeg: " in output and "Traceback" not in output
    line = [line for line in output.splitlines() if line.startswith("input error: ")][-1]
    assert not _japanese(_without_os_text(line))
    # 読み取りも書き出しも、起動できないのは同じ InputError (FileNotFoundError はそのまま出さない)
    with pytest.raises(person.InputError):
        list(person.read_frames(video, 10, ffmpeg=str(tmp_path / "no-such-ffmpeg")))
    with pytest.raises(person.InputError):
        person._writer(tmp_path / "x.mp4", 16, 16, 10, str(tmp_path / "no-such-ffmpeg"))


@pytest.mark.parametrize("problem", ["wrong_bytes", "unreachable"])
def test_a_failed_model_download_exits_with_5(tmp_path, problem):
    """SHA-256 が合わないモデルも、ダウンロードできないモデルも、終了コード 5 (軽い段階にしても直らない)。"""
    wrong = tmp_path / "wrong.onnx"
    wrong.write_bytes(b"not the real model")
    url = wrong.as_uri() if problem == "wrong_bytes" else (tmp_path / "missing.onnx").as_uri()
    models = tmp_path / "empty_models"
    code, output = _run_person_cli(tmp_path, [tmp_path / "in.mp4", tmp_path / "m.mp4", tmp_path / "b.mp4", "--model", "anime",
                                              "--profile", "gpu"], model_url=url, model_dir=models)
    assert code == person.EXIT_MODEL_ERROR == 5 and "Traceback" not in output
    line = output.strip().splitlines()[-1]
    assert line.startswith("model error: ") and ("sha256 mismatch: " if problem == "wrong_bytes" else "download failed: ") in line
    assert not _japanese(_without_os_text(line))   # 説明は GUI の翻訳に任せ、対象と元の例外の文だけ (F6)
    assert not list(models.glob("*"))   # 壊れたモデルも、書きかけの .part も残さない


def test_download_failures_are_all_model_errors(tmp_path):
    """404・途中で切れた・合わない中身は、どれも ModelError (RuntimeError の一種)。書きかけのファイルは残さない。"""
    import http.server
    import socket
    import struct
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/404":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", "1000" if self.path == "/reset" else "11")
            self.end_headers()
            self.wfile.write(b"wrong bytes" if self.path == "/wrong" else b"partial")
            self.wfile.flush()
            if self.path == "/reset":   # 途中で RST を返して切る
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                self.connection.close()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/404", "/wrong", "/reset"):
            target = tmp_path / "cache" / "model.onnx"
            with pytest.raises(person.ModelError) as caught:
                person.download_model(f"http://127.0.0.1:{server.server_address[1]}{path}", target, "0" * 64)
            assert isinstance(caught.value, RuntimeError)
            assert not target.exists() and not list(target.parent.glob("*.part")), path
        # つながらない (閉じたポート) もそう
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
        sock.close()
        with pytest.raises(person.ModelError):
            person.download_model(f"http://127.0.0.1:{closed_port}/x", tmp_path / "cache" / "model.onnx", "0" * 64)
    finally:
        server.shutdown()
        server.server_close()


def test_pipe_output_is_switched_to_utf8_in_one_shared_place(monkeypatch):
    import io

    import convert
    from mvcodec import resources

    assert convert.use_utf8_for_pipes is resources.use_utf8_for_pipes   # convert.py は共通の場所の関数を使う

    class Tty(io.TextIOWrapper):
        def isatty(self):
            return True

    pipe_out = io.TextIOWrapper(io.BytesIO(), encoding="cp932")
    pipe_err = io.TextIOWrapper(io.BytesIO(), encoding="cp932")
    monkeypatch.setattr(sys, "stdout", pipe_out)
    monkeypatch.setattr(sys, "stderr", pipe_err)
    resources.use_utf8_for_pipes()
    assert pipe_out.encoding == pipe_err.encoding == "utf-8" and pipe_out.line_buffering
    # 端末はそのまま。標準出力が無い (windowed EXE) ときも落ちない
    terminal = Tty(io.BytesIO(), encoding="cp932")
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(sys, "stderr", None)
    resources.use_utf8_for_pipes()
    assert terminal.encoding == "cp932"


def test_gui_stops_at_once_when_the_input_or_the_model_is_the_problem(tmp_path, monkeypatch):
    """終了コード 4 (入力の動画) は軽い段階にしても直らない: 1 段だけ試して理由を知らせる。5 (モデルを取得できない) は、アニメ向けなら
    別のモデルを使う photo の段階へ進み、photo でもだめなら理由を知らせる。3 とそれ以外の異常は今までどおり次の段階へ。"""
    import queue

    import video_player_gui as gui
    from gui_i18n import Translator
    from mvcodec import resources

    gib = 1 << 30
    monkeypatch.setattr(resources, "system_memory", lambda: (32 * gib, 16 * gib))
    monkeypatch.setattr(resources, "gpu_memory", lambda: (16 * gib, 14 * gib))

    class Headless:
        def __init__(self, codes, output):
            self.tr = Translator("ja")
            self.messages = queue.Queue()
            self.codes = list(codes)
            self.output = output
            self.profiles = []
            self.argvs = []

        def _person_process(self, argv):
            self.argvs.append(argv)
            self.profiles.append(argv[argv.index("--profile") + 1])
            return self.codes.pop(0), self.output

    def split(app, model="anime"):
        return gui.PackBuilderApp._split_person(app, tmp_path / "in.mp4", tmp_path / "work", 80, 45, 10, None, "ffmpeg", model)

    def texts(app):
        return [str(app.messages.get()) for _ in range(app.messages.qsize())]

    # 入力の動画を読めない (4): 次の段階を試さない
    line = "input error: could not read the video: in.mp4"
    app = Headless([person.EXIT_INPUT_ERROR, 0, 0, 0], "profile full\n" + line)
    with pytest.raises(RuntimeError) as caught:
        split(app)
    assert app.profiles == ["full"]
    assert str(caught.value) == app.tr("build.person_input_error", detail=line.split(": ", 1)[1])
    assert not [text for text in texts(app) if "やり直します" in text]   # 「軽い設定でやり直します」は出さない
    # アニメ向けのモデルを取得できない (5): gpu・cpu を飛ばして photo の段階へ進む (photo は別のモデル。v6.28.0 と同じく作れる)
    line = "model error: download failed: https://example.invalid/isnetis.onnx (boom)"
    detail = line.split(": ", 1)[1]
    app = Headless([person.EXIT_MODEL_ERROR, 0], "profile full\n" + line)
    mask, background = split(app)
    assert app.profiles == ["full", "photo"] and mask.name == "mask.mp4"
    assert app.argvs[1][app.argvs[1].index("--profile-reason") + 1] == "retry_model"   # 統計に、なぜ photo になったかを残す
    shown = texts(app)
    assert app.tr("build.person_retry_model", detail=detail) in shown   # 知らせる (訳した文 + 元の知らせ)
    assert not [text for text in shown if "メモリが足りずに" in text]
    # photo の段階でも 5 (写真向けの重みを取得できない) なら、今までどおり理由を知らせて止まる
    app = Headless([person.EXIT_MODEL_ERROR, person.EXIT_MODEL_ERROR, 0], "profile full\n" + line)
    with pytest.raises(RuntimeError) as caught:
        split(app)
    assert app.profiles == ["full", "photo"] and str(caught.value) == app.tr("build.person_model_error", detail=detail)
    # 切り抜きのモデルが photo のときは、ほかの段階が無いのでそのまま止まる
    app = Headless([person.EXIT_MODEL_ERROR, 0], "profile photo\n" + line)
    with pytest.raises(RuntimeError):
        split(app, "photo")
    assert app.profiles == ["photo"]
    # 3 (メモリ不足)・落ちた (-1073741819)・ほかの 0 でないコード (1) は、今までどおり次の段階へ進む
    app = Headless([person.EXIT_OUT_OF_MEMORY, -1073741819, 1, 0], "something happened")
    split(app)
    assert app.profiles == ["full", "gpu", "cpu", "photo"]
    app = Headless([1, 0], "Traceback (most recent call last): ...")
    split(app)
    assert app.profiles == ["full", "gpu"]
    # 4 の知らせの後ろに別の行 (警告など) が続いても、知らせの行を理由にする
    app = Headless([person.EXIT_INPUT_ERROR], "input error: could not read the video: in.mp4\nW: late warning")
    with pytest.raises(RuntimeError) as caught:
        split(app)
    assert "could not read the video: in.mp4" in str(caught.value) and "late warning" not in str(caught.value)
    # GUI が測った空きメモリを子プロセスに渡す (子で測り直さない。測れなかったときは -1)
    app = Headless([0], "")
    split(app)
    argv = app.argvs[0]
    assert argv[argv.index("--free-ram") + 1] == str(16 * gib) and argv[argv.index("--free-vram") + 1] == str(14 * gib)
    monkeypatch.setattr(resources, "gpu_memory", lambda: None)
    app = Headless([0], "")
    split(app)
    assert app.argvs[0][app.argvs[0].index("--free-vram") + 1] == "-1"


# ---- BiRefNet (目立つ物の切り抜き) をその場で外す ----

class _Photo:
    """写真向けのモデルの偽物 (呼ばれた回数を数える)。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, rgb):
        self.calls += 1
        return np.full(rgb.shape[:2], 0.8, np.float32)


class _BiRefNetLog:
    """onnxruntime の偽物。ISNet (何も見つけない) と BiRefNet (全面を「目立つ」とする) の偽のセッションを作り分ける。
    BiRefNet の run は、失敗させる番号 (fail_at、試しの推論が 1 回目) で例外を投げ、sleep 秒止まる (試しの推論は止まらない)。"""

    def __init__(self, fail_at=None, error="[ONNXRuntimeError] : 1 : FAIL : DmlExecutionProvider 887A0005", sleep=0.0):
        import weakref

        self.fail_at, self.error, self.sleep = fail_at, error, sleep
        self.runs = 0
        self.sessions = []
        self.weakref = weakref
        self.module = types.SimpleNamespace(
            InferenceSession=self.create, SessionOptions=lambda: types.SimpleNamespace(),
            get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])

    def create(self, path, options=None, providers=None):
        if Path(path).name == person.SALIENT_MODEL_FILE:
            session = _BiRefNetSession(self)
            self.sessions.append(self.weakref.ref(session))
            return session
        return _IsnetSession()

    def released(self):
        import gc

        gc.collect()
        return all(ref() is None for ref in self.sessions)


class _IsnetSession:
    def get_inputs(self):
        return [types.SimpleNamespace(name="img")]

    def get_providers(self):
        return ["DmlExecutionProvider", "CPUExecutionProvider"]

    def run(self, _outputs, feeds):
        return [np.zeros((1, 1, person.ANIME_INPUT_SIZE, person.ANIME_INPUT_SIZE), np.float32)]   # 人物を見つけない


class _BiRefNetSession:
    def __init__(self, log):
        self.log = log

    def get_inputs(self):
        return [types.SimpleNamespace(name="input_image")]

    def get_providers(self):
        return ["DmlExecutionProvider", "CPUExecutionProvider"]

    def run(self, _outputs, feeds):
        log = self.log
        log.runs += 1
        assert feeds["input_image"].shape == (1, 3, person.SALIENT_INPUT_SIZE, person.SALIENT_INPUT_SIZE)
        if log.sleep and log.runs > 1:
            time.sleep(log.sleep)
        if log.runs == log.fail_at:
            raise RuntimeError(log.error)
        return [np.full((1, 1, person.SALIENT_INPUT_SIZE, person.SALIENT_INPUT_SIZE), 8.0, np.float32)]


@pytest.fixture
def birefnet(tmp_path, monkeypatch):
    """GPU 付きの偽の環境: モデルのファイルがあり (ダウンロードしない)、写真向けのモデルは偽物。"""
    folder = tmp_path / "models"
    folder.mkdir()
    (folder / person.ANIME_MODEL_FILE).write_bytes(b"x")
    (folder / person.SALIENT_MODEL_FILE).write_bytes(b"x")
    monkeypatch.setenv("BVP_MODEL_DIR", str(folder))
    photo = _Photo()
    monkeypatch.setattr(person, "PersonSegmenter", lambda: photo)

    def install(**options):
        log = _BiRefNetLog(**options)
        monkeypatch.setitem(sys.modules, "onnxruntime", log.module)
        return log, photo

    return install


FRAME = np.zeros((90, 160, 3), np.uint8)


def _released_when_reported(monkeypatch, log):
    """外したと知らせる時点で、BiRefNet のセッションがもう解放されているかを集める (知らせの後ではなく、外す処理の中で手放す)。"""
    seen = []
    report = person.AnimeSegmenter._report_dropped
    monkeypatch.setattr(person.AnimeSegmenter, "_report_dropped",
                        lambda self, *args: (seen.append(log.released()), report(self, *args))[1])
    return seen


def test_a_birefnet_that_runs_out_of_memory_is_dropped_and_the_frame_is_redone(birefnet, capsys, monkeypatch):
    log, photo = birefnet(fail_at=3)   # 1 回目は試しの推論、2 回目はフレーム 0、3 回目 (フレーム 1) で DirectML のメモリ不足
    released = _released_when_reported(monkeypatch, log)
    segmenter = person.AnimeSegmenter()
    assert isinstance(segmenter.fallback_prob(FRAME, 0), np.ndarray) and segmenter.salient_dropped is None
    assert isinstance(segmenter.fallback, person.SalientPersonSegmenter) and log.runs == 2
    prob = segmenter.fallback_prob(FRAME, 1)
    assert segmenter.salient_dropped == (1, "oom") and log.runs == 3
    assert segmenter.fallback is photo   # 写真向けのモデルだけになる
    assert np.allclose(prob, 0.8)        # そのフレームは写真向けのモデルでやり直した
    before = photo.calls
    for index in range(2, 6):
        assert np.allclose(segmenter.fallback_prob(FRAME, index), 0.8)
    assert log.runs == 3 and photo.calls == before + 4   # 以後は BiRefNet を呼ばない
    # 1 行で知らせる: フレームの番号と理由
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("salient dropped")]
    assert len(lines) == 1 and lines[0].startswith("salient dropped at frame 1: oom") and "887A0005" in lines[0]
    # BiRefNet のセッションを手放した (gc の後で弱い参照が切れている。知らせる時点でもう手放している)
    assert len(log.sessions) == 1 and log.released() and released == [True]


def test_the_trial_inference_catches_the_out_of_memory_at_creation(birefnet, capsys, monkeypatch):
    log, photo = birefnet(fail_at=1)   # 試しの推論で失敗
    released = _released_when_reported(monkeypatch, log)
    segmenter = person.AnimeSegmenter()
    assert np.allclose(segmenter.fallback_prob(FRAME, 7), 0.8)
    assert segmenter.fallback is photo and segmenter.salient_dropped == (7, "oom") and log.runs == 1
    assert "salient dropped at frame 7: oom" in capsys.readouterr().out
    assert log.released() and released == [True]
    # メモリ不足以外で動かないとき (GPU が使えないなど) は、今までどおりの知らせで写真向けだけにする
    log, photo = birefnet(fail_at=1, error="something else is wrong")
    segmenter = person.AnimeSegmenter()
    assert np.allclose(segmenter.fallback_prob(FRAME, 0), 0.8) and segmenter.salient_dropped is None
    out = capsys.readouterr().out
    assert "写真向けのモデルだけを使います: something else is wrong" in out and "salient dropped" not in out
    assert log.released()
    # 試しの推論が通れば、そのまま使う
    log, photo = birefnet()
    segmenter = person.AnimeSegmenter()
    segmenter.fallback_prob(FRAME, 0)
    assert isinstance(segmenter.fallback, person.SalientPersonSegmenter) and log.runs == 2


def test_other_runtime_errors_of_the_birefnet_drop_it_and_the_frame_is_redone(birefnet, capsys):
    """メモリ不足でなくても (DirectML の DXGI_ERROR_DEVICE_HUNG など)、補助の BiRefNet だけの失敗なので外して続ける。"""
    log, photo = birefnet(fail_at=2, error="DXGI_ERROR_DEVICE_HUNG")
    segmenter = person.AnimeSegmenter()
    prob = segmenter.fallback_prob(FRAME, 0)
    assert segmenter.salient_dropped == (0, "error") and segmenter.fallback is photo and np.allclose(prob, 0.8)
    assert segmenter.report()["salient_dropped"] == {"frame": 0, "reason": "error"}
    assert segmenter.report()["fallback"]["why"] == "dropped_error"
    out = capsys.readouterr().out
    assert "salient dropped at frame 0: error" in out and "DXGI_ERROR_DEVICE_HUNG" in out
    assert log.released()


def test_an_error_of_the_photo_model_itself_is_still_raised(birefnet, monkeypatch):
    """BiRefNet が動いても、写真向けのモデル (DeepLab) 自身の例外は、メモリ不足以外は上へ伝える (外して続けない)。"""
    log, photo = birefnet()

    def broken(self, rgb):
        raise ValueError("deeplab broke")

    monkeypatch.setattr(type(photo), "__call__", broken)
    segmenter = person.AnimeSegmenter()
    with pytest.raises(ValueError, match="deeplab broke"):
        segmenter.fallback_prob(FRAME, 0)
    assert segmenter.salient_dropped is None and isinstance(segmenter.fallback, person.SalientPersonSegmenter)


def test_a_birefnet_that_slows_down_is_dropped_within_six_frames(birefnet, capsys):
    """VRAM が足りずに共有メモリへ退避すると、例外にならず 1 枚が 10 倍以上遅くなる。0.7 秒かかる偽のセッションは 6 回目までに外れる。"""
    log, photo = birefnet(sleep=0.7)
    segmenter = person.AnimeSegmenter()
    for index in range(6):
        prob = segmenter.fallback_prob(FRAME, index)
        assert prob is not None
        if segmenter.salient_dropped:
            break
    assert segmenter.salient_dropped == (4, "slow")   # 試しの推論を除く 5 回の中央値が 600 ms を超えた (5 枚目)
    assert segmenter.fallback is photo
    runs = log.runs
    assert runs == 6   # 試しの推論 + 5 枚。そのフレームの結果はそのまま使う (やり直さない)
    segmenter.fallback_prob(FRAME, 5)
    assert log.runs == runs
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("salient dropped")]
    assert len(lines) == 1 and lines[0].startswith("salient dropped at frame 4: slow")
    assert "> 600 ms" in lines[0]   # しきい値を必ず知らせる
    assert log.released()


def test_the_slow_check_uses_the_median_of_the_last_five_runs(tmp_path, monkeypatch):
    model = tmp_path / "salient.onnx"
    model.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "onnxruntime",
                        types.SimpleNamespace(InferenceSession=_SalientSession, SessionOptions=lambda: types.SimpleNamespace()))
    salient = person.SalientPersonSegmenter(lambda image: image, model_path=model)
    assert salient.slow_ms == person.SALIENT_SLOW_MS == 600
    salient.times.extend([90, 95, 1000, 92])
    assert not salient.is_slow() and salient.median_ms() == 0     # 5 回に満たない間は決めない
    salient.times.append(91)
    assert not salient.is_slow() and salient.median_ms() == 92    # 1 回だけ飛び抜けて遅い (読み込みの直後など) だけでは外さない
    salient.times.extend([700, 800])
    assert list(salient.times) == [1000, 92, 91, 700, 800] and salient.is_slow() and salient.median_ms() == 700   # 直近 5 回
    # しきい値は引数で変えられる (遅いが正常な GPU では大きくする)
    relaxed = person.SalientPersonSegmenter(lambda image: image, model_path=model, slow_ms=1000)
    relaxed.times.extend([700] * 5)
    assert not relaxed.is_slow()
    relaxed.times.extend([1100] * 3)
    assert relaxed.is_slow()


def test_the_salient_slow_limit_comes_from_the_command_line(monkeypatch):
    seen = {}

    def make_segmenter(model, profile, download_progress=None, salient_slow_ms=None):
        seen[(model, profile)] = salient_slow_ms
        return None

    monkeypatch.setattr(person, "make_segmenter", make_segmenter)
    monkeypatch.setattr(person, "make_person_videos", lambda *args, **kwargs: 1)
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "full"]) == 0
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "gpu", "--salient-slow-ms", "1500"]) == 0
    assert seen == {("anime", "full"): 600.0, ("anime", "gpu"): 1500.0}


@pytest.mark.parametrize("error, reason", [("[ONNXRuntimeError] : 1 : FAIL : DmlExecutionProvider 887A0005", "oom"),
                                           ("DXGI_ERROR_DEVICE_HUNG", "error")])
def test_a_full_cutout_continues_with_the_photo_model_after_the_birefnet_is_dropped(birefnet, tmp_path, capsys, monkeypatch,
                                                                                    error, reason):
    """BiRefNet が途中で 'DmlExecutionProvider 887A0005' (メモリ不足) や DXGI_ERROR_DEVICE_HUNG を投げても、動画は最後まで作られて
    終了コード 0。以後は写真向けのモデルを使う。"""
    import shutil
    import subprocess

    # この偽のモデルは全面を人物と返すので、予備の結果の面積の上限の検査は外す (検査は別のテストで確かめる)
    monkeypatch.setattr(person, "FALLBACK_MAX_AREA", 1.0)

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    video = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=1.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    log, photo = birefnet(fail_at=3, error=error)
    code = person.main([str(video), str(tmp_path / "m.mp4"), str(tmp_path / "b.mp4"), "--fps", "10", "--model", "anime",
                        "--profile", "full"])
    lines = capsys.readouterr().out.splitlines()
    assert code == 0 and lines[-1].startswith("done 15 frames")
    # アニメ向けが何も見つけないので 15 フレームとも予備。フレーム 1 で外れ、以後は写真向けだけ (BiRefNet の run は 3 回で止まる)
    assert f"salient dropped at frame 1: {reason}" in " ".join(lines) and log.runs == 3
    assert "sources anime=0 filled=0 matched=0 fallback=15 rejected=0 flagged=15" in lines
    assert photo.calls == 15 and log.released()
    stats_line = [line for line in lines if line.startswith("stats {")][0]
    assert json.loads(stats_line[len("stats "):])["salient_dropped"] == {"frame": 1, "reason": reason}
    masks = list(person.read_frames(tmp_path / "m.mp4", 10, None, "ffmpeg", 640))
    assert len(masks) == 15 and all(frame[..., 0].mean() > 100 for frame in masks)


# ---------------------------------------------------------------------------
# 統計 (stats): 出所ごとのフレーム数・空のフレーム・場面の切り替わり・モデルごとの推論の ms・工程ごとの ms・最大のメモリを、
# 1 行の JSON にして出し、GUI が 1〜2 行のまとめとログにする
# ---------------------------------------------------------------------------

def _write_video(path, frames, fps=10):
    """frames (高さ × 幅 × 3 の uint8 の並び) を、ほぼ劣化しない mp4 にする。"""
    import subprocess

    height, width = frames[0].shape[:2]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
                    "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", str(path)],
                   input=b"".join(frame.tobytes() for frame in frames), check=True)


class _VideoScripted:
    """映像を読んだ順に決めた確からしさを返す偽のアニメ向けの切り抜き。fallback_prob は渡されたフレームの番号で引く。
    report は、本物 (AnimeSegmenter.report) の形の決まった内容を返す。"""

    def __init__(self, primary, fallback):
        self.primary_probs, self.fallback_probs = primary, fallback
        self.reads = 0
        self.fallback_calls = []

    def primary(self, rgb):
        prob = self.primary_probs[self.reads]
        self.reads += 1
        return prob

    def fallback_prob(self, rgb, frame=None):
        self.fallback_calls.append(frame)
        return self.fallback_probs[frame]

    def report(self):
        return {"models": {"anime": {"providers": ["FakeProvider"], "requested": ["FakeProvider"], "ms": None}},
                "fallback": {"model": "photo", "why": "salient_off", "frames": {"salient": 0, "photo": len(self.fallback_calls)}},
                "salient_dropped": {"frame": 7, "reason": "slow"}}


@pytest.fixture
def person_bench():
    """benchmark/person_bench.py (マスクの動画から空のフレームや場面の切り替わりを数える)。"""
    sys.path.insert(0, str(ROOT / "benchmark"))
    try:
        import person_bench as module
    finally:
        sys.path.pop(0)
    return module


def test_stats_count_the_sources_reasons_empty_frames_and_scene_cuts_like_the_benchmark(tmp_path, person_bench):
    """決めた出所のとおりに切り抜く偽のモデルで、stats の数が決めた数と一致する。空のフレームの数と場面の切り替わりの数は、
    書き出したマスクの動画から person_bench が数えた値と同じ。"""
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    # 場面は 4 つ (0〜7 / 8〜14 / 15〜23 / 24〜26) で、場面の切り替わりは 3 つ
    frames = _scene_images([8, 7, 9, 3])
    video = tmp_path / "in.mp4"
    _write_video(video, frames)
    lost = {3, 4, 5, 8, 12, 23, 24, 25, 26}   # アニメ向けが人物を見つけないフレーム
    primary = [np.zeros(SHAPE, np.float32) if t in lost else _rect(30, 20 + t, 36, 20) for t in range(27)]
    # 予備は人物のあたりに塊をひとつ返す (合わせた結果は、近くの人物の外接箱の中に残る)。最後の場面 (24〜26) は全部見つからず、
    # 前後に頼れないので、予備が空のマスクを返す
    fallback = [np.zeros(SHAPE, np.float32) if t >= 24 else _cone((48, 30 + t), 25) for t in range(27)]
    segmenter = _VideoScripted(primary, fallback)

    stats = {}
    mask, background = tmp_path / "mask.mp4", tmp_path / "background.mp4"
    assert person.make_person_videos(video, mask, background, 10, segmenter=segmenter, stats=stats) == 27

    assert stats["frames"] == 27
    # 見つけたのは 18、前後で埋めたのは 12 の 1 枚、予備の結果を合わせたのは 3・4・5・8・23、そのままの予備は 24〜26
    assert stats["sources"] == {"anime": 18, "filled": 1, "matched": 5, "fallback": 3, "rejected": 0}
    assert sum(stats["sources"].values()) == 27 and stats["flagged"] == 9
    assert segmenter.fallback_calls == [3, 4, 5, 8, 23, 24, 25, 26]
    # 予備に頼った理由: 見つけない連なりが 3 枚 (3〜5 と 24〜26) で長い。8 と 23 は場面の端で、前後のどちらかに頼れない
    assert stats["fallback"]["reasons"] == {"long_run": 6, "no_neighbour": 2, "no_fallback": 0}
    assert stats["fallback"]["model"] == "photo" and stats["fallback"]["frames"] == {"salient": 0, "photo": 8}   # segmenter.report の内容
    assert stats["models"]["anime"]["providers"] == ["FakeProvider"] and stats["salient_dropped"] == {"frame": 7, "reason": "slow"}

    bench = person_bench.mask_statistics(mask, video, 10, "ffmpeg")
    assert bench["frames"] == 27
    assert stats["empty_frames"] == bench["empty_frames_area"] == 3
    assert stats["scene_cuts"] == bench["scene_cuts"] == 3

    # 工程ごとの ms: 呼び出し 1 回ごとの記録の個数と、小さい順の関係。segment は読んだ枚数 (動画の終わりで確定する分は flush_ms)
    stages = stats["stages_ms"]
    assert set(stages) == {"read", "segment", "split", "write"}
    for name, stage in stages.items():
        assert stage["n"] == 27, name
        assert 0 < stage["median"] <= stage["p95"] <= stage["max"] and stage["mean"] > 0 and stage["total_s"] > 0, name
    assert stages["segment"]["flush_ms"] >= 0 and stats["run_s"] >= stages["split"]["total_s"]
    json.dumps(stats)   # そのまま JSON にできる


def test_stats_of_a_photo_only_run_and_of_a_run_that_stops_halfway(tmp_path):
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が必要")
    video = tmp_path / "in.mp4"
    _write_video(video, _scene_images([12]))
    bright = lambda rgb: (rgb.mean(axis=2) > 100).astype(np.float32)
    stats = {}
    assert person.make_person_videos(video, tmp_path / "m.mp4", tmp_path / "b.mp4", 10, segmenter=bright, stats=stats) == 12
    # 写真向けだけ (TemporalGuard を通さない): 出所は photo だけで、予備・見つけなかった数はない
    assert stats["sources"] == {"photo": 12} and "flagged" not in stats and "fallback" not in stats
    assert stats["models"] == {} and stats["salient_dropped"] is None
    assert stats["stages_ms"]["segment"]["n"] == 12 and "flush_ms" not in stats["stages_ms"]["segment"]

    # 途中でメモリ不足になっても、そこまでの統計が入る (書き出した 5 枚、読んだのは 6 枚)
    calls = []

    def breaks(rgb):
        calls.append(1)
        if len(calls) == 6:
            raise MemoryError("not enough")
        return bright(rgb)

    stats = {}
    with pytest.raises(MemoryError):
        person.make_person_videos(video, tmp_path / "m2.mp4", tmp_path / "b2.mp4", 10, segmenter=breaks, stats=stats)
    assert stats["frames"] == 5 and stats["stages_ms"]["read"]["n"] == 6 and stats["stages_ms"]["write"]["n"] == 5


def test_summarize_ms_gives_median_p95_mean_and_max():
    values = list(range(1, 101))
    assert person.summarize_ms(values) == {"n": 100, "mean": 50.5, "median": 50.5, "p95": 95.05, "max": 100.0}
    assert person.summarize_ms([3.0]) == {"n": 1, "mean": 3.0, "median": 3.0, "p95": 3.0, "max": 3.0}
    assert person.summarize_ms([]) is None


def test_anime_segmenter_reports_models_providers_times_and_the_drop(birefnet):
    log, photo = birefnet(fail_at=4)   # 1 回目は試しの推論、2・3 回目はフレーム 0・1、4 回目 (フレーム 2) でメモリ不足
    segmenter = person.AnimeSegmenter()
    for _ in range(3):
        segmenter.primary(FRAME)
    report = segmenter.report()
    both = ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert report["models"]["anime"]["providers"] == both and report["models"]["anime"]["requested"] == both
    assert report["models"]["anime"]["ms"]["n"] == 3
    assert report["fallback"]["model"] == "unused" and report["fallback"]["load_s"] is None   # 予備はまだ作っていない
    assert report["salient_dropped"] is None
    segmenter.fallback_prob(FRAME, 0)
    segmenter.fallback_prob(FRAME, 1)
    report = segmenter.report()
    assert report["models"]["salient"]["providers"] == both and report["models"]["salient"]["ms"]["n"] == 2
    assert report["models"]["salient"]["no_blob_frames"] >= 0
    assert report["fallback"].pop("load_s") >= 0   # 予備を作る (試しの推論も含む) のにかかった秒数
    assert report["fallback"] == {"model": "salient", "why": "salient", "frames": {"salient": 2, "photo": 0}}
    segmenter.fallback_prob(FRAME, 2)   # BiRefNet が動かなくなる: 外して、このフレームを写真向けでやり直す
    report = segmenter.report()
    assert report["fallback"].pop("load_s") >= 0
    assert report["fallback"] == {"model": "photo", "why": "dropped_oom", "frames": {"salient": 2, "photo": 1}}
    assert report["salient_dropped"] == {"frame": 2, "reason": "oom"}
    assert report["models"]["salient"]["ms"]["n"] == 2   # 外した後も、使った分の記録は残る
    # 予備の選び方の理由: GPU が無い・この段階では使わない
    log, photo = birefnet()
    cpu = person.AnimeSegmenter(providers=["CPUExecutionProvider"])
    cpu.fallback_prob(FRAME, 0)
    assert {k: v for k, v in cpu.report()["fallback"].items() if k != "load_s"} == {
        "model": "photo", "why": "no_gpu", "frames": {"salient": 0, "photo": 1}}
    gpu = person.AnimeSegmenter(salient=False)
    gpu.fallback_prob(FRAME, 0)
    assert gpu.report()["fallback"]["why"] == "salient_off" and "salient" not in gpu.report()["models"]


def test_salient_cutout_counts_its_inferences_and_the_frames_without_an_approved_blob(tmp_path, monkeypatch):
    module = types.SimpleNamespace(InferenceSession=_SalientSession, SessionOptions=lambda: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    model = tmp_path / "salient.onnx"
    model.write_bytes(b"x")
    photo = lambda image: (image[..., 0] > 128).astype(np.float32) * 0.8   # 写真向けのモデルは赤だけを人物と見る
    segmenter = person.SalientPersonSegmenter(photo, model_path=model)
    with_person = np.zeros((90, 160, 3), np.uint8)
    with_person[20:70, 20:50, 0] = 255
    streak = np.zeros((90, 160, 3), np.uint8)
    streak[20:70, 100:150, 2] = 255      # 光の筋だけ (写真向けは人物と見ない)
    segmenter(with_person)
    assert segmenter.no_blob_frames == 0 and len(segmenter.inference_ms) == 1
    segmenter(streak)
    assert segmenter.no_blob_frames == 1 and len(segmenter.inference_ms) == 2 and all(ms >= 0 for ms in segmenter.inference_ms)


def test_photo_segmenter_reports_its_inference_times():
    photo = object.__new__(person.PersonSegmenter)   # 本物のモデルは作らない
    photo.device, photo.inference_ms = "cpu", [10.0, 20.0, 30.0]
    assert photo.report() == {"models": {"photo": {"providers": ["torch:cpu"], "ms": person.summarize_ms([10.0, 20.0, 30.0])}}}


def test_skipped_profiles_explain_why_a_heavier_one_was_not_taken():
    gib = 1 << 30
    skipped = person.skipped_profiles
    assert skipped("anime", "gpu", 20 * gib, 9 * gib) == {"full": "VRAM 9.0 < 12 GiB"}
    assert skipped("anime", "cpu", 6 * gib, gib // 2) == {"full": "RAM 6.0 < 12 GiB, VRAM 0.5 < 12 GiB", "gpu": "VRAM 0.5 < 1 GiB"}
    assert skipped("anime", "photo", 6 * gib, 20 * gib)["full"] == "RAM 6.0 < 12 GiB"
    assert skipped("anime", "gpu", 20 * gib, 20 * gib) == {"full": "not chosen"}   # 足りていたのに選ばなかった (指定・やり直し)
    assert skipped("anime", "gpu", None, None) == {"full": "not chosen"}           # 空きが分からなければ足りないとはしない
    assert skipped("anime", "full", 20 * gib, 20 * gib) == {} and skipped("photo", "photo", 0, 0) == {}


def test_process_memory_peaks_matches_psutil_on_windows():
    from mvcodec import resources

    first = resources.process_memory_peaks()
    if sys.platform != "win32":
        assert first is None
        return
    block = np.ones(60_000_000, np.uint8)   # 60 MB を使う
    block += 1
    second = resources.process_memory_peaks()
    assert second["peak_working_set"] >= second["working_set"] >= 60_000_000
    assert second["peak_commit"] >= second["commit"] >= 60_000_000
    assert second["peak_working_set"] >= first["peak_working_set"] and second["peak_commit"] >= first["peak_commit"]
    del block
    third = resources.process_memory_peaks()
    assert third["peak_working_set"] >= second["peak_working_set"]   # 最大は減らない
    try:
        import psutil
    except ImportError:
        return
    info = psutil.Process().memory_info()
    assert abs(third["peak_working_set"] - info.peak_wset) < 0.1 * info.peak_wset
    assert abs(third["peak_commit"] - info.peak_pagefile) < 0.1 * info.peak_pagefile


def _fake_pipeline(monkeypatch, stats_for_run=None, error=None):
    """person.main の下の、モデルと動画の処理を偽物にする (空きメモリは RAM 20 GiB・VRAM 9 GiB)。"""
    from mvcodec import resources

    monkeypatch.setattr(resources, "system_memory", lambda: (32 << 30, 20 << 30))
    monkeypatch.setattr(resources, "gpu_memory", lambda: (16 << 30, 9 << 30))
    monkeypatch.setattr(person, "make_segmenter", lambda *args, **kwargs: "segmenter")

    def make(source, mask, background, fps, duration, ffmpeg, segmenter=None, progress=None, long_side=None, stats=None):
        stats.update(stats_for_run or {"frames": 5, "sources": {"anime": 4, "filled": 1, "matched": 0, "fallback": 0}, "flagged": 1})
        if error:
            raise error
        return stats["frames"]

    monkeypatch.setattr(person, "make_person_videos", make)


def test_main_prints_one_stats_line_before_done_and_writes_the_file(tmp_path, monkeypatch, capsys):
    _fake_pipeline(monkeypatch)
    folder = tmp_path / "logs"
    folder.mkdir()
    argv = ["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "gpu", "--profile-reason", "retry_memory"]
    assert person.main(argv + ["--stats", str(folder)]) == 0
    lines = capsys.readouterr().out.splitlines()
    stats_lines = [line for line in lines if line.startswith("stats ")]
    assert len(stats_lines) == 1 and lines[-2] == stats_lines[0] and lines[-1].startswith("done 5 frames")
    stats = json.loads(stats_lines[0][len("stats "):])
    # 選んだモデルと段階とその理由、始めたときの空きメモリ
    assert (stats["model"], stats["profile"], stats["profile_reason"]) == ("anime", "gpu", "retry_memory")
    assert stats["profile_skipped"] == {"full": "VRAM 9.0 < 12 GiB"}
    assert stats["free_at_start"] == {"ram_mb": 20480, "vram_mb": 9216}
    assert stats["error"] is None and stats["wall_s"] >= 0 and stats["load_s"] >= 0
    if sys.platform == "win32":
        memory = stats["memory"]
        assert memory["peak_working_set_mb"] > 0 and memory["peak_commit_mb"] >= memory["peak_working_set_mb"] * 0.5
    else:
        assert stats["memory"] is None
    # フォルダを渡すと、その中に person-stats-<日時>.json を作る。中身は 1 行と同じ
    files = list(folder.glob("person-stats-*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text(encoding="utf-8")) == stats
    # ファイルを渡せば、そのファイル
    target = tmp_path / "deep" / "stats.json"
    assert person.main(argv + ["--stats", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["profile"] == "gpu"
    # 指定が無ければ段階は空きメモリから選び、理由は auto (VRAM 9 GiB では最高の段階に足りない)
    capsys.readouterr()
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime"]) == 0
    out = capsys.readouterr().out
    stats = json.loads(next(line for line in out.splitlines() if line.startswith("stats "))[len("stats "):])
    assert (stats["profile"], stats["profile_reason"]) == ("gpu", "auto")
    assert "sources anime=4 filled=1 matched=0 fallback=0 flagged=1" in out
    # 理由を渡せば、段階を空きメモリから選んでも、渡された理由を残す
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile-reason", "wizard"]) == 0
    out = capsys.readouterr().out
    assert json.loads(next(line for line in out.splitlines() if line.startswith("stats "))[len("stats "):])["profile_reason"] == "wizard"


def test_main_prints_the_stats_before_the_failure_line(tmp_path, monkeypatch, capsys):
    """失敗でも stats を出す (理由と、そこまでの統計)。知らせの行 (input error: など) は今までどおり最後。"""
    _fake_pipeline(monkeypatch, error=person.InputError("動画を読めませんでした: in.mp4"))
    folder = tmp_path / "logs"
    folder.mkdir()
    argv = ["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "gpu", "--stats", str(folder)]
    assert person.main(argv) == person.EXIT_INPUT_ERROR
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].startswith("input error: ") and lines[-2].startswith("stats ")
    stats = json.loads(lines[-2][len("stats "):])
    assert stats["error"] == {"kind": "input", "message": "動画を読めませんでした: in.mp4"} and stats["frames"] == 5
    assert list(folder.glob("person-stats-*.json"))
    for error, kind, code in ((MemoryError("out of memory"), "out_of_memory", person.EXIT_OUT_OF_MEMORY),
                              (person.ModelError("download failed"), "model", person.EXIT_MODEL_ERROR)):
        _fake_pipeline(monkeypatch, error=error)
        assert person.main(argv) == code
        lines = capsys.readouterr().out.splitlines()
        assert json.loads(lines[-2][len("stats "):])["error"]["kind"] == kind and not lines[-1].startswith("stats ")
    # 思いがけない失敗は、stats を出してから、そのまま投げ直す
    _fake_pipeline(monkeypatch, error=ValueError("bug"))
    with pytest.raises(ValueError):
        person.main(argv)
    stats_line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("stats "))
    assert json.loads(stats_line[len("stats "):])["error"] == {"kind": "crash", "message": "ValueError: bug"}


def test_the_stats_file_is_written_even_when_there_is_no_standard_output(tmp_path, monkeypatch):
    """windowed の EXE では標準出力が無い (捨てられる) ことがある。統計はファイルにも書く。"""
    _fake_pipeline(monkeypatch)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    target = tmp_path / "stats.json"
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "gpu", "--stats", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["sources"]["anime"] == 4


# ---- GUI: 短いまとめ・注意・ログのファイル ----

def _stats(**changes):
    stats = {"version": 1, "model": "anime", "profile": "gpu", "frames": 100, "wall_s": 42.4, "empty_frames": 3,
             "sources": {"anime": 80, "filled": 6, "matched": 14, "fallback": 0}, "flagged": 20, "salient_dropped": None,
             "models": {"anime": {"providers": ["DmlExecutionProvider", "CPUExecutionProvider"], "ms": None}}}
    stats.update(changes)
    return stats


class _GuiApp:
    """GUI の画面なしで _person_process・_split_person を動かす入れ物。"""

    def __init__(self, language="ja"):
        import queue

        from gui_i18n import Translator

        self.tr = Translator(language)
        self.messages = queue.Queue()

    def lines(self):
        return [str(self.messages.get()) for _ in range(self.messages.qsize())]


def test_gui_summary_is_one_or_two_lines_and_warns_only_when_a_condition_holds():
    import video_player_gui as gui
    from gui_i18n import Translator

    tr = Translator("ja")
    log = Path("C:/Users/someone/AppData/Local/BlockVideoPlayer/logs/person-20261027-120000.log")
    lines = gui.person_summary_lines(tr, _stats(), log)
    # まとめは 2 行 (結果・ログの場所)。注意は無い
    assert len(lines) == 2 and not any("注意" in line for line in lines)
    assert "100 フレーム" in lines[0] and "42 秒" in lines[0] and "DmlExecutionProvider" in lines[0] and "GPU" in lines[0]
    assert "80" in lines[0] and "14" in lines[0] and "3" in lines[0]    # そのまま・予備・人物なし
    assert str(log) in lines[1] and "ユーザー名" in lines[1]            # 共有するときの注意を添える
    assert len(gui.person_summary_lines(tr, _stats(), None)) == 1       # ログを作れなかったとき

    def warns(**changes):
        found = [line for line in gui.person_summary_lines(tr, _stats(**changes), log) if "注意" in line]
        assert len(found) <= 1   # 条件がいくつ当てはまっても、注意は 1 行
        return found

    # 人物が空のフレームが 10% を超えたら (10% ちょうどは警告しない)
    assert not warns(empty_frames=10)
    assert "11%" in warns(empty_frames=11)[0]
    # 予備のモデルが 50% を超えたら。前後で埋めたフレームは予備のモデルを使っていない
    assert not warns(sources={"anime": 30, "filled": 20, "matched": 30, "fallback": 20})
    assert "51%" in warns(sources={"anime": 29, "filled": 20, "matched": 31, "fallback": 20})[0]
    # BiRefNet を外した
    assert "フレーム 12" in warns(salient_dropped={"frame": 12, "reason": "oom"})[0]
    assert "メモリ不足" in warns(salient_dropped={"frame": 12, "reason": "oom"})[0]
    assert "遅く" in warns(salient_dropped={"frame": 9, "reason": "slow"})[0]
    assert "失敗" in warns(salient_dropped={"frame": 4, "reason": "error"})[0]
    # 予備の結果を検査で退けて空にしたフレームの数は、まとめの 1 行に入る (無ければ入れない。注意の条件にはしない)
    rejected = gui.person_summary_lines(tr, _stats(sources={"anime": 80, "filled": 6, "matched": 8, "fallback": 0, "rejected": 6}), log)
    assert len(rejected) == 2 and "予備を退けた 6" in rejected[0] and "退けた" not in lines[0]
    assert not warns(sources={"anime": 80, "filled": 6, "matched": 8, "fallback": 0, "rejected": 6})
    everything = warns(empty_frames=40, salient_dropped={"frame": 3, "reason": "slow"},
                       sources={"anime": 10, "filled": 0, "matched": 90, "fallback": 0})
    assert len(everything) == 1 and "40%" in everything[0] and "90%" in everything[0] and "フレーム 3" in everything[0]
    # 写真向けだけのとき
    photo = _stats(model="photo", profile="photo", sources={"photo": 100}, models={"photo": {"providers": ["torch:cpu"], "ms": None}})
    del photo["flagged"]
    lines = gui.person_summary_lines(tr, photo, log)
    assert len(lines) == 2 and "torch:cpu" in lines[0] and "100" in lines[0]


_FAKE_CHILD = '''
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    print(line, flush=True)
sys.exit(int(sys.argv[2]))
'''


def _run_fake_child(tmp_path, monkeypatch, lines, code=0, language="ja"):
    """_person_process を、決めた行を出して終わる子プロセスで動かす。(終了コード, 出力の最後の部分, 入れ物, ログのファイル)。"""
    import video_player_gui as gui

    script = tmp_path / "fake_child.py"
    script.write_text(_FAKE_CHILD, encoding="utf-8")
    output = tmp_path / "child_output.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(gui, "person_command", lambda: [sys.executable, str(script)])
    app = _GuiApp(language)
    app.person_log = gui.new_person_log()
    result = gui.PackBuilderApp._person_process(app, [str(output), str(code)])
    return (*result, app, app.person_log)


def test_gui_turns_the_stats_line_into_a_summary_and_logs_everything(tmp_path, monkeypatch):
    child = ["profile gpu", "download 1048576 4194304", "W:onnxruntime: some warning", "10 frames", "50 frames",
             "salient dropped at frame 12: oom (887A0005)", "sources anime=80 filled=6 matched=14 fallback=0 flagged=20",
             "stats " + json.dumps(_stats(empty_frames=20, salient_dropped={"frame": 12, "reason": "oom"}), separators=(",", ":")),
             "done 100 frames -> m.mp4, b.mp4"]
    code, tail, app, log = _run_fake_child(tmp_path, monkeypatch, child)
    shown = app.lines()
    assert code == 0 and "stats {" not in tail   # 失敗の知らせに使う最後の部分に、統計の JSON を入れない
    # 画面: 統計の JSON と sources の行はそのまま流さない。まとめ 2 行 + 注意 1 行
    assert not [line for line in shown if "stats {" in line or "sources anime" in line]
    assert shown[-3].startswith("  切り抜きの結果") and str(log) in shown[-2] and shown[-1].startswith("  注意")
    assert "salient dropped at frame 12: oom (887A0005)" in "\n".join(shown)   # 外したときの知らせは流れる
    assert not [line for line in shown if "W:onnxruntime" in line]
    # ログのファイル: 子プロセスの出力は全部 (流さなかった行も)、統計の JSON と終了コード
    text = log.read_text(encoding="utf-8")
    for line in child:
        assert line in text, line
    assert "fake_child.py" in text and text.rstrip().endswith("exit code 0")

    # 条件に当てはまらなければ注意は無い。失敗 (0 でない終了コード) ではまとめを出さない
    code, tail, app, log = _run_fake_child(tmp_path, monkeypatch, ["stats " + json.dumps(_stats(), separators=(",", ":"))])
    shown = app.lines()
    assert code == 0 and len(shown) == 3 and shown[0].startswith("$ ") and not [line for line in shown if "注意" in line]
    code, tail, app, log = _run_fake_child(tmp_path, monkeypatch, ["out of memory: x"], code=3)
    assert code == 3 and not [line for line in app.lines() if "切り抜きの結果" in line]
    assert log.read_text(encoding="utf-8").rstrip().endswith("exit code 3")
    # 統計の行が壊れていれば、そのまま流す (まとめは出さない)
    code, tail, app, log = _run_fake_child(tmp_path, monkeypatch, ["stats {broken"])
    assert code == 0 and "  stats {broken" in app.lines()


def test_person_logs_keep_only_the_newest_ten(tmp_path, monkeypatch):
    import os

    import video_player_gui as gui

    folder = tmp_path / "logs"
    monkeypatch.setenv("BVP_LOG_DIR", str(folder))
    assert gui.logs_dir() == folder
    folder.mkdir()
    for i in range(12):
        for name in (f"person-20260101-0000{i:02d}.log", f"person-stats-20260101-0000{i:02d}.json"):
            (folder / name).write_text("old", encoding="utf-8")
            os.utime(folder / name, (1_700_000_000 + i * 10, 1_700_000_000 + i * 10))
    (folder / "settings.txt").write_text("not a log", encoding="utf-8")
    new = gui.new_person_log()
    assert new.parent == folder and new.name.startswith("person-") and new.suffix == ".log" and new.read_text() == ""
    logs = sorted(path.name for path in folder.glob("person-*.log"))
    assert len(logs) == 10 and new.name in logs and logs[0] == "person-20260101-000003.log"   # 古い 3 つが消え、新しい 9 つ + 今回
    # 子プロセスが書く統計は、今回の 1 つが増えて 10 個になるよう 9 個残す
    assert sorted(path.name for path in folder.glob("person-stats-*.json"))[0] == "person-stats-20260101-000003.json"
    assert len(list(folder.glob("person-stats-*.json"))) == 9
    assert (folder / "settings.txt").exists()
    # 同じ秒に続けて作っても、別のファイル
    second = gui.new_person_log()
    assert second != new and second.exists() and len(list(folder.glob("person-*.log"))) == 10
    # 作れないとき (フォルダの場所がファイル) はログなしで続ける
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    monkeypatch.setenv("BVP_LOG_DIR", str(blocker / "logs"))
    assert gui.new_person_log() is None
    with gui.open_log(None) as write:
        write("ignored")   # ログなしでも落ちない


def test_logs_dir_defaults_to_local_app_data(monkeypatch, tmp_path):
    import video_player_gui as gui

    monkeypatch.delenv("BVP_LOG_DIR")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert gui.logs_dir() == tmp_path / "Local" / "BlockVideoPlayer" / "logs"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert gui.logs_dir() == tmp_path / "state" / "BlockVideoPlayer" / "logs"


def test_gui_passes_the_stats_folder_and_the_reason_and_names_the_log_when_it_fails(tmp_path, monkeypatch):
    import video_player_gui as gui
    from mvcodec import resources

    gib = 1 << 30
    monkeypatch.setattr(resources, "system_memory", lambda: (32 * gib, 16 * gib))
    monkeypatch.setattr(resources, "gpu_memory", lambda: (16 * gib, 14 * gib))

    class Headless(_GuiApp):
        def __init__(self, codes):
            super().__init__()
            self.codes, self.argvs = list(codes), []

        def _person_process(self, argv):
            self.argvs.append(argv)
            return self.codes.pop(0), "last words of the child"

    def split(app):
        return gui.PackBuilderApp._split_person(app, tmp_path / "in.mp4", tmp_path / "work", 80, 45, 10, None, "ffmpeg", "anime")

    app = Headless([person.EXIT_OUT_OF_MEMORY, -1073741819, 0])
    split(app)
    reasons = [argv[argv.index("--profile-reason") + 1] for argv in app.argvs]
    profiles = [argv[argv.index("--profile") + 1] for argv in app.argvs]
    assert profiles == ["full", "gpu", "cpu"] and reasons == ["auto", "retry_memory", "retry_crash"]
    # 統計はログのフォルダへ。やり直しても同じログ (1 回の切り抜きにつき 1 つ)
    folder = gui.logs_dir()
    assert all(argv[argv.index("--stats") + 1] == str(folder) for argv in app.argvs)
    assert len(list(folder.glob("person-*.log"))) == 1
    header = next(folder.glob("person-*.log")).read_text(encoding="utf-8")
    assert "anime" in header and str(tmp_path / "in.mp4") in header

    # 全部の段階でだめなら、失敗のダイアログ (RuntimeError の文) に、ログの場所を書く
    before = set(folder.glob("person-*.log"))
    app = Headless([person.EXIT_OUT_OF_MEMORY] * 4)
    with pytest.raises(RuntimeError) as caught:
        split(app)
    (this_time,) = set(folder.glob("person-*.log")) - before
    assert str(this_time) in str(caught.value) and "last words of the child" in str(caught.value)
    # ログを作れないときも、失敗の知らせは出る (--stats は渡さない)
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    monkeypatch.setenv("BVP_LOG_DIR", str(blocker / "logs"))
    app = Headless([person.EXIT_OUT_OF_MEMORY] * 4)
    with pytest.raises(RuntimeError) as caught:
        split(app)
    assert "last words of the child" in str(caught.value) and not any("--stats" in argv for argv in app.argvs)


# ---------------------------------------------------------------------------
# 予備の結果の検査 (面積の上限・位置)、一様な画面は場面の切り替わりにしない、背景の動画のフレームの数
# ---------------------------------------------------------------------------

def test_a_fallback_that_covers_most_of_the_frame_is_rejected_and_the_mask_is_empty():
    """アニメ向けが何も見つけない動画 (人物がいない) で、予備が画面の 9 割を「人物」にしても採らない。空にする。フレームの数・順序は変わらない。"""
    frames = _scene_images([10])
    big = _rect(0, 0, 81, 160)
    assert _fraction(big) == pytest.approx(0.9)
    segmenter = _Scripted(frames, [np.zeros(SHAPE, np.float32)] * 10, [big] * 10)
    guard, settled = _settle_all(segmenter, frames)
    assert guard.counts == {"anime": 0, "filled": 0, "matched": 0, "fallback": 0, "rejected": 10, "flagged": 10}
    assert len(settled) == 10 and all(item[0] is frame for item, frame in zip(settled, frames))   # 1:1 で順序のまま
    assert all(not prob.any() for _, prob, _ in settled)
    # 面積が上限の内側の塊 (位置を決める正常なフレームが無い) は、今までどおりそのまま
    blob = _rect(10, 10, 40, 60)
    segmenter = _Scripted(frames, [np.zeros(SHAPE, np.float32)] * 10, [blob] * 10)
    guard, settled = _settle_all(segmenter, frames)
    assert guard.counts["fallback"] == 10 and guard.counts["rejected"] == 0
    assert all(np.array_equal(prob, blob) for _, prob, _ in settled)


def test_a_fallback_far_from_the_last_normal_frame_is_rejected():
    frames = _scene_images([40])
    people = [_rect(30, 20, 36, 20) if t < 3 else np.zeros(SHAPE, np.float32) for t in range(40)]
    near_blob, far_blob = _cone((48, 30), 20), _rect(0, 130, 12, 20)    # 人物のあたり / 画面の右上 (字幕や光の筋のつもり)
    fallback = [far_blob if t % 2 == 0 else near_blob for t in range(40)]
    segmenter = _Scripted(frames, people, fallback)
    guard, settled = _settle_all(segmenter, frames)
    assert len(settled) == 40 and all(item[0] is frame for item, frame in zip(settled, frames))
    assert person.FALLBACK_REF_FRAMES == 30
    for t in range(3, 33):   # 直近の正常なフレームの箱から離れた塊は採らず、近い塊は採る (窓の中は match_to_found の範囲、窓の外はその箱)
        prob = settled[t][1]
        if t % 2 == 0:
            assert not prob.any(), t
        else:
            assert (prob >= 0.5).any() and not (prob[:25, 120:] >= 0.5).any(), t
    # 正常なフレーム (番号 2) が FALLBACK_REF_FRAMES より前になったら、位置の手がかりにしない (人物はもう別の所にいるかもしれない)
    for t in (34, 36, 38):
        assert np.array_equal(settled[t][1], far_blob), t
    assert guard.counts["anime"] == 3 and guard.counts["flagged"] == 37 and guard.counts["rejected"] == 15
    assert guard.counts["matched"] + guard.counts["fallback"] == 22


def test_a_normal_fallback_is_still_matched_to_the_people_nearby():
    """正常な予備 (人物のあたり・大きすぎない) は、今までどおり match_to_found で合わせる。出力は検査の前と変わらない。"""
    frames = _scene_images([16])
    people = [_rect(30, 20 + 4 * t, 36, 20) for t in range(16)]
    for t in range(5, 10):
        people[t] = np.zeros(SHAPE, np.float32)
    fallback = [_cone((48, 30 + 4 * t), 30) for t in range(16)]
    segmenter = _Scripted(frames, people, fallback)
    guard, settled = _settle_all(segmenter, frames)
    assert guard.counts["matched"] == 5 and guard.counts["rejected"] == 0
    for t in range(5, 10):
        near = [u for u in range(max(0, t - 6), min(16, t + 7)) if not 5 <= u < 10]
        assert np.array_equal(settled[t][1], person.match_to_found(fallback[t], [_GuardLike(people[u]) for u in near])), t


class _GuardLike:
    """match_to_found に渡す、面積と箱を持つだけの入れ物。"""

    def __init__(self, prob):
        mask = prob >= 0.5
        self.area, self.box = float(mask.mean()), person.bounding_box(mask)


def test_the_salient_cutout_does_not_return_a_huge_photo_result_when_it_has_no_blob(tmp_path, monkeypatch):
    module = types.SimpleNamespace(InferenceSession=_SalientSession, SessionOptions=lambda: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    model = tmp_path / "salient.onnx"
    model.write_bytes(b"x")
    photo = lambda image: (image[..., 1] > 128).astype(np.float32) * 0.8   # 写真向けのモデルは緑だけを人物と見る
    segmenter = person.SalientPersonSegmenter(photo, model_path=model)
    streak_only = np.zeros((90, 160, 3), np.uint8)
    streak_only[82:90, :, 2] = 255            # 光の筋 (青)。写真向けは人物と見ないので、人物らしい塊は無い
    huge = streak_only.copy()
    huge[:80, :, 1] = 255                     # 写真向けが画面の 89% を人物とする
    small = streak_only.copy()
    small[:20, :, 1] = 255                    # 22%
    assert not segmenter(huge).any() and segmenter.last_rejected and segmenter.rejected_frames == 1
    assert (segmenter(small) >= 0.5).mean() == pytest.approx(20 / 90) and not segmenter.last_rejected
    assert segmenter.no_blob_frames == 2 and segmenter.rejected_frames == 1


@pytest.mark.parametrize("level, noise", [(0, 0), (16, 1), (16, 3), (40, 2), (128, 2), (250, 1)])
def test_a_uniform_screen_is_not_a_scene_cut_on_every_frame(level, noise):
    """暗転・単色・白飛びがほぼ同じまま続くとき、切り替わりは 0 (平均を引いた構図は意味が無い)。"""
    rng = np.random.default_rng(level + noise)
    frames = [np.clip(level + rng.integers(-noise, noise + 1, (90, 160, 3)), 0, 255).astype(np.uint8) for _ in range(20)]
    features = [person.scene_features(frame) for frame in frames]
    assert [i for i in range(1, 20) if person.is_scene_cut(features[i - 1], features[i])] == []


def test_a_cut_between_a_patterned_screen_and_a_uniform_one_is_still_a_cut():
    features = person.scene_features
    pattern = _scene_images([1])[0]
    white, black = np.full_like(pattern, 255), np.zeros_like(pattern)
    assert person.is_scene_cut(features(pattern), features(white)) and person.is_scene_cut(features(white), features(pattern))
    assert person.is_scene_cut(features(pattern), features(black)) and person.is_scene_cut(features(black), features(pattern))
    # 両方が一様なら、平均の明るさの差だけで決める
    assert person.is_scene_cut(features(black), features(white)) and person.is_scene_cut(features(black), features(np.full_like(pattern, 60)))
    assert not person.is_scene_cut(features(np.full_like(pattern, 16)), features(np.full_like(pattern, 30)))
    # 標準偏差と平均を持たない 2 つ組の特徴 (模様のある画面) の規則は変わらない
    assert person.is_scene_cut(features(pattern)[:2], features(white)[:2]) is not None


@pytest.mark.parametrize("duration", [0.35, 0.33])
def test_the_background_video_has_as_many_frames_as_were_sent_even_when_the_duration_has_a_fraction(tmp_path, duration):
    """-t を出力全体に付けると、duration が 1 フレームの長さの倍数でないとき背景の動画が 1 フレーム短かった。
    音声の入力だけを duration で切る: 送った 4 フレームがそのまま 4 フレームの背景になる。"""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg と ffprobe が必要")
    source = tmp_path / "with_sound.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=10:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(source)], check=True)
    sent = 4   # 送るフレームの数 (duration * 10 fps を切り上げた数。以前は出力の -t で 3 フレームに切られた)
    out = tmp_path / "background.mp4"
    writer = person._writer(out, 64, 36, 10, "ffmpeg", audio_from=source, duration=duration)
    for _ in range(sent):
        writer.stdin.write(np.full((36, 64, 3), 90, np.uint8).tobytes())
    writer.stdin.close()
    assert writer.wait() == 0

    def probe(stream, entries):
        return subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", stream, "-show_entries", entries,
                               "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True).stdout.strip()

    assert probe("v:0", "stream=nb_read_frames") == str(sent)
    assert float(probe("a:0", "stream=duration")) <= duration + 0.1   # 音声は duration で切る
