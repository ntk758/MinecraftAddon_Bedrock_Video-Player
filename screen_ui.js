/**
 * Block & Image Video Player - スクリーンブロックの操作画面 (ゲーム内の GUI)
 *
 * スクリーンブロックを使う (右クリック・長押し・コントローラーの使うボタン) と開く。コマンドの入力はいらない。
 * フォームはボタンとスライダー・切り替え・選択だけで作る (タッチ・マウス・コントローラーのどれでも操作でき、小さい画面でも
 * 押しやすい)。URL の入力やファイルの追加はここでは行わない (Windows 版・ブラウザ版の役目)。
 *
 * スクリーンの操作は ui (main.js が渡す) だけを通す。ui の関数:
 *   tr(key, ...args)        翻訳の RawMessage (key は "screen.title" のように bvp. を除いたもの)
 *   status(screenId)        { screenNumber, mediaId, mediaTitle, state, position, duration, volume, loop, scale, width,
 *                             height, rendererType } か null (スクリーンがもう無い)
 *   media(screenId)         [{ id, title, width, height, seconds, supported, reason }] (選べる動画)
 *   scaleOptions(screenId, mediaId)  [{ scale, width, height }] (その動画をこのスクリーンに置ける大きさ)
 *   renderers()             [{ type, label }] (このパックで使える描画方式)
 *   act(screenId, action, ...args)   操作 (play・pause・stop・seek・volume・loop・select・remove)。結果か結果の Promise
 *   tell(player, result)    失敗なら理由を、成功なら短い知らせをその人だけに出す
 *   later(callback, ticks)  ticks 後に callback を呼ぶ (プレイヤーが別の画面を開いていて、フォームを出せなかったとき)
 */
import { ActionFormData, ModalFormData } from "@minecraft/server-ui";

const ICONS = {
  play: "textures/items/emerald",
  pause: "textures/items/emerald",
  stop: "textures/blocks/redstone_block",
  seek: "textures/items/clock",
  library: "textures/items/book_portfolio",
  settings: "textures/items/repeater",
  remove: "textures/blocks/barrier",
};
// 別の画面 (チャット・インベントリ) を開いていてフォームを出せなかったときに、出し直す回数と間隔
const BUSY_RETRIES = 3;
const BUSY_RETRY_TICKS = 10;

/** 秒を「分:秒」にする (時間のある長い動画は「時:分:秒」)。 */
export function formatTime(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function lines(...parts) {
  const rawtext = [];
  parts.forEach((part, index) => {
    if (index > 0) rawtext.push({ text: "\n" });
    rawtext.push(part);
  });
  return { rawtext };
}

/** フォームを出す。別の画面を開いていて出せなければ、少し待って出し直す。 */
function showForm(form, player, ui, onResponse, retries = BUSY_RETRIES) {
  form.show(player).then((response) => {
    if (response.canceled) {
      if (response.cancelationReason === "UserBusy" && retries > 0) {
        ui.later(() => showForm(form, player, ui, onResponse, retries - 1), BUSY_RETRY_TICKS);
      }
      return;
    }
    onResponse(response);
  }).catch((e) => {
    console.warn(`[screen] form failed: ${e}`);
  });
}

/** 結果が Promise でも、そうでなくても、終わったら知らせる。 */
function report(player, ui, result, then = null) {
  Promise.resolve(result).then((value) => {
    ui.tell(player, value);
    if (value?.ok && then) then(value);
  }).catch((e) => ui.tell(player, { ok: false, reason: "render_error", detail: [String(e?.message ?? e).slice(0, 120)] }));
}

/** スクリーンの操作画面 (いちばん上)。 */
export function showScreenMenu(player, screenId, ui) {
  const s = ui.status(screenId);
  if (!s) {
    ui.tell(player, { ok: false, reason: "no_screen" });
    return;
  }
  const playing = s.state === "playing";
  const body = [ui.tr("screen.status_line", ui.tr(`screen.state.${s.state}`))];
  body.push(ui.tr("screen.video_line", s.mediaId ? { text: s.mediaTitle } : ui.tr("screen.no_video")));
  if (s.mediaId) {
    body.push(ui.tr("screen.position_line", formatTime(s.position), formatTime(s.duration)));
    body.push(ui.tr("screen.size_line", s.width, s.height, s.scale));
  }
  // フォームの本文は % をもう一度読むので %% と書く (main.js のリモコンと同じ)
  body.push(ui.tr("screen.volume_line", `${Math.round(s.volume * 100)}%%`, ui.tr(s.loop ? "screen.loop_on" : "screen.loop_off")));
  const renderer = ui.renderers().find((r) => r.type === s.rendererType);
  body.push(ui.tr("screen.renderer_line", renderer ? ui.tr(renderer.label) : { text: s.rendererType }));

  const form = new ActionFormData().title(ui.tr("screen.title", s.screenNumber)).body(lines(...body));
  const actions = [];
  const add = (action, label, icon) => {
    form.button(ui.tr(label), icon);
    actions.push(action);
  };
  if (!s.mediaId) {
    add("library", "screen.choose_video", ICONS.library);
  } else {
    add(playing ? "pause" : "play", playing ? "screen.pause" : "screen.play", playing ? ICONS.pause : ICONS.play);
    add("stop", "screen.stop", ICONS.stop);
    add("seek", "screen.seek", ICONS.seek);
    add("library", "screen.choose_video", ICONS.library);
  }
  add("settings", "screen.settings", ICONS.settings);
  add("remove", "screen.remove", ICONS.remove);

  showForm(form, player, ui, (response) => {
    switch (actions[response.selection]) {
      case "play":
        report(player, ui, ui.act(screenId, "play"));
        break;
      case "pause":
        report(player, ui, ui.act(screenId, "pause"));
        break;
      case "stop":
        report(player, ui, ui.act(screenId, "stop"));
        break;
      case "seek":
        showSeekForm(player, screenId, ui);
        break;
      case "library":
        showLibrary(player, screenId, ui);
        break;
      case "settings":
        showSettings(player, screenId, ui);
        break;
      case "remove":
        showRemoveConfirm(player, screenId, ui);
        break;
      default:
        break;
    }
  });
}

/** 動画の一覧。選ぶと画面を用意して、そのまま再生する。 */
export function showLibrary(player, screenId, ui) {
  const s = ui.status(screenId);
  if (!s) {
    ui.tell(player, { ok: false, reason: "no_screen" });
    return;
  }
  const items = ui.media(screenId);
  if (items.length === 0) {
    ui.tell(player, { ok: false, reason: "no_videos" });
    return;
  }
  const form = new ActionFormData().title(ui.tr("screen.library.title")).body(ui.tr("screen.library.body"));
  for (const item of items) {
    const rawtext = [{ text: item.title }];
    if (item.id === s.mediaId) rawtext.push({ text: " " }, ui.tr("screen.library.current"));
    rawtext.push({ text: "\n" });
    rawtext.push(item.supported ? ui.tr("screen.library.item", item.width, item.height, formatTime(item.seconds))
      : ui.tr("screen.library.unsupported"));
    form.button({ rawtext });
  }
  showForm(form, player, ui, (response) => {
    const item = items[response.selection];
    if (!item) return;
    if (!item.supported) {
      ui.tell(player, { ok: false, reason: item.reason ?? "unsupported_video" });
      return;
    }
    ui.tell(player, { ok: true, notice: "screen.preparing" });
    report(player, ui, ui.act(screenId, "select", item.id), () => report(player, ui, ui.act(screenId, "play")));
  });
}

/** 音量・ループ・大きさ・描画方式。 */
export function showSettings(player, screenId, ui) {
  const s = ui.status(screenId);
  if (!s) {
    ui.tell(player, { ok: false, reason: "no_screen" });
    return;
  }
  const form = new ModalFormData().title(ui.tr("screen.settings.title"));
  form.slider(ui.tr("screen.settings.volume"), 0, 100, { valueStep: 10, defaultValue: Math.round(s.volume * 100) });
  form.toggle(ui.tr("screen.settings.loop"), { defaultValue: s.loop });
  const fields = ["volume", "loop"];
  const scales = s.mediaId ? ui.scaleOptions(screenId, s.mediaId) : [];
  if (scales.length > 1) {
    const current = Math.max(0, scales.findIndex((o) => o.scale === s.scale));
    form.dropdown(ui.tr("screen.settings.scale"), scales.map((o) => ui.tr("screen.settings.scale_item", o.scale, o.width, o.height)),
      { defaultValueIndex: current });
    fields.push("scale");
  }
  const renderers = ui.renderers();
  if (renderers.length > 1) {
    const current = Math.max(0, renderers.findIndex((r) => r.type === s.rendererType));
    form.dropdown(ui.tr("screen.settings.renderer"), renderers.map((r) => ui.tr(r.label)), { defaultValueIndex: current });
    fields.push("renderer");
  }
  showForm(form, player, ui, (response) => {
    const values = response.formValues ?? [];
    const value = (name) => values[fields.indexOf(name)];
    const volume = Number(value("volume")) / 100;
    if (Number.isFinite(volume) && Math.abs(volume - s.volume) > 1e-6) report(player, ui, ui.act(screenId, "volume", volume));
    const loop = !!value("loop");
    if (loop !== s.loop) report(player, ui, ui.act(screenId, "loop", loop));
    if (fields.includes("renderer")) {
      const chosen = renderers[Number(value("renderer"))];
      if (chosen && chosen.type !== s.rendererType) ui.tell(player, { ok: false, reason: "renderer_unavailable" });
    }
    if (fields.includes("scale")) {
      const chosen = scales[Number(value("scale"))];
      if (chosen && chosen.scale !== s.scale) {
        ui.tell(player, { ok: true, notice: "screen.preparing" });
        report(player, ui, ui.act(screenId, "scale", chosen.scale));
      }
    }
  });
}

/** 再生位置 (秒) のスライダー。 */
export function showSeekForm(player, screenId, ui) {
  const s = ui.status(screenId);
  if (!s || !s.mediaId) {
    ui.tell(player, { ok: false, reason: s ? "no_media_selected" : "no_screen" });
    return;
  }
  const form = new ModalFormData().title(ui.tr("screen.seek.title"));
  form.slider(ui.tr("screen.seek.slider"), 0, Math.max(1, Math.floor(s.duration)),
    { valueStep: 1, defaultValue: Math.min(Math.floor(s.position), Math.max(1, Math.floor(s.duration))) });
  showForm(form, player, ui, (response) => {
    const seconds = Number(response.formValues?.[0]);
    if (Number.isFinite(seconds)) report(player, ui, ui.act(screenId, "seek", seconds));
  });
}

/** 削除の確認。削除すると、このスクリーンが置いたブロックだけを片付ける。 */
export function showRemoveConfirm(player, screenId, ui) {
  const s = ui.status(screenId);
  if (!s) {
    ui.tell(player, { ok: false, reason: "no_screen" });
    return;
  }
  const form = new ActionFormData().title(ui.tr("screen.remove.title", s.screenNumber)).body(ui.tr("screen.remove.body"))
    .button(ui.tr("screen.remove.confirm"), ICONS.remove)
    .button(ui.tr("screen.remove.cancel"));
  showForm(form, player, ui, (response) => {
    if (response.selection === 0) report(player, ui, ui.act(screenId, "remove"));
  });
}
