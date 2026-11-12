# Minecraft Bedrock Video Player

<!-- 言語の切り替え (README の各言語で同じ。tests/test_readme_languages.py) -->
<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.de.md">Deutsch</a> ·
  <b>Français</b> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.pt-BR.md">Português</a> ·
  <a href="README.ru.md">Русский</a>
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Minecraft Bedrock](https://img.shields.io/badge/Minecraft-Bedrock-brightgreen.svg)](https://www.minecraft.net/)
[![Release](https://img.shields.io/github/v/release/ntk758/MinecraftAddon_Bedrock_Video-Player)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest)
[![CI](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml/badge.svg)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml)

Transformez une vidéo (MP4 et autres) en vrais blocs Minecraft et lisez-la avec le son dans Minecraft Bedrock Edition. Choisissez une vidéo, appuyez sur un bouton, puis importez le `.mcaddon`.

<p align="center">
  <img src="docs/images/demo.gif" alt="À gauche : vidéo source, à droite : la même vidéo en 128×72 blocs Minecraft" width="800">
  <br>
  <sub>À gauche : source / À droite : convertie en 128×72 blocs (reconstruite avec les règles du jeu)</sub>
</p>

<p align="center">
  <img src="docs/images/ingame_trompe.gif" alt="Enregistrement en jeu : une danseuse peinte au sol et au mur pour l'œil du joueur" width="640">
  <br>
  <sub><b>Trompe-l'œil selon le point de vue</b> (vrai enregistrement en jeu) : la danseuse est peinte au sol et sur les murs telle que votre œil la voit, et repeinte quand vous marchez, si bien qu'elle semble vraiment debout là</sub>
</p>

> Tout le projet (convertisseur, scripts de l'add-on, interface et ce README) a été conçu et écrit par une IA.

## Fonctionnalités

- 🧱 **De vrais blocs** : jusqu'à 221 sortes (béton, terre cuite, laine, bois, bûches, pierre, minerais…). Les couleurs sont mesurées sur les textures du jeu, et chaque scène choisit automatiquement jusqu'à 127 couleurs.
- 🔊 **Le son reste synchronisé**, même après une pause ou un déplacement dans la vidéo.
- 🧊 **Hologrammes 3D** dont on peut faire le tour : relief, silhouettes extrudées ou arrondies, et animations MagicaVoxel (`.vox`).
- 🪄 **Trompe-l'œil** : une silhouette peinte pour votre œil, qui paraît en volume où que vous soyez (exact pour un seul spectateur).
- 🧭 **Télécommande** avec une boussole : lecture, pause, arrêt, avance/retour, volume, vidéo suivante/précédente, liste des vidéos.
- 📚 Plusieurs vidéos dans un même add-on, et en option la version plate d'une vidéo 3D dans le même add-on.
- 🔓 **Ni triche ni fonctionnalités expérimentales** (uniquement la Script API stable).
- 🌐 **10 langues** : les messages et la télécommande suivent la langue du jeu de chaque joueur.
- 🖼 **Mode image (expérimental)** : un add-on séparé qui affiche la vidéo en textures jusqu'à 30 ips.

## Démarrage rapide

**A. Essayer l'exemple (sans conversion, environ 3 minutes)**
1. Téléchargez `BlockVideoPlayer-sample-….mcaddon` (plat) ou `BlockVideoPlayer-sample-3d-….mcaddon` (3D) depuis la [dernière version](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) et ouvrez-le pour l'importer dans Minecraft.
2. Dans les paramètres du monde, activez **les deux** : le pack de comportements et le pack de ressources (même nom). La triche peut rester désactivée.
3. Allez sur un terrain plat et dégagé (l'écran **remplace les blocs à cet endroit**, utilisez un monde de test). Regardez là où vous voulez l'écran et tapez `/badapple:setup wall`, puis `/badapple:start`.
4. Utilisez une boussole pour ouvrir la télécommande. La première fois que vous entrez dans un monde sans écran, un court message dans le chat rappelle ces étapes.

**B. Convertir votre vidéo dans le navigateur (sans installation, bêta)**
- Ouvrez le [convertisseur web](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/), choisissez des vidéos et appuyez sur *Créer le .mcaddon*. Fonctionne sous Windows, Mac, Chromebook et sur téléphone ; vos vidéos ne quittent jamais le navigateur.
- Images, clips de caméra, `.vox` et modèles MMD avec une danse (`.pmx` + `.vmd`) marchent aussi. Après la création, vous pouvez prévisualiser le rendu dans Minecraft.
- Pour **Minecraft 1.21.80 à 1.26.0**, choisissez-le dans *Version de Minecraft* (ces add-ons demandent la triche et utilisent les commandes `/scriptevent`).

**C. Convertir sous Windows (plus rapide, add-ons plus petits)**
1. Décompressez `BlockVideoPlayer-…-windows-x64.zip` depuis la [dernière version](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) et lancez `BlockVideoPlayer.exe` (pas besoin de Python). **FFmpeg est nécessaire à part** : lancez `winget install Gyan.FFmpeg` ou placez `ffmpeg.exe` à côté de l'EXE.
2. Le guide de démarrage s'ouvre la première fois : il vérifie votre PC, propose des réglages et affiche les commandes pour lire la vidéo. Sans lui : *Ajouter une vidéo* → *Adapter au format de la vidéo* → *Créer le .mcaddon*.
3. Si l'**estimation de charge** indique *lourd*, baissez la largeur, la hauteur ou les ips.

## Commandes

`badapple` est le préfixe des commandes (vous le choisissez à la conversion). Les opérateurs peuvent utiliser les commandes ; avec la triche, l'ancienne forme `/scriptevent badapple:…` marche aussi.

| Commande | Effet |
|---|---|
| `/badapple:setup` | Poser l'écran ici, à plat au sol (3D : debout à 2 blocs au nord) |
| `/badapple:setup wall` | Dresser un écran plat comme une télé, à 3 blocs devant vous |
| `/badapple:setup face` | À plat, mais tourné dans la direction de votre regard |
| `/badapple:start` | Lire la vidéo choisie depuis le début |
| `/badapple:play <ID>` / `/badapple:list` | Lire une vidéo / lister les vidéos |
| `/badapple:remote` | Ouvrir la télécommande (comme la boussole) |
| `/badapple:stop` | Arrêter et effacer l'écran |
| `/badapple:stats` | ips réelles, TPS et images en retard de la dernière lecture |

## Mode image (expérimental)

Cochez *Créer aussi l'add-on en mode image* dans l'application Windows ou le convertisseur web (ou lancez `python convert_image_mode.py video.mp4`). Vous obtenez un second fichier, `<nom>_image.mcaddon`. En jeu : `/badapple_img:setup`, puis `/badapple_img:start`. Il lit jusqu'à 30 ips sans remplacer de blocs, mais utilise de la mémoire de textures : gardez la vidéo courte. **Pas encore testé dans le vrai Minecraft.** Détails : [docs/IMAGE_MODE.md](docs/IMAGE_MODE.md) (en japonais).

## Configuration requise

| | |
|---|---|
| Minecraft | Bedrock Edition **1.26.10 ou plus récent** (1.21.80–1.26.0 : à créer dans le convertisseur web) |
| Convertisseur | EXE pour Windows 10/11, ou un navigateur récent. Depuis les sources : Python 3.12 + FFmpeg (la ligne de commande marche aussi sous Linux) |
| GPU (facultatif) | NVIDIA (CUDA), AMD (ROCm / DirectML) ou Intel (DirectML) ; le processeur suffit aussi |

Testé sur un seul PC sous Windows 11. Téléphones, consoles et serveurs devraient fonctionner mais ne sont **pas encore testés** ([compatibilité](docs/COMPATIBILITY.md)).

## FAQ

- **Java Edition ?** Non, Bedrock uniquement.
- **Pas de son ?** Activez les deux packs, vérifiez le volume dans la télécommande et dans le jeu, et redémarrez Minecraft après l'import.
- **Saccades ?** Baissez la taille ou les ips et regardez `/badapple:stats`. Plus d'infos dans les [problèmes connus](docs/KNOWN_ISSUES.md).
- **Vos propres vidéos** : partager un add-on fait à partir de la vidéo de quelqu'un d'autre (anime, clips, streams) peut enfreindre le droit d'auteur. Ne partagez que des vidéos que vous avez faites ou que vous avez le droit de partager.

## Plus

- [Historique](CHANGELOG.md) · [Problèmes connus](docs/KNOWN_ISSUES.md) · [Performances](docs/PERFORMANCE.md) · [Signaler un problème](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) (documents surtout en japonais)
- La documentation complète (installation Python, GPU, modes 3D, développement, mesures) est dans le [README japonais](README.md).
- Inspiré de [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple). Traitement vidéo par FFmpeg. Projet de fan non officiel, sans lien avec Mojang Studios ni Microsoft.
- Licence : [MIT](LICENSE)
