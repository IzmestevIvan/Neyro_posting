/* Independent startup watchdog: no account data, network requests or auth bypass. */
(() => {
  'use strict';
  window.setTimeout(() => {
    const app = document.getElementById('app');
    const boot = document.getElementById('boot');
    if (!boot || (app && !app.hidden) || document.getElementById('retryBoot')) return;
    const english = !String(navigator.language || 'ru').toLowerCase().startsWith('ru');
    const card = document.createElement('div');
    card.className = 'card hero';
    card.setAttribute('role', 'alert');
    const title = document.createElement('h2');
    title.textContent = english ? 'Loading is taking too long' : 'Загрузка затянулась';
    const text = document.createElement('p');
    text.textContent = english ? 'Check your connection and try again. If this continues, open the app on another network.' : 'Проверьте соединение и повторите попытку. Если это не поможет, попробуйте другую сеть.';
    const retry = document.createElement('button');
    retry.className = 'accent';
    retry.textContent = english ? 'Reload' : 'Перезагрузить';
    retry.onclick = () => window.location.reload();
    card.append(title, text, retry);
    boot.replaceChildren(card);
    // The SDK may be the stalled resource. Release the native loading cover
    // using the same bridge event as Telegram.WebApp.ready(), never initData.
    try {
      if (window.Telegram?.WebApp?.ready) window.Telegram.WebApp.ready();
      else window.TelegramWebviewProxy?.postEvent('web_app_ready', '{}');
    } catch { /* The page remains usable in a normal browser. */ }
  }, 15000);
})();
