<!-- CurseForge の Files にファイルを上げるときの Changelog 欄 (Markdown)。前に上げたファイル (v6.30.0) からの変更を、
     ショーケースを遊ぶ人に関係する順に書く。次に上げるときは、この版からの変更に書き換える。 -->
## v6.32.1 (since v6.30.0)

**⚠ Now needs Minecraft Bedrock 1.26.10 or newer.** On an older Minecraft, use [v6.31.0 from GitHub](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/tag/v6.31.0).

### New
- **No cheats needed.** New short commands work with cheats off: `/badapple:setup` → `/badapple:start`. Also `/badapple:play <id>`, `/badapple:stop`, `/badapple:list`, `/badapple:remote` and more. The old `/scriptevent badapple:...` commands still work (with cheats).
- **No more fighting over ticking areas.** The screen is kept loaded with the add-on's own ticking areas, so it no longer uses the world's limit of 10. Old ticking areas from earlier versions are removed when you run `/badapple:setup` again.

### Fixed
- Choosing a video in the remote (*Next video*, *Previous video*, *Choose from the list*) now switches to it and plays it from the start right away. Before, it did not change until you pressed Play, and seeking could act on the old video.

### Updating from an older version
- Import the new `.mcaddon` over the old one, then run `/badapple:setup` once in your world.
- The showcase videos are the same as before.

### Known issue
- Video IDs don't appear as suggestions after `/badapple:play` (seems to be a Minecraft limitation). Use `/badapple:list` to see them.

### For your own videos (free converter)
- The [web converter](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/) now also takes pictures (still block art), camera clips and MMD models with a dance, makes OGG sound, has the Windows app's quality settings, and previews every frame of the result before you download.
- Both the web converter and the Windows app can put the 3D and the flat version of the same video in one add-on.

Full details: [CHANGELOG](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/blob/main/CHANGELOG.md)
