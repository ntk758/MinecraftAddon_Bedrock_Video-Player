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
  <b>Español</b> ·
  <a href="README.pt-BR.md">Português</a> ·
  <a href="README.ru.md">Русский</a>
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Minecraft Bedrock](https://img.shields.io/badge/Minecraft-Bedrock-brightgreen.svg)](https://www.minecraft.net/)
[![Release](https://img.shields.io/github/v/release/ntk758/MinecraftAddon_Bedrock_Video-Player)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest)
[![CI](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml/badge.svg)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml)

Convierte un vídeo (MP4 y más) en bloques reales de Minecraft y reprodúcelo con sonido en Minecraft Bedrock Edition. Elige un vídeo, pulsa un botón e importa el `.mcaddon`.

<p align="center">
  <img src="docs/images/demo.gif" alt="Izquierda: vídeo original, derecha: el mismo vídeo en 128×72 bloques de Minecraft" width="800">
  <br>
  <sub>Izquierda: original / Derecha: convertido a 128×72 bloques (reconstruido con las reglas del juego)</sub>
</p>

<p align="center">
  <img src="docs/images/ingame_trompe.gif" alt="Grabación en el juego: una bailarina pintada en el suelo y la pared para el ojo del jugador" width="640">
  <br>
  <sub><b>Trampantojo según el punto de vista</b> (grabación real en el juego): la bailarina se pinta en el suelo y las paredes tal como la ve tu ojo, y se vuelve a pintar al caminar, así que parece estar de pie allí</sub>
</p>

> Todo el proyecto (conversor, scripts del add-on, interfaz y este README) lo diseñó y escribió una IA.

## Características

- 🧱 **Bloques reales**: hasta 221 tipos (hormigón, terracota, lana, madera, troncos, piedra, minerales…). Los colores se miden en las texturas del juego y cada escena elige automáticamente hasta 127 colores.
- 🔊 **El sonido se mantiene sincronizado**, incluso tras pausar o saltar.
- 🧊 **Hologramas 3D** que puedes rodear: relieve, siluetas extruidas o redondeadas y animaciones de MagicaVoxel (`.vox`).
- 🪄 **Trampantojo**: una figura pintada para tu ojo que parece sólida desde donde estés (correcto para un solo espectador).
- 🧭 **Mando a distancia** con una brújula: reproducir, pausa, detener, saltar, volumen, vídeo siguiente/anterior, lista de vídeos.
- 📚 Varios vídeos en un add-on y, si quieres, la versión plana de un vídeo 3D en el mismo add-on.
- 🔓 **Sin trucos ni funciones experimentales** (solo la Script API estable).
- 🌐 **10 idiomas**: los mensajes y el mando siguen el idioma del juego de cada jugador.
- 🖼 **Modo imagen (experimental)**: un add-on aparte que muestra el vídeo como texturas a hasta 30 fps.

## Inicio rápido

**A. Prueba el ejemplo (sin conversión, unos 3 minutos)**
1. Descarga `BlockVideoPlayer-sample-….mcaddon` (plano) o `BlockVideoPlayer-sample-3d-….mcaddon` (3D) de la [última versión](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) y ábrelo para importarlo en Minecraft.
2. En los ajustes del mundo, activa **los dos**: el paquete de comportamiento y el de recursos (mismo nombre). Los trucos pueden seguir desactivados.
3. Ve a un terreno llano y despejado (la pantalla **reemplaza los bloques de ese lugar**; usa un mundo de prueba). Mira hacia donde quieras la pantalla y escribe `/badapple:setup wall` y luego `/badapple:start`.
4. Usa una brújula para abrir el mando. La primera vez que entras en un mundo sin pantalla, un mensaje breve en el chat te recuerda estos pasos.

**B. Convierte tu vídeo en el navegador (sin instalar, beta)**
- Abre el [conversor web](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/), elige vídeos y pulsa *Crear .mcaddon*. Funciona en Windows, Mac, Chromebook y móviles; tus vídeos nunca salen del navegador.
- También sirven imágenes, vídeos de cámara, `.vox` y modelos MMD con baile (`.pmx` + `.vmd`). Al terminar puedes ver cómo se verá en Minecraft.
- Para **Minecraft 1.21.80 a 1.26.0**, elígelo en *Versión de Minecraft* (esos add-ons necesitan trucos y usan comandos `/scriptevent`).

**C. Convierte en Windows (más rápido, add-ons más pequeños)**
1. Descomprime `BlockVideoPlayer-…-windows-x64.zip` de la [última versión](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) y ejecuta `BlockVideoPlayer.exe` (no hace falta Python). **FFmpeg se necesita aparte**: ejecuta `winget install Gyan.FFmpeg` o pon `ffmpeg.exe` junto al EXE.
2. La guía de inicio se abre la primera vez: revisa tu PC, sugiere ajustes y muestra los comandos para reproducir. Sin ella: *Añadir vídeo* → *Ajustar a la proporción del vídeo* → *Crear .mcaddon*.
3. Si la **estimación de carga** dice *pesado*, baja el ancho, el alto o los fps.

## Comandos

`badapple` es el prefijo de los comandos (lo eliges al convertir). Los operadores pueden usar los comandos; con trucos activados también funciona la forma antigua `/scriptevent badapple:…`.

| Comando | Qué hace |
|---|---|
| `/badapple:setup` | Colocar la pantalla aquí, plana en el suelo (3D: de pie a 2 bloques al norte) |
| `/badapple:setup wall` | Poner una pantalla plana de pie como una tele, 3 bloques delante de ti |
| `/badapple:setup face` | Plana, pero orientada hacia donde miras |
| `/badapple:start` | Reproducir desde el principio el vídeo elegido |
| `/badapple:play <ID>` / `/badapple:list` | Reproducir un vídeo / listar los vídeos |
| `/badapple:remote` | Abrir el mando (igual que la brújula) |
| `/badapple:stop` | Detener y borrar la pantalla |
| `/badapple:stats` | fps reales, TPS y fotogramas con retraso de la última reproducción |

## Modo imagen (experimental)

Marca *Crear también el add-on en modo imagen* en la app de Windows o en el conversor web (o ejecuta `python convert_image_mode.py video.mp4`). Obtienes un segundo archivo, `<nombre>_image.mcaddon`. En el juego: `/badapple_img:setup` y luego `/badapple_img:start`. Reproduce a hasta 30 fps sin reemplazar bloques, pero usa memoria de texturas, así que úsalo con vídeos cortos. **Aún no probado en el Minecraft real.** Detalles: [docs/IMAGE_MODE.md](docs/IMAGE_MODE.md) (en japonés).

## Requisitos

| | |
|---|---|
| Minecraft | Bedrock Edition **1.26.10 o posterior** (1.21.80–1.26.0: créalo en el conversor web) |
| Conversor | EXE para Windows 10/11 o cualquier navegador moderno. Desde el código: Python 3.12 + FFmpeg (la línea de comandos también funciona en Linux) |
| GPU (opcional) | NVIDIA (CUDA), AMD (ROCm / DirectML) o Intel (DirectML); la CPU también sirve |

Probado en un solo PC con Windows 11. Se espera que funcione en móviles, consolas y servidores, pero **aún no se ha probado** ([compatibilidad](docs/COMPATIBILITY.md)).

## Preguntas frecuentes

- **¿Java Edition?** No, solo Bedrock.
- **¿Sin sonido?** Activa los dos paquetes, revisa el volumen en el mando y en el juego, y reinicia Minecraft después de importar.
- **¿Va a tirones?** Baja el tamaño o los fps y mira `/badapple:stats`. Más en [problemas conocidos](docs/KNOWN_ISSUES.md).
- **Tus propios vídeos**: compartir un add-on hecho con vídeos de otras personas (anime, videoclips, directos) puede infringir los derechos de autor. Comparte solo vídeos que hayas hecho tú o que se puedan compartir.

## Más

- [Historial de cambios](CHANGELOG.md) · [Problemas conocidos](docs/KNOWN_ISSUES.md) · [Rendimiento](docs/PERFORMANCE.md) · [Informar de un problema](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) (documentos sobre todo en japonés)
- La documentación completa (instalación de Python, GPU, modos 3D, desarrollo, pruebas de rendimiento) está en el [README en japonés](README.md).
- Inspirado en [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple). Procesamiento de vídeo con FFmpeg. Proyecto de fans no oficial, sin relación con Mojang Studios ni Microsoft.
- Licencia: [MIT](LICENSE)
