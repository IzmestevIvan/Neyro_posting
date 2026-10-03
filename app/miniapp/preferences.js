/* Client preferences only. Never reads or changes post content. */
(() => {
  'use strict';
  const LANGUAGE_KEY = 'neyro:language';
  const THEME_KEY = 'neyro:theme';
  const languages = new Set(['auto', 'ru', 'en']);
  const themes = new Set(['system', 'light', 'dark']);
  const read = (key, allowed, fallback) => {
    try { const value = localStorage.getItem(key); return allowed.has(value) ? value : fallback; }
    catch { return fallback; }
  };
  let languagePreference = read(LANGUAGE_KEY, languages, 'auto');
  let themePreference = read(THEME_KEY, themes, 'system');
  let media;
  try { media = window.matchMedia('(prefers-color-scheme: dark)'); } catch {}
  const telegram = () => {
    const app = window.Telegram?.WebApp;
    return app && (app.initData || app.initDataUnsafe?.user || (app.platform && app.platform !== 'unknown')) ? app : null;
  };
  const clientLanguage = () => {
    const tg = telegram()?.initDataUnsafe?.user?.language_code;
    const candidates = tg ? [tg] : navigator.languages?.length ? navigator.languages : [navigator.language || 'ru'];
    for (const candidate of candidates) {
      const code = String(candidate).toLowerCase().split(/[-_]/)[0];
      if (code === 'ru' || code === 'en') return code;
    }
    return 'en';
  };
  const clientTheme = () => {
    const scheme = telegram()?.colorScheme;
    return scheme === 'dark' || scheme === 'light' ? scheme : media?.matches ? 'dark' : 'light';
  };
  const api = {
    get languagePreference() { return languagePreference; },
    get themePreference() { return themePreference; },
    get language() { return languagePreference === 'auto' ? clientLanguage() : languagePreference; },
    get theme() { return themePreference === 'system' ? clientTheme() : themePreference; },
    t(ru, en, values = {}) {
      const source = api.language === 'en' && en !== undefined ? en : ru;
      return String(source ?? '').replace(/\{(\w+)\}/g, (match, key) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match);
    },
    apply(root = document) {
      const selector = '[data-i18n],[data-i18n-placeholder],[data-i18n-title],[data-i18n-aria-label]';
      const nodes = [...root.querySelectorAll(selector)];
      if (root.matches?.(selector)) nodes.unshift(root);
      for (const node of nodes) {
        if (node.hasAttribute('data-i18n')) node.textContent = api.t(node.getAttribute('data-i18n'), node.getAttribute('data-i18n-en') ?? undefined);
        for (const attr of ['placeholder', 'title', 'aria-label']) {
          if (node.hasAttribute(`data-i18n-${attr}`)) node.setAttribute(attr, api.t(node.getAttribute(`data-i18n-${attr}`), node.getAttribute(`data-i18n-${attr}-en`) ?? undefined));
        }
      }
      for (const select of root.querySelectorAll('[data-pref-language]')) select.value = languagePreference;
      for (const select of root.querySelectorAll('[data-pref-theme]')) select.value = themePreference;
    },
    setLanguage(value) {
      if (!languages.has(value)) return;
      languagePreference = value;
      try { localStorage.setItem(LANGUAGE_KEY, value); } catch {}
      update();
    },
    setTheme(value) {
      if (!themes.has(value)) return;
      themePreference = value;
      try { localStorage.setItem(THEME_KEY, value); } catch {}
      update();
    },
  };
  function update() {
    document.documentElement.lang = api.language;
    document.documentElement.dataset.theme = api.theme;
    api.apply();
    document.dispatchEvent(new CustomEvent('neyro:preferences', {detail:{language:api.language, theme:api.theme, languagePreference, themePreference}}));
  }
  window.NeyroPrefs = api;
  document.addEventListener('change', event => {
    if (event.target.matches?.('[data-pref-language]')) api.setLanguage(event.target.value);
    if (event.target.matches?.('[data-pref-theme]')) api.setTheme(event.target.value);
  });
  if (media?.addEventListener) media.addEventListener('change', () => { if (themePreference === 'system') update(); });
  else if (media?.addListener) media.addListener(() => { if (themePreference === 'system') update(); });
  telegram()?.onEvent?.('themeChanged', () => { if (themePreference === 'system') update(); });
  window.addEventListener('languagechange', () => { if (languagePreference === 'auto') update(); });
  window.addEventListener('storage', event => {
    if (event.key === LANGUAGE_KEY || event.key === THEME_KEY || event.key === null) {
      languagePreference = read(LANGUAGE_KEY, languages, 'auto');
      themePreference = read(THEME_KEY, themes, 'system');
      update();
    }
  });
  update();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', update, {once:true});
})();
