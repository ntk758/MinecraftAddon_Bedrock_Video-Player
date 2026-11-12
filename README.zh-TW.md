# Minecraft Bedrock Video Player

<!-- 言語の切り替え (README の各言語で同じ。tests/test_readme_languages.py) -->
<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <b>繁體中文</b> ·
  <a href="README.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.de.md">Deutsch</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.pt-BR.md">Português</a> ·
  <a href="README.ru.md">Русский</a>
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Minecraft Bedrock](https://img.shields.io/badge/Minecraft-Bedrock-brightgreen.svg)](https://www.minecraft.net/)
[![Release](https://img.shields.io/github/v/release/ntk758/MinecraftAddon_Bedrock_Video-Player)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest)
[![CI](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml/badge.svg)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml)

把影片 (MP4 等) 轉換成真正的 Minecraft 方塊，在 Minecraft 基岩版中帶聲音播放。選好影片，按一個按鈕，再匯入產生的 `.mcaddon` 即可。

<p align="center">
  <img src="docs/images/demo.gif" alt="左: 原影片，右: 轉換成 128×72 方塊的同一影片" width="800">
  <br>
  <sub>左: 原影片 / 右: 轉換成 128×72 方塊 (依遊戲內的規則還原)</sub>
</p>

<p align="center">
  <img src="docs/images/ingame_trompe.gif" alt="遊戲內錄影: 依玩家視線畫在地面和牆上的舞者" width="640">
  <br>
  <sub><b>隨視角變化的錯視畫</b> (真實的遊戲內錄影): 舞者依你眼睛看到的樣子畫在地面和牆上，你走動時會重新繪製，所以看起來像站在那裡</sub>
</p>

> 整個專案 (轉換工具、附加內容腳本、GUI 和本 README) 都由 AI 設計和撰寫。

## 特色

- 🧱 **真實的方塊**: 最多 221 種 (混凝土、陶土、羊毛、木材、原木、石頭、礦石…)。顏色從遊戲貼圖實測，每個場景自動選擇最多 127 種顏色。
- 🔊 **聲音保持同步**，暫停和跳轉後也一樣。
- 🧊 可以繞著走的 **3D 全像**: 浮雕、擠出、圓潤的立體，以及 MagicaVoxel (`.vox`) 動畫。
- 🪄 **錯視畫**: 依你的視線繪製的人物，從哪裡看都像立體的 (只對一名觀看者正確)。
- 🧭 用指南針打開 **遙控器**: 播放、暫停、停止、跳轉、音量、上一部/下一部影片、影片清單。
- 📚 一個附加內容可以放多部影片，也可以把 3D 影片的平面版一起放進去。
- 🔓 **不需要作弊，也不需要實驗性玩法** (只用穩定版 Script API)。
- 🌐 **10 種語言**: 訊息和遙控器跟隨每位玩家的遊戲語言。
- 🖼 **影像模式 (試作)**: 另一個附加內容，用貼圖以最高 30 fps 顯示影片。

## 快速開始

**A. 試用範例 (不需轉換，約 3 分鐘)**
1. 從 [最新發行](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) 下載 `BlockVideoPlayer-sample-….mcaddon` (平面) 或 `BlockVideoPlayer-sample-3d-….mcaddon` (3D)，打開即可匯入 Minecraft。
2. 在世界設定中 **同時** 啟用行為包和資源包 (名稱相同)。作弊保持關閉即可。
3. 到平坦空曠的地方 (螢幕會 **取代那裡的方塊**，請使用測試世界)。面向想放螢幕的方向，輸入 `/badapple:setup wall`，然後輸入 `/badapple:start`。
4. 使用指南針打開遙控器。第一次進入還沒有螢幕的世界時，聊天欄會簡短提示這些步驟。

**B. 在瀏覽器中轉換自己的影片 (不需安裝，測試版)**
- 打開 [網頁版轉換工具](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/)，選擇影片，按「建立 .mcaddon」。可在 Windows、Mac、Chromebook 和手機上使用；影片不會離開瀏覽器。
- 圖片、相機拍的影片、`.vox`、帶舞蹈的 MMD 模型 (`.pmx` + `.vmd`) 也可以轉換。完成後可以預覽在 Minecraft 中的樣子。
- 對於 **Minecraft 1.21.80 至 1.26.0**，請在「Minecraft 版本」中選擇它 (這類附加內容需要作弊，並使用 `/scriptevent` 指令)。

**C. 在 Windows 上轉換 (更快，附加內容更小)**
1. 從 [最新發行](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) 下載 `BlockVideoPlayer-…-windows-x64.zip`，解壓縮後執行 `BlockVideoPlayer.exe` (不需 Python)。**另外需要 FFmpeg**: 執行 `winget install Gyan.FFmpeg`，或把 `ffmpeg.exe` 放在 EXE 旁邊。
2. 第一次啟動會打開入門指南: 檢查電腦、推薦設定，並顯示播放用的指令。不用指南時: 「新增影片」→「符合影片長寬比」→「建立 .mcaddon」。
3. 如果 **播放負荷估計** 顯示「重」，請降低寬度、高度或 fps。

## 指令

`badapple` 是指令前綴 (轉換時自己決定)。管理員可以使用這些指令；開啟作弊時，舊的 `/scriptevent badapple:…` 寫法也能用。

| 指令 | 作用 |
|---|---|
| `/badapple:setup` | 在這裡把螢幕平放在地面 (3D: 立在北邊 2 格處) |
| `/badapple:setup wall` | 把平面螢幕像電視一樣立在你前方 3 格處 |
| `/badapple:setup face` | 平放，但朝向你看的方向 |
| `/badapple:start` | 從頭播放選中的影片 |
| `/badapple:play <ID>` / `/badapple:list` | 播放影片 / 列出影片 |
| `/badapple:remote` | 打開遙控器 (與指南針相同) |
| `/badapple:stop` | 停止並清除螢幕 |
| `/badapple:stats` | 上一次播放的實際 fps、TPS 和延遲的影格 |

## 影像模式 (試作)

在 Windows 版或網頁版中勾選「也建立影像模式的附加內容」(或執行 `python convert_image_mode.py video.mp4`)，會得到第二個檔案 `<名稱>_image.mcaddon`。遊戲中: `/badapple_img:setup`，然後 `/badapple_img:start`。它不取代方塊，最高 30 fps 播放，但會佔用貼圖記憶體，所以請保持影片簡短。**尚未在真實的 Minecraft 中測試。** 詳情: [docs/IMAGE_MODE.md](docs/IMAGE_MODE.md) (日語)。

## 環境需求

| | |
|---|---|
| Minecraft | 基岩版 **1.26.10 或更新** (1.21.80–1.26.0: 用網頁版製作) |
| 轉換工具 | Windows 10/11 的 EXE，或任何現代瀏覽器。從原始碼執行: Python 3.12 + FFmpeg (命令列在 Linux 上也可用) |
| GPU (選用) | NVIDIA (CUDA)、AMD (ROCm / DirectML) 或 Intel (DirectML)；CPU 也可以 |

只在一台 Windows 11 電腦上測試過。手機、主機和伺服器預計可以執行，但 **尚未測試** ([相容性](docs/COMPATIBILITY.md))。

## 常見問題

- **Java 版?** 不支援，僅限基岩版。
- **沒有聲音?** 確認兩個包都已啟用，檢查遙控器和遊戲中的音量，匯入後重新啟動 Minecraft。
- **卡頓?** 降低尺寸或 fps，並查看 `/badapple:stats`。更多見 [已知問題](docs/KNOWN_ISSUES.md)。
- **自己的影片**: 分享用別人的影片 (動畫、MV、直播) 做成的附加內容可能侵犯著作權。只分享你自己製作或允許分享的影片。

## 更多

- [更新紀錄](CHANGELOG.md) · [已知問題](docs/KNOWN_ISSUES.md) · [效能](docs/PERFORMANCE.md) · [回報問題](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) (文件多為日語)
- 完整文件 (Python 安裝、GPU、3D 模式、開發、基準測試) 在 [日語 README](README.md) 中。
- 靈感來自 [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple)。影片處理使用 FFmpeg。非官方的粉絲作品，與 Mojang Studios 和 Microsoft 無關。
- 授權: [MIT](LICENSE)
