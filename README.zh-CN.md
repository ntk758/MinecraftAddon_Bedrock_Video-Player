# Minecraft Bedrock Video Player

<!-- 言語の切り替え (README の各言語で同じ。tests/test_readme_languages.py) -->
<p align="center">
  <a href="README.en.md">English</a> ·
  <b>简体中文</b> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
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

把视频 (MP4 等) 转换成真正的 Minecraft 方块，在 Minecraft 基岩版中带声音播放。选好视频，按一个按钮，再导入生成的 `.mcaddon` 即可。

<p align="center">
  <img src="docs/images/demo.gif" alt="左: 原视频，右: 转换成 128×72 方块的同一视频" width="800">
  <br>
  <sub>左: 原视频 / 右: 转换成 128×72 方块 (按游戏内的规则还原)</sub>
</p>

<p align="center">
  <img src="docs/images/ingame_trompe.gif" alt="游戏内录像: 按玩家视线画在地面和墙上的舞者" width="640">
  <br>
  <sub><b>随视角变化的错视画</b> (真实的游戏内录像): 舞者按你眼睛看到的样子画在地面和墙上，你走动时会重新绘制，所以看起来像站在那里</sub>
</p>

> 整个项目 (转换工具、附加包脚本、GUI 和本 README) 都由 AI 设计和编写。

## 特点

- 🧱 **真实的方块**: 最多 221 种 (混凝土、陶瓦、羊毛、木板、原木、石头、矿石…)。颜色从游戏贴图实测，每个场景自动选择最多 127 种颜色。
- 🔊 **声音保持同步**，暂停和跳转后也一样。
- 🧊 可以绕着走的 **3D 全息影像**: 浮雕、挤出、圆润的立体，以及 MagicaVoxel (`.vox`) 动画。
- 🪄 **错视画**: 按你的视线绘制的人物，从哪里看都像立体的 (只对一名观看者正确)。
- 🧭 用指南针打开 **遥控器**: 播放、暂停、停止、跳转、音量、上一个/下一个视频、视频列表。
- 📚 一个附加包可以放多个视频，也可以把 3D 视频的平面版一起放进去。
- 🔓 **不需要作弊，也不需要实验性玩法** (只用稳定版 Script API)。
- 🌐 **10 种语言**: 消息和遥控器跟随每位玩家的游戏语言。
- 🖼 **图像模式 (试作)**: 另一个附加包，用贴图以最高 30 fps 显示视频。

## 快速开始

**A. 试用示例 (无需转换，约 3 分钟)**
1. 从 [最新发布](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) 下载 `BlockVideoPlayer-sample-….mcaddon` (平面) 或 `BlockVideoPlayer-sample-3d-….mcaddon` (3D)，打开即可导入 Minecraft。
2. 在世界设置中 **同时** 启用行为包和资源包 (名称相同)。作弊保持关闭即可。
3. 到平坦空旷的地方 (屏幕会 **替换那里的方块**，请使用测试世界)。面向想放屏幕的方向，输入 `/badapple:setup wall`，然后输入 `/badapple:start`。
4. 使用指南针打开遥控器。第一次进入还没有屏幕的世界时，聊天栏会简短地提示这些步骤。

**B. 在浏览器中转换自己的视频 (无需安装，测试版)**
- 打开 [网页版转换工具](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/)，选择视频，按「创建 .mcaddon」。可在 Windows、Mac、Chromebook 和手机上使用；视频不会离开浏览器。
- 图片、相机拍的视频、`.vox`、带舞蹈的 MMD 模型 (`.pmx` + `.vmd`) 也可以转换。完成后可以预览在 Minecraft 中的样子。
- 对于 **Minecraft 1.21.80 至 1.26.0**，请在「Minecraft 版本」中选择它 (这类附加包需要作弊，并使用 `/scriptevent` 命令)。

**C. 在 Windows 上转换 (更快，附加包更小)**
1. 从 [最新发布](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) 下载 `BlockVideoPlayer-…-windows-x64.zip`，解压后运行 `BlockVideoPlayer.exe` (无需 Python)。**另外需要 FFmpeg**: 运行 `winget install Gyan.FFmpeg`，或把 `ffmpeg.exe` 放在 EXE 旁边。
2. 第一次启动会打开入门向导: 检查电脑、推荐设置，并显示播放用的命令。不用向导时: 「添加视频」→「匹配视频宽高比」→「创建 .mcaddon」。
3. 如果 **播放负荷估计** 显示「重」，请降低宽度、高度或 fps。

## 命令

`badapple` 是命令前缀 (转换时自己决定)。管理员可以使用这些命令；开启作弊时，旧的 `/scriptevent badapple:…` 写法也能用。

| 命令 | 作用 |
|---|---|
| `/badapple:setup` | 在这里把屏幕平放在地面 (3D: 立在北边 2 格处) |
| `/badapple:setup wall` | 把平面屏幕像电视一样立在你前方 3 格处 |
| `/badapple:setup face` | 平放，但朝向你看的方向 |
| `/badapple:start` | 从头播放选中的视频 |
| `/badapple:play <ID>` / `/badapple:list` | 播放视频 / 列出视频 |
| `/badapple:remote` | 打开遥控器 (与指南针相同) |
| `/badapple:stop` | 停止并清除屏幕 |
| `/badapple:stats` | 上一次播放的实际 fps、TPS 和延迟的帧 |

## 图像模式 (试作)

在 Windows 版或网页版中勾选「也创建图像模式的附加包」(或运行 `python convert_image_mode.py video.mp4`)，会得到第二个文件 `<名称>_image.mcaddon`。游戏中: `/badapple_img:setup`，然后 `/badapple_img:start`。它不替换方块，最高 30 fps 播放，但会占用贴图内存，所以请保持视频简短。**尚未在真实的 Minecraft 中测试。** 详情: [docs/IMAGE_MODE.md](docs/IMAGE_MODE.md) (日语)。

## 环境要求

| | |
|---|---|
| Minecraft | 基岩版 **1.26.10 或更新** (1.21.80–1.26.0: 用网页版制作) |
| 转换工具 | Windows 10/11 的 EXE，或任何现代浏览器。从源码运行: Python 3.12 + FFmpeg (命令行在 Linux 上也可用) |
| GPU (可选) | NVIDIA (CUDA)、AMD (ROCm / DirectML) 或 Intel (DirectML)；CPU 也可以 |

只在一台 Windows 11 电脑上测试过。手机、主机和服务器预计可以运行，但 **尚未测试** ([兼容性](docs/COMPATIBILITY.md))。

## 常见问题

- **Java 版?** 不支持，仅限基岩版。
- **没有声音?** 确认两个包都已启用，检查遥控器和游戏中的音量，导入后重启 Minecraft。
- **卡顿?** 降低尺寸或 fps，并查看 `/badapple:stats`。更多见 [已知问题](docs/KNOWN_ISSUES.md)。
- **自己的视频**: 分享用别人的视频 (动画、MV、直播) 做成的附加包可能侵犯版权。只分享你自己制作或允许分享的视频。

## 更多

- [更新记录](CHANGELOG.md) · [已知问题](docs/KNOWN_ISSUES.md) · [性能](docs/PERFORMANCE.md) · [报告问题](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) (文档多为日语)
- 完整文档 (Python 安装、GPU、3D 模式、开发、基准测试) 在 [日语 README](README.md) 中。
- 灵感来自 [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple)。视频处理使用 FFmpeg。非官方的粉丝作品，与 Mojang Studios 和 Microsoft 无关。
- 许可证: [MIT](LICENSE)
