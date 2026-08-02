"""動画からMinecraft Bedrock用の.mcpackを作成するGUI。複数動画搭載対応。"""

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
        self.minsize(780, 660)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=0)
        self.rowconfigure(9, weight=1)
        self.messages = queue.Queue()

        self.output_var = tk.StringVar(value=str(APP_DIR / "VideoPlayer.mcpack"))
        self.pack_name_var = tk.StringVar(value="Block Video Player")
        self.namespace_var = tk.StringVar(value="badapple")
        self.width_var = tk.IntVar(value=64)
        self.height_var = tk.IntVar(value=64)
        self.interval_var = tk.IntVar(value=1)
        self.duration_var = tk.StringVar()
        self.thumbnail_time_var = tk.StringVar(value="0")
        self.quality_var = tk.StringVar(value="高画質 (128×128 / 10fps)")
        self.palette_var = tk.StringVar(value="全39色（concrete + terracotta + 自発光）")
        self.dither_var = tk.StringVar(value="Floyd-Steinberg")
        self._build_ui()
        self._apply_quality_preset()
        self.after(100, self._drain_messages)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        # --- 動画リストセクション ---
        video_frame = ttk.LabelFrame(self, text="動画リスト（複数搭載対応）")
        video_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        video_frame.columnconfigure(0, weight=1)
        video_frame.rowconfigure(0, weight=1)

        cols = ("video_id", "file_path", "thumb_sec")
        self.video_tree = ttk.Treeview(video_frame, columns=cols, show="headings", height=5)
        self.video_tree.heading("video_id", text="動画ID")
        self.video_tree.heading("file_path", text="ファイルパス")
        self.video_tree.heading("thumb_sec", text="サムネ秒")
        self.video_tree.column("video_id", width=120, minwidth=80)
        self.video_tree.column("file_path", width=400, minwidth=200)
        self.video_tree.column("thumb_sec", width=80, minwidth=60)
        self.video_tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=6)

        tree_scroll = ttk.Scrollbar(video_frame, orient="vertical", command=self.video_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 10))
        self.video_tree.configure(yscrollcommand=tree_scroll.set)

        btn_frame = ttk.Frame(video_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))
        ttk.Button(btn_frame, text="動画を追加…", command=self._add_videos).pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="選択を削除", command=self._remove_selected).pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="IDを編集…", command=self._edit_video_id).pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="サムネ秒を編集…", command=self._edit_thumb_sec).pack(side="left")

        # --- 出力先 ---
        out_frame = ttk.Frame(self)
        out_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        out_frame.columnconfigure(1, weight=1)
        ttk.Label(out_frame, text="出力 .mcpack").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(out_frame, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(out_frame, text="保存先…", command=self._select_output).grid(row=0, column=2)

        # --- パック名・コマンド ---
        pack_settings = ttk.LabelFrame(self, text="パック名・実行コマンド")
        pack_settings.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        pack_settings.columnconfigure(1, weight=1)
        ttk.Label(pack_settings, text="パック表示名").grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        ttk.Entry(pack_settings, textvariable=self.pack_name_var).grid(row=0, column=1, padx=(0, 10), pady=6, sticky="ew")
        ttk.Label(pack_settings, text="コマンド接頭辞").grid(row=1, column=0, padx=(10, 4), pady=(0, 6), sticky="w")
        ttk.Entry(pack_settings, textvariable=self.namespace_var).grid(row=1, column=1, padx=(0, 10), pady=(0, 6), sticky="ew")
        self.command_hint = ttk.Label(pack_settings, text="", foreground="#555555")
        self.command_hint.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 6), sticky="w")
        self.namespace_var.trace_add("write", lambda *_: self._update_command_hint())
        self._update_command_hint()

        # --- 変換・再生設定 ---
        settings = ttk.LabelFrame(self, text="変換・再生設定")
        settings.grid(row=3, column=0, sticky="ew", padx=10, pady=4)
        for index, (label, variable, maximum) in enumerate((
            ("幅", self.width_var, 256),
            ("高さ", self.height_var, 256),
            ("再生間隔 (tick/フレーム)", self.interval_var, 20),
        )):
            ttk.Label(settings, text=label).grid(row=0, column=index * 2, padx=(10, 4), pady=6)
            ttk.Spinbox(settings, from_=1, to=maximum, textvariable=variable, width=7).grid(
                row=0, column=index * 2 + 1, padx=(0, 10), pady=6
            )
        ttk.Label(settings, text="最大秒数（空欄=全編）").grid(row=0, column=6, padx=(10, 4), pady=6)
        ttk.Entry(settings, textvariable=self.duration_var, width=10).grid(row=0, column=7, padx=(0, 10), pady=6)

        ttk.Label(settings, text="画質プリセット").grid(row=1, column=0, padx=(10, 4), pady=(0, 6))
        preset = ttk.Combobox(
            settings, textvariable=self.quality_var, state="readonly", width=25,
            values=("軽量 (64×64 / 20fps)", "標準 (96×96 / 10fps)", "高画質 (128×128 / 10fps)", "高精細 (128×128 / 5fps)"),
        )
        preset.grid(row=1, column=1, columnspan=2, padx=(0, 10), pady=(0, 6), sticky="w")
        preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_quality_preset())

        # パレット選択 (55色対応)
        ttk.Label(settings, text="パレット").grid(row=1, column=3, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.palette_var, state="readonly", width=36,
            values=(
                "全55色（concrete + terracotta + 自発光 + wool + 鉱石）",
                "全39色（concrete + terracotta + 自発光）",
                "拡張 33色（concrete + terracotta）",
                "基本 16色（concrete）",
            ),
        ).grid(row=1, column=4, columnspan=3, padx=(0, 10), pady=(0, 6), sticky="w")

        # ディザリング選択 (全5種対応)
        ttk.Label(settings, text="ディザリング").grid(row=2, column=0, padx=(10, 4), pady=(0, 6))
        ttk.Combobox(
            settings, textvariable=self.dither_var, state="readonly", width=25,
            values=("なし", "Floyd-Steinberg", "Atkinson", "Burkes", "Sierra Lite", "Ordered (Bayer)"),
        ).grid(row=2, column=1, columnspan=2, padx=(0, 10), pady=(0, 6), sticky="w")

        # --- 説明文 ---
        ttk.Label(
            self,
            text="動画をPNG連番へ抽出し、ブロック色の差分データに変換して、"
                 "インポート可能なBehavior Pack（.mcpack）を出力します。複数動画の一括搭載に対応。",
            wraplength=740,
        ).grid(row=4, column=0, sticky="w", **pad)

        ttk.Label(
            self,
            text=f"パック版 v{version_text()}  |  " + " / ".join(RELEASE_NOTES[:2]),
            wraplength=740,
            foreground="#555555",
        ).grid(row=5, column=0, sticky="w", **pad)

        self.build_button = ttk.Button(self, text=".mcpack を作成", command=self._start_build)
        self.build_button.grid(row=6, column=0, pady=6)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=7, column=0, sticky="ew", padx=10, pady=4)

        ttk.Label(self, text="処理ログ").grid(row=8, column=0, sticky="w", padx=10, pady=(4, 0))
        self.log = tk.Text(self, height=12, state="disabled", wrap="word")
        self.log.grid(row=9, column=0, sticky="nsew", padx=10, pady=(0, 10))

    # --- 動画リスト操作 ---
    def _add_videos(self) -> None:
        paths = filedialog.askopenfilenames(
            title="動画を選択（複数選択可）",
            filetypes=[("動画", "*.mp4 *.mkv *.avi *.mov *.webm"), ("すべてのファイル", "*.*")],
        )
        for path in paths:
            p = Path(path)
            # 動画IDはファイル名のstem（英数字・アンダースコアのみ）
            video_id = re.sub(r"[^a-z0-9_]", "_", p.stem.lower())
            # 重複チェック
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
            messagebox.showwarning("選択してください", "削除する動画をリストから選択してください。")
            return
        for iid in selected:
            self.video_tree.delete(iid)

    def _edit_video_id(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning("選択してください", "IDを編集する動画を選択してください。")
            return
        iid = selected[0]
        values = self.video_tree.item(iid)["values"]
        from tkinter import simpledialog
        new_id = simpledialog.askstring("動画ID編集", f"新しいID ({values[0]}):", initialvalue=values[0])
        if new_id and re.fullmatch(r"[a-z0-9_]+", new_id):
            self.video_tree.item(iid, values=(new_id, values[1], values[2]))
        elif new_id:
            messagebox.showerror("無効なID", "IDは英小文字・数字・アンダースコアのみです。")

    def _edit_thumb_sec(self) -> None:
        selected = self.video_tree.selection()
        if not selected:
            messagebox.showwarning("選択してください", "サムネ秒を編集する動画を選択してください。")
            return
        iid = selected[0]
        values = self.video_tree.item(iid)["values"]
        from tkinter import simpledialog
        new_sec = simpledialog.askstring("サムネイル秒数", f"秒数 ({values[2]}):", initialvalue=str(values[2]))
        if new_sec is not None:
            try:
                float(new_sec)
                self.video_tree.item(iid, values=(values[0], values[1], new_sec))
            except ValueError:
                messagebox.showerror("数値エラー", "数値を入力してください。")

    def _update_command_hint(self) -> None:
        namespace = self.namespace_var.get().strip() or "<接頭辞>"
        self.command_hint.configure(
            text=f"実行: /scriptevent {namespace}:setup | {namespace}:list | {namespace}:play <動画ID> | {namespace}:stop"
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

    def _select_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title=".mcpackの保存先",
            defaultextension=".mcpack",
            filetypes=[("Minecraft Pack", "*.mcpack")],
        )
        if path:
            self.output_var.set(path)

    def _start_build(self) -> None:
        # 動画リストから取得
        video_entries = []
        for iid in self.video_tree.get_children():
            vals = self.video_tree.item(iid)["values"]
            video_entries.append({
                "video_id": str(vals[0]),
                "file_path": str(vals[1]),
                "thumb_sec": str(vals[2]),
            })

        if not video_entries:
            messagebox.showerror("動画が必要です", "動画リストに少なくとも1つの動画を追加してください。")
            return

        output = Path(self.output_var.get().strip())
        if not output.name:
            messagebox.showerror("出力先が必要です", ".mcpackの保存先を指定してください。")
            return
        if output.suffix.lower() != ".mcpack":
            output = output.with_suffix(".mcpack")
            self.output_var.set(str(output))

        # 各動画ファイルの存在チェック
        for entry in video_entries:
            if not Path(entry["file_path"]).is_file():
                messagebox.showerror("ファイルが見つかりません", f"動画ファイルが見つかりません:\n{entry['file_path']}")
                return

        try:
            width, height, interval = self.width_var.get(), self.height_var.get(), self.interval_var.get()
            duration = float(self.duration_var.get()) if self.duration_var.get().strip() else None
            if min(width, height, interval) < 1 or (duration is not None and duration <= 0):
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror("変換設定", "幅・高さ・再生間隔は1以上にしてください。")
            return

        pack_name = self.pack_name_var.get().strip()
        namespace = self.namespace_var.get().strip()

        # パレット選択
        pal_text = self.palette_var.get()
        if pal_text.startswith("全55"):
            palette = "all_55"
        elif pal_text.startswith("全39"):
            palette = "full"
        elif pal_text.startswith("拡張"):
            palette = "expanded"
        else:
            palette = "concrete"

        # ディザリング選択
        dither_text = self.dither_var.get()
        if dither_text == "Floyd-Steinberg":
            dither_method = "floyd"
        elif dither_text == "Atkinson":
            dither_method = "atkinson"
        elif dither_text == "Burkes":
            dither_method = "burkes"
        elif dither_text == "Sierra Lite":
            dither_method = "sierra"
        elif dither_text.startswith("Ordered"):
            dither_method = "ordered"
        else:
            dither_method = "none"

        if not pack_name:
            messagebox.showerror("パック表示名", "パック表示名を入力してください。")
            return
        if not re.fullmatch(r"[a-z0-9_.\-]+", namespace) or namespace == "minecraft":
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
            args=(video_entries, output, pack_name, namespace, width, height, interval, duration, palette, dither_method),
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
        self, video_entries: list[dict], output: Path, pack_name: str, namespace: str,
        width: int, height: int, interval: int, duration: float | None, palette: str, dither_method: str
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
                pack_root = temp / "pack"
                scripts = pack_root / "scripts"
                scripts.mkdir(parents=True)

                video_index_entries = []
                first_thumbnail = None

                sound_definitions = {}

                total_videos = len(video_entries)
                for vi, entry in enumerate(video_entries, 1):
                    video = Path(entry["file_path"])
                    video_id = entry["video_id"]
                    thumb_sec = entry["thumb_sec"]

                    self.messages.put(f"--- [{vi}/{total_videos}] 動画 '{video_id}' を処理中 ---")

                    frames = temp / f"frames_{video_id}"
                    generated_data = scripts / f"frames_{video_id}.js"
                    thumbnail = temp / f"thumb_{video_id}.png"
                    frames.mkdir()

                    # サムネイル生成
                    self.messages.put(f"  サムネイルを切り出し中 (秒={thumb_sec})…")
                    self._run([
                        ffmpeg, "-y", "-ss", str(thumb_sec), "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=256:256:force_original_aspect_ratio=decrease,pad=256:256:(ow-iw)/2:(oh-ih)/2:black",
                        str(thumbnail),
                    ])
                    if not thumbnail.is_file() or thumbnail.stat().st_size == 0:
                        raise RuntimeError(f"サムネイル生成失敗: {video_id}")

                    # 最初の動画のサムネイルをパックアイコンに使用
                    if first_thumbnail is None:
                        first_thumbnail = thumbnail

                    # 音声切り出し (10秒分割 .ogg)
                    sounds_dir = pack_root / "sounds" / "music" / video_id
                    sounds_dir.mkdir(parents=True, exist_ok=True)
                    self.messages.put("  音声を10秒単位(.ogg)で切り出し中…")
                    try:
                        self._run([
                            ffmpeg, "-y", "-i", str(video),
                            "-f", "segment", "-segment_time", "10",
                            "-vn", "-acodec", "libvorbis",
                            str(sounds_dir / "chunk_%d.ogg")
                        ])
                    except Exception as e:
                        self.messages.put(f"  警告: 音声抽出スキップ (無音動画またはエラー): {e}")

                    # 抽出された ogg ファイル群をサウンド定義に登録
                    ogg_files = sorted(sounds_dir.glob("chunk_*.ogg"), key=lambda p: int(p.stem.split("_")[1]))
                    for ogg_file in ogg_files:
                        chunk_idx = int(ogg_file.stem.split("_")[1])
                        sound_key = f"{namespace}.{video_id}.chunk_{chunk_idx}"
                        sound_definitions[sound_key] = {
                            "category": "music",
                            "sounds": [
                                {
                                    "name": f"sounds/music/{video_id}/{ogg_file.stem}",
                                    "stream": True,
                                }
                            ]
                        }

                    # フレーム抽出
                    ffmpeg_command = [ffmpeg, "-y", "-i", str(video)]
                    if duration is not None:
                        ffmpeg_command.extend(["-t", str(duration)])
                    ffmpeg_command.extend([
                        "-vf", f"fps={20 / interval:g},scale={width}:{height}:flags=lanczos",
                        str(frames / "output_%04d.png"),
                    ])
                    self.messages.put("  フレームを抽出中…")
                    self._run(ffmpeg_command)

                    # ブロックデータ変換
                    self.messages.put("  ブロックデータへ変換中…")
                    converter_command = [
                        sys.executable, str(CONVERTER), "--frames-dir", str(frames),
                        "--output", str(generated_data), "--width", str(width), "--height", str(height),
                        "--palette", palette,
                        "--dither-method", dither_method,
                        "--video-id", video_id,
                    ]
                    self._run(converter_command)

                    # フレーム数を生成されたJSファイルから読み取り
                    frame_count = 0
                    try:
                        js_text = generated_data.read_text(encoding="utf-8")
                        import re as _re
                        fc_match = _re.search(r'"frame_count":(\d+)', js_text)
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
                    sound_def_file = pack_root / "sounds" / "sound_definitions.json"
                    sound_def_file.parent.mkdir(parents=True, exist_ok=True)
                    sound_def_file.write_text(
                        json.dumps({"format_version": "1.14.0", "sound_definitions": sound_definitions}, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )

                # videos.js を自動生成（静的インポートのみ）
                self.messages.put("videos.js（動画インデックス）を生成中…")
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
                manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
                manifest["header"]["name"] = f"{pack_name} v{version_text()}"
                manifest["header"]["description"] = manifest_description()
                manifest["header"]["version"] = list(PACK_VERSION)
                manifest["modules"][0]["version"] = list(PACK_VERSION)
                (pack_root / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )

                # main.js（名前空間・再生間隔を置換）
                main_text = MAIN_SCRIPT.read_text(encoding="utf-8")
                main_text = main_text.replace(
                    'const EVENT_NAMESPACE = "badapple";', f'const EVENT_NAMESPACE = "{namespace}";'
                ).replace(
                    "const FRAME_INTERVAL_TICKS = 1;", f"const FRAME_INTERVAL_TICKS = {interval};"
                )
                (scripts / "main.js").write_text(main_text, encoding="utf-8")

                # パックアイコン
                if first_thumbnail and first_thumbnail.is_file():
                    shutil.copy2(first_thumbnail, pack_root / "pack_icon.png")

                # CHANGELOG.md
                (pack_root / "CHANGELOG.md").write_text(changelog_markdown(pack_name), encoding="utf-8")

                # .mcpack (ZIP) を作成
                output.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in pack_root.rglob("*"):
                        if file.is_file():
                            archive.write(file, file.relative_to(pack_root))

            video_names = ", ".join(e["id"] for e in video_index_entries)
            self.messages.put((
                "success",
                f"作成完了: {output}\n"
                f"収録動画: {video_names} ({total_videos}本)\n"
                f"コマンド一覧:\n"
                f"  /scriptevent {namespace}:setup  … 原点セットアップ\n"
                f"  /scriptevent {namespace}:list   … 動画一覧表示\n"
                f"  /scriptevent {namespace}:play <動画ID>  … 再生\n"
                f"  /scriptevent {namespace}:stop   … 停止・クリア",
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
