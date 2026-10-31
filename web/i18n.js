// Block Video Player Web Converter: 画面の多言語対応
// 翻訳は Windows 版の GUI と同じ locales/<言語コード>.json (サイトでは app/locales/) を読む。キーは en.json が基準で、
// ブラウザ版だけの文は web.* に置く。翻訳が無いキーは英語、英語にも無ければキーそのものを出す。
"use strict";

const I18n = (() => {
  // 言語選択欄での並び順 (gui_i18n.LANGUAGE_ORDER と同じ)
  const LANGUAGES = ["ja", "en", "zh-CN", "zh-TW", "ko", "es", "pt-BR", "fr", "de", "ru"];
  const FALLBACK = "en";
  const STORAGE_KEY = "bvp.lang";
  const tables = {}; // 言語コード → 翻訳 (読み込んだものだけ)
  let current = FALLBACK;
  const listeners = [];

  /** "ja-JP" / "zh-Hant-TW" / "pt-PT" などを対応する言語コードに。対応しなければ null (gui_i18n と同じ規則)。 */
  function normalize(name) {
    if (!name) return null;
    const n = String(name).replace(/_/g, "-").toLowerCase();
    if (/^zh-(tw|hk|mo|hant)/.test(n)) return "zh-TW";
    if (n.startsWith("zh")) return "zh-CN";
    if (n.startsWith("pt")) return "pt-BR";
    const base = n.split("-")[0];
    return LANGUAGES.includes(base) ? base : null;
  }

  /** ?lang= → 前に選んだ言語 → ブラウザの言語の順で決める。 */
  function detect({ search = "", stored = null, languages = [] } = {}) {
    const fromQuery = normalize(new URLSearchParams(search).get("lang"));
    if (fromQuery) return fromQuery;
    const fromStorage = normalize(stored);
    if (fromStorage) return fromStorage;
    for (const name of languages) {
      const code = normalize(name);
      if (code) return code;
    }
    return FALLBACK;
  }

  /** "{name}" を params の値に置き換える (Python の str.format と同じ書き方。翻訳の確認は tests/test_i18n.py)。 */
  function format(text, params) {
    if (!params) return text;
    return text.replace(/\{(\w+)\}/g, (whole, name) => (name in params ? String(params[name]) : whole));
  }

  function t(key, params) {
    const text = (tables[current] && tables[current][key]) ?? (tables[FALLBACK] && tables[FALLBACK][key]) ?? key;
    return format(text, params);
  }

  async function load(code) {
    if (tables[code]) return tables[code];
    const response = await fetch(`app/locales/${code}.json`);
    if (!response.ok) throw new Error(`${code}.json: HTTP ${response.status}`);
    tables[code] = await response.json();
    return tables[code];
  }

  function storedLanguage() {
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }

  /** 画面の data-i18n (文字) / data-i18n-title (title 属性) / data-i18n-placeholder (入力欄の薄い字) を今の言語にする。 */
  function apply(root = document) {
    for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    for (const el of root.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
    for (const el of root.querySelectorAll("[data-i18n-placeholder]")) el.placeholder = t(el.dataset.i18nPlaceholder);
    document.documentElement.lang = current;
    for (const listener of listeners) listener(current);
  }

  async function use(code, { remember = false } = {}) {
    const next = normalize(code) || FALLBACK;
    await load(FALLBACK);
    try {
      await load(next);
      current = next;
    } catch (e) {
      current = FALLBACK; // 翻訳を読めないときは英語で表示する
    }
    if (remember) {
      try { localStorage.setItem(STORAGE_KEY, current); } catch (e) { /* 保存できない設定のブラウザ */ }
    }
    apply();
    return current;
  }

  /** 言語の選択欄を作る。表示名はそれぞれの言語での名前 (_language_name)。 */
  async function fillSelect(select) {
    await Promise.all(LANGUAGES.map((code) => load(code).catch(() => null)));
    select.innerHTML = "";
    for (const code of LANGUAGES) {
      if (!tables[code]) continue;
      const option = document.createElement("option");
      option.value = code;
      option.textContent = tables[code]._language_name || code;
      select.append(option);
    }
    select.value = current;
    select.addEventListener("change", () => use(select.value, { remember: true }));
  }

  function init() {
    return use(detect({ search: location.search, stored: storedLanguage(), languages: navigator.languages || [navigator.language] }));
  }

  return {
    LANGUAGES, normalize, detect, format, t, use, init, fillSelect, apply,
    get language() { return current; },
    onChange(listener) { listeners.push(listener); },
  };
})();

if (typeof module !== "undefined") module.exports = I18n; // テスト (Node) 用
