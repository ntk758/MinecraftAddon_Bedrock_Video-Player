// @minecraft/server-ui の最小モック。開いたフォームの内容を @minecraft/server モックの forms に記録し、
// uiResponses に積んだ応答 (ボタンの選択・入力値) を順に返す。
import { forms, uiResponses } from "@minecraft/server";

class RecordingForm {
  constructor(kind) {
    this.record = { kind, title: null, body: null, buttons: [], sliders: [] };
  }

  title(text) {
    this.record.title = text;
    return this;
  }

  body(text) {
    this.record.body = text;
    return this;
  }

  button(text) {
    this.record.buttons.push(text);
    return this;
  }

  // @minecraft/server-ui 2.0.0 の書き方: slider(ラベル, 最小, 最大, { valueStep, defaultValue })。1.x の
  // slider(ラベル, 最小, 最大, 刻み, 既定値) で呼ばれたら、実際の 2.0 と同じく使えないので失敗させる
  slider(label, minimum, maximum, sliderOptions) {
    if (typeof sliderOptions === "number" || arguments.length > 4) throw new Error("slider: 2.0 takes (label, min, max, options)");
    this.record.sliders.push(label);
    this.record.sliderOptions = [...(this.record.sliderOptions ?? []), { minimum, maximum, ...(sliderOptions ?? {}) }];
    return this;
  }

  show() {
    forms.push(this.record);
    return Promise.resolve(uiResponses.shift() ?? { canceled: true });
  }
}

export class ActionFormData extends RecordingForm {
  constructor() {
    super("action");
  }
}

export class ModalFormData extends RecordingForm {
  constructor() {
    super("modal");
  }
}
