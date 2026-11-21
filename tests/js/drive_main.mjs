// main.js をモックの Minecraft 上で、手順 (JSON) どおりに操作して結果を標準出力へ書く (tests/test_playback_control.py から実行)。
// 使い方: node drive_main.mjs '<手順の JSON 配列>'
//   {"event": "badapple:setup", "message": "..."}   /scriptevent を送る
//   {"tick": 10}                                      10 tick 進める
//   {"until": "bvp.playback_finished"}                そのメッセージが出るまで tick を進める (最大 20000)
//   {"settle": 3}                                     ブロックを置く呼び出しが 3 tick 続けて無くなるまで tick を進める
//   {"ui": [{"selection": 0}, {"formValues": [6]}]}   リモコン (コンパス) を使い、開いたフォームへ順に応答する
//   {"failZ": 92} / {"failZ": null}                   z がその値以上のマスへの設置を失敗させる / 元に戻す
//   {"unloadedZ": 92} / {"unloadedZ": null}           z がその値以上のチャンクを読み込まれていないことにする / 戻す
//   {"tickingOnly": true}                             常時読み込みの区画とプレイヤーの近くだけを読み込まれていることにする
//   {"y": 318} / {"x": 5, "z": 100}                   プレイヤーの立つ位置を変える
//   {"view": {"x": 1, "z": 0}}                        プレイヤーの見ている方向を変える
//   {"snap": "名前"}                                  いま選ばれている動画の盤面 (各マスのブロック) を記録する
//   {"world": "名前"}                                 ワールドに置かれたブロックをすべて記録する (だまし絵の舞台は snap の範囲に収まらない)
//   {"mark": "名前"}                                  いま数えている projectToStage の呼び出し回数を記録する (marks)
//   {"command": "badapple:setup", "args": ["face"]}   独自のコマンド (/badapple:setup face) を実行する (処理は次の tick)
//   {"spawn": true}                                   プレイヤーがワールドに入る (playerSpawn、initialSpawn)
//   {"secondPlayer": "join"} / {"secondPlayer": "leave"}  2 人目が入る (playerSpawn) / 抜ける
//   {"forgetTicking": true}                           ワールドを開き直したときのように、常時読み込みの区画を消す
// スクリーンブロック (Promise を待つ手順は、tick のたびにマイクロタスクも進める):
//   {"run": 10}                                       10 tick 進める (tick ごとに Promise の続きも進める)
//   {"runUntil": "bvp.screen.ready", "max": 400, "count": 2}  そのメッセージが count 回出るまで run する
//   {"tickMark": "名前"}                              いまの tick の数を記録する
//   {"placeScreen": {"x": 0, "y": 64, "z": 90}, "facing": "north"}  スクリーンブロックを置く (onPlace)
//   {"useScreen": {"x": 0, "y": 64, "z": 90}, "ui": [...]}  スクリーンブロックを使い、開いたフォームへ順に応答する
//   {"breakScreen": {...}} / {"explode": {...}}       プレイヤーが壊す / 爆発で消える
//   {"setBlock": {"x": 0, "y": 65, "z": 90, "id": "minecraft:stone"}}  イベントを送らずにブロックを置く (建物の代わり)
//   {"restart": true}                                 スクリプトを読み込み直す (ワールドを開き直したとき。ブロックと保存は残る)
//   {"worldLoad": true}                               worldLoad を送る
//   {"dp": "名前"} / {"intervals": "名前"}             動的プロパティ / 動いているタイマーの数を記録する
//   {"setDp": {"鍵": "値"}}                           ワールドの動的プロパティを書く (壊れた保存の確かめ)
import {
  board, commands, customCommands, customEnums, fireScriptEvent, forgetTicking, forms, getAnchor, messages, options, player,
  runCustomCommand, sounds, spawnPlayer, stats, tick as mockTick, uiResponses, useItem,
  // スクリーンブロックの手順 (テストが上の行を書き換えて名前を足すので、上の並びは変えない)
  activeIntervals, breakBlock, explodeBlock, fireWorldLoad, getDynamicProperties, interactBlock, placeBlock, restartScripts,
  secondPlayer, setBlockSilently, world,
} from "@minecraft/server";

const steps = JSON.parse(process.argv[2]);
let ticks = 0;
function tick() {
  ticks++;
  mockTick();
}
await import("./main.js");
const { VIDEOS } = await import("./videos.js");

const snaps = [];
const marks = {};
function snap(label, videoId) {
  const video = VIDEOS[videoId];
  const anchor = getAnchor("badapple:anchor");
  const volume = video.layout === "volume" || video.layout === "heightfield";
  const cells = video.width * video.height * (volume ? video.depth : 1);
  const cellsOut = [];
  for (let p = 0; p < cells; p++) {
    const x = anchor.x + (p % video.width);
    const row = Math.floor(p / video.width);
    const y = volume ? anchor.y + (video.height - 1 - (row % video.height)) : anchor.y;
    const z = volume ? anchor.z + Math.floor(row / video.height) : anchor.z + row;
    cellsOut.push(board.get(`${x},${y},${z}`) ?? null);
  }
  snaps.push({ label, anchor, board: cellsOut });
}

const worlds = [];
const errors = [];
const commandResults = [];
const dps = {};
const tickMarks = {};
const intervalCounts = {};
let restarts = 0;
/** Promise の続き (フォームの応答・画面の用意の後の再生など) を進める。 */
async function flush() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}
async function run(n) {
  for (let i = 0; i < n; i++) {
    tick();
    await flush();
  }
}
const SCREEN_BLOCK = "badapple:video_screen";
for (const step of steps) {
  try {
    if (step.event) fireScriptEvent(step.event, step.message ?? "");
    else if (step.command) commandResults.push(runCustomCommand(step.command, ...(step.args ?? [])));
    else if (step.forgetTicking) forgetTicking();
    else if (step.run) await run(step.run);
    else if (step.runUntil) {
      // count: そのメッセージがこの数だけ出るまで (既定 1)
      const seen = () => messages.filter((m) => JSON.stringify(m).includes(step.runUntil)).length;
      for (let n = 0; n < (step.max ?? 2000) && seen() < (step.count ?? 1); n++) await run(1);
    } else if (step.tickMark) tickMarks[step.tickMark] = ticks; else if (step.placeScreen) {
      placeBlock(step.placeScreen, SCREEN_BLOCK, step.facing ?? "north", step.by === 2 ? secondPlayer : player);
      await run(1);
    } else if (step.useScreen) {
      uiResponses.push(...(step.ui ?? []));
      interactBlock(step.useScreen, step.by === 2 ? secondPlayer : player);
      await run(step.wait ?? 3);
    } else if (step.breakScreen) {
      breakBlock(step.breakScreen);
      await run(1);
    } else if (step.explode) {
      explodeBlock(step.explode);
      await run(1);
    } else if (step.setBlock) setBlockSilently(step.setBlock, step.setBlock.id);
    else if (step.restart) {
      restartScripts();
      restarts++;
      await import(`./main.js?restart=${restarts}`);
    } else if (step.worldLoad) fireWorldLoad();
    else if (step.setDp) for (const [key, value] of Object.entries(step.setDp)) world.setDynamicProperty(key, value);
    else if (step.dp) dps[step.dp] = getDynamicProperties();
    else if (step.intervals) intervalCounts[step.intervals] = activeIntervals();
    else if (step.spawn) spawnPlayer(step.spawn !== "respawn");
    // 2 人目が入る ("join": playerSpawn も送る) / 抜ける ("leave")
    else if (step.secondPlayer === "join") { options.secondPlayer = true; spawnPlayer(true, "second"); }
    else if (step.secondPlayer === "leave") options.secondPlayer = false;
    else if (step.tick) for (let i = 0; i < step.tick; i++) tick();
    else if (step.sleepMs) await new Promise((resolve) => setTimeout(resolve, step.sleepMs));
    else if (step.settle) {
      // 置いた回数が step.settle tick 続けて変わらなくなるまで tick を進める (最大 2000)
      for (let quiet = 0, n = 0; quiet < step.settle && n < 2000; n++) {
        const calls = stats.setBlockCalls + stats.fillCalls;
        tick();
        quiet = stats.setBlockCalls + stats.fillCalls === calls ? quiet + 1 : 0;
      }
    }
    else if (step.until) {
      for (let n = 0; n < 20000 && !messages.some((m) => JSON.stringify(m).includes(step.until)); n++) tick();
    } else if (step.ui) {
      uiResponses.push(...step.ui);
      useItem("minecraft:compass", step.by === 2 ? secondPlayer : player);
      for (let i = 0; i < 10; i++) await Promise.resolve();
    } else if ("failZ" in step) options.failZ = step.failZ ?? undefined;
    else if ("unloadedZ" in step) options.unloadedZ = step.unloadedZ ?? undefined;
    else if ("tickingOnly" in step) options.tickingOnly = step.tickingOnly;
    else if (step.view) player.viewDirection = { y: 0, ...step.view };
    else if ("y" in step || "x" in step || "z" in step) Object.assign(player.location, step);
    else if (step.snap) snap(step.snap, step.video ?? Object.keys(VIDEOS)[0]);
    else if (step.mark) marks[step.mark] = globalThis.__bvpProjections ?? 0;
    else if (step.world) worlds.push({ label: step.world, blocks: [...board.entries()].map(([key, id]) => [...key.split(",").map(Number), id]) });
  } catch (e) {
    errors.push(String(e?.stack ?? e));
  }
}

const allBlocks = [...board.entries()].map(([key, id]) => [...key.split(",").map(Number), id]);
process.stdout.write(JSON.stringify({
  snaps, worlds, marks, ticks, stats, commands, errors, allBlocks, messages: messages.map((m) => JSON.stringify(m)),
  dps, intervalCounts, tickMarks,
  commandResults,
  sounds,
  forms,
  customCommands: [...customCommands.values()].map(({ command }) => command),
  customEnums: Object.fromEntries(customEnums),
  // テストが codec.js の projectToStage を包んで数えた回数 (包んでいなければ null)
  projections: globalThis.__bvpProjections ?? null,
  // テストが main.js に足した計測 (prepare で書き換えたとき。無ければ null)
  probe: globalThis.__bvpProbe ?? null,
}));
