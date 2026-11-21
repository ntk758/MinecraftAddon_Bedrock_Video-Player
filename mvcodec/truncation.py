"""途中で切れた動画 (ダウンロードの途中などで末尾が欠けたファイル) を見つける。

ffmpeg は、末尾の欠けた mp4 でも読めた所までのコマを出し、「partial file」などのエラーを書いたうえで終了コード 0 で
終わる。そのまま使うと、変換は成功したように見えて動画の後ろが黙って無くなる (外部テストの F-1: 5 秒の動画が 2.9 秒の
アドオンになった)。ブロックモード (convert.py)・画像モード (image_mode/atlas.py)・人物の切り抜き (mvcodec/person.py) の
3 つの読み込みで、読めた長さが動画の長さより明らかに短く、しかもデコードのエラーが出ていれば止める。

エラーの文が出ていない短さ (音声の方が長い動画の、映像の終わりなど) では止めない (正しい動画を断らないように)。
"""
from __future__ import annotations

import os
import re
import subprocess
import threading

# ffmpeg (-v error) が、壊れた・途中で切れた入力で書く文
DECODE_ERRORS = re.compile(
    r"partial file|Invalid data found|Error while decoding|Decoding error|Invalid NAL unit|error reading header"
    r"|corrupt|truncat|moov atom not found|End of file|ended prematurely|missing picture|concealing"
    r"|Packet mismatch|Read error",
    re.IGNORECASE,
)
# そのうち、ファイルの終わりが欠けていることを示す文 (Matroska / WebM は「File ended prematurely」だけを書く。#157)。
# 長さの情報が無い・短すぎる動画でも、これが出ていれば途中で切れている (#156・#176)
CUT_ERRORS = re.compile(r"partial file|ended prematurely|truncat|End of file|moov atom not found", re.IGNORECASE)
# 足りない長さの許し (fps に直すときの端数・最後のコマの丸め): 2 コマ分と、長さの 5% の大きい方。ただし 5% は
# この秒数まで (長い動画で、30 秒も欠けたまま通さないように。#169)
TOLERANCE_RATIO = 0.05
TOLERANCE_MAX_SECONDS = 2.0


class TruncatedVideoError(RuntimeError):
    """動画が途中で切れていて、最後まで読めなかった。"""

    def __init__(self, path, decoded_seconds: float, expected_seconds: float | None):
        self.path = str(path)
        self.decoded_seconds = decoded_seconds
        self.expected_seconds = expected_seconds
        if expected_seconds is None:
            # 長さが分からない・ファイルの長さより長く読めた (長さの情報が壊れている) とき
            super().__init__(
                f"動画が途中で切れています: ファイルの終わりが欠けていて、{decoded_seconds:.1f} 秒までしか読めませんでした ({path})。"
                "ダウンロードの途中で止まったファイルなどです。ファイルを最後まで取り直してから変換してください / "
                f"The video is truncated: the end of the file is missing and only {decoded_seconds:.1f} seconds could be read ({path})")
            return
        super().__init__(
            f"動画が途中で切れています: 読めたのは {decoded_seconds:.1f} 秒 / {expected_seconds:.1f} 秒 ({path})。"
            "ダウンロードの途中で止まったファイルなどです。ファイルを最後まで取り直してから変換してください / "
            f"The video is truncated: only {decoded_seconds:.1f} of {expected_seconds:.1f} seconds could be read ({path})")


class StderrCollector:
    """ffmpeg の stderr を別のスレッドで読み続ける。

    読まないと、壊れた動画で ffmpeg がエラーを書き続けてパイプ (64 KB) が詰まり、ffmpeg とこちらが待ち合って止まる
    (画像モードだけ読んでおらず止まっていた。#160)。表示には最後の tail_bytes だけ使い、途中で切れたかの判定には
    デコードのエラーの行を別に残す (最後の 8 KB だけでは、前の方のエラーの文が押し出されて見落としていた。#177)。
    """

    def __init__(self, stream, tail_bytes: int = 8192, max_error_lines: int = 64):
        self.stream = stream
        self.tail_bytes = tail_bytes
        self.max_error_lines = max_error_lines
        self.tail = bytearray()
        self.error_lines: list[str] = []
        self.error_count = 0
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> "StderrCollector":
        self._thread.start()
        return self

    def _keep(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if text and DECODE_ERRORS.search(text):
            self.error_count += 1
            if len(self.error_lines) < self.max_error_lines:
                self.error_lines.append(text)

    def _drain(self) -> None:
        pending = b""
        for chunk in iter(lambda: self.stream.read(4096), b""):
            self.tail.extend(chunk)
            del self.tail[:-self.tail_bytes]
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                self._keep(line)
            if len(pending) > 65536:  # 改行の無い長い出力
                self._keep(pending)
                pending = b""
        if pending:
            self._keep(pending)

    def join(self, timeout: float | None = 5) -> None:
        self._thread.join(timeout)

    @property
    def text(self) -> str:
        """表示用の最後の部分。"""
        return bytes(self.tail).decode("utf-8", errors="replace")

    @property
    def for_check(self) -> str:
        """check_complete に渡す文 (デコードのエラーの行と、最後の部分)。"""
        return "\n".join(self.error_lines) + "\n" + self.text


def _subprocess_kwargs():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def probe_duration(path, ffmpeg: str = "ffmpeg") -> float | None:
    """動画の長さ (秒)。ffmpeg -i の Duration の行から読む (ffprobe が無い環境でも動くように)。読めなければ None。"""
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60, check=False, **_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        return None
    hours, minutes, secs = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def expected_seconds(total: float | None, start: float | None = None, end: float | None = None,
                     duration: float | None = None) -> float | None:
    """読むはずの長さ (秒)。total は動画の長さ、start / end は切り出す範囲、duration は先頭からの長さの上限。"""
    if total is None or total <= 0:
        return None
    begin = max(0.0, start or 0.0)
    stop = total if end is None else min(total, end)
    if duration is not None:
        stop = min(stop, begin + duration)
    return max(0.0, stop - begin)


def check_complete(path, frames: int, fps: float, expected: float | None, stderr: str, warn=None) -> None:
    """読めた frames コマ (fps) と ffmpeg の stderr から、動画が途中で切れていれば TruncatedVideoError。

    - expected 秒より明らかに短く (2 コマ分と長さの 5% (2 秒まで) のどちらよりも多く足りない)、デコードのエラーがある
    - ファイルの終わりが欠けている文 (CUT_ERRORS) があり、長さが分からない、または長さより明らかに長く読めた
      (長さの情報が壊れていて、比べられない。#156・#176)

    止めないが、warn (1 行の文を受け取る関数) があれば知らせること:
    - エラーの文が無いのに明らかに短い (音声の方が長い動画か、きれいに切れた動画。正しい動画もあるので止めない。#155)
    - デコードのエラーがあるが長さは足りている (途中が壊れていて、読めなかったコマは前のコマで埋まっている。#162)
    """
    if fps <= 0:
        return
    text = stderr or ""
    decoded = frames / fps
    errors = DECODE_ERRORS.search(text)
    cut = CUT_ERRORS.search(text)
    if expected is None or expected <= 0:
        if cut:
            raise TruncatedVideoError(path, decoded, None)
    else:
        tolerance = max(2.0 / fps, min(expected * TOLERANCE_RATIO, TOLERANCE_MAX_SECONDS))
        if expected - decoded > tolerance:
            if errors:
                raise TruncatedVideoError(path, decoded, expected)
            if warn is not None:
                warn(f"注意: 映像は {decoded:.1f} 秒で終わりました (ファイルの長さは {expected:.1f} 秒)。音声の方が長い動画か、"
                     f"途中で切れた動画です / Note: the video ends at {decoded:.1f} s but the file is {expected:.1f} s long "
                     "(the audio is longer, or the video was cut)")
            return
        if decoded - expected > tolerance and cut:
            raise TruncatedVideoError(path, decoded, None)
    if errors and warn is not None:
        warn(f"注意: 動画に壊れた所があります。読めなかったコマは前のコマで埋めました (ffmpeg: {errors.string[errors.start():].splitlines()[0][:160]}) / "
             "Note: the video has damaged parts; frames that could not be decoded were filled with the previous frame")


def audio_seconds(chunk_files, chunk_seconds: float, ffmpeg: str = "ffmpeg") -> float | None:
    """チャンクに分けた音声の長さ (秒): (チャンクの数 - 1) × chunk_seconds + 最後のチャンクの長さ。分からなければ None。"""
    files = list(chunk_files)
    if not files:
        return None
    last = probe_duration(files[-1], ffmpeg)
    if last is None:
        return None
    return (len(files) - 1) * chunk_seconds + last


def check_audio_length(audio: float | None, video: float | None, warn) -> None:
    """音声が映像より明らかに短ければ知らせる (音声だけ途中で切れた動画で、アドオンの音が途中で消える。#178)。
    映像より短い音声は正しい動画にもあるので、止めはしない。"""
    if audio is None or video is None or video <= 0:
        return
    if video - audio > max(0.5, min(video * TOLERANCE_RATIO, TOLERANCE_MAX_SECONDS)):
        warn(f"注意: 音声は {audio:.1f} 秒で終わります (映像は {video:.1f} 秒)。その後は音が鳴りません / "
             f"Note: the audio ends at {audio:.1f} s (the video is {video:.1f} s); there is no sound after that")
