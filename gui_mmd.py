"""Windows 版の画面の「MMD のダンスを追加」の画面 (docs/research/mmd_image_mode_design_2026-11.md の 2 章)。

モデル・ダンス・表情と口・曲・ケースの奥の絵と、作るもの (画像モードはいつも、ブロックモードはチェック)・細かさ・表情・
物理を 1 つの画面で選ぶ。選んだ中身は dict (mmd_settings) で、動画リストの 1 行に持たせる。
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

DETAILS = ("standard", "high", "max")
DEFAULT_DETAIL = "high"
PHYSICS = ("off", "light", "standard", "full")
DEFAULT_PHYSICS = "standard"
DANCE_SUFFIXES = (".vmd", ".bvh")


def default_settings() -> dict:
    """新しく追加するときの選択 (画像モードの人物・高精細・表情あり・物理は標準・ブロックモードは作らない)。"""
    return {"pmx": "", "motion": "", "face_motions": [], "audio": None, "case_image": None, "block": False,
            "detail": DEFAULT_DETAIL, "expressions": True, "physics": DEFAULT_PHYSICS}


def validate(settings: dict) -> str | None:
    """足りない・おかしい所の翻訳キー。無ければ None。"""
    if not settings.get("pmx") or Path(settings["pmx"]).suffix.lower() != ".pmx":
        return "mmd.need_model"
    if not settings.get("motion") or Path(settings["motion"]).suffix.lower() not in DANCE_SUFFIXES:
        return "mmd.need_dance"
    return None


def glass_settings(settings: dict) -> dict:
    """動画リストの行に持たせる、画像モードの人物の設定 (_build_image_pack の figure に渡す形)。"""
    return {"audio": settings.get("audio") or None, "case_image": settings.get("case_image") or None,
            "face_motions": list(settings.get("face_motions") or []), "physics": settings.get("physics", DEFAULT_PHYSICS),
            "expressions": bool(settings.get("expressions", True)), "detail": settings.get("detail", DEFAULT_DETAIL),
            "block": bool(settings.get("block", False))}


class MmdDialog(tk.Toplevel):
    """「MMD のダンスを追加」の画面。閉じた後の result が選んだ設定 (やめたら None)。"""

    def __init__(self, parent, tr, initial: dict | None = None):
        super().__init__(parent)
        self.tr = tr
        self.result = None
        settings = {**default_settings(), **(initial or {})}
        self.title(tr("mmd.title"))
        self.transient(parent)
        self.resizable(True, False)
        self.columnconfigure(1, weight=1)
        self.pmx = tk.StringVar(value=settings["pmx"])
        self.motion = tk.StringVar(value=settings["motion"])
        self.audio = tk.StringVar(value=settings["audio"] or "")
        self.case_image = tk.StringVar(value=settings["case_image"] or "")
        self.face_motions = list(settings["face_motions"])
        self.block = tk.BooleanVar(value=settings["block"])
        self.expressions = tk.BooleanVar(value=settings["expressions"])
        self.detail_labels = [tr(f"mmd.detail.{key}") for key in DETAILS]
        self.physics_labels = [tr(f"mmd.physics.{key}") for key in PHYSICS]
        self.detail = tk.StringVar(value=self.detail_labels[DETAILS.index(settings["detail"])])
        self.physics = tk.StringVar(value=self.physics_labels[PHYSICS.index(settings["physics"])])

        row = 0
        files = [
            ("mmd.model", self.pmx, [(tr("dialog.mmd_files"), "*.pmx")]),
            ("mmd.dance", self.motion, [(tr("dialog.dance_files"), "*.vmd *.bvh")]),
            ("mmd.song", self.audio, [(tr("dialog.audio_files"), "*.wav *.mp3 *.ogg *.m4a *.flac")]),
            ("mmd.case_image", self.case_image, [(tr("dialog.image_files"), "*.png *.jpg *.jpeg *.webp *.bmp")]),
        ]
        for key, variable, types in files[:2]:
            row = self._file_row(row, key, variable, types)
        # 表情・口は複数選べる
        ttk.Label(self, text=tr("mmd.face")).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        self.face_label = ttk.Label(self, text="", width=48)
        self.face_label.grid(row=row, column=1, sticky="ew", padx=4)
        face_buttons = ttk.Frame(self)
        face_buttons.grid(row=row, column=2, sticky="e", padx=8)
        ttk.Button(face_buttons, text=tr("mmd.choose"), command=self._add_faces).pack(side="left")
        ttk.Button(face_buttons, text=tr("mmd.clear"), command=self._clear_faces).pack(side="left", padx=(4, 0))
        self._show_faces()
        row += 1
        for key, variable, types in files[2:]:
            row = self._file_row(row, key, variable, types, clearable=True)

        make = ttk.LabelFrame(self, text=tr("mmd.make"))
        make.grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 4))
        ttk.Label(make, text=tr("mmd.image_always")).grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(make, text=tr("mmd.block"), variable=self.block).grid(row=1, column=0, sticky="w", padx=6, pady=2)
        row += 1
        options = ttk.Frame(self)
        options.grid(row=row, column=0, columnspan=3, sticky="ew", padx=8, pady=4)
        ttk.Label(options, text=tr("mmd.detail")).grid(row=0, column=0, sticky="w")
        ttk.Combobox(options, textvariable=self.detail, values=self.detail_labels, state="readonly",
                     width=28).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(options, text=tr("mmd.physics")).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(options, textvariable=self.physics, values=self.physics_labels, state="readonly",
                     width=28).grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Checkbutton(options, text=tr("mmd.expressions"), variable=self.expressions).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        row += 1
        ttk.Label(self, text=tr("mmd.notice"), wraplength=520, foreground="#555").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2))
        row += 1
        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", padx=8, pady=8)
        ttk.Button(buttons, text=tr("mmd.add"), command=self._accept).pack(side="left", padx=4)
        ttk.Button(buttons, text=tr("mmd.cancel"), command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def _file_row(self, row, key, variable, types, clearable=False):
        ttk.Label(self, text=self.tr(key)).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(self, textvariable=variable, width=48).grid(row=row, column=1, sticky="ew", padx=4)
        frame = ttk.Frame(self)
        frame.grid(row=row, column=2, sticky="e", padx=8)
        all_files = (self.tr("dialog.all_files"), "*.*")

        def choose():
            path = filedialog.askopenfilename(parent=self, title=self.tr(key), filetypes=[*types, all_files])
            if path:
                variable.set(path)
        ttk.Button(frame, text=self.tr("mmd.choose"), command=choose).pack(side="left")
        if clearable:
            ttk.Button(frame, text=self.tr("mmd.clear"), command=lambda: variable.set("")).pack(side="left", padx=(4, 0))
        return row + 1

    def _add_faces(self):
        paths = filedialog.askopenfilenames(parent=self, title=self.tr("mmd.face"),
                                            filetypes=[("VMD", "*.vmd"), (self.tr("dialog.all_files"), "*.*")])
        for path in paths:
            if path not in self.face_motions:
                self.face_motions.append(path)
        self._show_faces()

    def _clear_faces(self):
        self.face_motions = []
        self._show_faces()

    def _show_faces(self):
        names = [Path(p).name for p in self.face_motions]
        self.face_label.configure(text=", ".join(names) if names else "—")

    def settings(self) -> dict:
        return {"pmx": self.pmx.get().strip(), "motion": self.motion.get().strip(),
                "face_motions": list(self.face_motions), "audio": self.audio.get().strip() or None,
                "case_image": self.case_image.get().strip() or None, "block": bool(self.block.get()),
                "detail": DETAILS[self.detail_labels.index(self.detail.get())],
                "expressions": bool(self.expressions.get()),
                "physics": PHYSICS[self.physics_labels.index(self.physics.get())]}

    def _accept(self):
        settings = self.settings()
        problem = validate(settings)
        if problem:
            messagebox.showerror(self.tr("mmd.title"), self.tr(problem), parent=self)
            return
        self.result = settings
        self.destroy()
