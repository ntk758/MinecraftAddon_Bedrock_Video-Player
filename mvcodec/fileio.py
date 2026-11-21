"""ファイルの書き方と、途中で止まった変換の後片付け。

- write_file_atomic: 同じフォルダの一時ファイル (.<名前>.<pid>.part) に書き終えてから置き換える。途中で失敗しても前の
  ファイルは壊れない (#164)
- 強制終了 (タスクマネージャーで終了・SIGKILL) された変換は、一時ファイルと作業フォルダを残す。次に同じ所へ書くとき・
  次の変換を始めるときに、作ったプロセスがもう無いものを消す (1 GB 近い .part が残り続けていた。#180)
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# 前の版の作業フォルダ (名前に pid が無い) は、この秒数より古ければ、止まった変換が残したものとして消す
OLD_TEMP_SECONDS = 24 * 3600


def pid_alive(pid: int) -> bool:
    """そのプロセスがまだ動いているか。分からなければ True (消さない側に倒す)。"""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: 動いているが見られない
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, AttributeError):
        return True
    return True


def remove_stale_parts(path) -> list[Path]:
    """path の一時ファイル (.<名前>.<pid>.part) のうち、作ったプロセスがもう無いものを消す。消したものを返す。"""
    path = Path(path)
    removed = []
    pattern = re.compile(rf"\.{re.escape(path.name)}\.(\d+)\.part")
    try:
        candidates = list(path.parent.iterdir())
    except OSError:
        return removed
    for candidate in candidates:
        match = pattern.fullmatch(candidate.name)
        if match and not pid_alive(int(match.group(1))):
            try:
                candidate.unlink()
                removed.append(candidate)
            except OSError:
                pass
    return removed


def write_file_atomic(path, data: bytes) -> Path:
    """data を path に書く。同じフォルダの一時ファイルに書き終えてから置き換えるので、途中で失敗 (ディスクがいっぱいなど) しても
    前からあるファイルは壊れず、書きかけのファイルも残らない。強制終了された前の変換の一時ファイルがあれば消す。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    remove_stale_parts(path)
    temp = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with open(temp, "wb") as out:
            out.write(data)
            out.flush()
            try:
                os.fsync(out.fileno())
            except (OSError, AttributeError):  # fsync の無いファイルシステム (ブラウザ版の Pyodide のメモリなど)
                pass
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return path


def write_text_atomic(path, text: str) -> Path:
    return write_file_atomic(path, text.encode("utf-8"))


def sweep_temp_dirs(prefix: str, folder=None) -> list[Path]:
    """一時フォルダ (folder、既定は OS の一時フォルダ) の <prefix><pid>-* のうち、作ったプロセスがもう無いもの、
    名前に pid の無い前の版の <prefix>* で OLD_TEMP_SECONDS より古いものを消す。消したものを返す。"""
    folder = Path(folder or tempfile.gettempdir())
    removed = []
    pattern = re.compile(rf"{re.escape(prefix)}(\d+)-.+")
    now = time.time()
    try:
        candidates = [p for p in folder.iterdir() if p.name.startswith(prefix) and p.is_dir()]
    except OSError:
        return removed
    for candidate in candidates:
        match = pattern.fullmatch(candidate.name)
        if match:
            stale = not pid_alive(int(match.group(1)))
        else:
            try:
                stale = now - candidate.stat().st_mtime > OLD_TEMP_SECONDS
            except OSError:
                stale = False
        if stale:
            shutil.rmtree(candidate, ignore_errors=True)
            if not candidate.exists():
                removed.append(candidate)
    return removed


@contextmanager
def work_dir(prefix: str):
    """作業用の一時フォルダ (<prefix><pid>-xxxx)。作る前に、止まった変換が残した同じ種類のフォルダを片付ける。"""
    sweep_temp_dirs(prefix)
    with tempfile.TemporaryDirectory(prefix=f"{prefix}{os.getpid()}-") as folder:
        yield folder
