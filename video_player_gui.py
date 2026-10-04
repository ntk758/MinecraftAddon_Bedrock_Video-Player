"""動画から Minecraft Bedrock 用の .mcaddon を作成する GUI。複数動画搭載・音声同期・多言語対応。"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gui_i18n import LOCALES, Translator, available_languages, initial_language, load_settings, save_settings
from pack_metadata import PACK_VERSION, RELEASE_NOTES, changelog_markdown, manifest_description, version_text


APP_DIR = Path(__file__).resolve().parent
MANIFEST = APP_DIR / "manifest.json"
MAIN_SCRIPT = APP_DIR / "main.js"
CODEC_SCRIPT = APP_DIR / "codec.js"
CONVERTER = APP_DIR / "convert.py"
# main.js の AUDIO_CHUNK_SECONDS と一致させること
AUDIO_CHUNK_SECONDS = 10
# EXE 版で自分自身を変換器として起動するための引数
RUN_CONVERTER_FLAG = "--run-converter"
# 再ビルドしたパックをワールドが「新しい版」と認識できるよう、ビルド時刻をパッチ番号に使う
BUILD_STAMP_EPOCH = 1704067200  # 2024-01-01T00:00:00Z


def subprocess_kwargs() -> dict:
    """EXE (windowed) から ffmpeg 等を起動する時にコンソール窓を出さない。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def converter_command() -> list[str]:
    """convert.py を起動するコマンドの先頭部分。EXE 版では自分自身を変換モードで起動する。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_CONVERTER_FLAG]
    return [sys.executable, str(CONVERTER)]


def stable_pack_uuid(namespace: str, pack_name: str, role: str) -> str:
    """同じパック名・接頭辞なら毎回同じ UUID にする (再ビルドしても別パック扱いにならない)。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"block-video-player/{namespace}/{pack_name}/{role}"))


def build_manifest_version(now: float | None = None) -> list[int]:
    """[major, minor, ビルド時刻(分)]。UUID が同じでも新しいビルドとして読み込ませるため。"""
    now = time.time() if now is None else now
    return [PACK_VERSION[0], PACK_VERSION[1], max(0, int(now - BUILD_STAMP_EPOCH) // 60)]


def get_ffmpeg_path() -> str | None:
    # 1. PyInstaller 内部展開ディレクトリ
    if hasattr(sys, "_MEIPASS"):
        meipass_ffmpeg = Path(getattr(sys, "_MEIPASS")) / "ffmpeg.exe"
        if meipass_ffmpeg.is_file():
            return str(meipass_ffmpeg)
    
    # 2. アプリ実行ファイルと同階層
    app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else APP_DIR
    for name in ("ffmpeg.exe", "ffmpeg"):
        local_ffmpeg = app_dir / name
        if local_ffmpeg.is_file():
            return str(local_ffmpeg)

    # 3. カレントディレクトリ
    for name in ("ffmpeg.exe", "ffmpeg"):
        cwd_ffmpeg = Path.cwd() / name
        if cwd_ffmpeg.is_file():
            return str(cwd_ffmpeg)

    # 4. システム PATH
    return shutil.which("ffmpeg")


# 選択肢の値 (convert.py に渡す値)。表示名は locales/*.json の "<種類>.<値>" キー
PALETTE_VALUES = ("auto", "full", "expanded", "concrete")
DITHER_VALUES = ("none", "blue_noise", "ordered", "floyd", "atkinson", "burkes", "sierra")
DEVICE_VALUES = ("auto", "cuda", "directml", "cpu")
# 画質プリセット: キー -> (幅, 高さ, 再生間隔 tick)
QUALITY_PRESETS: dict[str, tuple[int, int, int]] = {
    "light": (64, 64, 1),
    "standard": (96, 96, 2),
    "high": (128, 128, 2),
    "detail": (128, 128, 4),
    "ultra": (256, 256, 4),
    "extreme": (512, 512, 10),
}
DEFAULT_PRESET = "high"
DEFAULT_PALETTE = "expanded"
DEFAULT_DITHER = "blue_noise"
DEFAULT_DEVICE = "auto"


def option_labels(tr: Translator, kind: str, values) -> dict[str, str]:
    """{表示名: 値} (コンボボックス用、表示順を保つ)"""
    return {tr(f"{kind}.{value}"): value for value in values}


def _all_language_labels(kind: str, values) -> dict[str, str]:
    labels = {}
    for language in LOCALES:
        labels.update(option_labels(Translator(language), kind, values))
    return labels


# 日本語の表示名 -> 値 (互換用)
PALETTE_OPTIONS: dict[str, str] = option_labels(Translator("ja"), "palette", PALETTE_VALUES)
DITHER_OPTIONS: dict[str, str] = option_labels(Translator("ja"), "dither", DITHER_VALUES)
DEVICE_OPTIONS: dict[str, str] = option_labels(Translator("ja"), "device", DEVICE_VALUES)


def resolve_device(device_text: str) -> str:
    """デバイス表示文字列 (どの言語でも可) から convert.py 用の値を判定する。"""
    return _all_language_labels("device", DEVICE_VALUES).get(device_text, "auto")


def build_converter_args(
    video: Path, output: Path, ffmpeg: str, width: int, height: int, interval: int,
    palette: str, dither_method: str, keyframe_interval: int, device: str,
    perceptual: bool, duration: float | None,
) -> list[str]:
    """convert.py に渡す引数 (起動コマンド部分を除く)。"""
    args = [
        "--input-video", str(video),
        "--ffmpeg", ffmpeg,
        "--fps", str(20 / interval),
        "--output", str(output), "--width", str(width), "--height", str(height),
        "--palette", palette,
        "--dither-method", dither_method,
        "--keyframe-interval", str(keyframe_interval),
        "--device", device,
    ]
    if not perceptual:
        args.append("--no-perceptual")
    if duration is not None:
        args.extend(["--duration", str(duration)])
    return args


def resolve_palette(pal_text: str) -> str:
    """パレット表示文字列 (どの言語でも可) から convert.py 用の引数名を判定する。"""
    labels = _all_language_labels("palette", PALETTE_VALUES)
    if pal_text in labels:
        return labels[pal_text]
    if pal_text.startswith("自動"):
        return "auto"
    elif pal_text.startswith("全39"):
        return "full"
    elif pal_text.startswith("拡張"):
        return "expanded"
    else:
        return "concrete"


def resolve_dither(dither_text: str) -> str:
    """ディザ表示文字列 (どの言語でも可) から convert.py 用の引数名を判定する。"""
    labels = _all_language_labels("dither", DITHER_VALUES)
    if dither_text in labels:
        return labels[dither_text]
    if dither_text.startswith("Floyd"):
        return "floyd"
    elif dither_text.startswith("Atkinson"):
        return "atkinson"
    elif dither_text.startswith("Burkes"):
        return "burkes"
    elif dither_text.startswith("Sierra"):
        return "sierra"
    elif dither_text.startswith("Ordered"):
        return "ordered"
    elif dither_text.startswith("Blue Noise"):
        return "blue_noise"
    else:
        return "none"


class PackBuilderApp(tk.Tk):
    def __init__(self, language: str | None = None) -> None:
        super().__init__()
        self.settings = load_settings()
        self.tr = Translator(language or initial_language(self.settings))
        self.title(self.tr("app.title"))
        self.minsize(780, 700)
        self.columnconfigure(0, weight=1)
        self.messages = queue.Queue()
        self.building = False

        self.output_var = tk.StringVar(value=str(APP_DIR / "VideoPlayer.mcaddon"))
        self.pack_name_var = tk.StringVar(value="Block Video Player")
        self.namespace_var = tk.StringVar(value="badapple")
        self.width_var = tk.IntVar(value=64)
        self.height_var = tk.IntVar(value=64)
        self.interval_var = tk.IntVar(value=1)
        self.duration_var = tk.StringVar(value="")
        self.keyframe_interval_var = tk.IntVar(value=30)
        self.perceptual_var = tk.BooleanVar(value=True)
        # コンボボックスは表示名を持つので、言語切替時に値から表示名を作り直す
        self.language_var = tk.StringVar()
        self.quality_var = tk.StringVar()
        self.palette_var = tk.StringVar()
        self.dither_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.preset_key = DEFAULT_PRESET
        self._set_option_labels(DEFAULT_PALETTE, DEFAULT_DITHER, DEFAULT_DEVICE)
        self.namespace_var.trace_add("write", lambda *_: self._update_command_hint())

        self._build_ui()
        self._apply_quality_preset()
        self.after(100, self._drain_messages)

    # --- 言語 ---
    def _preset_labels(self) -> dict[str, str]:
        return {self.tr(f"preset.{key}"): key for key in QUALITY_PRESETS}

    def _selected_values(self) -> tuple[str, str, str]:
        """現在の言語の表示名から (パレット, ディザ, デバイス) の値を得る。"""
        palette = option_labels(self.tr, "palette", PALETTE_VALUES).get(self.palette_var.get(), DEFAULT_PALETTE)
        dither = option_labels(self.tr, "dither", DITHER_VALUES).get(self.dither_var.get(), DEFAULT_DITHER)
        device = option_labels(self.tr, "device", DEVICE_VALUES).get(self.device_var.get(), DEFAULT_DEVICE)
        return palette, dither, device

    def _set_option_labels(self, palette: str, dither: str, device: str) -> None:
        self.palette_var.set(self.tr(f"palette.{palette}"))
        self.dither_var.set(self.tr(f"dither.{dither}"))
        self.device_var.set(self.tr(f"device.{device}"))
        self.quality_var.set(self.tr(f"preset.{self.preset_key}"))
        self.language_var.set(dict(available_languages())[self.tr.language])

    def change_language(self, language: str) -> None:
        """表示言語を切り替える。入力内容・動画リスト・ログは引き継ぐ。"""
        if language == self.tr.language or self.building:
            return
        values = self._selected_values()
        videos = [self.video_tree.item(iid)["values"] for iid in self.video_tree.get_children()]
        log_text = self.log.get("1.0", "end-1c")

        self.tr = Translator(language)
        self._set_option_labels(*values)
        for child in self.winfo_children():
            child.destroy()
        self._build_ui()
        for row in videos:
            self.video_tree.insert("", "end", values=row)
        if log_text:
            self._append_log(log_text)
        self.title(self.tr("app.title"))

        self.settings["language"] = language
        save_settings(self.settings)

    def _on_language_selected(self) -> None:
        names = {name: code for code, name in available_languages()}
        self.change_language(names.get(self.language_var.get(), self.tr.language))

    def _build_ui(self) -> None:
        tr = self.tr
        pad = {"padx": 10, "pady": 4}

        # --- 言語選択 ---
        top_bar = ttk.Frame(self)
        top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        top_bar.columnconfigure(0, weight=1)
        ttk.Label(top_bar, text=tr("language.label")).grid(row=0, column=1, padx=(0, 6))
        language_box = ttk.Combobox(
            top_bar, textvariable=self.language_var, state="readonly", width=20,
            values=tuple(name for _code, name in available_languages()),
        )
        language_box.grid(row=0, column=2)
        language_box.bind("<<ComboboxSelected>>", lambda _event: self._on_language_selected())
        self.language_box = language_box

        # --- 動画リストセクション ---
        video_frame = ttk.LabelFrame(self, text=tr("videos.frame"))
        video_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(6, 4))
        video_frame.columnconfigure(0, weight=1)
        video_frame.rowconfigure(0, weight=1)

        cols = ("video_id", "file_path", "thumb_sec")
        self.video_tree = ttk.Treeview(video_frame, columns=cols, show="headings", height=5)
        self.video_tree.heading("video_id", text=tr("videos.col_id"))
        self.video_tree.heading("file_path", text=tr("videos.col_path"))
        self.video_tree.heading("thumb_sec", text=tr("videos.col_thumb"))
        self.video_tree.column("video_id", width=120, minwidth=80)
        self.video_tree.column("file_path", width=400, minwidth=200)
        self.video_tree.column("thumb_sec", width=100, minwidth=60)
        self.video_tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=6)

        tree_scroll = ttk.Scrollbar(video_frame, orient="vertical", command=self.video_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 10))
        self.video_tree.configure(yscrollcommand=tree_scroll.set)

        btn_frame = ttk.Frame(video_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))
        ttk.Button(btn_frame, text=tr("videos.add"), command=self._add_videos).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text=tr("videos.remove"), command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(btn_frame, text=tr("videos.edit_id"), command=self._edit_video_id).pack(side="left", padx=4)
        ttk.Button(btn_frame, text=tr("videos.edit_thumb"), command=self._edit_thumb_sec).pack(side="left", padx=4)

        # --- 出力先 ---
        out_frame = ttk.Frame(self)
        out_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        out_frame.columnconfigure(1, weight=1)
        ttk.Label(out_frame, text=tr("output.label")).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(out_frame, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(out_frame, text=tr("output.browse"), command=self._select_output).grid(row=0, column=2)

        # --- パック基本設定 ---
        pack_settings = ttk.LabelFrame(self, text=tr("pack.frame"))
        pack_settings.grid(row=3, column=0, sticky="ew", padx=10, pady=4)
        pack_settings.columnconfigure(1, weight=1)
        ttk.Label(pack_settings, text=tr("pack.name")).grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        ttk.Entry(pack_settings, textvariable=self.pack_name_var).grid(row=0, column=1, padx=(0, 10), pady=6, sticky="ew")
        ttk.Label(pack_settings, text=tr("pack.namespace")).grid(row=1, column=0, padx=(10, 4), pady=(0, 6), sticky="w")
        ttk.Entry(pack_settings, textvariable=self.namespace_var).grid(row=1, column=1, padx=(0, 10), pady=(0, 6), sticky="ew")
        self.command_hint = ttk.Label(pack_settings, text="", foreground="#555555")
        self.command_hint.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 6), sticky="w")
        self._update_command_hint()

        # --- 変換・再生設定 ---
        settings = ttk.LabelFrame(self, text=tr("settings.frame"))
        settings.grid(row=4, column=0, sticky="ew", padx=10, pady=4)
        for index, (key, variable, maximum) in enumerate((
            ("settings.width", self.width_var, 512),
            ("settings.height", self.height_var, 512),
            ("settings.interval", self.interval_var, 20),
        )):
            ttk.Label(settings, text=tr(key)).grid(row=0, column=index * 2, padx=(10, 4), pady=6)
            ttk.Spinbox(settings, from_=1, to=maximum, textvariable=variable, width=7).grid(
                row=0, column=index * 2 + 1, padx=(0, 10), pady=6
            )
        ttk.Label(settings, text=tr("settings.keyframe")).grid(row=0, column=6, padx=(10, 4), pady=6)
        ttk.Spinbox(settings, from_=0, to=300, textvariable=self.keyframe_interval_var, width=7).grid(row=0, column=7, padx=(0, 10), pady=6)

        ttk.Label(settings, text=tr("settings.preset")).grid(row=1, column=0, padx=(10, 4), pady=(0, 6))
        preset = ttk.Combobox(
            settings, textvariable=self.quality_var, state="readonly", width=28,
            values=tuple(self._preset_labels()),
        )
        preset.grid(row=1, column=1, columnspan=2, padx=(0, 10), pady=(0, 6), sticky="w")
        preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_quality_preset())

        # パレット選択
        ttk.Label(settings, text=tr("settings.palette")).grid(row=1, column=3, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.palette_var, state="readonly", width=46,
            values=tuple(option_labels(tr, "palette", PALETTE_VALUES)),
        ).grid(row=1, column=4, columnspan=4, padx=(0, 10), pady=(0, 6), sticky="w")

        # ディザリング選択 (全7種対応)
        ttk.Label(settings, text=tr("settings.dither")).grid(row=2, column=0, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.dither_var, state="readonly", width=46,
            values=tuple(option_labels(tr, "dither", DITHER_VALUES)),
        ).grid(row=2, column=1, columnspan=3, padx=(0, 10), pady=(0, 6), sticky="w")

        # 知覚最適化(エッジ減衰)オプション
        ttk.Checkbutton(
            settings, text=tr("settings.perceptual"), variable=self.perceptual_var
        ).grid(row=3, column=0, columnspan=3, padx=(10, 4), pady=(0, 6), sticky="w")

        # 変換デバイス (GPU: CUDA / ROCm / DirectML)
        ttk.Label(settings, text=tr("settings.device")).grid(row=3, column=3, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.device_var, state="readonly", width=46,
            values=tuple(option_labels(tr, "device", DEVICE_VALUES)),
        ).grid(row=3, column=4, columnspan=4, padx=(0, 10), pady=(0, 6), sticky="w")

        # --- 説明文 ---
        ttk.Label(self, text=tr("info.description"), wraplength=740).grid(row=5, column=0, sticky="w", **pad)

        version_info = tr("info.version", version=version_text())
        if tr.language == "ja":
            # リリースノートは日本語のみ
            version_info += "  |  " + " / ".join(RELEASE_NOTES[:2])
        ttk.Label(self, text=version_info, wraplength=740, foreground="#555555").grid(row=6, column=0, sticky="w", **pad)

        self.build_button = ttk.Button(self, text=tr("build.button"), command=self._start_build)
        self.build_button.grid(row=7, column=0, pady=6)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=8, column=0, sticky="ew", padx=10, pady=4)

        ttk.Label(self, text=tr("log.label")).grid(row=9, column=0, sticky="w", padx=10, pady=(4, 0))
        self.log = tk.Text(self, height=12, state="disabled", wrap="word")
        self.log.grid(row=10, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.rowconfigure(10, weight=1)

        if self.building:
            self.build_button.configure(state="disabled")
            language_box.configure(state="disabled")

    # --- 動画リスト操作 ---
    def _add_videos(self) -> None:
        paths = filedialog.askopenfilenames(
            title=self.tr("dialog.select_videos_title"),
            filetypes=[(self.tr("dialog.video_files"), "*.mp4 *.mkv *.avi *.mov *.webm"), (self.tr("dialog.all_files"), "*.*")],
        )
        for path in paths:
            p = Path(path)
            video_id = re.sub(r"[^a-z0-9_]", "_", p.stem.lower())
            existing_ids = [self.video_tree.item(iid)["values"][0] for iid in self.video_tree.get_children()]
            if video_id in existing_ids:
                suffix = 2
                while f"{video_id}_{suffix}" in existing_ids:
                    suffix += 1
                video_id = f"{video_id}_{suffix}"
            self.video_tree.insert("", "end", values=(video_id, str(p), "0"))

    def _remove_selected(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning(self.tr("warn.select_title"), self.tr("warn.select_remove"))
            return
        for iid in selected:
            self.video_tree.delete(iid)

    def _edit_video_id(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning(self.tr("warn.select_title"), self.tr("warn.select_edit_id"))
            return
        iid = selected[0]
        values = self.video_tree.item(iid)["values"]
        from tkinter import simpledialog
        new_id = simpledialog.askstring(
            self.tr("edit_id.title"), self.tr("edit_id.prompt", current=values[0]), initialvalue=values[0], parent=self
        )
        if new_id and re.fullmatch(r"[a-z0-9_]+", new_id):
            self.video_tree.item(iid, values=(new_id, values[1], values[2]))
        elif new_id:
            messagebox.showerror(self.tr("edit_id.invalid_title"), self.tr("edit_id.invalid"))

    def _edit_thumb_sec(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning(self.tr("warn.select_title"), self.tr("warn.select_edit_thumb"))
            return
        iid = selected[0]
        values = self.video_tree.item(iid)["values"]
        from tkinter import simpledialog
        new_sec = simpledialog.askstring(
            self.tr("edit_thumb.title"), self.tr("edit_thumb.prompt", current=values[2]), initialvalue=str(values[2]), parent=self
        )
        if new_sec is not None:
            try:
                float(new_sec)
                self.video_tree.item(iid, values=(values[0], values[1], new_sec))
            except ValueError:
                messagebox.showerror(self.tr("edit_thumb.invalid_title"), self.tr("edit_thumb.invalid"))

    def _update_command_hint(self) -> None:
        if not hasattr(self, "command_hint") or not self.command_hint.winfo_exists():
            return
        namespace = self.namespace_var.get().strip() or self.tr("pack.namespace_placeholder")
        self.command_hint.configure(text=self.tr("pack.hint", ns=namespace))

    def _apply_quality_preset(self) -> None:
        self.preset_key = self._preset_labels().get(self.quality_var.get(), self.preset_key)
        width, height, interval = QUALITY_PRESETS[self.preset_key]
        self.width_var.set(width)
        self.height_var.set(height)
        self.interval_var.set(interval)

    def _select_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title=self.tr("dialog.save_title"),
            defaultextension=".mcaddon",
            filetypes=[("Minecraft Addon", "*.mcaddon")],
        )
        if path:
            self.output_var.set(path)

    def _start_build(self) -> None:
        video_entries = []
        for iid in self.video_tree.get_children():
            vals = self.video_tree.item(iid)["values"]
            video_entries.append({
                "video_id": str(vals[0]),
                "file_path": str(vals[1]),
                "thumb_sec": str(vals[2]),
            })

        if not video_entries:
            messagebox.showerror(self.tr("error.no_videos_title"), self.tr("error.no_videos"))
            return

        output = Path(self.output_var.get().strip())
        if not output.name:
            messagebox.showerror(self.tr("error.no_output_title"), self.tr("error.no_output"))
            return
        if output.suffix.lower() != ".mcaddon":
            output = output.with_suffix(".mcaddon")
            self.output_var.set(str(output))

        for entry in video_entries:
            if not Path(entry["file_path"]).is_file():
                messagebox.showerror(self.tr("error.file_missing_title"), self.tr("error.file_missing", path=entry["file_path"]))
                return

        # 解像度
        width = self.width_var.get()
        height = self.height_var.get()

        try:
            interval = self.interval_var.get()
            duration = float(self.duration_var.get()) if self.duration_var.get().strip() else None
            if min(width, height, interval) < 1 or (duration is not None and duration <= 0):
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror(self.tr("error.settings_title"), self.tr("error.settings"))
            return

        pack_name = self.pack_name_var.get().strip()
        namespace = self.namespace_var.get().strip()

        palette, dither_method, device = self._selected_values()

        if not pack_name:
            messagebox.showerror(self.tr("error.pack_name_title"), self.tr("error.pack_name"))
            return
        if not re.fullmatch(r"[a-z0-9_.\-]+", namespace) or namespace == "minecraft":
            messagebox.showerror(self.tr("error.namespace_title"), self.tr("error.namespace"))
            return

        self.building = True
        self.build_button.configure(state="disabled")
        self.language_box.configure(state="disabled")
        self.progress.start(12)
        threading.Thread(
            target=self._build_pack,
            args=(video_entries, output, pack_name, namespace, width, height, interval, duration, palette, dither_method,
                  device),
            daemon=True,
        ).start()

    def _run(self, command: list[str]) -> None:
        self.messages.put("$ " + subprocess.list2cmdline(command))
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", **subprocess_kwargs(),
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.messages.put(line.rstrip())
        if process.wait() != 0:
            raise RuntimeError(self.tr("build.cmd_failed", code=process.returncode))

    def _build_pack(
        self, video_entries: list[dict], output: Path, pack_name: str, namespace: str,
        width: int, height: int, interval: int, duration: float | None, palette: str, dither_method: str,
        device: str,
    ) -> None:
        try:
            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                raise RuntimeError(self.tr("build.ffmpeg_missing"))
            required_files = [MANIFEST, MAIN_SCRIPT, CODEC_SCRIPT]
            if not getattr(sys, "frozen", False):
                required_files.append(CONVERTER)
            for required in required_files:
                if not required.is_file():
                    raise RuntimeError(self.tr("build.missing_file", path=required))

            with tempfile.TemporaryDirectory(prefix="block-video-player-") as temp_dir:
                temp = Path(temp_dir)
                bp_root = temp / "BP"
                rp_root = temp / "RP"
                scripts = bp_root / "scripts"
                scripts.mkdir(parents=True)
                rp_sounds = rp_root / "sounds"
                rp_sounds.mkdir(parents=True)

                video_index_entries = []
                first_thumbnail = None
                sound_definitions = {}

                total_videos = len(video_entries)
                for vi, entry in enumerate(video_entries, 1):
                    video = Path(entry["file_path"])
                    video_id = entry["video_id"]
                    thumb_sec = entry["thumb_sec"]

                    self.messages.put(self.tr("build.processing", index=vi, total=total_videos, id=video_id))

                    generated_data = scripts / f"frames_{video_id}.js"
                    thumbnail = temp / f"thumb_{video_id}.png"

                    # サムネイル生成
                    self.messages.put(self.tr("build.thumbnail", sec=thumb_sec))
                    self._run([
                        ffmpeg, "-y", "-ss", str(thumb_sec), "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=256:256:force_original_aspect_ratio=decrease,pad=256:256:(ow-iw)/2:(oh-ih)/2:black",
                        str(thumbnail),
                    ])
                    if not thumbnail.is_file() or thumbnail.stat().st_size == 0:
                        raise RuntimeError(self.tr("build.thumbnail_failed", id=video_id))

                    if first_thumbnail is None:
                        first_thumbnail = thumbnail

                    # 音声切り出し (10秒分割 .ogg, 44.1kHz ステレオ)
                    sounds_dir = rp_root / "sounds" / "music" / video_id
                    sounds_dir.mkdir(parents=True, exist_ok=True)
                    self.messages.put(self.tr("build.audio", sec=AUDIO_CHUNK_SECONDS))
                    try:
                        self._run([
                            ffmpeg, "-y", "-i", str(video),
                            "-f", "segment", "-segment_time", str(AUDIO_CHUNK_SECONDS),
                            "-vn", "-acodec", "libvorbis",
                            "-ar", "44100", "-ac", "2", "-b:a", "192k",
                            str(sounds_dir / "chunk_%d.ogg")
                        ])
                    except Exception as e:
                        self.messages.put(self.tr("build.audio_skipped", error=e))

                    ogg_files = sorted(sounds_dir.glob("chunk_*.ogg"), key=lambda p: int(p.stem.split("_")[1]))
                    for ogg_file in ogg_files:
                        chunk_idx = int(ogg_file.stem.split("_")[1])
                        sound_key = f"{namespace}.{video_id}.chunk_{chunk_idx}"
                        sound_definitions[sound_key] = {
                            "category": "ui",
                            "sounds": [
                                {
                                    "name": f"sounds/music/{video_id}/{ogg_file.stem}",
                                    "stream": True,
                                }
                            ]
                        }

                    # ブロックデータ変換
                    self.messages.put(self.tr("build.converting"))
                    converter_args = [
                        *converter_command(),
                        *build_converter_args(
                            video, generated_data, ffmpeg, width, height, interval, palette, dither_method,
                            self.keyframe_interval_var.get(), device, self.perceptual_var.get(), duration,
                        ),
                    ]
                    self._run(converter_args)

                    frame_count = 0
                    try:
                        js_text = generated_data.read_text(encoding="utf-8")
                        import re as _re
                        fc_match = _re.search(r'"frame_count":\s*(\d+)', js_text)
                        if fc_match:
                            frame_count = int(fc_match.group(1))
                    except Exception:
                        pass

                    video_index_entries.append({
                        "id": video_id,
                        "title": video.stem,
                        "frame_count": frame_count,
                        "width": width,
                        "height": height,
                    })

                # sound_definitions.json の生成
                if sound_definitions:
                    sound_def_file = rp_root / "sounds" / "sound_definitions.json"
                    sound_def_file.parent.mkdir(parents=True, exist_ok=True)
                    sound_def_file.write_text(
                        json.dumps({"format_version": "1.14.0", "sound_definitions": sound_definitions}, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )

                # videos.js 自動生成
                self.messages.put(self.tr("build.videos_index"))
                videos_js_lines = []
                for entry in video_index_entries:
                    vid = entry["id"]
                    videos_js_lines.append(f'import {{ FRAME_DATA as video_{vid} }} from "./frames_{vid}.js";')
                videos_js_lines.append("")
                videos_obj_entries = ", ".join(f'"{e["id"]}": video_{e["id"]}' for e in video_index_entries)
                videos_js_lines.append(f"export const VIDEOS = {{ {videos_obj_entries} }};")
                videos_js_lines.append("")
                video_list_json = json.dumps(video_index_entries, ensure_ascii=False, separators=(",", ":"))
                videos_js_lines.append(f"export const VIDEO_LIST = {video_list_json};")
                videos_js_lines.append("")
                (scripts / "videos.js").write_text("\n".join(videos_js_lines), encoding="utf-8")

                # manifest.json
                bp_uuid = stable_pack_uuid(namespace, pack_name, "bp")
                rp_uuid = stable_pack_uuid(namespace, pack_name, "rp")
                bp_mod_uuid = stable_pack_uuid(namespace, pack_name, "bp-script")
                rp_mod_uuid = stable_pack_uuid(namespace, pack_name, "rp-resources")
                manifest_version = build_manifest_version()

                # BP manifest
                bp_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
                bp_manifest["header"]["name"] = f"{pack_name} v{version_text()}"
                bp_manifest["header"]["description"] = manifest_description()
                bp_manifest["header"]["uuid"] = bp_uuid
                bp_manifest["header"]["version"] = manifest_version
                bp_manifest["modules"][0]["uuid"] = bp_mod_uuid
                bp_manifest["modules"][0]["version"] = manifest_version
                bp_manifest.setdefault("dependencies", []).append({"uuid": rp_uuid, "version": manifest_version})
                (bp_root / "manifest.json").write_text(
                    json.dumps(bp_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )

                # RP manifest
                rp_manifest = {
                    "format_version": 2,
                    "header": {
                        "name": f"{pack_name} Res v{version_text()}",
                        "description": manifest_description(),
                        "uuid": rp_uuid,
                        "version": manifest_version,
                        "min_engine_version": bp_manifest["header"]["min_engine_version"]
                    },
                    "modules": [
                        {
                            "description": "Resources",
                            "type": "resources",
                            "uuid": rp_mod_uuid,
                            "version": manifest_version
                        }
                    ],
                    "dependencies": [{"uuid": bp_uuid, "version": manifest_version}]
                }
                (rp_root / "manifest.json").write_text(
                    json.dumps(rp_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )

                # main.js
                main_text = MAIN_SCRIPT.read_text(encoding="utf-8")
                # 再生速度は各動画データの fps から決まるため、ここでは接頭辞だけ差し替える
                main_text = main_text.replace(
                    'const EVENT_NAMESPACE = "badapple";', f'const EVENT_NAMESPACE = "{namespace}";'
                )
                (scripts / "main.js").write_text(main_text, encoding="utf-8")
                shutil.copy2(CODEC_SCRIPT, scripts / "codec.js")

                if first_thumbnail and first_thumbnail.is_file():
                    shutil.copy2(first_thumbnail, rp_root / "pack_icon.png")
                    shutil.copy2(first_thumbnail, bp_root / "pack_icon.png")

                (bp_root / "CHANGELOG.md").write_text(changelog_markdown(pack_name), encoding="utf-8")
                (rp_root / "CHANGELOG.md").write_text(changelog_markdown(pack_name), encoding="utf-8")

                output.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                    for pack_dir in (bp_root, rp_root):
                        for file in pack_dir.rglob("*"):
                            if file.is_file():
                                archive.write(file, file.relative_to(temp))

            video_names = ", ".join(e["id"] for e in video_index_entries)
            self.messages.put((
                "success",
                self.tr("build.success", output=output, videos=video_names, count=total_videos, ns=namespace),
            ))
        except Exception as error:
            self.messages.put(("error", str(error)))

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if isinstance(message, tuple):
                    self.building = False
                    self.progress.stop()
                    self.build_button.configure(state="normal")
                    self.language_box.configure(state="readonly")
                    kind, text = message
                    if kind == "success":
                        self._append_log(text)
                        messagebox.showinfo(self.tr("build.done_title"), text + "\n" + self.tr("build.import_hint"))
                    else:
                        self._append_log(self.tr("log.error_prefix") + text)
                        messagebox.showerror(self.tr("build.failed_title"), text)
                else:
                    self._append_log(message)
        except queue.Empty:
            pass
        self.after(100, self._drain_messages)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def run_converter_mode(argv: list[str]) -> int:
    """EXE 版の変換モード。GUI から `BlockVideoPlayer.exe --run-converter ...` として起動される。"""
    if sys.stdout is None:  # windowed EXE で標準出力が無い場合
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    import convert

    return convert.main(argv)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == RUN_CONVERTER_FLAG:
        sys.exit(run_converter_mode(sys.argv[2:]))
    PackBuilderApp().mainloop()
