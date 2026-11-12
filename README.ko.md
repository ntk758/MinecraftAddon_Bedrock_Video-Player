# Minecraft Bedrock Video Player

<!-- 言語の切り替え (README の各言語で同じ。tests/test_readme_languages.py) -->
<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.md">日本語</a> ·
  <b>한국어</b> ·
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

영상 (MP4 등)을 진짜 Minecraft 블록으로 바꿔, Minecraft 베드락 에디션에서 소리와 함께 재생합니다. 영상을 고르고 버튼 하나를 누른 뒤, 만들어진 `.mcaddon`을 가져오기만 하면 됩니다.

<p align="center">
  <img src="docs/images/demo.gif" alt="왼쪽: 원본 영상, 오른쪽: 128×72 블록으로 바꾼 같은 영상" width="800">
  <br>
  <sub>왼쪽: 원본 / 오른쪽: 128×72 블록으로 변환 (게임 안과 같은 규칙으로 복원)</sub>
</p>

<p align="center">
  <img src="docs/images/ingame_trompe.gif" alt="게임 내 녹화: 플레이어의 눈에 맞춰 바닥과 벽에 그린 댄서" width="640">
  <br>
  <sub><b>시점에 맞춘 착시 그림</b> (실제 게임 내 녹화): 댄서를 당신의 눈에 보이는 그대로 바닥과 벽에 그리고, 걸을 때마다 다시 그려서 그곳에 서 있는 것처럼 보입니다</sub>
</p>

> 이 프로젝트 전체 (변환기, 애드온 스크립트, GUI, 이 README)는 AI가 설계하고 작성했습니다.

## 특징

- 🧱 **진짜 블록**: 최대 221종 (콘크리트, 테라코타, 양털, 목재, 원목, 돌, 광석…). 색은 게임 텍스처에서 실측하고, 장면마다 최대 127색을 자동으로 고릅니다.
- 🔊 일시 정지나 탐색 후에도 **소리가 맞춰집니다**.
- 🧊 주위를 걸어 다닐 수 있는 **3D 홀로그램**: 부조, 돌출, 둥근 입체, MagicaVoxel (`.vox`) 애니메이션.
- 🪄 **착시 그림**: 당신의 눈에 맞춰 그린 인물이 어디서 봐도 입체로 보입니다 (한 명에게만 정확).
- 🧭 나침반으로 여는 **리모컨**: 재생, 일시 정지, 정지, 탐색, 음량, 이전/다음 영상, 영상 목록.
- 📚 하나의 애드온에 여러 영상, 그리고 3D 영상의 평면판을 함께 넣을 수 있습니다.
- 🔓 **치트도 실험적 기능도 필요 없습니다** (안정판 Script API만 사용).
- 🌐 **10개 언어**: 메시지와 리모컨은 각 플레이어의 게임 언어를 따릅니다.
- 🖼 **이미지 방식 (시험판)**: 영상을 텍스처로 최대 30 fps로 보여 주는 별도의 애드온.

## 빠른 시작

**A. 샘플 체험 (변환 없음, 약 3분)**
1. [최신 릴리스](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest)에서 `BlockVideoPlayer-sample-….mcaddon` (평면) 또는 `BlockVideoPlayer-sample-3d-….mcaddon` (3D)를 내려받아 열면 Minecraft로 가져옵니다.
2. 월드 설정에서 행동 팩과 리소스 팩을 **둘 다** 켭니다 (같은 이름). 치트는 꺼 둔 채로 괜찮습니다.
3. 평평하고 넓은 곳으로 갑니다 (화면은 **그 자리의 블록을 바꿉니다**. 테스트용 월드를 쓰세요). 화면을 둘 방향을 보고 `/badapple:setup wall`, 그다음 `/badapple:start`를 입력합니다.
4. 나침반을 사용하면 리모컨이 열립니다. 화면이 없는 월드에 처음 들어가면 채팅에 이 순서가 짧게 표시됩니다.

**B. 브라우저에서 내 영상 변환 (설치 불필요, 베타)**
- [웹 변환기](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/)를 열고 영상을 고른 뒤 「.mcaddon 만들기」를 누릅니다. Windows, Mac, Chromebook, 휴대폰에서 쓸 수 있고, 영상은 브라우저 밖으로 나가지 않습니다.
- 그림, 카메라로 찍은 영상, `.vox`, 춤이 있는 MMD 모델 (`.pmx` + `.vmd`)도 변환됩니다. 다 만들면 Minecraft에서 어떻게 보일지 미리 볼 수 있습니다.
- **Minecraft 1.21.80~1.26.0**용은 「Minecraft 버전」에서 선택하세요 (이 애드온은 치트가 필요하고 `/scriptevent` 명령을 씁니다).

**C. Windows에서 변환 (더 빠르고 애드온이 더 작음)**
1. [최신 릴리스](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest)의 `BlockVideoPlayer-…-windows-x64.zip`을 풀고 `BlockVideoPlayer.exe`를 실행합니다 (Python 불필요). **FFmpeg가 따로 필요합니다**: `winget install Gyan.FFmpeg`를 실행하거나 `ffmpeg.exe`를 EXE 옆에 두세요.
2. 처음 실행하면 시작 가이드가 열려 PC를 확인하고, 설정을 추천하고, 재생 명령을 알려 줍니다. 가이드 없이: 「영상 추가」→「영상 비율에 맞추기」→「.mcaddon 만들기」.
3. **재생 부하 예상**이 「무거움」이면 너비, 높이 또는 fps를 낮추세요.

## 명령어

`badapple`은 명령어 접두사입니다 (변환할 때 정합니다). 관리자가 명령어를 쓸 수 있고, 치트를 켜면 예전의 `/scriptevent badapple:…` 형식도 됩니다.

| 명령어 | 동작 |
|---|---|
| `/badapple:setup` | 여기에 화면을 바닥에 눕혀 설치 (3D: 북쪽 2블록에 세움) |
| `/badapple:setup wall` | 평면 화면을 TV처럼 앞쪽 3블록에 세움 |
| `/badapple:setup face` | 눕힌 채로, 보는 방향으로 설치 |
| `/badapple:start` | 선택한 영상을 처음부터 재생 |
| `/badapple:play <ID>` / `/badapple:list` | 영상 재생 / 영상 목록 |
| `/badapple:remote` | 리모컨 열기 (나침반과 같음) |
| `/badapple:stop` | 정지하고 화면을 지움 |
| `/badapple:stats` | 직전 재생의 실제 fps, TPS, 늦은 프레임 |

## 이미지 방식 (시험판)

Windows 판이나 웹 변환기에서 「이미지 방식 애드온도 만들기」를 체크하세요 (또는 `python convert_image_mode.py video.mp4`). 두 번째 파일 `<이름>_image.mcaddon`이 생깁니다. 게임에서는 `/badapple_img:setup`, 그다음 `/badapple_img:start`. 블록을 바꾸지 않고 최대 30 fps로 재생하지만 텍스처 메모리를 쓰므로 짧게 쓰세요. **실제 Minecraft에서는 아직 확인하지 않았습니다.** 자세한 내용: [docs/IMAGE_MODE.md](docs/IMAGE_MODE.md) (일본어).

## 요구 사항

| | |
|---|---|
| Minecraft | 베드락 에디션 **1.26.10 이상** (1.21.80–1.26.0: 웹 변환기로 만들기) |
| 변환기 | Windows 10/11 EXE 또는 최신 브라우저. 소스에서: Python 3.12 + FFmpeg (명령줄은 Linux에서도 동작) |
| GPU (선택) | NVIDIA (CUDA), AMD (ROCm / DirectML), Intel (DirectML). CPU도 됩니다 |

Windows 11 PC 한 대에서만 확인했습니다. 휴대폰, 콘솔, 서버는 동작할 것으로 예상하지만 **아직 확인하지 않았습니다** ([호환성](docs/COMPATIBILITY.md)).

## 자주 묻는 질문

- **Java 에디션?** 아니요, 베드락 전용입니다.
- **소리가 안 나요?** 두 팩을 모두 켜고, 리모컨과 게임의 음량을 확인하고, 가져온 뒤 Minecraft를 다시 시작하세요.
- **끊겨요?** 크기나 fps를 낮추고 `/badapple:stats`를 확인하세요. 자세한 내용은 [알려진 문제](docs/KNOWN_ISSUES.md).
- **내 영상**: 다른 사람의 영상 (애니메이션, 뮤직비디오, 방송)으로 만든 애드온을 공유하면 저작권 침해가 될 수 있습니다. 직접 만들었거나 공유가 허락된 영상만 공유하세요.

## 더 보기

- [변경 기록](CHANGELOG.md) · [알려진 문제](docs/KNOWN_ISSUES.md) · [성능](docs/PERFORMANCE.md) · [문제 신고](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) (문서는 대부분 일본어)
- 전체 문서 (Python 설정, GPU, 3D 모드, 개발, 벤치마크)는 [일본어 README](README.md)에 있습니다.
- [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple)에서 영감을 받았습니다. 영상 처리는 FFmpeg. 비공식 팬 프로젝트이며 Mojang Studios, Microsoft와 관계없습니다.
- 라이선스: [MIT](LICENSE)
