# Minecraft Bedrock Video Player

<!-- 言語の切り替え (README の各言語で同じ。tests/test_readme_languages.py) -->
<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <b>Deutsch</b> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.pt-BR.md">Português</a> ·
  <a href="README.ru.md">Русский</a>
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Minecraft Bedrock](https://img.shields.io/badge/Minecraft-Bedrock-brightgreen.svg)](https://www.minecraft.net/)
[![Release](https://img.shields.io/github/v/release/ntk758/MinecraftAddon_Bedrock_Video-Player)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest)
[![CI](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml/badge.svg)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml)

Verwandle ein Video (MP4 und mehr) in echte Minecraft-Blöcke und spiele es in der Minecraft Bedrock Edition mit Ton ab. Video wählen, einen Knopf drücken und die `.mcaddon` importieren.

<p align="center">
  <img src="docs/images/demo.gif" alt="Links: Originalvideo, rechts: dasselbe Video als 128×72 Minecraft-Blöcke" width="800">
  <br>
  <sub>Links: Original / Rechts: in 128×72 Blöcke umgewandelt (nach den Regeln im Spiel nachgebaut)</sub>
</p>

<p align="center">
  <img src="docs/images/ingame_trompe.gif" alt="Aufnahme im Spiel: eine Tänzerin, für das Auge des Spielers auf Boden und Wand gemalt" width="640">
  <br>
  <sub><b>Blickabhängiges Trompe-l'œil</b> (echte Aufnahme im Spiel): Die Tänzerin wird so auf Boden und Wände gemalt, wie dein Auge sie sieht, und beim Gehen neu gemalt – sie wirkt, als stünde sie wirklich da</sub>
</p>

> Das ganze Projekt (Konverter, Add-on-Skripte, GUI und diese README) wurde von einer KI entworfen und geschrieben.

## Funktionen

- 🧱 **Echte Blöcke**: bis zu 221 Arten (Beton, Terrakotta, Wolle, Holz, Stämme, Stein, Erze…). Die Farben sind aus den Spieltexturen gemessen; jede Szene wählt automatisch bis zu 127 Farben.
- 🔊 **Ton bleibt synchron**, auch nach Pause und Spulen.
- 🧊 **3D-Hologramme** zum Herumlaufen: Relief, extrudierte oder gerundete Figuren und MagicaVoxel-Animationen (`.vox`).
- 🪄 **Trompe-l'œil**: eine für dein Auge gemalte Figur, die von überall räumlich wirkt (richtig für einen Zuschauer).
- 🧭 **Fernbedienung** mit dem Kompass: Abspielen, Pause, Stopp, Spulen, Lautstärke, nächstes/vorheriges Video, Videoliste.
- 📚 Mehrere Videos in einem Add-on und optional eine flache Version eines 3D-Videos im selben Add-on.
- 🔓 **Keine Cheats und keine experimentellen Funktionen** nötig (nur die stabile Script API).
- 🌐 **10 Sprachen**: Meldungen und Fernbedienung folgen der Spielsprache jedes Spielers.
- 🖼 **Bildmodus (experimentell)**: ein eigenes Add-on, das das Video als Texturen mit bis zu 30 fps zeigt.

## Schnellstart

**A. Beispiel ausprobieren (ohne Umwandlung, etwa 3 Minuten)**
1. Lade `BlockVideoPlayer-sample-….mcaddon` (flach) oder `BlockVideoPlayer-sample-3d-….mcaddon` (3D) vom [neuesten Release](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) herunter und öffne die Datei, um sie in Minecraft zu importieren.
2. Aktiviere in den Welteinstellungen **beide** Pakete, das Verhaltenspaket und das Ressourcenpaket (gleicher Name). Cheats können aus bleiben.
3. Geh auf flaches, freies Gelände (der Bildschirm **ersetzt die Blöcke dort**, nimm also eine Testwelt). Schau dorthin, wo der Bildschirm stehen soll, und gib `/badapple:setup wall` und dann `/badapple:start` ein.
4. Benutze einen Kompass, um die Fernbedienung zu öffnen. Wenn du zum ersten Mal eine Welt ohne Bildschirm betrittst, nennt dir eine kurze Chatmeldung diese Schritte.

**B. Eigenes Video im Browser umwandeln (ohne Installation, Beta)**
- Öffne den [Web-Konverter](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/), wähle Videos und drücke *.mcaddon erstellen*. Läuft unter Windows, Mac, Chromebook und auf Handys; deine Videos verlassen den Browser nicht.
- Auch Bilder, Kameraclips, `.vox` und MMD-Modelle mit Tanz (`.pmx` + `.vmd`) funktionieren. Danach siehst du eine Vorschau, wie es in Minecraft aussieht.
- Für **Minecraft 1.21.80 bis 1.26.0** wähle das unter *Minecraft-Version* (diese Add-ons brauchen Cheats und nutzen `/scriptevent`-Befehle).

**C. Unter Windows umwandeln (schneller, kleinere Add-ons)**
1. Entpacke `BlockVideoPlayer-…-windows-x64.zip` vom [neuesten Release](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) und starte `BlockVideoPlayer.exe` (kein Python nötig). **FFmpeg wird extra gebraucht**: `winget install Gyan.FFmpeg` ausführen oder `ffmpeg.exe` neben die EXE legen.
2. Beim ersten Start öffnet sich die Einstiegshilfe: Sie prüft deinen PC, schlägt Einstellungen vor und zeigt die Befehle zum Abspielen. Ohne sie: *Video hinzufügen* → *An Seitenverhältnis anpassen* → *.mcaddon erstellen*.
3. Wenn die **Lastschätzung** *schwer* anzeigt, verringere Breite, Höhe oder fps.

## Befehle

`badapple` ist das Befehlspräfix (du legst es beim Umwandeln fest). Operatoren können die Befehle nutzen; mit Cheats geht auch die alte Form `/scriptevent badapple:…`.

| Befehl | Wirkung |
|---|---|
| `/badapple:setup` | Bildschirm hier flach auf den Boden legen (3D: steht 2 Blöcke nördlich) |
| `/badapple:setup wall` | Flachen Bildschirm wie einen Fernseher 3 Blöcke vor dir aufstellen |
| `/badapple:setup face` | Flach legen, aber in deine Blickrichtung |
| `/badapple:start` | Gewähltes Video von vorn abspielen |
| `/badapple:play <ID>` / `/badapple:list` | Video abspielen / Videos auflisten |
| `/badapple:remote` | Fernbedienung öffnen (wie der Kompass) |
| `/badapple:stop` | Stoppen und Bildschirm leeren |
| `/badapple:stats` | Echte fps, TPS und verspätete Frames der letzten Wiedergabe |

## Bildmodus (experimentell)

Setze in der Windows-App oder im Web-Konverter den Haken *Auch das Bild-Add-on erstellen* (oder `python convert_image_mode.py video.mp4`). Du bekommst eine zweite Datei, `<Name>_image.mcaddon`. Im Spiel: `/badapple_img:setup`, dann `/badapple_img:start`. Es spielt mit bis zu 30 fps, ohne Blöcke zu ersetzen, braucht aber Texturspeicher – halte es kurz. **Noch nicht im echten Minecraft getestet.** Details: [docs/IMAGE_MODE.md](docs/IMAGE_MODE.md) (Japanisch).

## Voraussetzungen

| | |
|---|---|
| Minecraft | Bedrock Edition **1.26.10 oder neuer** (1.21.80–1.26.0: im Web-Konverter erstellen) |
| Konverter | Windows 10/11 EXE oder ein aktueller Browser. Aus dem Quellcode: Python 3.12 + FFmpeg (die Kommandozeile läuft auch unter Linux) |
| GPU (optional) | NVIDIA (CUDA), AMD (ROCm / DirectML) oder Intel (DirectML); die CPU geht auch |

Getestet auf einem Windows-11-PC. Handys, Konsolen und Server sollten funktionieren, sind aber **noch nicht getestet** ([Kompatibilität](docs/COMPATIBILITY.md)).

## FAQ

- **Java Edition?** Nein, nur Bedrock.
- **Kein Ton?** Beide Pakete aktivieren, die Lautstärke in der Fernbedienung und im Spiel prüfen und Minecraft nach dem Import neu starten.
- **Ruckeln?** Größe oder fps verringern und `/badapple:stats` prüfen. Mehr unter [bekannte Probleme](docs/KNOWN_ISSUES.md).
- **Eigene Videos**: Ein Add-on aus fremden Videos (Anime, Musikvideos, Streams) weiterzugeben, kann das Urheberrecht verletzen. Teile nur Videos, die du selbst gemacht hast oder teilen darfst.

## Mehr

- [Änderungen](CHANGELOG.md) · [Bekannte Probleme](docs/KNOWN_ISSUES.md) · [Leistung](docs/PERFORMANCE.md) · [Problem melden](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) (Dokumente meist auf Japanisch)
- Die vollständige Dokumentation (Python-Einrichtung, GPU, 3D-Modi, Entwicklung, Benchmarks) steht in der [japanischen README](README.md).
- Inspiriert von [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple). Videoverarbeitung mit FFmpeg. Inoffizielles Fanprojekt, nicht mit Mojang Studios oder Microsoft verbunden.
- Lizenz: [MIT](LICENSE)
