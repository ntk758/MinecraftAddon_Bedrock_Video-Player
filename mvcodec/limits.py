"""名前の決まり (動画 ID・接頭辞・パック名) を、変換器・アドオンのスクリプト・ブラウザ版でそろえる。

アドオンのスクリプト (screen_system.js) は 128 文字より長い動画 ID を保存から読み戻せず、ワールドを開き直すと
スクリーンが消えていた (#199)。変換器は長さを見ていなかったので、250 文字の ID で 305 文字のパスを作っていた (#186)。
"""
from __future__ import annotations

import math
import re
import unicodedata

# 動画 ID の長さの上限。Windows 版が足す名前 (<ID>_bg・<ID>_flat) と、重なったときの番号 (_2 など) も含めて
MAX_ID_LENGTH = 64
# ファイル名から作る既定の ID は、これより長ければ切る (後ろに _flat・_bg・番号を足しても MAX_ID_LENGTH に収まる)
DEFAULT_ID_LENGTH = 48
# コマンドの接頭辞 (名前空間) の長さの上限
MAX_NAMESPACE_LENGTH = 32
# パック名の長さの上限 (manifest の名前・UUID の元)
MAX_PACK_NAME_LENGTH = 100


def has_control_characters(text: str) -> bool:
    """改行・タブなどの制御文字 (Unicode の Cc) があるか。"""
    return any(unicodedata.category(c) == "Cc" for c in str(text))


def clean_title(text: str) -> str:
    """表示名の制御文字を空白にする (ファイル名から作る名前。Linux ではファイル名に改行も入る)。"""
    return " ".join("".join(" " if unicodedata.category(c) == "Cc" else c for c in str(text)).split())


def pack_name_problem(name: str) -> str | None:
    """パック名が使えなければ理由 (日本語 / 英語)、使えれば None。"""
    if not str(name).strip():
        return "パック名が空です / the pack name is empty"
    if has_control_characters(name):
        return "パック名に改行などの制御文字は使えません / the pack name must not contain control characters (newlines etc.)"
    if len(name) > MAX_PACK_NAME_LENGTH:
        return (f"パック名は {MAX_PACK_NAME_LENGTH} 文字以内にしてください / the pack name must be at most "
                f"{MAX_PACK_NAME_LENGTH} characters")
    return None


def shorten_id(base: str, limit: int = DEFAULT_ID_LENGTH) -> str:
    """ファイル名から作った ID を limit 文字以内に切る (後ろの _ は取る)。"""
    return base[:limit].rstrip("_") or "video"


def is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


ID_CHARS = re.compile(r"[a-z0-9_]+")
