"""人物の切り抜きのメモリの扱い (mvcodec/person.py): 安全閾値の環境変数、続けて処理したときのモデルの解放、
cpu の段階の後に gpu の段階で作り直す、GUI が測った空きメモリを子プロセスに渡す、モデルごとの最大のメモリ。

本物のモデル・GPU・onnxruntime は使わない (CI の ubuntu には onnxruntime が無い)。偽の onnxruntime と偽の写真向けのモデルで、
セッションを作った回数・要求した実行プロバイダー・手放したか (弱い参照) を確かめる。
"""
import gc
import json
import sys
import types
import weakref
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mvcodec.person as person  # noqa: E402

GIB = 1 << 30


@pytest.fixture(autouse=True)
def no_real_probes(monkeypatch):
    """空きメモリを実際には測らない (VRAM は PowerShell を起動して約 2.6 秒かかる)。環境変数は各テストが決める。"""
    from mvcodec import resources

    monkeypatch.setattr(resources, "gpu_memory", lambda: None)
    for name in (person.ENV_PROFILE_NEEDS, person.ENV_MIN_FREE_RAM):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 偽の onnxruntime と、写真向けのモデル
# ---------------------------------------------------------------------------

class _Session:
    """ISNet (人物を見つけない) と BiRefNet (全面を目立つとする) の偽のセッション。作ったことを弱い参照で数える。"""

    def __init__(self, name, providers):
        self.name, self.providers = name, list(providers)

    def get_inputs(self):
        return [types.SimpleNamespace(name="input_image" if self.name == person.SALIENT_MODEL_FILE else "img")]

    def get_providers(self):
        return self.providers

    def run(self, _outputs, feeds):
        if self.name == person.SALIENT_MODEL_FILE:
            return [np.full((1, 1, person.SALIENT_INPUT_SIZE, person.SALIENT_INPUT_SIZE), 8.0, np.float32)]
        return [np.zeros((1, 1, person.ANIME_INPUT_SIZE, person.ANIME_INPUT_SIZE), np.float32)]


class _FakeOrt:
    def __init__(self):
        self.created = []   # (弱い参照, モデルのファイル名, 要求した実行プロバイダー)
        self.module = types.SimpleNamespace(InferenceSession=self.create, SessionOptions=lambda: types.SimpleNamespace(),
                                            get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])

    def create(self, path, options=None, providers=None):
        session = _Session(Path(path).name, providers or ["CPUExecutionProvider"])
        self.created.append((weakref.ref(session), session.name, session.providers))
        return session

    def alive(self):
        gc.collect()
        return [name for ref, name, _ in self.created if ref() is not None]

    def requested(self, name):
        return [providers for _, file, providers in self.created if file == name]


class _Photo:
    """写真向けのモデルの偽物: 画面の左上 4 分の 1 を人物とする。"""

    def __init__(self):
        self.inference_ms = []

    def __call__(self, rgb):
        prob = np.zeros(rgb.shape[:2], np.float32)
        prob[:rgb.shape[0] // 2, :rgb.shape[1] // 2] = 0.8
        return prob


@pytest.fixture
def fake_environment(tmp_path, monkeypatch):
    folder = tmp_path / "models"
    folder.mkdir()
    (folder / person.ANIME_MODEL_FILE).write_bytes(b"x")
    (folder / person.SALIENT_MODEL_FILE).write_bytes(b"x")
    monkeypatch.setenv("BVP_MODEL_DIR", str(folder))
    ort = _FakeOrt()
    monkeypatch.setitem(sys.modules, "onnxruntime", ort.module)
    monkeypatch.setattr(person, "PersonSegmenter", _Photo)
    return ort


def _fake_pipeline(monkeypatch, frames=8):
    """ffmpeg を使わずに make_person_videos を動かす: 読む側は合成のフレーム、書く側は捨てる。"""
    class Sink:
        def __init__(self):
            self.stdin = types.SimpleNamespace(write=lambda data: None, close=lambda: None)

        def wait(self):
            return 0

    rng = np.random.default_rng(0)
    clip = [rng.integers(0, 255, (90, 160, 3), dtype=np.uint8) for _ in range(frames)]
    monkeypatch.setattr(person, "read_frames", lambda *args, **kwargs: iter([frame.copy() for frame in clip]))
    monkeypatch.setattr(person, "_writer", lambda *args, **kwargs: Sink())


def _cut_out_one_video(profile, tmp_path, stats):
    """段階 profile のモデルを作って 1 本の動画を処理する (GUI の子プロセスが 1 回にすることと同じ)。"""
    segmenter = person.make_segmenter("anime", profile)
    count = person.make_person_videos("in.mp4", tmp_path / "m.mp4", tmp_path / "b.mp4", 10, segmenter=segmenter, stats=stats)
    return count, segmenter.report()


# ---------------------------------------------------------------------------
# (1) 続けて 2 本の動画を処理しても、モデルの参照が解放され、2 本目でセッションを作り直す
# ---------------------------------------------------------------------------

def test_two_videos_in_a_row_release_the_models_and_rebuild_the_sessions(fake_environment, monkeypatch, tmp_path):
    ort = fake_environment
    _fake_pipeline(monkeypatch)
    for number in (1, 2):
        stats = {}
        count, report = _cut_out_one_video("full", tmp_path, stats)
        assert count == 8
        # アニメ向けが何も見つけないので予備 (写真向け + 目立つ物の切り抜き) が作られ、使われた
        assert report["models"]["salient"]["providers"] == ["DmlExecutionProvider", "CPUExecutionProvider"]
        assert len(ort.created) == 2 * number           # 1 本ごとに ISNet と BiRefNet を作り直した (前のを使い回さない)
        # 処理を終えて、切り抜きへの参照を手放したら、このセッションも残らない (弱い参照が切れる)
        assert ort.alive() == [], f"{number} 本目の後にセッションが残っている: {ort.alive()}"
        assert stats["frames"] == 8
        json.dumps(stats)
    # 2 本目も 1 本目と同じ実行プロバイダーを要求した
    assert ort.requested(person.ANIME_MODEL_FILE)[0] == ort.requested(person.ANIME_MODEL_FILE)[1]
    assert ort.requested(person.SALIENT_MODEL_FILE)[0] == ort.requested(person.SALIENT_MODEL_FILE)[1]


def test_dropping_the_birefnet_releases_its_session_while_the_run_continues(fake_environment, monkeypatch, tmp_path):
    """BiRefNet が動かなくなって外したとき、セッションはその場で手放す (処理の途中でも GPU のメモリが返る)。"""
    ort = fake_environment
    segmenter = person.make_segmenter("anime", "full")
    segmenter.fallback_prob(np.zeros((90, 160, 3), np.uint8), 0)
    assert person.SALIENT_MODEL_FILE in ort.alive()
    segmenter._drop_salient("oom", "E_OUTOFMEMORY")
    assert person.SALIENT_MODEL_FILE not in ort.alive() and person.ANIME_MODEL_FILE in ort.alive()
    del segmenter
    assert ort.alive() == []


# ---------------------------------------------------------------------------
# (2) cpu の段階で処理した後に gpu の段階でセッションを作ると、DmlExecutionProvider を要求する
# ---------------------------------------------------------------------------

def test_a_gpu_stage_after_a_cpu_stage_asks_for_directml_again(fake_environment, monkeypatch, tmp_path):
    ort = fake_environment
    _fake_pipeline(monkeypatch)
    _, cpu_report = _cut_out_one_video("cpu", tmp_path, {})
    assert cpu_report["models"]["anime"]["requested"] == ["CPUExecutionProvider"]
    assert ort.requested(person.ANIME_MODEL_FILE) == [["CPUExecutionProvider"]]
    assert "salient" not in cpu_report["models"]                       # cpu の段階は BiRefNet を使わない
    gc.collect()
    _, gpu_report = _cut_out_one_video("gpu", tmp_path, {})            # cpu の後でも、GPU の要求は前の段階に引きずられない
    assert gpu_report["models"]["anime"]["requested"][0] == "DmlExecutionProvider"
    assert ort.requested(person.ANIME_MODEL_FILE)[1][0] == "DmlExecutionProvider"
    assert gpu_report["models"]["anime"]["providers"][0] == "DmlExecutionProvider"
    assert "salient" not in gpu_report["models"]                       # gpu の段階は BiRefNet を使わない (full だけ)
    _, full_report = _cut_out_one_video("full", tmp_path, {})
    assert ort.requested(person.SALIENT_MODEL_FILE)[-1][0] == "DmlExecutionProvider"
    assert full_report["models"]["salient"]["providers"][0] == "DmlExecutionProvider"
    gc.collect()
    assert ort.alive() == []


# ---------------------------------------------------------------------------
# (3) 環境変数の上書き: 段階表と、RAM の見張り
# ---------------------------------------------------------------------------

def test_the_ladder_follows_the_environment_variable(monkeypatch):
    # 既定: VRAM の空きが 8 GiB なら full を飛ばして gpu から
    assert person.profile_ladder("anime", 16 * GIB, 8 * GIB) == ["gpu", "cpu", "photo"]
    assert person.profile_needs() == person.PROFILE_NEEDS
    # full の VRAM を 6 GiB にすれば full から。gpu が要る VRAM を 9 GiB にすれば gpu が外れる
    monkeypatch.setenv(person.ENV_PROFILE_NEEDS, "full=12:6,gpu=2:9")
    assert person.profile_needs()["full"] == (12 * GIB, 6 * GIB) and person.profile_needs()["gpu"] == (2 * GIB, 9 * GIB)
    assert person.profile_needs()["cpu"] == person.PROFILE_NEEDS["cpu"]
    assert person.profile_ladder("anime", 16 * GIB, 8 * GIB) == ["full", "cpu", "photo"]
    assert person.profile_ladder("anime", 16 * GIB, 5 * GIB) == ["cpu", "photo"]
    # RAM も同じ。小数の GiB も使える
    monkeypatch.setenv(person.ENV_PROFILE_NEEDS, "full=20:12")
    assert person.profile_ladder("anime", 16 * GIB, 14 * GIB)[0] == "gpu"
    monkeypatch.setenv(person.ENV_PROFILE_NEEDS, "full=0.5:0.25")
    assert person.profile_ladder("anime", GIB, GIB // 2)[0] == "full"
    # 空きの理由 (stats の profile_skipped) も同じ値を使う
    monkeypatch.setenv(person.ENV_PROFILE_NEEDS, "full=12:6")
    assert person.skipped_profiles("anime", "gpu", 16 * GIB, 5 * GIB) == {"full": "VRAM 5.0 < 6 GiB"}
    # 写真向けは変わらない (ほかの段階が無い)
    assert person.profile_ladder("photo", 0, 0) == ["photo"]


@pytest.mark.parametrize("value", ["", "garbage", "full", "full=", "full=12", "full=x:y", "full=-1:2", "full=nan:1", "full=inf:1",
                                   "huge=1:1", "=1:1", "full=12:12:12x", ",,", "full:12:12"])
def test_invalid_needs_are_ignored_and_the_defaults_are_used(monkeypatch, value):
    monkeypatch.setenv(person.ENV_PROFILE_NEEDS, value)
    assert person.profile_needs() == person.PROFILE_NEEDS
    assert person.profile_ladder("anime", 16 * GIB, 8 * GIB) == ["gpu", "cpu", "photo"]


def test_a_bad_entry_does_not_hide_the_good_ones(monkeypatch):
    monkeypatch.setenv(person.ENV_PROFILE_NEEDS, "full=oops, gpu=1:0.5 ,cpu=-3:0,nothing=1:1")
    needs = person.profile_needs()
    assert needs["gpu"] == (GIB, GIB // 2) and needs["full"] == person.PROFILE_NEEDS["full"]
    assert needs["cpu"] == person.PROFILE_NEEDS["cpu"]


def test_the_ram_guard_follows_the_environment_variable(monkeypatch):
    from mvcodec import resources

    assert person.min_free_ram() == person.MIN_FREE_RAM
    monkeypatch.setattr(resources, "system_memory", lambda: (8 * GIB, 600 << 20))
    person._check_free_ram()                               # 既定の 400 MB より多い: 止めない
    monkeypatch.setenv(person.ENV_MIN_FREE_RAM, "1000")
    assert person.min_free_ram() == 1000 << 20
    with pytest.raises(MemoryError):
        person._check_free_ram()                           # 1000 MB 要るとすると 600 MB では止まる
    monkeypatch.setenv(person.ENV_MIN_FREE_RAM, "0")        # 0 なら見張らない
    person._check_free_ram()
    for bad in ("", "abc", "-5", "nan", "inf"):
        monkeypatch.setenv(person.ENV_MIN_FREE_RAM, bad)
        assert person.min_free_ram() == person.MIN_FREE_RAM, bad


def test_the_command_line_uses_the_overridden_needs_to_choose_the_stage(monkeypatch, capsys):
    """--profile が無いとき、段階は profile_ladder (環境変数を読む) で選ぶ。"""
    seen = {}

    def make_segmenter(model, profile, download_progress=None, salient_slow_ms=None):
        seen["profile"] = profile

    monkeypatch.setattr(person, "make_segmenter", make_segmenter)
    monkeypatch.setattr(person, "make_person_videos", lambda *args, **kwargs: 1)
    from mvcodec import resources

    monkeypatch.setattr(resources, "system_memory", lambda: (32 * GIB, 16 * GIB))
    monkeypatch.setattr(resources, "gpu_memory", lambda: (16 * GIB, 8 * GIB))
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime"]) == 0
    assert seen["profile"] == "gpu"
    monkeypatch.setenv(person.ENV_PROFILE_NEEDS, "full=12:6")
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime"]) == 0
    assert seen["profile"] == "full"


# ---------------------------------------------------------------------------
# F7: GUI が測った空きメモリを子に渡すと、子は測り直さない / モデルごとの最大のメモリ
# ---------------------------------------------------------------------------

def test_free_memory_passed_by_the_gui_is_not_measured_again(monkeypatch, capsys):
    from mvcodec import resources

    def must_not_be_called():
        raise AssertionError("渡された空きメモリを測り直した")

    monkeypatch.setattr(resources, "gpu_memory", must_not_be_called)
    monkeypatch.setattr(resources, "system_memory", must_not_be_called)
    monkeypatch.setattr(person, "make_segmenter", lambda *args, **kwargs: None)
    monkeypatch.setattr(person, "make_person_videos", lambda *args, **kwargs: 1)
    stats = []
    emit = person._emit_stats
    monkeypatch.setattr(person, "_emit_stats", lambda s, *a, **k: (stats.append(dict(s)), emit(s, *a, **k))[1])
    argv = ["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "gpu",
            "--free-ram", str(20 * GIB), "--free-vram", str(5 * GIB)]
    assert person.main(argv) == 0
    # stats の始めたときの空きと、選ばなかった段階の理由は、渡された値で作る
    assert stats[0]["free_at_start"] == {"ram_mb": 20 * 1024, "vram_mb": 5 * 1024}
    assert stats[0]["profile_skipped"] == {"full": "VRAM 5.0 < 12 GiB"}
    # 分からない (負) は、測り直さずに「分からない」
    stats.clear()
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "gpu", "--free-ram", "-1",
                        "--free-vram", "-1"]) == 0
    assert stats[0]["free_at_start"] == {"ram_mb": None, "vram_mb": None} and stats[0]["profile_skipped"] == {"full": "not chosen"}
    # 渡されなければ、今までどおり測る
    monkeypatch.setattr(resources, "system_memory", lambda: (8 * GIB, 6 * GIB))
    calls = []
    monkeypatch.setattr(resources, "gpu_memory", lambda: calls.append(1) or (16 * GIB, 7 * GIB))
    stats.clear()
    assert person.main(["in.mp4", "m.mp4", "b.mp4", "--model", "anime", "--profile", "gpu"]) == 0
    assert calls == [1] and stats[0]["free_at_start"] == {"ram_mb": 6 * 1024, "vram_mb": 7 * 1024}


def test_models_report_how_much_the_process_peak_grew(fake_environment, monkeypatch):
    """モデルを作る前から最初の推論の後までに、プロセスの最大のメモリ (commit・working set) が増えた分を models.<名前> に入れる。
    測れない環境 (Windows 以外) ではキーを入れない。"""
    mb = 1 << 20
    peaks = iter([(100 * mb, 50 * mb), (160 * mb, 70 * mb),      # ISNet: 作る前 / 最初の推論の後
                  (160 * mb, 70 * mb), (500 * mb, 80 * mb)])     # BiRefNet (写真向けの偽物は測らない): 作る前 / 試しの推論の後
    monkeypatch.setattr(person, "memory_peaks", lambda: next(peaks))
    segmenter = person.make_segmenter("anime", "full")
    segmenter.primary(np.zeros((90, 160, 3), np.uint8))
    segmenter.primary(np.zeros((90, 160, 3), np.uint8))           # 最初の推論だけを測る
    segmenter.fallback_prob(np.zeros((90, 160, 3), np.uint8), 0)
    models = segmenter.report()["models"]
    assert models["anime"]["peak_mb"] == 60.0 and models["anime"]["peak_ws_mb"] == 20.0
    assert models["salient"]["peak_mb"] == 340.0 and models["salient"]["peak_ws_mb"] == 10.0
    # 測れないとき
    monkeypatch.setattr(person, "memory_peaks", lambda: None)
    segmenter = person.make_segmenter("anime", "gpu")
    segmenter.primary(np.zeros((90, 160, 3), np.uint8))
    assert "peak_mb" not in segmenter.report()["models"]["anime"]
    assert person.peak_growth(None) is None


def test_peak_growth_never_goes_below_zero(monkeypatch):
    mb = 1 << 20
    monkeypatch.setattr(person, "memory_peaks", lambda: (10 * mb, 10 * mb))
    assert person.peak_growth((20 * mb, 30 * mb)) == {"peak_mb": 0.0, "peak_ws_mb": 0.0}
