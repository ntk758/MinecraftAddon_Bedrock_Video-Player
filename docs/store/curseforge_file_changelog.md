<!-- CurseForge の Files にファイルを上げるときの Changelog 欄 (Markdown)。前に上げたファイル (v6.32.1) からの変更を、
     ショーケースを遊ぶ人に関係する順に書く。次に上げるときは、この版からの変更に書き換える。 -->
## v6.36.0 (since v6.32.1)

The project is now called **Block & Image Video Player**: besides videos made of real blocks, the free converter can make **image mode** add-ons (smooth textures, see below). Needs Minecraft Bedrock 1.26.10 or newer (same as before).

### New
- **Help for first-time players.** When you join a world that has no screen yet, a short message tells you once what to do: face where you want the screen, run `/badapple:setup wall`, then `/badapple:start`. Open the remote with `/badapple:remote` or a compass.
- **Tips after setup.** A screen laid on the floor tells you to look down at it from above (or use `setup wall`); a 3D figure tells you to step back a few blocks.
- **Sound for players who join late.** Someone who joins while a video is playing now hears the sound too.
- **Several options at once:** `/badapple:setup wall align` and `/badapple:setup face align` now work (before, the second word was a syntax error).
- **Lighter playback:** when the colour set changes inside a video, blocks that stay the same are no longer placed again (about 22% fewer blocks placed at 512×512 in a simulated test; little change on small screens).

### Fixed
- **Running `/badapple:setup` again** stops the old playback and clears the old screen (back to air), even when you set up far away from it.
- **The remote shows the volume as "100%"** again (the % sign was missing).
- **Switching videos** no longer leaves blocks of the previous video behind, even after stop.
- **Seeking** after stop or pause now always draws the right picture.
- **After reopening the world**, stop, seek and volume work again, and the chosen video and volume are kept.
- **Volume** no longer resets to 100% when you change the video.
- **Falling behind:** on a slow device the video now skips ahead to stay on time instead of running later and later.
- **Large screens:** if part of the screen is in chunks that are not loaded, the rest is still drawn correctly. A screen too tall for the world is not placed; you get a message with what to do instead.

### Updating from an older version
- Import the new `.mcaddon` over the old one, then run `/badapple:setup` once in your world.
- The showcase videos are the same as before.

### Known issue
- Video IDs don't appear as suggestions after `/badapple:play` (seems to be a Minecraft limitation). Use `/badapple:list` to see them.

### For your own videos (free converter)
- **Image mode (new):** the Windows app can also make an add-on that plays the video as a texture on a thin panel: smooth up to 30 fps and up to 512×288 pixels, with the same remote (a **clock**), volume, `setup wall` / `face` / `align`, seek and 10 languages. Long videos don't fit in the game's texture memory, so you choose what to keep: picture quality (lower fps), smoothness (smaller picture) or length. You can put block mode and image mode in one `.mcaddon`. Tested on PC only so far.
- The [web converter](https://ntk758.github.io/MinecraftAddon_Bedrock_Video-Player/) can make add-ons for **older Minecraft (1.21.80 to 1.26.0)**: pick it under *Minecraft version*. Those add-ons need cheats on and use `/scriptevent` commands.
- Smaller add-ons from the Windows app: the sound is now stored at the same quality as the web converter's.

Full details: [CHANGELOG](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/blob/main/CHANGELOG.md)
