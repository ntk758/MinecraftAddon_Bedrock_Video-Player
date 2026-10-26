"""PC のメモリ (RAM) と GPU のメモリ (VRAM) の空きを調べる (AI の人物の切り抜きの重さを決めるため)。

追加のライブラリは使わない:
  - RAM: Windows の GlobalMemoryStatusEx (それ以外の OS では /proc/meminfo か os.sysconf)
  - VRAM の大きさ: Windows のレジストリ (ディスプレイ アダプターの HardwareInformation.qwMemorySize、64 ビットの値。
    WMI の AdapterRAM は 4 GB で頭打ちになるので使わない)
  - VRAM の使用量: Windows の性能カウンター「GPU Adapter Memory / Dedicated Usage」(PowerShell の Get-Counter)
調べられないときは None を返す (呼び出し側は「分からない」として扱う)。

ほかに use_utf8_for_pipes: GUI からパイプ経由で起動される子プロセス (convert.py・人物の切り抜き) が日本語の出力を UTF-8 にする。
"""
import ctypes
import os
import subprocess
import sys

GIB = 1 << 30
# ディスプレイ アダプターのクラスのレジストリ キー
_DISPLAY_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _subprocess_kwargs():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def use_utf8_for_pipes():
    """GUI などからパイプ経由で起動された時、ログの日本語を UTF-8 で書き出す (cp932 で化けるのを防ぐ)。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            try:
                # 行ごとに書き出す (GUI のログに進み具合がすぐ出るように。既定ではパイプ先へはまとめて書かれる)
                stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except (OSError, ValueError):  # 読み出しの後など、設定を変えられない流れはそのまま使う
                pass


def system_memory():
    """(全体, 空き) のバイト数。分からなければ None。"""
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys), int(status.ullAvailPhys)
        return None
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                key, value = line.split(":", 1)
                info[key] = int(value.split()[0]) * 1024
        return info["MemTotal"], info.get("MemAvailable", info["MemFree"])
    except (OSError, KeyError, ValueError):
        pass
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        return page * os.sysconf("SC_PHYS_PAGES"), page * os.sysconf("SC_AVPHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def gpu_memory_total():
    """一番大きい GPU の専用メモリ (VRAM) のバイト数。Windows 以外や、分からなければ None。"""
    if sys.platform != "win32":
        return None
    import winreg

    best = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY) as root:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                if not name.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, name) as adapter:
                        value, _kind = winreg.QueryValueEx(adapter, "HardwareInformation.qwMemorySize")
                except OSError:
                    continue
                size = int.from_bytes(value, "little") if isinstance(value, bytes) else int(value)
                if size > 0 and (best is None or size > best):
                    best = size
    except OSError:
        return None
    return best


def gpu_memory_used(timeout=8):
    """GPU の専用メモリの使用量 (全部のプロセスの合計、一番使っている GPU) のバイト数。分からなければ None。"""
    if sys.platform != "win32":
        return None
    command = ("(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction Stop).CounterSamples | "
               "ForEach-Object { [int64]$_.CookedValue }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, timeout=timeout, **_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    values = [int(line) for line in result.stdout.split() if line.strip().lstrip("-").isdigit()]
    return max(values) if values else None


def gpu_memory():
    """(全体, 空き) のバイト数。どちらかが分からなければ None。"""
    total = gpu_memory_total()
    if total is None:
        return None
    used = gpu_memory_used()
    if used is None:
        return None
    return total, max(0, total - used)
