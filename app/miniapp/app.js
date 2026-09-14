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
let adminOpen = false;
let monitoringBusy = false;
let saveQueue = Promise.resolve();
let savingCount = 0;
const revisions = {};
let feedView = 'pending';
let adsView = 'active';
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
  $('#publishNow').disabled = publishingNow || !stats.sources || stats.remaining <= 0;
  $('#publishNow').innerHTML = channel.business_mode ? 'Подготовить пост компании' : `${icon('bolt')} Опубликовать сейчас`;
  if (channel.business_mode) $('#publishNow').disabled = publishingNow;
  $('#publishHint').textContent = publishingNow ? 'Проверяю источники и готовлю один свежий пост…'
    : stats.remaining <= 0 ? 'Дневной лимит исчерпан.'
    : !stats.sources ? 'Добавьте источник, чтобы подготовить свежий пост.'
    : 'Найдёт свежий материал, проверит и опубликует один пост сейчас, вне расписания. Старые новости не отправляет.';
  const next = !stats.sources ? ['Подключите первый источник', 'Добавьте канал или RSS-ленту, чтобы получать материалы.', 'sources', 'Добавить источник']
    : stats.paused ? ['Сбор материалов на паузе', 'Чтобы получать новые материалы, снимите паузу в настройках.', 'settings', 'Открыть настройки']
    : stats.pending ? ['Есть материалы для проверки', 'Прочитайте текст и вердикт проверки перед публикацией.', 'feed', 'Проверить посты']
    : ['Источники подключены', stats.mode === 'автопостинг' ? 'Готовые материалы публикуются по вашим правилам.' : 'Новые материалы появятся в разделе «Посты» для вашего одобрения.', 'sources', 'Посмотреть источники'];
  $('#nextStep').innerHTML = `<div><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ</span><h3>${next[0]}</h3><p>${next[1]}</p></div><button class="ghost" data-go="${next[2]}">${next[3]} ${icon('back')}</button>`;

  if (channel.business_mode) {
    $('#publishHint').textContent = 'Досье компании → черновик → ваше подтверждение. Без согласования ничего не публикуем.';
    $('#nextStep').innerHTML = `<div><span class="eyebrow">РЕДАКТОР КОМПАНИИ</span><h3>Публикации от лица бизнеса</h3><p>Досье — в настройках. Готовые черновики — во вкладке «Посты».</p></div><button class="ghost" data-go="settings">Досье компании ${icon('back')}</button>`;
  }
  $('#statsGrid').innerHTML = cells
    .map(([cls, value, label]) => `<div class="stat ${cls}"><b>${value ?? 0}</b><i>${label}</i></div>`)
    .join('');

  const time = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  $('#status').textContent = `${stats.paused ? 'на паузе' : 'работает'} · ${stats.mode} · ${time}`;
  const waiting = stats.waiting || {};
  const details = [];
  if (waiting.last_published_at) details.push(`Последняя публикация: ${ago(waiting.last_published_at)}.`);
  if (waiting.next_at) details.push(`Ближайший срок в очереди: ${new Date(waiting.next_at).toLocaleString('ru-RU')}.`);
  if (waiting.digest) details.push(`В дайджесте: ${waiting.digest}, время выпуска — ${channel.digest_time} (${channel.tz}).`);
  if (waiting.processing) details.push(`Ожидают обработки: ${waiting.processing}.`);
  if (!stats.queued && stats.sources) details.push('Очередь пуста — ожидаем подходящие новости. Темп не гарантирует количество постов.');
  if (stats.last_rejection) details.push(`Последний отсев (${ago(stats.last_rejection.created_at)}): ${stats.last_rejection.reason}.`);
  $('#queueDetails').hidden = !details.length;
  $('#queueReason').textContent = details.join(' ');

  drawChart('#postsChart', stats.posts_chart, false);
  drawChart('#subsChart', stats.subscribers_chart, true);
  // Блок системы приходит только администратору; у остальных его просто нет.
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
  if (sys.last_error) lines.push(['alert', `Последняя записанная ошибка: ${esc(sys.last_error)}`]);
  lines.push(['inbox', `Память приложения: ${sys.process_mb} МБ`]);

  const ramPercent = sys.ram_percent;
  $('#system').innerHTML = `
    <div class="meter"><span class="name">CPU</span><span class="track"><span class="fill" style="width:${sys.cpu}%"></span></span><span>${sys.cpu}%</span></div>
    <div class="meter"><span class="name">RAM</span><span class="track"><span class="fill" style="width:${ramPercent}%"></span></span><span>${sys.ram_used}/${sys.ram_total} ГБ</span></div>
    <div class="meter"><span class="name">Диск</span><span class="track"><span class="fill" style="width:${sys.disk_percent}%"></span></span><span>${sys.disk_used}/${sys.disk_total} ГБ</span></div>
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
        ${attached}${warn}${post.reason ? `<p class="note warn">${esc(post.reason)}</p>` : ''}
        <div class="text">${esc(post.text_out || post.raw_text)}</div>
        <div class="acts">
          <button class="ok" data-act="approve" ${post.text_out?.trim() ? '' : 'disabled title="Нет готового текста"'}>${icon('check')} ${post.business_draft ? 'Согласовать и опубликовать' : 'Опубликовать'}</button>
          <button data-act="${post.business_draft ? 'edit' : 'regen'}">${icon('refresh')} ${post.business_draft ? 'Править' : 'Переписать'}</button>
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
  if (action === 'edit') {
    if (article.querySelector('.business-edit')) return;
    const editor = document.createElement('div');
    editor.className = 'business-edit';
    editor.innerHTML = '<label>Текст перед согласованием<textarea maxlength="3000"></textarea></label><button data-act="save-edit">Сохранить правки</button><button data-act="cancel-edit">Отмена</button>';
    editor.querySelector('textarea').value = article.querySelector('.text').textContent;
    article.appendChild(editor);
    article.querySelector('[data-act="approve"]').disabled = true;
    return;
  }
  if (action === 'cancel-edit') {
    article.querySelector('.business-edit').remove();
    article.querySelector('[data-act="approve"]').disabled = false;
    return;
  }

  $$('.post .acts button').forEach((b) => (b.disabled = true));
  try {
    const result = await api(`/posts/${id}/${action === 'save-edit' ? 'edit' : action}`, { method: 'POST',
      ...(action === 'save-edit' ? {body: JSON.stringify({text:article.querySelector('.business-edit textarea').value})}
        : action === 'approve' ? {body:JSON.stringify({text:article.querySelector('.text').textContent})} : {}) });
    tg?.HapticFeedback?.notificationOccurred('success');
    if (action === 'regen' || action === 'save-edit') {
      await loadFeed();
      toast('Черновик сохранён. Для публикации подтвердите его.');
    } else {
      article.remove();
      toast(action === 'approve' ? 'Опубликовано' : 'Отклонено');
      loadFeed().catch(() => {});
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    $$('.post .acts button').forEach((b) => (b.disabled = b.dataset.act === 'approve' && (b.hasAttribute('title') || !!b.closest('.post').querySelector('.business-edit'))));
  }
});

/* ---------- ads ---------- */

async function loadAds() {
  if (!channel) return;
  const current = readTicket('ads');
  const offers = await api(`/channels/${channel.id}/ads?view=${adsView}`);
  if (!current()) return;
  $('#adsCount').textContent = offers.length;
  if (adsView === 'active') setBadge('#adsBadge', offers.filter((o) => o.status === 'new').length);
  if (!offers.length) {
    $('#ads').innerHTML = `<p class="empty-note">${adsView === 'archive' ? 'Архив пуст. Здесь появятся убранные материалы и ошибочные срабатывания.' : 'Нет материалов на разбор. Здесь появится возможная реклама из источников.'}</p>`;
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
        <details class="ad-reasons"><summary>Почему материал попал сюда</summary><p>${esc((offer.reasons || []).join(' · ') || 'Причина не сохранена для старой записи')}</p></details>
        ${offer.status === 'false_positive' ? '<p class="note">Вы отметили: это не реклама</p>' : ''}
        <div class="excerpt">${esc(offer.raw_text)}</div>
        <div class="acts">
          ${adsView === 'archive' ? '<button data-act="new">Вернуть на разбор</button>' : `
          <button data-act="${offer.status === 'contacted' ? 'new' : 'contacted'}">${offer.status === 'contacted' ? 'Не связывался' : 'Связался'}</button>
          <button data-act="false_positive">Это не реклама</button>
          <button data-act="archived" aria-label="Убрать в архив">${icon('inbox')}</button>`}
        </div>
      </article>`;
    })
    .join('');
}

$('#ads').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-act]');
  if (!button) return;
  const card = button.closest('.ad');
  const { id } = card.dataset;
  card.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    await api(`/ads/${id}/${button.dataset.act}`, { method: 'POST' });
    await loadAds();
    toast(button.dataset.act === 'false_positive' ? 'Отмечено как ошибка. Материал доступен в архиве.' : 'Статус обновлён');
  } catch (error) {
    toast(error.message, true);
  } finally {
    card.querySelectorAll('button').forEach(b => b.disabled = false);
  }
});

/* ---------- sources ---------- */

let sourceBatchRunning = false;
let sourceBatchCancelled = false;
$('#sourceBatchOpen').addEventListener('click', () => {
  $('#sourceBatchPanel').hidden = false;
  $('#sourceBatchInput').focus();
});
$('#sourceCopyOpen').addEventListener('click', () => {
  $('#copySourcesPanel').open = true;
  $('#copyFromChannel').focus();
});
$('#sourceBotOpen').addEventListener('click', () => {
  if (!/^[a-zA-Z0-9_]+$/.test(boot?.bot_username || '')) return toast('Не удалось получить адрес бота', true);
  const url = `https://t.me/${boot.bot_username}?start=add_source`;
  if (tg?.openTelegramLink) tg.openTelegramLink(url);
  else window.open(url, '_blank', 'noopener,noreferrer');
});
$('#sourceBatchCancel').addEventListener('click', () => { sourceBatchCancelled = true; });
$('#sourceBatchAdd').addEventListener('click', async () => {
  if (!channel || sourceBatchRunning) return;
  const refs = [...new Set($('#sourceBatchInput').value.trim().split(/\s+/).filter(Boolean))];
  if (!refs.length || refs.length > 20) return toast('Вставьте от 1 до 20 адресов, по одному на строку', true);
  const target = channel.id;
  const results = [];
  sourceBatchRunning = true; sourceBatchCancelled = false;
  $('#sourceBatchAdd').disabled = true;
  $('#sourceBatchCancel').hidden = false;
  $('#sourceBatchStatus').textContent = 'Проверяю источники…';
  try {
    for (const ref of refs) {
      if (sourceBatchCancelled || channel?.id !== target) break;
      try {
        const result = await api(`/channels/${target}/sources`, {method:'POST', body:JSON.stringify({ref})});
        results.push({ref, ok:true, text:result.already_exists ? 'Уже добавлен' : 'Добавлен'});
      } catch (error) { results.push({ref, ok:false, text:error.message}); }
      $('#sourceBatchStatus').innerHTML = results.map(r => `<p><b>${esc(r.ref)}</b><br>${esc(r.text)}</p>`).join('');
    }
    $('#sourceBatchInput').value = refs.filter(ref => !results.some(r => r.ref === ref && r.ok)).join('\n');
    if (channel?.id === target) await loadSources();
  } finally {
    sourceBatchRunning = false;
    $('#sourceBatchAdd').disabled = false;
    $('#sourceBatchCancel').hidden = true;
  }
});

function resetSourceCopy() {
  $('#copySourcesPanel').open = false;
  $('#copyFromChannel').innerHTML = '<option value="">Выберите канал</option>' +
    (boot?.channels || []).filter(c => c.id !== channel?.id).map(c => `<option value="${c.id}">${esc(c.title || c.username)}</option>`).join('');
  $('#copySourceList').innerHTML = '';
  $('#copySources').disabled = true;
  $('#copySourcesStatus').textContent = (boot?.channels || []).length < 2 ? 'Для копирования подключите второй канал.' : '';
}

$('#copyFromChannel').addEventListener('change', async () => {
  const current = readTicket('copySources');
  const origin = Number($('#copyFromChannel').value);
  $('#copySourceList').innerHTML = '';
  $('#copySources').disabled = true;
  $('#copySourcesStatus').textContent = origin ? 'Загружаю источники…' : '';
  if (!origin) return;
  try {
    const sources = await api(`/channels/${origin}/sources`);
    if (!current()) return;
    $('#copySourceList').innerHTML = sources.map(s => `<label class="copy-source"><input type="checkbox" value="${s.id}"><span><b>${esc(s.title || s.ref)}</b><small>${esc(s.kind === 'tg' ? '@' + s.ref : s.ref)}</small></span></label>`).join('');
    $('#copySourcesStatus').textContent = sources.length ? 'Отметьте нужные источники.' : 'В этом канале пока нет источников.';
  } catch (error) { if (current()) $('#copySourcesStatus').textContent = error.message; }
});
$('#copySourceList').addEventListener('change', () => {
  const n = $('#copySourceList').querySelectorAll('input:checked').length;
  $('#copySources').disabled = !n;
  $('#copySources').textContent = n ? `Добавить выбранные · ${n}` : 'Добавить выбранные';
});
$('#copySources').addEventListener('click', async () => {
  if (!channel) return;
  const current = readTicket('copySources');
  const target = channel.id;
  const source_ids = [...$('#copySourceList').querySelectorAll('input:checked')].map(el => Number(el.value));
  if (!source_ids.length) return;
  $('#copySources').disabled = true;
  $('#copyFromChannel').disabled = true;
  $('#copySourceList').querySelectorAll('input').forEach(el => el.disabled = true);
  try {
    const result = await api(`/channels/${target}/sources/copy`, {method:'POST', body:JSON.stringify({from_channel_id:Number($('#copyFromChannel').value), source_ids})});
    if (!current()) return;
    $('#copySourcesStatus').textContent = `Добавлено: ${result.added}. Уже были в канале: ${result.skipped}.`;
    $('#copySourceList').querySelectorAll('input').forEach(el => el.checked = false);
    await loadSources();
  } catch (error) { if (current()) $('#copySourcesStatus').textContent = error.message; }
  finally {
    $('#copyFromChannel').disabled = false;
    if (current()) {
      $('#copySourceList').querySelectorAll('input').forEach(el => el.disabled = false);
      $('#copySources').disabled = !$('#copySourceList').querySelectorAll('input:checked').length;
      $('#copySources').textContent = 'Добавить выбранные';
    }
  }
});

async function loadSources() {
  if (!channel) return;
  const current = readTicket('sources');
  const sources = await api(`/channels/${channel.id}/sources`);
  if (!current()) return;
  if ($('#copySourcesPanel').dataset.channel !== String(channel.id)) { resetSourceCopy(); $('#copySourcesPanel').dataset.channel = String(channel.id); }
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
    await loadSources();
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
    await loadSources();
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
  let profile = {};
  try { profile = JSON.parse(channel.business_profile || '{}'); } catch {}
  $$('[data-business]').forEach(el => { el.value = drafts.get(`${channel.id}:business:${el.dataset.business}`) ?? profile[el.dataset.business] ?? ''; });
  $('#businessBrief').value = drafts.get(`${channel.id}:businessBrief`) || '';
  $('#businessUrl').value = drafts.get(`${channel.id}:businessUrl`) || '';
  $('#generateBusiness').disabled = !channel.business_mode || publishingNow;
  $('#logoStatus').textContent = channel.logo_configured ? 'Логотип сохранён.' : 'Логотип пока не загружен.';
  $$('[data-field]').forEach((el) => {
    const value = drafts.get(`${channel.id}:${el.dataset.field}`) ?? channel[el.dataset.field];
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value ?? '';
    if (el.dataset.field === 'gemini_key') el.placeholder = channel.gemini_key_configured ? 'Ключ сохранён. Введите новый для замены' : 'Общий ключ сервиса';
  });
  $('#quality').value = QUALITY.indexOf(channel.quality);
  ['autopost', 'digest_enabled'].forEach(key => { $(`[data-field="${key}"]`).disabled = !!channel.business_mode; });
  updateQualityLabel();
  renderChips('#delayChips', DELAY_LABELS, channel.delay_mode, (key) => save({ delay_mode: key }));
  renderChips('#paceChips', PACE_LABELS, channel.pace, (key) => save({ pace: key }));
  updateSignaturePreview();
  renderPlan();
}

function openBusiness() {
  openPage('settings');
  $('#businessSettings').open = true;
  $('#businessSettings').scrollIntoView({behavior: 'smooth', block: 'start'});
}

$$('[data-business]').forEach(el => el.addEventListener('input', () => {
  if (channel) drafts.set(`${channel.id}:business:${el.dataset.business}`, el.value);
}));
['businessBrief', 'businessUrl'].forEach(id => $(`#${id}`).addEventListener('input', () => {
  if (channel) drafts.set(`${channel.id}:${id}`, $(`#${id}`).value);
}));
$('#saveBusiness').addEventListener('click', async () => {
  const profile = Object.fromEntries($$('[data-business]').map(el => [el.dataset.business, el.value]));
  await save({business_profile: profile});
});
$('#generateBusiness').addEventListener('click', async () => {
  if (!channel || publishingNow || savingCount) return;
  const id = channel.id;
  const input = {brief: $('#businessBrief').value, url: $('#businessUrl').value};
  let saved = {};
  try { saved = JSON.parse(channel.business_profile || '{}'); } catch {}
  if ($$('[data-business]').some(el => el.value.trim() !== (saved[el.dataset.business] || ''))) {
    toast('Сначала сохраните изменения досье', true); return;
  }
  publishingNow = true;
  $('#generateBusiness').disabled = true;
  $('#businessStatus').textContent = 'Готовим черновик. Это может занять несколько минут; публикации не будет.';
  try {
    await api(`/channels/${id}/business/draft`, {method:'POST', body:JSON.stringify(input)});
    toast('Черновик готов — требуется ваше согласование');
    if (channel?.id === id) { $('#businessStatus').textContent = 'Черновик сохранён во вкладке «Посты».'; openPage('feed'); }
  } catch (error) {
    toast(error.message, true);
    if (channel?.id === id) $('#businessStatus').textContent = error.message;
  } finally {
    publishingNow = false;
    $('#generateBusiness').disabled = !channel?.business_mode;
    await refreshStats();
  }
});

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
      if (['business_mode', 'business_auto'].some(key => key in body)) fillSettings();
      if (['paused', 'autopost', 'business_mode'].some(key => key in body)) await refreshStats();
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

$('#logoUpload').addEventListener('change', async (event) => {
  const file = event.target.files?.[0];
  if (!file || !channel) return;
  const id = channel.id;
  event.target.disabled = true;
  try {
    if (file.size > 4 * 1024 * 1024 || !/\.png$/i.test(file.name)) throw new Error('Выберите PNG до 4 МБ');
    await saveQueue;
    const updated = await api(`/channels/${id}/logo`, {method: 'POST', headers: {'Content-Type': 'image/png'}, body: file});
    if (channel?.id === id) {
      channel.logo_configured = updated.logo_configured;
      channel.watermark = updated.watermark;
      fillSettings();
      toast('PNG сохранён, водяной знак включён');
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    event.target.disabled = false;
    event.target.value = '';
  }
});

/* ---------- admin ---------- */

async function refreshAdminMonitoring() {
  if (!adminOpen || monitoringBusy || document.hidden) return;
  monitoringBusy = true;
  try {
    const data = await api('/admin/monitoring');
    if (!adminOpen) return;
    renderSystem(data.system);
    $('#adminEvents').innerHTML = (data.events || []).map(e => `<div class="card"><b>${e.emergency ? '🚨 ' : ''}${esc(e.title)}</b><p>${esc(e.explanation)}</p><p class="hint">${esc(e.channel?.title || 'Сервис')} · ${esc(new Date(e.time).toLocaleString('ru-RU'))}</p><details><summary>Технические детали</summary><p class="hint">${esc(e.detail)}</p></details></div>`).join('') || '<p class="hint">Событий пока нет.</p>';
    const c = data.capacity, q = data.queue;
    $('#adminMonitoring').innerHTML = [
      [c.users, 'пользователей'], [c.channels, 'каналов'],
      [c.active_channels, 'каналов без паузы'],
      [Math.round(c.database_bytes / 1024 ** 2), 'МБ в базе'],
      [q.new, 'ожидают обработки'], [q.pending, 'на модерации'],
      [q.ready, 'готовы / в дайджесте'], [q.publishing, 'публикуются'],
      [q.attention, 'требуют проверки доставки'],
    ].map(([v, label]) => `<div class="stat"><b>${Number(v) || 0}</b><i>${label}</i></div>`).join('');
    $('#adminUpdated').textContent = `Обновлено: ${new Date().toLocaleTimeString('ru-RU')}`;
  } catch (error) {
    if (adminOpen) $('#adminUpdated').textContent = `Не удалось обновить показатели: ${error.message}. Показаны последние полученные данные.`;
  } finally {
    monitoringBusy = false;
  }
}

async function loadAdmin() {
  await loadApiKeys();
  const data = await api('/admin/overview');
  const { totals } = data;
  $('#adminTotals').innerHTML = [
    ['', totals.posts, 'постов в базе'],
    ['ok', totals.published, 'опубликовано в базе'],
    ['accent-t', totals.ai_today, 'запросов ИИ'],
  ].map(([cls, v, l]) => `<div class="stat ${cls}"><b>${v ?? 0}</b><i>${l}</i></div>`).join('');

  $('#adminUsers').innerHTML = data.users.map((u) => `
    <div class="adminrow">
      <span>${esc(u.first_name || u.tg_id)}${u.username ? ` @${esc(u.username)}` : ''}
        <div class="sub">id ${u.tg_id} · каналов ${u.channels}${u.promo_code ? ' · ' + esc(u.promo_code) : ''}</div></span>
      <label class="admin-limit"><span>Постов/день</span><input type="number" min="0" max="100000" value="${u.daily_limit}" data-user="${u.tg_id}" title="лимит постов в день"></label>
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

async function loadApiKeys() {
  const data = await api('/admin/api-keys');
  $('#apiKeyStatus').textContent = `Сохранено ${data.keys.length}/${data.limit}. Ключ из настроек сервера: ${data.server_key_configured ? 'есть, используется как резерв' : 'не задан'}.`;
  $('#apiKeyList').innerHTML = data.keys.map(k => {
    const cooling = k.cooldown_until && new Date(k.cooldown_until) > new Date();
    const state = !k.enabled ? 'Отключён' : cooling ? `Пауза до ${new Date(k.cooldown_until).toLocaleTimeString('ru-RU')}` : k.last_success_at ? 'Доступен для запросов' : 'Ожидает первого успешного запроса';
    return `<div class="card key-card"><b>${esc(k.label)}</b><p class="hint">${esc(state)}${k.last_error ? ' · ' + esc(k.last_error) : ''}</p><div class="form-actions"><button class="ghost" data-key-toggle="${k.id}" data-enabled="${k.enabled}">${k.enabled ? 'Отключить' : 'Включить'}</button><button class="danger" data-key-delete="${k.id}">Удалить</button></div></div>`;
  }).join('');
  $('#apiKeyList').querySelectorAll('button').forEach(button => button.addEventListener('click', async () => {
    const remove = button.dataset.keyDelete;
    if (remove && !window.confirm('Удалить этот ключ из пула? Для возврата потребуется вставить его заново.')) return;
    button.disabled = true;
    try {
      await api(`/admin/api-keys/${remove || button.dataset.keyToggle}`, remove ? {method:'DELETE'} : {method:'PATCH', body:JSON.stringify({enabled:button.dataset.enabled !== 'true'})});
      await loadApiKeys();
    } catch (error) { toast(error.message, true); button.disabled = false; }
  }));
}

$('#apiKeyAdd').addEventListener('click', async () => {
  const input = $('#apiKeyInput'), button = $('#apiKeyAdd');
  button.disabled = true;
  try {
    const result = await api('/admin/api-keys', {method:'POST', body:JSON.stringify({keys:input.value})});
    input.value = '';
    await loadApiKeys();
    toast(`Добавлено ключей: ${result.added}`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$('#apiKeyRefresh').addEventListener('click', () => loadApiKeys().catch(error => toast(error.message, true)));

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
    await loadPromoCodes();
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
    await loadPromoCodes();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('#promoCreate').disabled = false;
  }
});

$('#openAdmin').addEventListener('click', () => {
  adminOpen = true;
  $$('.page').forEach((s) => (s.hidden = s.id !== 'page-admin'));
  $('#nav').hidden = true;
  $('#channelBar').hidden = true;
  loadAdmin().catch((e) => toast(e.message, true));
  refreshAdminMonitoring();
});

$('#closeAdmin').addEventListener('click', () => {
  adminOpen = false;
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
  resetSourceCopy();
  fillSettings();
  openPage(page);
});

$('#publishNow').addEventListener('click', async () => {
  if (!channel) return;
  if (channel.business_mode) { openBusiness(); return; }
  publishingNow = true;
  $('#publishNow').disabled = true;
  $('#publishHint').textContent = 'Проверяю источники и готовлю один свежий пост…';
  try {
    await api(`/channels/${channel.id}/publish_now`, { method: 'POST' });
    toast('Свежий пост опубликован');
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

let tourReturnFocus = null;

function closeTour() {
  try { localStorage.setItem(TOUR_KEY, '1'); } catch { /* Storage may be unavailable. */ }
  $('#tour').hidden = true;
  $('#app').inert = false;
  document.body.classList.remove('tour-open');
  if (tourReturnFocus?.isConnected && tourReturnFocus !== document.body) tourReturnFocus.focus();
}

function setupTour() {
  const tour = $('#tour');
  const track = $('#tourTrack');
  const slides = $$('#tourTrack .slide');
  const dots = $('#tourDots');
  const next = $('#tourNext');
  const prev = $('#tourPrev');
  tourReturnFocus = document.activeElement;
  let index = 0;
  let touch = null;
  dots.innerHTML = slides.map((slide, i) => `<button aria-label="Слайд ${i + 1}: ${esc(slide.querySelector('h2').textContent)}" data-slide="${i}"><span></span></button>`).join('');

  function show(i) {
    index = Math.max(0, Math.min(i, slides.length - 1));
    slides.forEach((slide, n) => { slide.hidden = n !== index; });
    [...dots.children].forEach((dot, n) => { dot.classList.toggle('on', n === index); dot.setAttribute('aria-pressed', String(n === index)); });
    tour.setAttribute('aria-labelledby', slides[index].querySelector('h2').id);
    $('#tourCounter').textContent = `${String(index + 1).padStart(2, '0')} / ${String(slides.length).padStart(2, '0')}`;
    prev.disabled = index === 0;
    next.innerHTML = index === slides.length - 1 ? `В редакцию ${icon('check')}` : `Дальше ${icon('back')}`;
    track.scrollTop = 0;
  }

  next.onclick = () => index === slides.length - 1 ? closeTour() : show(index + 1);
  prev.onclick = () => show(index - 1);
  dots.onclick = event => { const dot = event.target.closest('[data-slide]'); if (dot) show(Number(dot.dataset.slide)); };
  $('#tourSkip').onclick = closeTour;
  document.onkeydown = event => {
    if (tour.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeTour(); }
    if (event.key === 'ArrowRight') { event.preventDefault(); show(index + 1); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); show(index - 1); }
    if (event.key === 'Tab') {
      const buttons = [...tour.querySelectorAll('button:not(:disabled)')];
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (!tour.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  };
  track.onpointerdown = event => { touch = { x:event.clientX, y:event.clientY, id:event.pointerId }; };
  track.onpointercancel = () => { touch = null; };
  track.onpointerup = event => {
    if (!touch || touch.id !== event.pointerId) return;
    const dx = event.clientX - touch.x, dy = event.clientY - touch.y;
    touch = null;
    if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.5) show(index + (dx < 0 ? 1 : -1));
  };
  tour.hidden = false;
  $('#app').inert = true;
  document.body.classList.add('tour-open');
  show(0);
  $('#tourSkip').focus();
}

$('#replayTour').addEventListener('click', setupTour);

/* ---------- start ---------- */

function fillOptions(select, entries) {
  select.innerHTML = entries.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
}

async function start() {
  tg?.ready();
  tg?.expand();

  try {
    boot = await api('/bootstrap');
    if (/^[A-Za-z0-9_]{5,32}$/.test(boot.support_username || '')) {
      $('#supportLink').href = `https://t.me/${boot.support_username}`;
      $('#supportLink').hidden = false;
    }
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

  setInterval(() => {
    if (document.hidden) return;
    if (adminOpen) refreshAdminMonitoring();
    else if (page === 'home' && channel) refreshStats();
  }, 15000);

  if (!boot.user.has_access) return showGate();
  if (!boot.channels.length) return showOnboarding();

  channel = boot.channels[0];
  enterNormalMode();
}

$('#nextStep').addEventListener('click', e => { const button = e.target.closest('[data-go]'); if (button) openPage(button.dataset.go); });
const HISTORY_LABELS = { expired: 'Устарел', published: 'Опубликован', failed: 'Ошибка публикации', filtered: 'Отфильтрован', duplicate: 'Дубликат', rejected: 'Отклонён', approved: 'В очереди', new: 'Обрабатывается', digest: 'В дайджесте', publishing: 'Отправляется', uncertain: 'Нужна сверка с каналом', partial: 'Отправлена только часть', digest_item: 'Включён в дайджест' };
async function loadHistory() {
  if (!channel) return;
  const current = readTicket('history');
  const rows = await api(`/channels/${channel.id}/history`);
  if (!current()) return;
  $('#history').innerHTML = rows.length ? '<p class="hint">Последние 50 событий обработки.</p>' + rows.map(row => `<article class="post history-item"><header><span>${esc(row.source_title || 'Ручной пост')}</span><span class="tag">${esc(HISTORY_LABELS[row.status] || row.status)}</span></header><p>${esc(row.preview)}</p>${row.reason ? `<p class="hint">${esc(row.reason)}</p>` : ''}${row.delivery_receipts?.length ? `<p class="hint">Telegram подтвердил сообщения: ${esc(row.delivery_receipts.flatMap(step => step.message_ids).map(id => '#' + id).join(', '))}</p>` : ''}<span class="hint">${esc(ago(row.published_at || row.created_at))}</span>${['partial','uncertain'].includes(row.status) ? `<div class="acts"><button data-reconcile="confirm_sent" data-id="${row.id}">Проверено: пост опубликован полностью</button>${row.status === 'uncertain' ? `<button data-reconcile="confirm_absent" data-id="${row.id}">Проверено: в канале ничего нет</button>` : ''}</div>` : ''}</article>`).join('') : '<div class="empty-note">Здесь будет история обработки и публикаций.</div>';
}
function switchFeed(view) {
  feedView = view;
  $('#feed').hidden = view !== 'pending'; $('#history').hidden = view !== 'history';
  [$('#showPending'), $('#showHistory')].forEach((el, i) => { const active = (i === 0) === (view === 'pending'); el.classList.toggle('on', active); el.setAttribute('aria-pressed', active); });
  (view === 'pending' ? loadFeed() : loadHistory()).catch(e => toast(e.message, true));
}
$('#showPending').onclick = () => switchFeed('pending');
$('#showHistory').onclick = () => switchFeed('history');
async function switchAds(view) {
  adsView = view;
  revisions.ads = (revisions.ads || 0) + 1;
  $('#ads').innerHTML = '<p class="empty-note" role="status">Загружаем материалы…</p>';
  $('#adsActive').classList.toggle('on', view === 'active');
  $('#adsArchive').classList.toggle('on', view === 'archive');
  try { await loadAds(); } catch (error) { $('#ads').innerHTML = '<p class="empty-note">Не удалось загрузить материалы. Нажмите вкладку, чтобы повторить.</p>'; toast(error.message, true); }
}
$('#adsActive').onclick = () => switchAds('active');
$('#adsArchive').onclick = () => switchAds('archive');
$('#history').addEventListener('click', async event => {
  const button = event.target.closest('[data-reconcile]');
  if (!button) return;
  const action = button.dataset.reconcile;
  const question = action === 'confirm_sent' ? 'Вы проверили канал и видите весь пост, включая текст и медиа? Это отметит публикацию завершённой.' : 'Вы проверили канал и убедились, что ни текст, ни медиа не появились? Пост вернётся на ручную проверку. Сейчас ничего не отправится.';
  if (!(await ask(question))) return;
  button.disabled = true;
  try {
    await api(`/posts/${button.dataset.id}/${action}`, {method:'POST'});
    await loadHistory();
    toast('Результат сверки сохранён');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
start();
