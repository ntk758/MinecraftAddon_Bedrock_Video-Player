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

  slider(label) {
    this.record.sliders.push(label);
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
