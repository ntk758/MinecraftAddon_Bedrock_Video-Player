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

# ffmpeg (-v error) が、壊れた・途中で切れた入力で書く文
DECODE_ERRORS = re.compile(
    r"partial file|Invalid data found|Error while decoding|Decoding error|Invalid NAL unit|error reading header"
    r"|corrupt|truncat|moov atom not found|End of file",
    re.IGNORECASE,
)


class TruncatedVideoError(RuntimeError):
    """動画が途中で切れていて、最後まで読めなかった。"""

    def __init__(self, path, decoded_seconds: float, expected_seconds: float):
        self.path = str(path)
        self.decoded_seconds = decoded_seconds
        self.expected_seconds = expected_seconds
        super().__init__(
            f"動画が途中で切れています: 読めたのは {decoded_seconds:.1f} 秒 / {expected_seconds:.1f} 秒 ({path})。"
            "ダウンロードの途中で止まったファイルなどです。ファイルを最後まで取り直してから変換してください / "
            f"The video is truncated: only {decoded_seconds:.1f} of {expected_seconds:.1f} seconds could be read ({path})")


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


def check_complete(path, frames: int, fps: float, expected: float | None, stderr: str) -> None:
    """frames コマ (fps) が expected 秒より明らかに短く、stderr にデコードのエラーがあれば TruncatedVideoError。

    明らかに短い = 2 コマ分と、長さの 5% のどちらよりも多く足りない (fps に直すときの端数・最後のコマの丸めでは止めない)。
    """
    if expected is None or expected <= 0 or fps <= 0:
        return
    decoded = frames / fps
    tolerance = max(2.0 / fps, expected * 0.05)
    if expected - decoded > tolerance and DECODE_ERRORS.search(stderr or ""):
        raise TruncatedVideoError(path, decoded, expected)
