// Block Video Player Web Converter: 簡易的な利用数の計測
// GoatCounter (https://www.goatcounter.com/) の計測用の画像 (GET /count) を読むだけで数える。外部のスクリプトは
// 読み込まず、Cookie も使わない。送るのは、ページを開いたこと・アドオンを作り始めた / 作り終えた / 失敗したことと、
// 表示モードの種類・画面の言語だけ。ファイル名・動画・設定の数値は送らない。
//
// 計測先のコード (https://<コード>.goatcounter.com) は config.js の BVP_CONFIG.goatcounter に、
// scripts/build_web.py --goatcounter <コード> (GitHub Pages ではリポジトリの変数 GOATCOUNTER_CODE) で入れる。
// 空なら何も送らない。手元のサーバー・自動テスト・Do Not Track・利用者が送信を止めたときも送らない。
"use strict";

const Usage = (() => {
  const OPT_OUT_KEY = "bvp.usage.optout";
  const CODE_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;

  function config() {
    const c = (typeof self !== "undefined" && self.BVP_CONFIG) || {};
    return { code: typeof c.goatcounter === "string" && CODE_PATTERN.test(c.goatcounter) ? c.goatcounter : "" };
  }

  function optedOut(storage) {
    try { return storage.getItem(OPT_OUT_KEY) === "1"; } catch (e) { return false; }
  }

  /**
   * 送ってよいか。env は { code, protocol, hostname, search, webdriver, doNotTrack, globalPrivacyControl, optedOut }。
   * 手元のサーバー (localhost・IP アドレス・file:) とテスト用の ?src= では送らない (数が実際の利用と混ざるため)。
   */
  function allowed(env) {
    if (!env.code) return false;
    if (env.protocol !== "https:") return false;
    const host = (env.hostname || "").toLowerCase();
    if (!host || host === "localhost" || host.endsWith(".localhost") || /^[\d.]+$/.test(host) || host.includes(":")) return false;
    if (new URLSearchParams(env.search || "").has("src")) return false;
    if (env.webdriver) return false;
    if (env.doNotTrack === "1" || env.doNotTrack === "yes" || env.globalPrivacyControl === true) return false;
    if (env.optedOut) return false;
    return true;
  }

  function environment() {
    const nav = typeof navigator !== "undefined" ? navigator : {};
    const win = typeof window !== "undefined" ? window : {};
    let storage = null;
    try { storage = win.localStorage || null; } catch (e) { storage = null; }
    return {
      code: config().code,
      protocol: typeof location !== "undefined" ? location.protocol : "",
      hostname: typeof location !== "undefined" ? location.hostname : "",
      search: typeof location !== "undefined" ? location.search : "",
      webdriver: Boolean(nav.webdriver),
      doNotTrack: nav.doNotTrack || win.doNotTrack || null,
      globalPrivacyControl: nav.globalPrivacyControl === true,
      optedOut: storage ? optedOut(storage) : false,
    };
  }

  /** GoatCounter の計測用の URL。path はページ ("/...") か出来事の名前 (先頭に / を付けない)。 */
  function countUrl(code, { path, title = "", event = false }) {
    const params = new URLSearchParams({ p: path });
    if (title) params.set("t", title);
    if (event) params.set("e", "true");
    params.set("rnd", Math.random().toString(36).slice(2, 10)); // キャッシュよけ (GoatCounter は無視する)
    return `https://${code}.goatcounter.com/count?${params}`;
  }

  /** 送ってよければ計測用の画像を 1 枚読む。送ったら true。 */
  function send(options) {
    const env = environment();
    if (!allowed(env)) return false;
    try {
      const img = new Image(); // 1×1 の GIF を読むだけ (CORS がいらない)
      img.referrerPolicy = "no-referrer";
      img.src = countUrl(env.code, options);
      return true;
    } catch (e) {
      return false; // 数えられなくても変換には影響させない
    }
  }

  /** 表示モードの種類 (平面・立体・.vox・だまし絵)。細かい数値は送らない。 */
  function kindOf(settings, hasVox, hasVideo) {
    if (settings.anamorphic && (hasVox || settings.mode !== "screen")) return "trompe";
    if (hasVox && !hasVideo) return "vox";
    return settings.mode === "screen" ? "screen" : "3d";
  }

  return {
    OPT_OUT_KEY, allowed, countUrl, kindOf, environment,
    get enabled() { return allowed(environment()); },
    /** 計測先が設定されているか (送信を止める選択肢を見せるかどうかに使う)。 */
    get configured() { return Boolean(config().code); },
    pageview(lang) { return send({ path: "/", title: `Web Converter (${lang})` }); },
    event(name) { return send({ path: name, event: true }); },
    setOptOut(value) {
      try {
        if (value) localStorage.setItem(OPT_OUT_KEY, "1");
        else localStorage.removeItem(OPT_OUT_KEY);
      } catch (e) { /* 保存できない設定のブラウザ */ }
    },
    get optedOut() { return environment().optedOut; },
  };
})();

if (typeof module !== "undefined") module.exports = Usage; // テスト (Node) 用
