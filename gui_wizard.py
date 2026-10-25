"""はじめてガイド (Windows 版の GUI): 動画を選んでからゲーム内で再生するまでを 1 つの画面で案内する。

  1. 環境の確認: FFmpeg・GPU・メモリ・AI の切り抜きに要るもの・保存先の空き・Minecraft を自動で調べる
  2. 動画を選ぶ: 縦横比に合わせた勧める設定と、できるアドオンの大きさの見込みを出す
  3. 作る: メインの画面の「.mcaddon を作成」と同じ
  4. できたら: 保存先を開く・Minecraft に読み込む・ゲーム内で再生するコマンド

画面を持たない計算 (環境の確認・大きさの見込み・勧める設定・案内の文) は、テストのため関数に分けてある。
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# アドオンの大きさの見込み: 1 マス 1 フレームあたりのデータ (base64 の文字数)。評価用の実写・ジャンル 40 本 (Blue Noise、
# 自動パレット) の 2 割点・中央・9 割点。立体 (2.5D) は色と高さの 2 枚分
SIZE_BYTES_PER_CELL_FRAME = (0.06, 0.15, 0.30)
# .mcaddon (zip) にすると base64 の文字は約 0.76 倍になる。音声は 192 kbps の ogg (1 秒 24 KB)。スクリプトなど約 40 KB
ZIP_RATIO = 0.76
AUDIO_BYTES_PER_SECOND = 24_000
ADDON_OVERHEAD_BYTES = 40_000
# 勧める設定: 長い辺 128 ブロック・10 fps。長い動画 (5 分を超える) は 5 fps
RECOMMENDED_LONG_SIDE = 128
RECOMMENDED_FPS = 10
LONG_VIDEO_SECONDS = 300
LONG_VIDEO_FPS = 5
# 保存先の空きがこれより少なければ注意を出す
MIN_FREE_DISK = 1 << 30

_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_video_duration(path, ffmpeg):
    """動画の長さ (秒) を ffmpeg の情報表示から読む。読めなければ None。"""
    if not ffmpeg:
        return None
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _DURATION_PATTERN.search(result.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def estimate_addon_size(duration, width, height, fps, volume=False, audio=True):
    """できるアドオンの大きさの見込み (少なめ, ふつう, 多め) のバイト数。映像の動きの多さで大きく変わる。"""
    cells = width * height * (2 if volume else 1)
    frames = max(1, round(duration * fps))
    fixed = ADDON_OVERHEAD_BYTES + (AUDIO_BYTES_PER_SECOND * duration if audio else 0)
    return tuple(int(cells * frames * rate * ZIP_RATIO + fixed) for rate in SIZE_BYTES_PER_CELL_FRAME)


def recommend_settings(source_width, source_height, duration, suggest_size):
    """勧める設定 {"width", "height", "fps"}。suggest_size は GUI の縦横比に合わせる関数。"""
    width, height = suggest_size(source_width, source_height, RECOMMENDED_LONG_SIDE)
    fps = LONG_VIDEO_FPS if duration and duration > LONG_VIDEO_SECONDS else RECOMMENDED_FPS
    return {"width": width, "height": height, "fps": fps}


def format_size(size):
    """バイト数を「1.2 MB」のように。"""
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    return f"{max(1, size >> 10)} KB"


def minecraft_installed():
    """統合版の Minecraft のデータのフォルダがあるか (Windows のみ。分からなければ None)。"""
    if sys.platform != "win32":
        return None
    roaming = Path(os.environ.get("APPDATA", ""))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    if (roaming / "Minecraft Bedrock").is_dir():
        return True
    packages = local / "Packages"
    return packages.is_dir() and any(packages.glob("Microsoft.MinecraftUWP_*"))


def _gpu_names():
    """AI と変換に使える GPU の説明 (名前の一覧)。"""
    names = []
    try:
        import onnxruntime

        providers = onnxruntime.get_available_providers()
        if "DmlExecutionProvider" in providers:
            names.append("DirectML (onnxruntime)")
        if "CUDAExecutionProvider" in providers:
            names.append("CUDA (onnxruntime)")
    except Exception:  # noqa: BLE001 (入っていない・壊れているときは何も足さない)
        pass
    if importlib.util.find_spec("torch_directml") is not None:
        try:
            import torch_directml

            if torch_directml.is_available():
                names.append(f"DirectML: {torch_directml.device_name(0)}")
        except Exception:  # noqa: BLE001
            pass
    if importlib.util.find_spec("torch") is not None:
        try:
            import torch

            if torch.cuda.is_available():
                names.append(f"CUDA: {torch.cuda.get_device_name(0)}")
        except Exception:  # noqa: BLE001
            pass
    return names


def environment_checks(ffmpeg, output_path):
    """環境の確認の結果の一覧。各項目は {"key", "status" ("ok" / "warn" / "error"), "detail"}。
    key は表示名の翻訳キー (wizard.check.<key>)。重い確認 (GPU・PyTorch の読み込み) を含むので、画面とは別のスレッドで呼ぶ。"""
    from mvcodec.resources import GIB, gpu_memory_total, system_memory

    checks = []
    if ffmpeg:
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=15,
                                   **kwargs).stdout.splitlines()[0]
            checks.append({"key": "ffmpeg", "status": "ok", "detail": first.split(" Copyright")[0][:60]})
        except (OSError, subprocess.SubprocessError, IndexError):
            checks.append({"key": "ffmpeg", "status": "error", "detail": ffmpeg})
    else:
        checks.append({"key": "ffmpeg", "status": "error", "detail": ""})
    gpus = _gpu_names()
    checks.append({"key": "gpu", "status": "ok" if gpus else "warn", "detail": ", ".join(gpus)})
    ram = system_memory()
    vram = gpu_memory_total()
    detail = (f"RAM {ram[0] / GIB:.0f} GB ({ram[1] / GIB:.1f} GB free)" if ram else "RAM ?")
    detail += f", VRAM {vram / GIB:.0f} GB" if vram else ""
    checks.append({"key": "memory", "status": "warn" if ram and ram[0] < 8 * GIB else "ok", "detail": detail})
    photo = all(importlib.util.find_spec(name) is not None for name in ("torch", "torchvision"))
    checks.append({"key": "ai_photo", "status": "ok" if photo else "warn", "detail": "torch, torchvision"})
    anime = importlib.util.find_spec("onnxruntime") is not None
    checks.append({"key": "ai_anime", "status": "ok" if anime else "warn", "detail": "onnxruntime"})
    folder = Path(output_path).parent
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    try:
        free = shutil.disk_usage(folder).free
        checks.append({"key": "disk", "status": "ok" if free >= MIN_FREE_DISK else "warn",
                       "detail": f"{folder}: {free / GIB:.1f} GB free"})
    except OSError:
        checks.append({"key": "disk", "status": "warn", "detail": str(folder)})
    installed = minecraft_installed()
    checks.append({"key": "minecraft", "status": "ok" if installed else "warn",
                   "detail": "" if installed is not None else "?"})
    return checks


def play_commands(namespace, video_ids):
    """ゲーム内で打つコマンドの一覧 (準備 → 再生 → 止める)。"""
    commands = [f"/scriptevent {namespace}:setup"]
    commands += [f"/scriptevent {namespace}:play {video_id}" for video_id in video_ids[:3]]
    commands.append(f"/scriptevent {namespace}:stop")
    return commands


def open_folder(path):
    """ファイルのあるフォルダを開き、そのファイルを選んだ状態にする (Windows)。"""
    path = Path(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def import_to_minecraft(path):
    """.mcaddon を開く (Windows では Minecraft が読み込む)。開けなければ False。"""
    if sys.platform != "win32":
        return False
    try:
        os.startfile(str(path))  # noqa: S606 (利用者が作ったアドオンを、関連付けられた Minecraft で開く)
        return True
    except OSError:
        return False


class SetupWizard:
    """はじめてガイドの画面。app は PackBuilderApp (動画リスト・設定・作成をそちらに任せる)。"""

    STATUS_MARK = {"ok": "✔", "warn": "⚠", "error": "✖"}

    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        tr = app.tr
        self.window = tk.Toplevel(app)
        self.window.title(tr("wizard.title"))
        self.window.minsize(680, 720)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(4, weight=1)
        self.video_path = None
        self.recommended = None
        pad = {"padx": 10, "pady": 6, "sticky": "ew"}

        ttk.Label(self.window, text=tr("wizard.intro"), wraplength=600).grid(row=0, column=0, **pad)

        # 1. 環境の確認
        env = ttk.LabelFrame(self.window, text=tr("wizard.step_env"))
        env.grid(row=1, column=0, **pad)
        env.columnconfigure(0, weight=1)
        self.env_text = self._text_box(env, height=10)
        ttk.Button(env, text=tr("wizard.recheck"), command=self.run_checks).grid(row=1, column=0, sticky="w", padx=6,
                                                                                pady=(0, 6))

        # 2. 動画を選ぶ
        video = ttk.LabelFrame(self.window, text=tr("wizard.step_video"))
        video.grid(row=2, column=0, **pad)
        video.columnconfigure(1, weight=1)
        ttk.Button(video, text=tr("wizard.choose_video"), command=self.choose_video).grid(row=0, column=0, padx=6,
                                                                                         pady=6)
        self.video_label = ttk.Label(video, text=tr("wizard.no_video"), wraplength=440)
        self.video_label.grid(row=0, column=1, sticky="w", padx=6)
        self.recommend_label = ttk.Label(video, text="", wraplength=600)
        self.recommend_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))

        # 3. 作る
        build = ttk.LabelFrame(self.window, text=tr("wizard.step_build"))
        build.grid(row=3, column=0, **pad)
        self.build_button = ttk.Button(build, text=tr("build.button"), command=self.build, state="disabled")
        self.build_button.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.build_label = ttk.Label(build, text=tr("wizard.build_hint"), wraplength=480)
        self.build_label.grid(row=0, column=1, sticky="w", padx=6)

        # 4. できたら
        done = ttk.LabelFrame(self.window, text=tr("wizard.step_play"))
        done.grid(row=4, column=0, padx=10, pady=6, sticky="nsew")
        done.columnconfigure(0, weight=1)
        done.rowconfigure(1, weight=1)
        buttons = ttk.Frame(done)
        buttons.grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.folder_button = ttk.Button(buttons, text=tr("after.open_folder"), command=self.open_output_folder,
                                        state="disabled")
        self.folder_button.grid(row=0, column=0, padx=(0, 6))
        self.import_button = ttk.Button(buttons, text=tr("after.import"), command=self.import_output, state="disabled")
        self.import_button.grid(row=0, column=1)
        self.guide_text = self._text_box(done, height=11, row=1)
        self._set_text(self.guide_text, tr("wizard.play_wait"))

        ttk.Button(self.window, text=tr("wizard.close"), command=self.window.destroy).grid(row=5, column=0, pady=8)
        self.run_checks()

    # --- 1. 環境の確認 ---
    def run_checks(self):
        """環境の確認を別のスレッドで行い、結果を待つ (Tk の画面は作ったスレッドからしか触らない)。"""
        import queue
        import threading

        self._set_text(self.env_text, self.app.tr("wizard.checking"))
        ffmpeg = self.app.ffmpeg_path()
        output = self.app.output_var.get()
        results = queue.Queue()
        threading.Thread(target=lambda: results.put(environment_checks(ffmpeg, output)), daemon=True).start()
        self._wait_checks(results)

    def _wait_checks(self, results):
        import queue

        if not self.window.winfo_exists():
            return
        try:
            checks = results.get_nowait()
        except queue.Empty:
            self.window.after(200, lambda: self._wait_checks(results))
            return
        self._show_checks(checks)

    def _show_checks(self, checks):
        tr = self.app.tr
        lines = []
        for check in checks:
            line = f"{self.STATUS_MARK[check['status']]} {tr('wizard.check.' + check['key'])}"
            if check["detail"]:
                line += f": {check['detail']}"
            if check["status"] != "ok":
                line += "\n    " + tr(f"wizard.fix.{check['key']}")
            lines.append(line)
        self._set_text(self.env_text, "\n".join(lines))

    # --- 2. 動画を選ぶ ---
    def choose_video(self):
        from tkinter import filedialog

        tr = self.app.tr
        path = filedialog.askopenfilename(parent=self.window, title=tr("dialog.select_videos_title"),
                                          filetypes=[(tr("dialog.video_files"), "*.mp4 *.mkv *.avi *.mov *.webm"),
                                                     (tr("dialog.all_files"), "*.*")])
        if not path:
            return
        self.video_path = path
        self.app.add_video_path(path)
        self.video_label.configure(text=Path(path).name)
        ffmpeg = self.app.ffmpeg_path()
        size = self.app.probe_size(path)
        duration = probe_video_duration(path, ffmpeg)
        if size is None or duration is None:
            self.recommend_label.configure(text=tr("wizard.probe_failed"))
            self.build_button.configure(state="normal")
            return
        self.recommended = recommend_settings(size[0], size[1], duration, self.app.suggest_size)
        self.app.apply_recommended(self.recommended)
        low, typical, high = estimate_addon_size(duration, self.recommended["width"], self.recommended["height"],
                                                 self.recommended["fps"])
        self.recommend_label.configure(text=tr(
            "wizard.recommended", src_w=size[0], src_h=size[1], seconds=round(duration),
            width=self.recommended["width"], height=self.recommended["height"], fps=self.recommended["fps"],
            typical=format_size(typical), low=format_size(low), high=format_size(high)))
        self.build_button.configure(state="normal")

    # --- 3. 作る ---
    def build(self):
        self.build_label.configure(text=self.app.tr("wizard.building"))
        self.app.start_build_from_wizard()

    # --- 4. できたら ---
    def build_finished(self, output, ok):
        if not self.window.winfo_exists():
            return
        tr = self.app.tr
        if not ok:
            self.build_label.configure(text=tr("wizard.build_failed"))
            return
        self.build_label.configure(text=tr("wizard.build_done", output=output))
        self.folder_button.configure(state="normal")
        self.import_button.configure(state="normal")
        self._set_text(self.guide_text, self.app.play_guide_text())

    def open_output_folder(self):
        self.app.open_output_folder()

    def import_output(self):
        self.app.import_output()

    @staticmethod
    def _text_box(parent, height, row=0):
        """読むだけの文の欄 (スクロールでき、日本語は文字ごとに折り返す)。"""
        import tkinter as tk
        from tkinter import ttk

        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="nsew", padx=6, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, height=height, wrap="char", state="disabled", font="TkDefaultFont",
                       relief="flat", background="#f7f7f7")
        text.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        bar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=bar.set)
        return text

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")
