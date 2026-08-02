"""動画からMinecraft Bedrock用の.mcpackを作成するGUI。"""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pack_metadata import PACK_VERSION, RELEASE_NOTES, changelog_markdown, manifest_description, version_text


APP_DIR = Path(__file__).resolve().parent
MANIFEST = APP_DIR / "manifest.json"
MAIN_SCRIPT = APP_DIR / "main.js"
CONVERTER = APP_DIR / "convert.py"


class PackBuilderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Block Video Player Pack Builder")
        self.minsize(720, 520)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(9, weight=1)
        self.messages = queue.Queue()

        self.video_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(APP_DIR / "VideoPlayer.mcpack"))
        self.pack_name_var = tk.StringVar(value="Block Video Player")
        self.namespace_var = tk.StringVar(value="badapple")
        self.width_var = tk.IntVar(value=64)
        self.height_var = tk.IntVar(value=64)
        self.interval_var = tk.IntVar(value=1)
        self.duration_var = tk.StringVar()
        self.thumbnail_time_var = tk.StringVar(value="0")
        self.quality_var = tk.StringVar(value="高画質 (128×128 / 10fps)")
        self.palette_var = tk.StringVar(value="拡張 33色（concrete + terracotta）")
        self.dither_var = tk.BooleanVar(value=True)
        self._build_ui()
        self._apply_quality_preset()
        self.after(100, self._drain_messages)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        ttk.Label(self, text="動画ファイル").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(self, textvariable=self.video_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(self, text="選択…", command=self._select_video).grid(row=0, column=2, **pad)

        ttk.Label(self, text="出力 .mcpack").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(self, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(self, text="保存先…", command=self._select_output).grid(row=1, column=2, **pad)

        pack_settings = ttk.LabelFrame(self, text="パック名・実行コマンド")
        pack_settings.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        pack_settings.columnconfigure(1, weight=1)
        ttk.Label(pack_settings, text="パック表示名").grid(row=0, column=0, padx=(10, 4), pady=8, sticky="w")
        ttk.Entry(pack_settings, textvariable=self.pack_name_var).grid(row=0, column=1, padx=(0, 10), pady=8, sticky="ew")
        ttk.Label(pack_settings, text="コマンド接頭辞").grid(row=1, column=0, padx=(10, 4), pady=(0, 8), sticky="w")
        ttk.Entry(pack_settings, textvariable=self.namespace_var).grid(row=1, column=1, padx=(0, 10), pady=(0, 8), sticky="ew")
        self.command_hint = ttk.Label(pack_settings, text="", foreground="#555555")
        self.command_hint.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="w")
        self.namespace_var.trace_add("write", lambda *_: self._update_command_hint())
        self._update_command_hint()

        settings = ttk.LabelFrame(self, text="変換・再生設定")
        settings.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        for index, (label, variable, maximum) in enumerate((
            ("幅", self.width_var, 256),
            ("高さ", self.height_var, 256),
            ("再生間隔 (tick/フレーム)", self.interval_var, 20),
        )):
            ttk.Label(settings, text=label).grid(row=0, column=index * 2, padx=(10, 4), pady=8)
            ttk.Spinbox(settings, from_=1, to=maximum, textvariable=variable, width=7).grid(
                row=0, column=index * 2 + 1, padx=(0, 10), pady=8
            )
        ttk.Label(settings, text="最大秒数（空欄=全編）").grid(row=0, column=6, padx=(10, 4), pady=8)
        ttk.Entry(settings, textvariable=self.duration_var, width=10).grid(row=0, column=7, padx=(0, 10), pady=8)
        ttk.Label(settings, text="サムネイル秒数").grid(row=1, column=0, padx=(10, 4), pady=(0, 8))
        ttk.Entry(settings, textvariable=self.thumbnail_time_var, width=10).grid(row=1, column=1, padx=(0, 10), pady=(0, 8))
        ttk.Label(settings, text="画質プリセット").grid(row=1, column=2, padx=(10, 4), pady=(0, 8))
        preset = ttk.Combobox(
            settings, textvariable=self.quality_var, state="readonly", width=25,
            values=("軽量 (64×64 / 20fps)", "標準 (96×96 / 10fps)", "高画質 (128×128 / 10fps)", "高精細 (128×128 / 5fps)"),
        )
        preset.grid(row=1, column=3, columnspan=2, padx=(0, 10), pady=(0, 8), sticky="w")
        preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_quality_preset())
        ttk.Combobox(
            settings, textvariable=self.palette_var, state="readonly", width=34,
            values=("拡張 33色（concrete + terracotta）", "基本 16色（concrete）"),
        ).grid(row=1, column=5, columnspan=2, padx=(0, 10), pady=(0, 8), sticky="w")
        ttk.Checkbutton(settings, text="ディザリングで中間色を滑らかにする", variable=self.dither_var).grid(
            row=2, column=0, columnspan=5, padx=10, pady=(0, 8), sticky="w"
        )

        ttk.Label(
            self,
            text="動画をPNG連番へ抽出し、16色コンクリートの差分データに変換して、"
                 "インポート可能なBehavior Pack（.mcpack）を出力します。",
            wraplength=680,
        ).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(
            self,
            text=f"パック版 v{version_text()}  |  " + " / ".join(RELEASE_NOTES),
            wraplength=680,
            foreground="#555555",
        ).grid(row=5, column=0, columnspan=3, sticky="w", **pad)

        self.build_button = ttk.Button(self, text=".mcpack を作成", command=self._start_build)
        self.build_button.grid(row=6, column=0, columnspan=3, pady=8)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", **pad)

        ttk.Label(self, text="処理ログ").grid(row=8, column=0, sticky="w", **pad)
        self.log = tk.Text(self, height=15, state="disabled", wrap="word")
        self.log.grid(row=9, column=0, columnspan=3, sticky="nsew", padx=10, pady=(0, 10))

    def _update_command_hint(self) -> None:
        namespace = self.namespace_var.get().strip() or "<接頭辞>"
        self.command_hint.configure(
            text=f"実行: /scriptevent {namespace}:setup  |  {namespace}:start  |  {namespace}:stop"
        )

    def _apply_quality_preset(self) -> None:
        presets = {
            "軽量 (64×64 / 20fps)": (64, 64, 1),
            "標準 (96×96 / 10fps)": (96, 96, 2),
            "高画質 (128×128 / 10fps)": (128, 128, 2),
            "高精細 (128×128 / 5fps)": (128, 128, 4),
        }
        width, height, interval = presets[self.quality_var.get()]
        self.width_var.set(width)
        self.height_var.set(height)
        self.interval_var.set(interval)

    def _select_video(self) -> None:
        path = filedialog.askopenfilename(
            title="動画を選択",
            filetypes=[("動画", "*.mp4 *.mkv *.avi *.mov *.webm"), ("すべてのファイル", "*.*")],
        )
        if path:
            self.video_var.set(path)

    def _select_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title=".mcpackの保存先",
            defaultextension=".mcpack",
            filetypes=[("Minecraft Pack", "*.mcpack")],
        )
        if path:
            self.output_var.set(path)

    def _start_build(self) -> None:
        video = Path(self.video_var.get().strip())
        output = Path(self.output_var.get().strip())
        if not video.is_file():
            messagebox.showerror("動画が必要です", "変換する動画ファイルを選択してください。")
            return
        if not output.name:
            messagebox.showerror("出力先が必要です", ".mcpackの保存先を指定してください。")
            return
        if output.suffix.lower() != ".mcpack":
            output = output.with_suffix(".mcpack")
            self.output_var.set(str(output))
        try:
            width, height, interval = self.width_var.get(), self.height_var.get(), self.interval_var.get()
            duration = float(self.duration_var.get()) if self.duration_var.get().strip() else None
            thumbnail_time = float(self.thumbnail_time_var.get())
            if min(width, height, interval) < 1 or thumbnail_time < 0 or (duration is not None and duration <= 0):
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror("変換設定", "幅・高さ・再生間隔は1以上、サムネイル秒数は0以上にしてください。")
            return
        pack_name = self.pack_name_var.get().strip()
        namespace = self.namespace_var.get().strip()
        palette = "expanded" if self.palette_var.get().startswith("拡張") else "concrete"
        if not pack_name:
            messagebox.showerror("パック表示名", "パック表示名を入力してください。")
            return
        if not re.fullmatch(r"[a-z0-9_.-]+", namespace) or namespace == "minecraft":
            messagebox.showerror(
                "コマンド接頭辞",
                "`minecraft` は予約済みです。badapple や movie のような独自の名前を、"
                "英小文字・数字・_・-・.だけで入力してください。",
            )
            return

        self.build_button.configure(state="disabled")
        self.progress.start(12)
        threading.Thread(
            target=self._build_pack,
            args=(video, output, pack_name, namespace, width, height, interval, duration, thumbnail_time, palette, self.dither_var.get()),
            daemon=True,
        ).start()

    def _run(self, command: list[str]) -> None:
        self.messages.put("$ " + subprocess.list2cmdline(command))
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert process.stdout is not None
        for line in process.stdout:
            self.messages.put(line.rstrip())
        if process.wait() != 0:
            raise RuntimeError(f"コマンドが終了コード {process.returncode} で失敗しました。")

    def _build_pack(
        self, video: Path, output: Path, pack_name: str, namespace: str, width: int, height: int,
        interval: int, duration: float | None, thumbnail_time: float, palette: str, dither: bool
    ) -> None:
        try:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("ffmpeg がPATHから見つかりません。インストールとPATH設定を確認してください。")
            for required in (MANIFEST, MAIN_SCRIPT, CONVERTER):
                if not required.is_file():
                    raise RuntimeError(f"必要なファイルがありません: {required}")

            with tempfile.TemporaryDirectory(prefix="block-video-player-") as temp_dir:
                temp = Path(temp_dir)
                frames = temp / "frames"
                generated_data = temp / "frames_data.js"
                thumbnail = temp / "pack_icon.png"
                frames.mkdir()
                self.messages.put("パックのサムネイルを切り出しています…")
                self._run([
                    ffmpeg, "-y", "-ss", str(thumbnail_time), "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=256:256:force_original_aspect_ratio=decrease,pad=256:256:(ow-iw)/2:(oh-ih)/2:black",
                    str(thumbnail),
                ])
                if not thumbnail.is_file() or thumbnail.stat().st_size == 0:
                    raise RuntimeError("サムネイル用フレームを作成できませんでした。秒数を確認してください。")
                ffmpeg_command = [ffmpeg, "-y", "-i", str(video)]
                if duration is not None:
                    ffmpeg_command.extend(["-t", str(duration)])
                ffmpeg_command.extend([
                    "-vf", f"fps={20 / interval:g},scale={width}:{height}:flags=lanczos",
                    str(frames / "output_%04d.png"),
                ])
                self.messages.put("フレームを抽出しています…")
                self._run(ffmpeg_command)

                self.messages.put("ブロックデータへ変換しています…")
                converter_command = [
                    sys.executable, str(CONVERTER), "--frames-dir", str(frames),
                    "--output", str(generated_data), "--width", str(width), "--height", str(height),
                    "--palette", palette,
                ]
                if dither:
                    converter_command.append("--dither")
                self._run(converter_command)

                pack_root = temp / "pack"
                scripts = pack_root / "scripts"
                scripts.mkdir(parents=True)
                manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
                manifest["header"]["name"] = f"{pack_name} v{version_text()}"
                manifest["header"]["description"] = manifest_description()
                manifest["header"]["version"] = list(PACK_VERSION)
                manifest["modules"][0]["version"] = list(PACK_VERSION)
                (pack_root / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                main_text = MAIN_SCRIPT.read_text(encoding="utf-8")
                main_text = main_text.replace(
                    'const EVENT_NAMESPACE = "badapple";', f'const EVENT_NAMESPACE = "{namespace}";'
                ).replace(
                    "const FRAME_INTERVAL_TICKS = 1;", f"const FRAME_INTERVAL_TICKS = {interval};"
                )
                (scripts / "main.js").write_text(main_text, encoding="utf-8")
                shutil.copy2(generated_data, scripts / "frames_data.js")
                shutil.copy2(thumbnail, pack_root / "pack_icon.png")
                (pack_root / "CHANGELOG.md").write_text(changelog_markdown(pack_name), encoding="utf-8")
                output.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in pack_root.rglob("*"):
                        if file.is_file():
                            archive.write(file, file.relative_to(pack_root))
            self.messages.put((
                "success",
                f"作成完了: {output}\n実行コマンド: /scriptevent {namespace}:setup, {namespace}:start, {namespace}:stop",
            ))
        except Exception as error:
            self.messages.put(("error", str(error)))

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if isinstance(message, tuple):
                    self.progress.stop()
                    self.build_button.configure(state="normal")
                    kind, text = message
                    if kind == "success":
                        self._append_log(text)
                        messagebox.showinfo("完了", text + "\nMinecraftでファイルを開いてインポートしてください。")
                    else:
                        self._append_log("エラー: " + text)
                        messagebox.showerror("作成に失敗しました", text)
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


if __name__ == "__main__":
    PackBuilderApp().mainloop()
