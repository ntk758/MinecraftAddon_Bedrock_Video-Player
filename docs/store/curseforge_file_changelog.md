<!-- CurseForge の Files にファイルを上げるときの Changelog 欄 (Markdown)。前に上げたファイル (v6.36.0) からの変更を、
     ショーケースを遊ぶ人に関係する順に書く。次に上げるときは、この版からの変更に書き換える。 -->
## v6.41.2 (since v6.36.0)

The showcase videos are the same as before. Needs Minecraft Bedrock 1.26.10 or newer (same as before).

### New
- **Video Screen block.** Take a **Video Screen** from the creative inventory (*Items* tab), place it and use it: choose a video and it plays on a screen that stands up above the block. No commands and no cheats. Use the block again to pause, stop, seek, switch videos, or change the volume, loop and size (1× to 4× for flat videos). Breaking the block removes its screen.
  - Up to 16 screens, 4 playing at once. Each screen has its own video, position and volume; stopping one doesn't affect the others.
  - A screen never replaces your builds: if a block is in the way, it doesn't place the screen and tells you where.
  - After reopening the world, a screen that was playing comes back paused, ready to continue.
  - Sound plays only to players within 48 blocks of the screen.
  - Tested in game on a Windows 11 PC. Phones, consoles and sound with two players are not tested yet.
- The `/badapple:setup` commands and the compass remote still work as before, for one screen of their own. They don't place a screen where a Video Screen's picture is.

### Changed
- **Multiplayer permissions.** When other players are in the world, the remote (compass or clock) can only be used by operators, and a Video Screen can only be used by the player who placed it and by operators. Playing alone, nothing changes.

### Fixed
- **Golems no longer appear inside the picture.** Carved pumpkins in the video could form a golem on top of copper or snow blocks and break the screen (wall screens and 3D figures). They are now placed as plain pumpkins of the same colour.
- **The seek button in the remote** showed a purple-and-black checkerboard. It now shows a clock.
- A Video Screen playing the same video as another no longer stops the other screen's sound.
- Video Screens whose video has a long ID are no longer lost when the world is reopened.
- Broken video data can no longer make the add-on loop forever.

### Updating from an older version
- Import the new `.mcaddon` over the old one. If a screen made with the old version acts strangely, run `/badapple:setup` again.

### Known issues
- Video IDs don't appear as suggestions after `/badapple:play` (seems to be a Minecraft limitation). Use `/badapple:list` to see them.
- Two Video Screens close to each other: players near both hear both sounds (there is no fade with distance yet). Set one screen's volume to 0.

### For your own videos (free converter)
- **Windows app: use v6.41.1 or later.** The v6.41.0 Windows app could not build any add-on; this is fixed.
- **Image mode:** the picture keeps its brightness at night and in dark places; sound and picture stay in time when the server slows down; sound starts right away after seeking or resuming; smaller add-ons at the same quality.
- **Converter fixes:** videos that were cut off partway (an unfinished download, for example) are refused or reported instead of being silently shortened; videos with non-square pixels are no longer squeezed; broken `.vox`, `.pmx` and `.vmd` files are refused with a reason; a cancel button in the Windows app and the web converter.
- **Web converter:** a redesigned page; it remembers your settings, skips a file that fails instead of stopping the whole batch, and no longer hangs on a broken video.

Full details: [CHANGELOG](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/blob/main/CHANGELOG.md)
