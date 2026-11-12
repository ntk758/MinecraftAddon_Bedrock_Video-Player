# Minecraft Bedrock Video Player

<!-- 言語の切り替え (README の各言語で同じ。tests/test_readme_languages.py) -->
<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.de.md">Deutsch</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <b>Português</b> ·
  <a href="README.ru.md">Русский</a>
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Minecraft Bedrock](https://img.shields.io/badge/Minecraft-Bedrock-brightgreen.svg)](https://www.minecraft.net/)
[![Release](https://img.shields.io/github/v/release/ntk758/MinecraftAddon_Bedrock_Video-Player)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest)
[![CI](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml/badge.svg)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml)

Transforme um vídeo (MP4 e outros) em blocos reais do Minecraft e reproduza-o com som no Minecraft Bedrock Edition. Escolha um vídeo, aperte um botão e importe o `.mcaddon`.

<p align="center">
  <img src="docs/images/demo.gif" alt="Esquerda: vídeo original, direita: o mesmo vídeo em 128×72 blocos do Minecraft" width="800">
  <br>
  <sub>Esquerda: original / Direita: convertido em 128×72 blocos (reconstruído com as regras do jogo)</sub>
</p>

<p align="center">
  <img src="docs/images/ingame_trompe.gif" alt="Gravação no jogo: uma dançarina pintada no chão e na parede para o olho do jogador" width="640">
  <br>
  <sub><b>Trompe-l'œil conforme o ponto de vista</b> (gravação real no jogo): a dançarina é pintada no chão e nas paredes do jeito que o seu olho a vê e repintada enquanto você anda, então parece estar ali de pé</sub>
</p>

> O projeto inteiro (conversor, scripts do add-on, interface e este README) foi projetado e escrito por IA.

## Recursos

- 🧱 **Blocos de verdade**: até 221 tipos (concreto, terracota, lã, madeira, troncos, pedra, minérios…). As cores são medidas nas texturas do jogo, e cada cena escolhe automaticamente até 127 cores.
- 🔊 **O som fica sincronizado**, mesmo depois de pausar ou avançar.
- 🧊 **Hologramas 3D** para andar ao redor: relevo, silhuetas extrudadas ou arredondadas e animações do MagicaVoxel (`.vox`).
- 🪄 **Trompe-l'œil**: uma figura pintada para o seu olho que parece sólida de onde você estiver (correto para um espectador).
- 🧭 **Controle remoto** com uma bússola: tocar, pausar, parar, avançar, volume, vídeo seguinte/anterior, lista de vídeos.
- 📚 Vários vídeos em um add-on e, se quiser, a versão plana de um vídeo 3D no mesmo add-on.
- 🔓 **Sem trapaças e sem recursos experimentais** (só a Script API estável).
- 🌐 **10 idiomas**: as mensagens e o controle seguem o idioma do jogo de cada jogador.
- 🖼 **Modo de imagem (experimental)**: um add-on separado que mostra o vídeo como texturas a até 30 fps.

## Início rápido

**A. Experimente o exemplo (sem conversão, cerca de 3 minutos)**
1. Baixe `BlockVideoPlayer-sample-….mcaddon` (plano) ou `BlockVideoPlayer-sample-3d-….mcaddon` (3D) da [versão mais recente](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) e abra o arquivo para importá-lo no Minecraft.
2. Nas configurações do mundo, ative **os dois**: o pacote de comportamento e o pacote de recursos (mesmo nome). As trapaças podem ficar desligadas.
3. Vá para um terreno plano e aberto (a tela **substitui os blocos ali**, use um mundo de teste). Olhe para onde quer a tela e digite `/badapple:setup wall` e depois `/badapple:start`.
4. Use uma bússola para abrir o controle. Na primeira vez que você entra num mundo sem tela, uma mensagem curta no chat mostra estes passos.

**B. Converta seu vídeo no navegador (sem instalar, beta)**
- Abra o [conversor web](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/), escolha vídeos e aperte *Criar .mcaddon*. Funciona no Windows, Mac, Chromebook e celular; seus vídeos nunca saem do navegador.
- Imagens, vídeos de câmera, `.vox` e modelos MMD com dança (`.pmx` + `.vmd`) também funcionam. Depois de criar, dá para ver uma prévia de como vai ficar no Minecraft.
- Para **Minecraft 1.21.80 a 1.26.0**, escolha isso em *Versão do Minecraft* (esses add-ons precisam de trapaças e usam comandos `/scriptevent`).

**C. Converta no Windows (mais rápido, add-ons menores)**
1. Descompacte `BlockVideoPlayer-…-windows-x64.zip` da [versão mais recente](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) e rode `BlockVideoPlayer.exe` (não precisa de Python). **O FFmpeg é necessário à parte**: rode `winget install Gyan.FFmpeg` ou coloque `ffmpeg.exe` ao lado do EXE.
2. O guia de primeiros passos abre na primeira vez: verifica seu PC, sugere configurações e mostra os comandos para reproduzir. Sem ele: *Adicionar vídeo* → *Ajustar à proporção do vídeo* → *Criar .mcaddon*.
3. Se a **estimativa de carga** disser *pesado*, diminua a largura, a altura ou os fps.

## Comandos

`badapple` é o prefixo dos comandos (você escolhe ao converter). Operadores podem usar os comandos; com trapaças ligadas, a forma antiga `/scriptevent badapple:…` também funciona.

| Comando | O que faz |
|---|---|
| `/badapple:setup` | Colocar a tela aqui, deitada no chão (3D: em pé a 2 blocos ao norte) |
| `/badapple:setup wall` | Pôr uma tela plana em pé como uma TV, 3 blocos à sua frente |
| `/badapple:setup face` | Deitada, mas virada para onde você olha |
| `/badapple:start` | Tocar do começo o vídeo escolhido |
| `/badapple:play <ID>` / `/badapple:list` | Tocar um vídeo / listar os vídeos |
| `/badapple:remote` | Abrir o controle (igual à bússola) |
| `/badapple:stop` | Parar e limpar a tela |
| `/badapple:stats` | fps reais, TPS e quadros atrasados da última reprodução |

## Modo de imagem (experimental)

Marque *Também criar o add-on do modo de imagem* no app do Windows ou no conversor web (ou rode `python convert_image_mode.py video.mp4`). Você recebe um segundo arquivo, `<nome>_image.mcaddon`. No jogo: `/badapple_img:setup` e depois `/badapple_img:start`. Ele toca a até 30 fps sem substituir blocos, mas usa memória de texturas, então use vídeos curtos. **Ainda não testado no Minecraft de verdade.** Detalhes: [docs/IMAGE_MODE.md](docs/IMAGE_MODE.md) (em japonês).

## Requisitos

| | |
|---|---|
| Minecraft | Bedrock Edition **1.26.10 ou mais recente** (1.21.80–1.26.0: crie no conversor web) |
| Conversor | EXE para Windows 10/11 ou qualquer navegador moderno. Pelo código-fonte: Python 3.12 + FFmpeg (a linha de comando também funciona no Linux) |
| GPU (opcional) | NVIDIA (CUDA), AMD (ROCm / DirectML) ou Intel (DirectML); a CPU também serve |

Testado em um só PC com Windows 11. Celulares, consoles e servidores devem funcionar, mas **ainda não foram testados** ([compatibilidade](docs/COMPATIBILITY.md)).

## Perguntas frequentes

- **Java Edition?** Não, só Bedrock.
- **Sem som?** Ative os dois pacotes, confira o volume no controle e no jogo, e reinicie o Minecraft depois de importar.
- **Travando?** Diminua o tamanho ou os fps e veja `/badapple:stats`. Mais em [problemas conhecidos](docs/KNOWN_ISSUES.md).
- **Seus próprios vídeos**: compartilhar um add-on feito com vídeos de outras pessoas (anime, clipes, lives) pode violar direitos autorais. Compartilhe só vídeos que você fez ou que podem ser compartilhados.

## Mais

- [Histórico de mudanças](CHANGELOG.md) · [Problemas conhecidos](docs/KNOWN_ISSUES.md) · [Desempenho](docs/PERFORMANCE.md) · [Relatar um problema](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) (documentos quase todos em japonês)
- A documentação completa (instalação do Python, GPU, modos 3D, desenvolvimento, medições) está no [README em japonês](README.md).
- Inspirado em [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple). Processamento de vídeo com FFmpeg. Projeto de fã não oficial, sem relação com a Mojang Studios ou a Microsoft.
- Licença: [MIT](LICENSE)
