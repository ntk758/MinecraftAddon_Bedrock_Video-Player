"""画像モードの時計の模擬 (TODO.md の P0-8)。

サーバー側は image_mode/image_main.js そのものを、test_image_main.py のモックで動かす。tick ごとの実時間 (tickMs) を変えて
TPS の低下やばらつきを作り、クライアントに届いたプロパティ (frame・seq・state) と鳴らした音声のチャンクの時刻を記録する。
クライアント側は、image_mode/rp.py の pre_animation と同じ計算を Python で行う:

    seq が変わったのを見た時刻を base_time、そのときの frame を base_frame にして、base_frame + floor((t - base_time) * fps)

前提 (実機で確かめるまでは仮定): クライアントの時計 (q.life_time) は、サーバーの TPS が落ちても実時間で進む。プロパティは
書いた次の tick にクライアントへ届き、さらに LATENCY 秒遅れて見える。音声のチャンクは、鳴らした時刻から実時間で進む。

数えるもの: 映像 (クライアントのコマ) と音声の位置のずれ、合わせ直しのときのコマの飛び、チャンクの間の無音、途中で入った人の遅れ。
"""
import json
import math

import pytest

from test_image_main import run

FPS = 30
CHUNK = 4
LATENCY = 0.05


def video(seconds):
    frames = seconds * FPS
    return {"id": "clock", "title": "clock", "fps": FPS, "frame_count": frames, "audio_chunks": math.ceil(seconds / CHUNK),
            "audio_chunk_seconds": CHUNK, "screen_blocks": [16, 9], "methods": ["a"], "width": 256, "height": 144}


class Client:
    """pre_animation の計算。observe(時刻, プロパティ) を時刻の順に呼び、frame_at(t) で t 秒に描くコマを返す。"""

    def __init__(self, frame_count, joined_at=0.0):
        self.last = frame_count - 1
        self.joined_at = joined_at
        self.props = {}
        self.seen_seq = None
        self.base_time = self.base_frame = 0
        self.events = []   # (見えた時刻, frame, seq, state)

    def add(self, time, props):
        self.events.append((time + LATENCY, props))

    def frame_at(self, t):
        # t までに見えたプロパティを反映する。途中で入った人は、入った時刻に今のプロパティを初めて見る
        while self.events and self.events[0][0] <= t:
            seen, props = self.events.pop(0)
            self.props.update(props)
            if seen < self.joined_at:
                continue
            self._check(max(seen, self.joined_at))
        if t >= self.joined_at:
            self._check(self.joined_at if self.seen_seq is None else None)
        if self.props.get("state") != 1:
            return self.base_frame
        return min(self.base_frame + math.floor((t - self.base_time) * FPS + 1e-9), self.last)

    def _check(self, when):
        seq = self.props.get("seq")
        if seq is not None and seq != self.seen_seq and when is not None:
            self.seen_seq = seq
            self.base_time = when
            self.base_frame = self.props.get("frame", 0)


def simulate(tmp_path, seconds, tick_ms, extra_steps=(), main=None, join_at=None):
    """seconds 秒の動画を最後まで再生し、模擬のクライアントで 1/60 秒ごとに描いたコマと、音声の位置を返す。
    tick_ms は数か関数 (tick の番号 → ms)。"""
    steps = [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}]
    total_ms = 0
    n = 0
    while total_ms < seconds * 1000 + 500:
        ms = tick_ms(n) if callable(tick_ms) else tick_ms
        steps += [{"tickMs": ms}, {"tick": 1}]
        total_ms += ms
        n += 1
        if join_at is not None and abs(total_ms - join_at * 1000) < ms / 2:
            steps.append({"join": True})
    steps += list(extra_steps)
    kwargs = {"main": main} if main else {}
    out = run(tmp_path, steps, videos=[video(seconds)], **kwargs)
    writes = out["log"]["writes"]
    sounds = [(now, sound) for _, sound, now in out["log"]["sounds"]]
    origin = sounds[0][0] / 1000   # 最初のチャンクを鳴らした実時間を 0 秒とする
    client = Client(seconds * FPS, joined_at=(join_at or 0.0))
    for now, _entity, props in writes:
        client.add(now / 1000 - origin, {k.split(":")[1]: v for k, v in props.items()})
    starts = [now / 1000 - origin for now, _ in sounds]
    samples = []
    t = 0.0
    while t < seconds - 0.05:
        frame = client.frame_at(t)
        audio = None
        for k, start in enumerate(starts):
            if start <= t < start + CHUNK:
                audio = k * CHUNK + (t - start)
        samples.append((t, frame, audio))
        t += 1 / 60
    return samples, starts


def offsets(samples, start=0.0):
    """映像と音声の位置の差 (秒) の最大と、後ろへ飛んだコマの数の最大。"""
    worst = 0.0
    back = 0
    previous = None
    for t, frame, audio in samples:
        if t < start:
            previous = frame
            continue
        if audio is not None:
            worst = max(worst, abs(frame / FPS - audio))
        if previous is not None and frame < previous:
            back = max(back, previous - frame)
        previous = frame
    return worst, back


def gaps(starts):
    """チャンクの終わりから次のチャンクが鳴るまでの無音 (秒、重なりは負)。"""
    return [starts[k + 1] - (starts[k] + CHUNK) for k in range(len(starts) - 1)]


def test_tps_20_has_no_jumps_and_keeps_audio_and_video_together(tmp_path):
    samples, starts = simulate(tmp_path, 20, 50)
    worst, back = offsets(samples)
    assert back <= 2
    assert worst <= 0.15   # 1 tick と、届くまでの LATENCY
    assert max(gaps(starts)) <= 0.06


@pytest.mark.parametrize("tick_ms", [100, 200], ids=["tps10", "tps5"])
def test_low_tps_keeps_audio_and_video_together(tmp_path, tick_ms):
    """TPS が下がっても、再生位置は実時間で進む: ずれは 0.2 秒以内、チャンクの間の無音は 0.3 秒以内 (P0-2 の完了条件)。"""
    samples, starts = simulate(tmp_path, 60, tick_ms)
    worst, back = offsets(samples)
    # ずれは 1 tick (音声はチャンクの境目を過ぎた tick で鳴る) と LATENCY まで。TPS 10 で 0.2 秒以内 (P0-2 の完了条件)
    assert worst <= tick_ms / 1000 + LATENCY + 0.02, worst
    assert back <= 2
    assert max(gaps(starts)) <= 0.3


def test_uneven_ticks_keep_audio_and_video_together(tmp_path):
    """tick の長さがばらつく (30〜120 ms、平均で TPS 約 13)。"""
    pattern = [30, 120, 50, 90, 40, 110, 70, 60]
    samples, starts = simulate(tmp_path, 40, lambda n: pattern[n % len(pattern)])
    worst, _ = offsets(samples)
    assert worst <= 0.2
    assert max(gaps(starts)) <= 0.3


def test_late_joiner_is_resynchronized_at_once(tmp_path):
    """途中で入った人: 入った時刻に、その前の合わせ直しの frame を見る。入った tick に書き直すので、遅れは 0.25 秒以内 (P0-2 の A)。"""
    # 定期の合わせ直し (5.0 秒) から 0.7 秒たって入り、次の合わせ直し (6.0 秒) の前までを見る (古い版では約 0.7 秒遅れる)
    samples, _ = simulate(tmp_path, 10, 50, join_at=5.7)
    lag = max(abs(t * FPS - frame) for t, frame, _ in samples if 5.8 <= t <= 5.95) / FPS
    assert lag <= 0.25, lag


def test_a_long_stall_counts_only_up_to_the_cap(tmp_path):
    """シングルプレイのポーズ画面などで tick が 10 秒止まっても、再生位置は 250 ms しか進まない (止まる前の続きから)。"""
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 40}, {"cmd": "stats"},
                         {"wait": 10000}, {"tick": 1}, {"cmd": "stats"}], videos=[video(60)])
    frames = [json.loads(m.split(" ", 1)[1])["rawtext"][2]["text"] for m in out["log"]["messages"]
              if m.startswith("bvpimg.stats ")]
    before, after = (int(f) for f in frames)
    # 40 tick (2 秒) → 60 コマ。止まった 10 秒は 250 ms (7 コマ) だけ進む
    assert before == 60
    assert after - before == 7
    clock = [m for m in out["log"]["messages"] if m.startswith("bvpimg.stats_clock")]
    assert len(clock) == 2 and '"0.0"' in clock[0] and '"9.8"' in clock[1]   # 10 秒 - 250 ms を数えていない


def test_a_world_saved_by_the_tick_clock_resumes_at_the_same_position(tmp_path):
    """古い版 (tick で数えていた) が保存した状態から、同じ位置で続ける。保存するときは古い版のために playTicks も書く。"""
    old = {"videoId": "clock", "method": "a", "entityId": None, "dimension": "minecraft:overworld", "location": None,
           "rotation": 0, "mode": 1, "playTicks": 200, "pausedFrame": 0, "volume": 100}
    out = run(tmp_path, [{"setState": json.dumps(old)}, {"reload": 0}, {"state": "loaded"}, {"tick": 20},
                         {"state": "saved"}], videos=[video(60)])
    saved = json.loads(out["snaps"][-1]["state"])
    # 200 tick = 10 秒から、20 tick (1 秒) 進んだ
    assert saved["playMs"] == pytest.approx(11000, abs=60)
    assert saved["playTicks"] == round(saved["playMs"] / 50)
