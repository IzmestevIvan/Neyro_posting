const tg = window.Telegram?.WebApp;
const params = new URLSearchParams(location.search);
const initData = tg?.initData || (params.get('dev') ? `dev:${params.get('dev')}` : '');

const QUALITY = ['fast', 'balanced', 'super'];
const QUALITY_TITLE = { fast: 'Быстро', balanced: 'Баланс', super: 'Суперпостинг' };
const QUALITY_HINT = {
  fast: 'Один запрос: лёгкий рерайт без проверок. Самый дешёвый режим.',
  balanced: 'Отсев рекламы и оффтопа нейросетью, затем рерайт.',
  super: 'Отсев рекламы, глубокий рерайт с контекстом и финальная проверка второй моделью на выдумки.',
};
const DELAY_LABELS = [['instant', 'Мгновенно'], ['1-10', '1–10 мин'], ['10-30', '10–30 мин'], ['30-90', '30–90 мин']];
const PACE_LABELS = [['as_they_come', 'Как приходят'], ['3', '3 в день'], ['6', '6 в день'], ['12', '12 в день'], ['24', '24 в день']];

let boot = null;
let channel = null;
let page = 'home';
let saveQueue = Promise.resolve();
let savingCount = 0;
const revisions = {};
let feedView = 'pending';
let publishingNow = false;
const drafts = new Map();
const readTicket = (key) => { const token = (revisions[key] || 0) + 1; revisions[key] = token; const id = channel?.id; return () => channel?.id === id && revisions[key] === token; };
const safeLink = (value) => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const icon = (name) => `<svg class="ic"><use href="#i-${name}"/></svg>`;

function esc(text) {
  return String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function toast(message, isError = false) {
  const el = document.createElement('div');
  el.className = `toast${isError ? ' err' : ''}`;
  el.textContent = message;
  el.setAttribute('role', isError ? 'alert' : 'status');
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3400);
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-Init-Data': initData, ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === 'string' ? payload.detail : 'Проверьте значения полей и попробуйте снова';
    throw new Error(detail || `Ошибка ${response.status}`);
  }
  return payload;
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

function ago(iso) {
  if (!iso) return null;
  const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (!Number.isFinite(minutes)) return 'дата неизвестна';
  if (minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${plural(hours, 'час', 'часа', 'часов')} назад`;
  const days = Math.floor(hours / 24);
  return `${days} ${plural(days, 'день', 'дня', 'дней')} назад`;
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
}

/* ---------- home ---------- */

function renderStats(stats) {
  const cells = [
    ['ok', stats.today, 'опубликовано сегодня'],
    ['accent-t', stats.pending, 'ждут проверки'],
    ['', stats.subscribers || '—', 'подписчики'],
    ['', stats.remaining, 'постов осталось · все каналы'],
  ];
  const ready = stats.ready || 0;
  $('#publishNow').disabled = publishingNow || !ready || stats.remaining <= 0;
  $('#publishHint').textContent = stats.remaining <= 0 ? 'Дневной лимит исчерпан. Готовые посты останутся в очереди.'
    : !ready ? 'Готовых постов пока нет. Добавьте источники или отправьте текст боту.'
    : `Готово: ${ready}. Кнопка отправит один пост сразу, минуя расписание и паузу.`;
  const next = !stats.sources ? ['Подключите первый источник', 'Добавьте канал или RSS-ленту, чтобы получать материалы.', 'sources', 'Добавить источник']
    : stats.paused ? ['Сбор материалов на паузе', 'Чтобы получать новые материалы, снимите паузу в настройках.', 'settings', 'Открыть настройки']
    : stats.pending ? ['Есть материалы для проверки', 'Прочитайте текст и вердикт проверки перед публикацией.', 'feed', 'Проверить посты']
    : ['Источники подключены', stats.mode === 'автопостинг' ? 'Готовые материалы публикуются по вашим правилам.' : 'Новые материалы появятся в разделе «Посты» для вашего одобрения.', 'sources', 'Посмотреть источники'];
  $('#nextStep').innerHTML = `<div><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ</span><h3>${next[0]}</h3><p>${next[1]}</p></div><button class="ghost" data-go="${next[2]}">${next[3]} ${icon('back')}</button>`;

  $('#statsGrid').innerHTML = cells
    .map(([cls, value, label]) => `<div class="stat ${cls}"><b>${value ?? 0}</b><i>${label}</i></div>`)
    .join('');

  const time = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  $('#status').textContent = `${stats.paused ? 'на паузе' : 'работает'} · ${stats.mode} · ${time}`;

  drawChart('#postsChart', stats.posts_chart, false);
  drawChart('#subsChart', stats.subscribers_chart, true);
  // Блок системы приходит только администратору; у остальных его просто нет.
  $('#systemBlock').hidden = !stats.system;
  if (stats.system) renderSystem(stats.system);
  setBadge('#feedBadge', stats.pending);
  $('#pendingCount').textContent = stats.pending;
}

function drawChart(selector, data, green) {
  const box = $(selector);
  if (!data?.length) {
    box.innerHTML = '<p class="empty-note" style="margin:auto">пока нет данных</p>';
    return;
  }
  const max = Math.max(...data.map((d) => d.count), 1);
  box.innerHTML = data
    .map((d) => {
      const height = d.count ? Math.max(Math.round((d.count / max) * 100), 4) : 2;
      const day = d.day.slice(5).split('-').reverse().join('.');
      return `<div class="bar${green ? ' green' : ''}">
        <em>${d.count || ''}</em><u style="height:${height}%"></u><span>${day}</span></div>`;
    })
    .join('');
}

function renderSystem(sys) {
  const lines = [];
  lines.push([sys.polling ? 'sources' : 'bolt',
    sys.polling ? `Читаю источник: ${esc(sys.polling)}` : esc(sys.activity)]);

  const bits = [];
  const last = ago(sys.last_publish_at);
  if (last) bits.push(`Последний пост: ${last}`);
  if (sys.model_cooldown) bits.push(`осн. модель ещё ${sys.model_cooldown} мин`);
  if (bits.length) lines.push(['inbox', bits.join(' · ')]);
  if (sys.last_error) lines.push(['alert', esc(sys.last_error)]);

  const ramPercent = sys.ram_total ? Math.round((sys.ram_used / sys.ram_total) * 100) : 0;
  $('#system').innerHTML = `
    <div class="meter"><span class="name">CPU</span><span class="track"><span class="fill" style="width:${sys.cpu}%"></span></span><span>${sys.cpu}%</span></div>
    <div class="meter"><span class="name">RAM</span><span class="track"><span class="fill" style="width:${ramPercent}%"></span></span><span>${sys.ram_used}/${sys.ram_total} ГБ</span></div>
    ${lines.map(([ico, text]) => `<p>${icon(ico)}<span>${text}</span></p>`).join('')}`;
}

function setBadge(selector, count) {
  const badge = $(selector);
  badge.hidden = !count;
  badge.textContent = count;
}

/* ---------- feed ---------- */

async function loadFeed() {
  if (!channel) return;
  const current = readTicket('feed');
  const posts = await api(`/channels/${channel.id}/feed`);
  if (!current()) return;
  $('#pendingCount').textContent = posts.length;
  setBadge('#feedBadge', posts.length);
  if (!posts.length) {
    $('#feed').innerHTML = '<p class="empty-note">Пусто — все посты разобраны.</p>';
    return;
  }
  $('#feed').innerHTML = (posts.length === 30 ? '<p class="hint">Показаны последние 30 постов. После обработки появятся остальные.</p>' : '') + posts
    .map((post) => {
      const photo = post.media.find((m) => m.type === 'photo' && (m.url || '').startsWith('http'));
      const attached = !photo && post.media.length
        ? `<p class="note">${icon('clip')}<span>${post.media.length} медиа из чата — прикрепится при публикации</span></p>` : '';
      const warn = post.fact_check && post.fact_check.ok === false
        ? `<p class="note warn">${icon('alert')}<span>Фактчек: ${esc(post.fact_check.verdict || 'есть замечания')}</span></p>` : `<p class="note ${post.fact_check?.ok === true ? 'ok' : ''}">${icon('shield')}<span>${post.fact_check?.ok === true ? 'Проверка пройдена · сверьте важные факты с оригиналом' : 'Без финального фактчека · проверьте текст перед публикацией'}</span></p>`;
      return `<article class="post" data-id="${post.id}">
        <header>
          <span>${esc(post.source_title || 'источник')} · ${ago(post.created_at) || ''}</span>
          ${post.url ? `<a href="${esc(safeLink(post.url))}" target="_blank" rel="noopener noreferrer">${icon('link')}оригинал</a>` : ''}
        </header>
        ${photo ? `<img src="${esc(photo.url)}" loading="lazy" alt="">` : ''}
        ${attached}${warn}
        <div class="text">${esc(post.text_out || post.raw_text)}</div>
        <div class="acts">
          <button class="ok" data-act="approve" ${post.text_out?.trim() ? '' : 'disabled title="Нет готового текста"'}>${icon('check')} Опубликовать</button>
          <button data-act="regen">${icon('refresh')} Переписать</button>
          <button class="no" data-act="reject" aria-label="Отклонить пост">${icon('close')}</button>
        </div>
      </article>`;
    })
    .join('');
}

$('#feed').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-act]');
  if (!button) return;
  const article = button.closest('.post');
  const { id } = article.dataset;
  const action = button.dataset.act;

  $$('.post .acts button').forEach((b) => (b.disabled = true));
  try {
    const result = await api(`/posts/${id}/${action}`, { method: 'POST' });
    tg?.HapticFeedback?.notificationOccurred('success');
    if (action === 'regen') {
      await loadFeed();
      toast('Переписано');
    } else {
      article.remove();
      toast(action === 'approve' ? 'Опубликовано' : 'Отклонено');
      loadFeed().catch(() => {});
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    $$('.post .acts button').forEach((b) => (b.disabled = false));
  }
});

/* ---------- ads ---------- */

async function loadAds() {
  if (!channel) return;
  const current = readTicket('ads');
  const offers = await api(`/channels/${channel.id}/ads`);
  if (!current()) return;
  $('#adsCount').textContent = offers.length;
  setBadge('#adsBadge', offers.filter((o) => o.status === 'new').length);
  if (!offers.length) {
    $('#ads').innerHTML = '<p class="empty-note">Пока никого. Как только бот отсеет рекламу из источников, она появится здесь.</p>';
    return;
  }
  $('#ads').innerHTML = offers
    .map((offer) => {
      const contacts = (offer.contacts || []).map((c) => {
        const href = c.startsWith('t.me/') ? `https://${c}` : c.startsWith('@') ? `https://t.me/${c.slice(1)}`
          : c.includes('@') ? `mailto:${c}` : null;
        return href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(c)}</a>` : `<span>${esc(c)}</span>`;
      }).join('');
      return `<article class="ad${offer.status === 'contacted' ? ' done' : ''}" data-id="${offer.id}">
        <header>
          <span class="who">${esc(offer.advertiser || 'рекламодатель не определён')}</span>
          <span class="seen">${offer.seen_count > 1 ? offer.seen_count + '× · ' : ''}${ago(offer.last_seen_at) || 'только что'}</span>
        </header>
        <p class="note">${icon('sources')}<span>${esc(offer.source_title || 'источник')}${offer.url ? ` · <a href="${esc(safeLink(offer.url))}" target="_blank" rel="noopener noreferrer" style="color:var(--accent)">оригинал</a>` : ''}</span></p>
        ${contacts ? `<div class="contacts">${contacts}</div>` : '<p class="note">Контактов в тексте нет — смотрите оригинал.</p>'}
        <div class="excerpt">${esc(offer.raw_text)}</div>
        <div class="acts">
          <button class="${offer.status === 'contacted' ? '' : 'ok'}" data-act="${offer.status === 'contacted' ? 'new' : 'contacted'}">
            ${offer.status === 'contacted' ? 'Вернуть в работу' : 'Связался'}</button>
          <button class="no" data-act="archived">${icon('trash')}</button>
        </div>
      </article>`;
    })
    .join('');
}

$('#ads').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-act]');
  if (!button) return;
  const { id } = button.closest('.ad').dataset;
  try {
    await api(`/ads/${id}/${button.dataset.act}`, { method: 'POST' });
    loadAds();
  } catch (error) {
    toast(error.message, true);
  }
});

/* ---------- sources ---------- */

async function loadSources() {
  if (!channel) return;
  const current = readTicket('sources');
  const sources = await api(`/channels/${channel.id}/sources`);
  if (!current()) return;
  $('#sourcesCount').textContent = sources.length;
  $('#sources').innerHTML = sources.length
    ? sources
        .map((s) => `<div class="src${s.error ? ' err' : ''}">
            <span class="kind">${s.kind === 'rss' ? 'RSS' : 'TG'}</span>
            <span class="info"><b>${esc(s.title || s.ref)}</b>
              <i>${esc(s.error || (s.kind === 'rss' ? s.ref : '@' + s.ref))}</i></span>
            <button data-id="${s.id}">${icon('trash')}</button>
          </div>`)
        .join('')
    : '<p class="empty-note">Источников пока нет. Добавьте первый — бот начнёт проверять его каждые полторы минуты.</p>';
}

$('#sources').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-id]');
  if (!button) return;
  try {
    await api(`/sources/${button.dataset.id}`, { method: 'DELETE' });
    loadSources();
  } catch (error) {
    toast(error.message, true);
  }
});

$('#addSource').addEventListener('click', async () => {
  if (!channel) return;
  const input = $('#sourceInput');
  if (!input.value.trim()) return;
  $('#addSource').disabled = true;
  try {
    await api(`/channels/${channel.id}/sources`, { method: 'POST', body: JSON.stringify({ ref: input.value.trim() }) });
    input.value = '';
    loadSources();
    toast('Источник добавлен');
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('#addSource').disabled = false;
  }
});

/* ---------- settings ---------- */

function fillSettings() {
  if (!channel) return;
  $$('[data-field]').forEach((el) => {
    const value = drafts.get(`${channel.id}:${el.dataset.field}`) ?? channel[el.dataset.field];
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value ?? '';
  });
  $('#quality').value = QUALITY.indexOf(channel.quality);
  updateQualityLabel();
  renderChips('#delayChips', DELAY_LABELS, channel.delay_mode, (key) => save({ delay_mode: key }));
  renderChips('#paceChips', PACE_LABELS, channel.pace, (key) => save({ pace: key }));
  updateSignaturePreview();
  renderPlan();
}

function renderPlan() {
  const { user } = boot;
  $('#planCard').innerHTML = `
    <span class="ttl">Ваш доступ</span>
    <div class="row"><span><b>${user.is_admin ? 'Администратор' : 'Тариф по коду ' + esc(user.promo_code || '—')}</b>
      <i>${user.is_admin ? 'без ограничений' : 'действует до ' + formatDate(user.access_until)}</i></span></div>
    <div class="row"><span><b>${user.daily_limit} постов в день</b><i>лимит на все ваши каналы</i></span></div>
    <div class="row"><span><b>${user.max_channels} ${plural(user.max_channels, 'канал', 'канала', 'каналов')}</b><i>сколько можно подключить</i></span></div>`;
}

function renderChips(selector, labels, active, onPick) {
  const box = $(selector);
  box.innerHTML = labels
    .map(([key, label]) => `<button class="chip${key === String(active) ? ' on' : ''}" data-key="${key}">${label}</button>`)
    .join('');
  box.onclick = (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    box.querySelectorAll('.chip').forEach((c) => c.classList.toggle('on', c === chip));
    onPick(chip.dataset.key);
  };
}

function updateQualityLabel() {
  const mode = QUALITY[$('#quality').value];
  $('#qualityTitle').textContent = QUALITY_TITLE[mode];
  $('#qualityHint').textContent = QUALITY_HINT[mode];
}

function updateSignaturePreview() {
  const text = $('[data-field="signature_text"]').value.trim();
  const preview = $('#sigPreview');
  preview.textContent = text || '— без подписи —';
  preview.className = text ? '' : 'none';
  $('#sigUrl').placeholder = channel?.username ? `пусто — на t.me/${channel.username}` : 'пусто — на этот канал';
}

function save(body, silent = false) {
  if (!channel) return Promise.resolve();
  const id = channel.id;
  savingCount++;
  $('#saveStatus').textContent = 'Сохраняем изменения…';
  $('#channelSelect').disabled = true;
  $('#addChannel').disabled = true;
  const run = async () => {
    try {
      const updated = await api(`/channels/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
      boot.channels = boot.channels.map(c => c.id === id ? updated : c);
      if (channel?.id === id) channel = updated;
      Object.entries(body).forEach(([key, value]) => { if (drafts.get(`${id}:${key}`) === value) drafts.delete(`${id}:${key}`); });
      $('#saveStatus').textContent = 'Изменения сохранены';
      if (!silent) toast('Сохранено');
      if (['paused', 'autopost'].some(key => key in body)) await refreshStats();
    } catch (error) {
      $('#saveStatus').textContent = `Не сохранено: ${error.message}. Повторите изменение.`;
      toast(error.message, true);
      if (channel?.id === id) {
        for (const [key, selector] of [['delay_mode', '#delayChips'], ['pace', '#paceChips']]) {
          if (key in body) $(selector).querySelectorAll('.chip').forEach(el => el.classList.toggle('on', el.dataset.key === String(channel[key])));
        }
        if ('quality' in body) { $('#quality').value = QUALITY.indexOf(channel.quality); updateQualityLabel(); }
      }
      // Restore only failed fields, preserving unrelated text drafts.
      if (channel?.id === id) Object.keys(body).forEach(key => {
        const el = $(`[data-field="${key}"]`);
        if (el && (el.type === 'checkbox' || el.type === 'number' || el.tagName === 'SELECT')) {
          if (el.type === 'checkbox') el.checked = !!channel[key]; else el.value = channel[key];
        }
      });
    } finally {
      savingCount--;
      $('#channelSelect').disabled = savingCount > 0;
      $('#addChannel').disabled = savingCount > 0;
    }
  };
  saveQueue = saveQueue.then(run, run);
  return saveQueue;
}

$$('[data-field]').forEach((el) => {
  if (el.tagName === 'TEXTAREA' || ['text', 'password'].includes(el.type)) {
    el.addEventListener('input', () => { if (channel) drafts.set(`${channel.id}:${el.dataset.field}`, el.value); });
  }
  if (el.type === 'checkbox' || el.tagName === 'SELECT' || el.type === 'number') {
    el.addEventListener('change', () => {
      save({ [el.dataset.field]: el.type === 'checkbox' ? el.checked : el.value }, true);

    });
  }
});

$('[data-field="signature_text"]').addEventListener('input', updateSignaturePreview);
$('#quality').addEventListener('input', updateQualityLabel);
$('#quality').addEventListener('change', () => save({ quality: QUALITY[$('#quality').value] }));

$$('[data-save]').forEach((button) => {
  button.addEventListener('click', () => {
    const what = button.dataset.save;
    save(what === 'signature'
      ? { signature_text: $('[data-field="signature_text"]').value, signature_url: $('[data-field="signature_url"]').value }
      : { [what]: $(`[data-field="${what}"]`).value });
  });
});

function ask(question) {
  return new Promise((resolve) => {
    if (tg?.showConfirm) tg.showConfirm(question, resolve);
    else resolve(confirm(question));
  });
}

$('#deleteChannel').addEventListener('click', async () => {
  if (!channel) return;
  if (!(await ask('Удалить канал из панели? Посты и источники будут стёрты.'))) return;
  try {
    await api(`/channels/${channel.id}`, { method: 'DELETE' });
    location.reload();
  } catch (error) {
    toast(error.message, true);
  }
});

/* ---------- admin ---------- */

async function loadAdmin() {
  const data = await api('/admin/overview');
  const { totals } = data;
  $('#adminTotals').innerHTML = [
    ['', totals.posts, 'постов всего'],
    ['ok', totals.published, 'опубликовано'],
    ['accent-t', totals.ai_today, 'запросов ИИ'],
  ].map(([cls, v, l]) => `<div class="stat ${cls}"><b>${v ?? 0}</b><i>${l}</i></div>`).join('');

  $('#adminUsers').innerHTML = data.users.map((u) => `
    <div class="adminrow">
      <span>${esc(u.first_name || u.tg_id)}${u.username ? ` @${esc(u.username)}` : ''}
        <div class="sub">id ${u.tg_id} · каналов ${u.channels}${u.promo_code ? ' · ' + esc(u.promo_code) : ''}</div></span>
      <input type="number" value="${u.daily_limit}" data-user="${u.tg_id}" title="лимит постов в день">
    </div>`).join('') || '<p class="empty-note">Пользователей пока нет.</p>';

  $('#adminChannels').innerHTML = data.channels.map((c) => `
    <div class="adminrow">
      <span>${esc(c.title || c.username)}
        <div class="sub">владелец ${c.owner_id} · источников ${c.sources}</div></span>
      <span class="tag${c.paused ? '' : ' live'}">${c.paused ? 'пауза' : c.autopost ? 'авто' : 'модерация'}</span>
    </div>`).join('') || '<p class="empty-note">Каналов пока нет.</p>';

  $('#adminUsers').querySelectorAll('input[data-user]').forEach((input) => {
    input.addEventListener('change', async () => {
      try {
        await api(`/admin/users/${input.dataset.user}`, {
          method: 'POST', body: JSON.stringify({ daily_limit: Number(input.value) }),
        });
        toast('Лимит обновлён');
      } catch (error) {
        toast(error.message, true);
      }
    });
  });

  await loadPromoCodes();
}

async function loadPromoCodes() {
  const codes = await api('/admin/promo');
  $('#promoList').innerHTML = codes.map((c) => `
    <div class="adminrow">
      <span class="codechip${c.used_by ? ' used' : ''}">${esc(c.code)}
        <div class="sub">${esc(c.plan)} · ${c.daily_limit}/день · ${c.max_channels} кан. · ${c.days} дн.${
          c.used_by ? ` · активировал ${esc(c.first_name || c.used_by)}` : ''}${c.note ? ' · ' + esc(c.note) : ''}</div></span>
      ${c.used_by ? '<span class="tag">занят</span>'
        : `<span class="pair"><button class="ghost square" data-copy="${esc(c.code)}">${icon('copy')}</button>
           <button class="ghost square" data-drop="${esc(c.code)}">${icon('trash')}</button></span>`}
    </div>`).join('') || '<p class="empty-note">Кодов пока нет — выпустите первый.</p>';
}

$('#promoList').addEventListener('click', async (event) => {
  const copy = event.target.closest('[data-copy]');
  if (copy) {
    try {
      await navigator.clipboard.writeText(copy.dataset.copy);
      toast(`Код ${copy.dataset.copy} скопирован`);
    } catch {
      toast(copy.dataset.copy);
    }
    return;
  }
  const drop = event.target.closest('[data-drop]');
  if (!drop) return;
  if (!(await ask(`Удалить код ${drop.dataset.drop}?`))) return;
  try {
    await api(`/admin/promo/${drop.dataset.drop}`, { method: 'DELETE' });
    loadPromoCodes();
  } catch (error) {
    toast(error.message, true);
  }
});

$('#promoCreate').addEventListener('click', async () => {
  $('#promoCreate').disabled = true;
  try {
    const result = await api('/admin/promo', {
      method: 'POST',
      body: JSON.stringify({
        count: Number($('#promoCount').value) || 1,
        plan: $('#promoPlan').value,
        note: $('#promoNote').value,
      }),
    });
    $('#promoNote').value = '';
    toast(`Выпущено кодов: ${result.codes.length}`);
    loadPromoCodes();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('#promoCreate').disabled = false;
  }
});

$('#openAdmin').addEventListener('click', () => {
  $$('.page').forEach((s) => (s.hidden = s.id !== 'page-admin'));
  $('#nav').hidden = true;
  $('#channelBar').hidden = true;
  loadAdmin().catch((e) => toast(e.message, true));
});

$('#closeAdmin').addEventListener('click', () => {
  if (!boot.user.has_access) return showGate();
  if (!channel) return showOnboarding();
  $('#nav').hidden = false;
  $('#channelBar').hidden = false;
  openPage(page);
});

/* ---------- promo gate ---------- */

$('#promoSubmit').addEventListener('click', async () => {
  const code = $('#promoInput').value.trim();
  if (!code) return toast('Введите код', true);
  $('#promoSubmit').disabled = true;
  try {
    await api('/promo/redeem', { method: 'POST', body: JSON.stringify({ code }) });
    toast('Код принят');
    location.reload();
  } catch (error) {
    toast(error.message, true);
    $('#gateHint').textContent = error.message;
  } finally {
    $('#promoSubmit').disabled = false;
  }
});

$('#promoInput').addEventListener('input', (event) => {
  const clean = event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
  event.target.value = clean.length > 4 ? `${clean.slice(0, 4)}-${clean.slice(4)}` : clean;
});

/* ---------- channels ---------- */

async function createChannel(input, button) {
  const ref = input.value.trim();
  if (!ref) return toast('Введите @username канала', true);
  button.disabled = true;
  try {
    const created = await api('/channels', { method: 'POST', body: JSON.stringify({ ref }) });
    boot.channels.push(created);
    channel = created;
    input.value = '';
    $('#addChannelRow').hidden = true;
    enterNormalMode();
    toast('Канал добавлен');
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

$('#addChannel').addEventListener('click', () => {
  const row = $('#addChannelRow');
  row.hidden = !row.hidden;
  if (!row.hidden) $('#channelInput').focus();
});
$('#channelSubmit').addEventListener('click', () => createChannel($('#channelInput'), $('#channelSubmit')));
$('#firstChannelSubmit').addEventListener('click', () => createChannel($('#firstChannelInput'), $('#firstChannelSubmit')));

const SUBMIT_BY_INPUT = {
  sourceInput: '#addSource',
  channelInput: '#channelSubmit',
  firstChannelInput: '#firstChannelSubmit',
  promoInput: '#promoSubmit',
};

Object.keys(SUBMIT_BY_INPUT).forEach((id) => {
  $(`#${id}`).addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    event.target.blur();
    $(SUBMIT_BY_INPUT[id]).click();
  });
});

$('#channelSelect').addEventListener('change', (event) => {
  Object.keys(revisions).forEach(key => revisions[key]++);
  channel = boot.channels.find((c) => c.id === Number(event.target.value));
  ['feed', 'ads', 'sources', 'statsGrid', 'nextStep', 'history'].forEach(id => { $(`#${id}`).innerHTML = ''; });
  $('#publishNow').disabled = true;
  setBadge('#feedBadge', 0); setBadge('#adsBadge', 0);
  fillSettings();
  openPage(page);
});

$('#publishNow').addEventListener('click', async () => {
  if (!channel) return;
  publishingNow = true;
  $('#publishNow').disabled = true;
  try {
    await api(`/channels/${channel.id}/publish_now`, { method: 'POST' });
    toast('Опубликовано');
    refreshStats();
  } catch (error) {
    toast(error.message, true);
  } finally {
    publishingNow = false;
    await refreshStats();
  }
});

function renderChannelList() {
  $('#channelSelect').innerHTML = boot.channels
    .map((c) => `<option value="${c.id}"${c.id === channel?.id ? ' selected' : ''}>${esc(c.title || '@' + c.username)}</option>`)
    .join('');
}

async function refreshStats() {
  if (!channel) return;
  const current = readTicket('stats');
  try {
    const stats = await api(`/channels/${channel.id}/stats`);
    if (current()) renderStats(stats);
  } catch (error) {
    if (current()) $('#status').textContent = error.message;
  }
}

/* ---------- navigation ---------- */

const PAGE_LOADERS = { home: refreshStats, feed: () => feedView === 'pending' ? loadFeed() : loadHistory(), ads: loadAds, sources: loadSources };

function openPage(name) {
  if (!channel) return;
  page = name;
  $$('.nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === name));
  $$('.page').forEach((s) => (s.hidden = s.id !== `page-${name}`));
  const load = PAGE_LOADERS[name];
  if (load) Promise.resolve(load()).catch((e) => toast(e.message, true));
}

$$('.nav button').forEach((b) => b.addEventListener('click', () => openPage(b.dataset.page)));

function showOnly(id) {
  $$('.page').forEach((s) => (s.hidden = s.id !== id));
  $('#nav').hidden = true;
  $('#channelBar').hidden = true;
  $('#addChannelRow').hidden = true;
}

function showGate() {
  channel = null;
  $('#status').textContent = 'доступ не активирован';
  $('#gateHint').textContent = boot.user.access_until
    ? `Прошлый доступ закончился ${formatDate(boot.user.access_until)}.` : '';
  showOnly('gate');
}

function showOnboarding() {
  channel = null;
  $('#status').textContent = 'канал ещё не подключён';
  $('#botName').textContent = boot.bot_username ? `@${boot.bot_username}` : 'бота';
  showOnly('onboarding');
}

function enterNormalMode() {
  $('#gate').hidden = true;
  $('#onboarding').hidden = true;
  $('#nav').hidden = false;
  $('#channelBar').hidden = false;
  renderChannelList();
  fillSettings();
  openPage('home');
  loadAds().catch(() => {});
}

/* ---------- приветственный тур ---------- */

const TOUR_KEY = 'neyro:tour_seen';

function tourSeen() {
  try {
    return localStorage.getItem(TOUR_KEY) === '1';
  } catch {
    return false; // приватный режим — покажем тур, это не страшно
  }
}

function closeTour() {
  try {
    localStorage.setItem(TOUR_KEY, '1');
  } catch {
    /* не смогли запомнить — тур покажется ещё раз, ничего не ломается */
  }
  $('#tour').hidden = true;
}

function setupTour() {
  const track = $('#tourTrack');
  const slides = $$('#tourTrack .slide');
  const dots = $('#tourDots');
  const next = $('#tourNext');
  let index = 0;

  dots.innerHTML = slides.map((_, i) => `<i class="${i ? '' : 'on'}"></i>`).join('');

  function show(i) {
    index = Math.max(0, Math.min(i, slides.length - 1));
    [...dots.children].forEach((d, n) => d.classList.toggle('on', n === index));
    next.textContent = index === slides.length - 1 ? 'Начать' : 'Дальше';
  }

  track.addEventListener('scroll', () => {
    const at = Math.round(track.scrollLeft / track.clientWidth);
    if (at !== index) show(at);
  }, { passive: true });

  next.addEventListener('click', () => {
    if (index === slides.length - 1) return closeTour();
    track.scrollTo({ left: (index + 1) * track.clientWidth, behavior: 'smooth' });
    show(index + 1);
  });

  $('#tourSkip').addEventListener('click', closeTour);
  show(0);
  $('#tour').hidden = false;
}

/* ---------- start ---------- */

function fillOptions(select, entries) {
  select.innerHTML = entries.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
}

async function start() {
  tg?.ready();
  tg?.expand();

  try {
    boot = await api('/bootstrap');
  } catch (error) {
    $('#boot').innerHTML = `<div class="card hero"><h2>Не удалось открыть редакцию</h2><p class="muted">${esc(error.message)}</p><p>Откройте приложение через кнопку в Telegram-боте.</p><button class="accent" id="retryBoot">Попробовать снова</button></div>`;
    $('#retryBoot').onclick = start;
    return;
  }

  fillOptions($('#langSelect'), Object.entries(boot.languages));
  fillOptions($('#tzSelect'), boot.timezones.map((t) => [t, t.split('/').pop().replace('_', ' ')]));
  fillOptions($('#digestTime'), Array.from({ length: 48 }, (_, i) => {
    const value = `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 ? '30' : '00'}`;
    return [value, value];
  }));
  fillOptions($('#promoPlan'), [['start', 'Старт'], ['pro', 'Про'], ['unlim', 'Безлимит']]);

  $('#boot').hidden = true;
  $('#app').hidden = false;
  $('#openAdmin').hidden = !boot.user.is_admin;
  if (!tourSeen()) setupTour();

  setInterval(() => page === 'home' && channel && refreshStats(), 15000);

  if (!boot.user.has_access) return showGate();
  if (!boot.channels.length) return showOnboarding();

  channel = boot.channels[0];
  enterNormalMode();
}

$('#nextStep').addEventListener('click', e => { const button = e.target.closest('[data-go]'); if (button) openPage(button.dataset.go); });
const HISTORY_LABELS = { published: 'Опубликован', failed: 'Ошибка публикации', filtered: 'Отфильтрован', duplicate: 'Дубликат', rejected: 'Отклонён', approved: 'В очереди', new: 'Обрабатывается', digest: 'В дайджесте', publishing: 'Отправляется' };
async function loadHistory() {
  if (!channel) return;
  const current = readTicket('history');
  const rows = await api(`/channels/${channel.id}/history`);
  if (!current()) return;
  $('#history').innerHTML = rows.length ? '<p class="hint">Последние 50 событий обработки.</p>' + rows.map(row => `<article class="post history-item"><header><span>${esc(row.source_title || 'Ручной пост')}</span><span class="tag">${esc(HISTORY_LABELS[row.status] || row.status)}</span></header><p>${esc(row.preview)}</p>${row.reason ? `<p class="hint">${esc(row.reason)}</p>` : ''}<span class="hint">${esc(ago(row.published_at || row.created_at))}</span></article>`).join('') : '<div class="empty-note">Здесь будет история обработки и публикаций.</div>';
}
function switchFeed(view) {
  feedView = view;
  $('#feed').hidden = view !== 'pending'; $('#history').hidden = view !== 'history';
  [$('#showPending'), $('#showHistory')].forEach((el, i) => { const active = (i === 0) === (view === 'pending'); el.classList.toggle('on', active); el.setAttribute('aria-pressed', active); });
  (view === 'pending' ? loadFeed() : loadHistory()).catch(e => toast(e.message, true));
}
$('#showPending').onclick = () => switchFeed('pending');
$('#showHistory').onclick = () => switchFeed('history');
start();
