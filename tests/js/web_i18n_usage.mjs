// web/i18n.js と web/usage.js の、画面に依らない関数を node で動かす (tests/test_web_i18n.py から実行)。
// 使い方: node web_i18n_usage.mjs <web フォルダ>   標準入力の JSON の問いに答え、結果を JSON で出力する
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const web = process.argv[2];
const I18n = require(path.resolve(web, "i18n.js"));
const Usage = require(path.resolve(web, "usage.js"));

const input = JSON.parse(readFileSync(0, "utf8"));
const output = {
  languages: I18n.LANGUAGES,
  normalize: input.normalize.map((name) => I18n.normalize(name)),
  detect: input.detect.map((options) => I18n.detect(options)),
  format: input.format.map(([text, params]) => I18n.format(text, params)),
  allowed: input.allowed.map((env) => Usage.allowed(env)),
  countUrl: input.countUrl.map(([code, options]) => Usage.countUrl(code, options)),
  kindOf: input.kindOf.map(([settings, hasVox, hasVideo]) => Usage.kindOf(settings, hasVox, hasVideo)),
};
process.stdout.write(JSON.stringify(output));
