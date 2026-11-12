<!-- CurseForge の Files にファイルを上げるときの Changelog 欄 (Markdown)。前に上げたファイル (v6.32.1) からの変更を、
     ショーケースを遊ぶ人に関係する順に書く。次に上げるときは、この版からの変更に書き換える。 -->
## v6.34.0 (since v6.32.1)

Needs Minecraft Bedrock 1.26.10 or newer (same as before). For 1.21.80 to 1.26.0, make your add-on in the [web converter](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/) (see below).

### New
- **Help for first-time players.** When you join a world that has no screen yet, a short message tells you once what to do: face where you want the screen, run `/badapple:setup wall`, then `/badapple:start`. Open the remote with `/badapple:remote` or a compass.
- **Tips after setup.** A screen laid on the floor tells you to look down at it from above (or use `setup wall`); a 3D figure tells you to step back a few blocks.
- **Sound for players who join late.** Someone who joins while a video is playing now hears the sound too (from the next part of the sound at the latest).

### Fixed
- **Running `/badapple:setup` again** no longer leaves the old playback running in the background.
- **Switching videos** no longer leaves blocks of the previous video behind, even after stop.
- **Seeking** after stop or pause now always draws the right picture.
- **After reopening the world**, stop, seek and volume work again, and the chosen video and volume are kept.
- **Volume** no longer resets to 100% when you change the video.
- **Falling behind:** on a slow device the video now skips ahead to stay on time instead of running later and later.
- **Large screens:** if part of the screen is in chunks that are not loaded, the rest is still drawn correctly. A screen too tall for the world is not placed; you get a message with what to do instead. Very wide screens warn about render distance.

### Updating from an older version
- Import the new `.mcaddon` over the old one, then run `/badapple:setup` once in your world.
- The showcase videos are the same as before.

### Known issue
- Video IDs don't appear as suggestions after `/badapple:play` (seems to be a Minecraft limitation). Use `/badapple:list` to see them.

### For your own videos (free converter)
- The [web converter](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/) can now make add-ons for **older Minecraft (1.21.80 to 1.26.0)**: pick it under *Minecraft version*. Those add-ons need cheats on and use `/scriptevent` commands.
- The web converter's preview shows every frame of long videos at full speed.
- The Windows app checks video IDs and settings before building, and can optionally give people's colours priority using the AI cutout.

Full details: [CHANGELOG](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/blob/main/CHANGELOG.md)
