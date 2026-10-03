const tg = window.Telegram?.WebApp;
const params = new URLSearchParams(location.search);
const initData = tg?.initData || (params.get('dev') ? `dev:${params.get('dev')}` : '');
const tr = (ru, en) => window.NeyroPrefs?.t(ru, en) ?? ru;
const uiLocale = () => window.NeyroPrefs?.language === 'en' ? 'en-GB' : 'ru-RU';

const QUALITY = ['fast', 'balanced', 'super'];
const ACCESS_PLANS = {start:{get name(){return tr("Код · Старт","Code \u00b7 Starter");},daily:100,channels:1,days:30},pro:{get name(){return tr("Код · Про","Code \u00b7 Pro");},daily:500,channels:3,days:30},unlim:{get name(){return tr("Код · Расширенный","Code \u00b7 Extended");},daily:2000,channels:10,days:365},custom:{get name(){return tr("Код · Индивидуальный","Code \u00b7 Custom");},daily:100,channels:1,days:30}};
const QUALITY_TITLE = {get fast(){return tr('Быстро','Fast');},get balanced(){return tr('Баланс','Balanced');},get super(){return tr('Суперпостинг','Superposting');}};
const QUALITY_HINT = {
  get fast(){return tr('Один запрос: лёгкий рерайт без проверок. Самый дешёвый режим.','One request: a light rewrite with no checks. The least expensive mode.');},
  get balanced(){return tr('Отсев рекламы и оффтопа нейросетью, затем рерайт.','AI filters ads and off-topic content, then rewrites the text.');},
  get super(){return tr('Отсев рекламы, глубокий рерайт с контекстом и финальная проверка второй моделью на выдумки.','Ad filtering, a detailed contextual rewrite and a final check by a second model.');},
};
const DELAY_LABELS = [['instant', 'Мгновенно','Instant'], ['1-10', '1–10 мин','1–10 min'], ['10-30', '10–30 мин','10–30 min'], ['30-90', '30–90 мин','30–90 min']];
const PACE_LABELS = [['as_they_come', 'Как приходят','As they arrive'], ['3', '3 в день','3 per day'], ['6', '6 в день','6 per day'], ['12', '12 в день','12 per day'], ['24', '24 в день','24 per day']];

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
let subscription = null;
let billingLoading = false;
let billingReturnPage = 'home';
let exploring = false;
const exploreDisabled = new Map();
const drafts = new Map();
const readTicket = (key) => { const token = (revisions[key] || 0) + 1; revisions[key] = token; const id = channel?.id; return () => channel?.id === id && revisions[key] === token; };
const safeLink = (value) => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } };

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const icon = (name) => `<svg class="ic"><use href="#i-${name}"/></svg>`;

function esc(text) {
  return String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function researchCard(research) {
  if (!research?.sources?.length) return '';
  const links = research.sources.map(s => `<p><a href="${esc(safeLink(s.uri))}" target="_blank" rel="noopener noreferrer">${esc(s.title || 'Найденная страница')}</a></p>`).join('');
  const entry = research.search_entry ? `<iframe title="Поисковые подсказки Google" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" srcdoc="${esc(research.search_entry)}"></iframe>` : '';
  return `<details class="business-research"><summary>Что найдено о компании · проверьте совпадение</summary>${links}${entry}</details>`;
}

function toast(message, isError = false) {
  document.querySelectorAll('.toast').forEach(el => el.remove());
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
    const detail = typeof payload.detail === 'string' ? payload.detail : tr('Проверьте значения полей и попробуйте снова','Check the fields and try again');
    const known = {'Слишком много запросов':'Too many requests. Please wait and try again.', 'Доступ запрещён':'Access denied', 'Канал не найден':'Channel not found', 'Пост не найден':'Post not found', 'Дневной лимит исчерпан':'Daily limit reached', 'Требуется авторизация':'Please sign in again'};
    throw new Error(known[detail] ? tr(detail,known[detail]) : detail || tr(`Ошибка ${response.status}`,`Error ${response.status}`));
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
  if (!Number.isFinite(minutes)) return tr("дата неизвестна","unknown date");
  if (minutes < 1) return tr("только что","just now");
  if (minutes < 60) return tr(`${minutes} мин назад`,`${minutes} min ago`);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return tr(`${hours} ${plural(hours, 'час', 'часа', 'часов')} назад`,`${hours} ${hours===1?'hour':'hours'} ago`);
  const days = Math.floor(hours / 24);
  return tr(`${days} ${plural(days, 'день', 'дня', 'дней')} назад`,`${days} ${days===1?'day':'days'} ago`);
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(uiLocale(), { day: 'numeric', month: 'long', year: 'numeric' });
}

/* ---------- home ---------- */

let lastRenderedStats = null;
function renderStats(stats) {
  lastRenderedStats = {channelId:channel?.id,stats};
  const cells = [
    ['ok', stats.today, tr("опубликовано сегодня","published today")],
    ['accent-t', stats.pending, tr("ждут проверки","awaiting review")],
    ['', stats.ai_remaining ?? stats.remaining, tr("постов с ИИ осталось","AI posts remaining")],
  ];
  $('#publishNow').disabled = publishingNow || !!channel.business_mode || !stats.sources || (stats.ai_remaining ?? stats.remaining) <= 0;
  $('#publishNow').innerHTML = `${tr("Опубликовать сейчас","Publish now")} ${icon('bolt')}`;
  $('#publishHint').textContent = publishingNow ? tr("Проверяю источники и готовлю один свежий пост…","Checking sources and preparing one fresh post…")
    : (stats.ai_remaining ?? stats.remaining) <= 0 ? tr("Дневной лимит исчерпан.","Daily limit reached.")
    : !stats.sources ? tr("Добавьте источник, чтобы подготовить свежий пост.","Add a source to prepare a fresh post.")
    : tr("Найдёт свежий материал, проверит и опубликует один пост сейчас, вне расписания. Старые новости не отправляет.","Finds, checks and publishes one fresh post now, outside the schedule. Old news is skipped.");
  const next = !stats.sources ? [tr("Подключите первый источник","Connect your first source"), tr("Добавьте канал или RSS-ленту, чтобы получать материалы.","Add a channel or RSS feed to receive content."), 'sources', tr("Добавить источник","Add a source")]
    : stats.paused ? [tr("Сбор материалов на паузе","Source checks paused"), tr("Чтобы получать новые материалы, снимите паузу в настройках.","Turn off pause in Settings to receive new content."), 'settings', tr("Открыть настройки","Open settings")]
    : stats.pending ? [tr("Есть материалы для проверки","Content is ready for review"), tr("Прочитайте текст и вердикт проверки перед публикацией.","Read the text and check notes before publishing."), 'feed', tr("Проверить посты","Review posts")]
    : [tr("Источники подключены","Sources connected"), stats.mode === 'автопостинг' ? tr("Готовые материалы публикуются по вашим правилам.","Prepared content is published using your rules.") : tr("Новые материалы появятся в разделе «Посты» для вашего одобрения.","New content will appear in Posts for your approval."), 'sources', tr("Посмотреть источники","View sources")];
  $('#nextStep').innerHTML = `<div><span class="eyebrow">${tr("СЛЕДУЮЩИЙ ШАГ","NEXT STEP")}</span><h3>${next[0]}</h3><p>${next[1]}</p></div><button class="ghost" data-go="${next[2]}">${next[3]} ${icon('back')}</button>`;

  if (channel.business_mode) $('#publishHint').textContent = tr("Подготовка новых постов для этого канала временно недоступна.","New posts are temporarily unavailable for this channel.");
  $('#statsGrid').innerHTML = cells
    .map(([cls, value, label]) => `<div class="stat ${cls}"><b>${value ?? 0}</b><i>${label}</i></div>`)
    .join('');

  $('#status').textContent = stats.paused ? tr("Сбор материалов на паузе","Source checks paused") : stats.mode === 'автопостинг' ? tr("Автопостинг включён","Autoposting enabled") : tr("Публикация после проверки","Publishing after review");
  const waiting = stats.waiting || {};
  const details = [];
  $('#channelBlockers').hidden = !stats.blockers?.length;
  $('#channelBlockers').textContent = (stats.blockers || []).join(' ');
  if (waiting.last_published_at) details.push(tr(`Последняя публикация: ${ago(waiting.last_published_at)}.`,`Last published: ${ago(waiting.last_published_at)}.`));
  if (waiting.next_at) details.push(tr(`Ближайший срок в очереди: ${new Date(waiting.next_at).toLocaleString(uiLocale())}.`,`Next queued time: ${new Date(waiting.next_at).toLocaleString(uiLocale())}.`));
  if (waiting.digest) details.push(tr(`В дайджесте: ${waiting.digest}, время выпуска — ${channel.digest_time} (${channel.tz}).`,`In digest: ${waiting.digest}, scheduled at ${channel.digest_time} (${channel.tz}).`));
  if (waiting.processing) details.push(tr(`Ожидают обработки: ${waiting.processing}.`,`Waiting to process: ${waiting.processing}.`));
  if (!stats.queued && stats.sources) details.push(tr("Очередь пуста — ожидаем подходящие новости. Темп не гарантирует количество постов.","The queue is empty while suitable news is awaited. Pace does not guarantee a number of posts."));
  if (stats.last_rejection) details.push(tr(`Последний отсев (${ago(stats.last_rejection.created_at)}): ${stats.last_rejection.reason}.`,`Last filtered item (${ago(stats.last_rejection.created_at)}): ${stats.last_rejection.reason}.`));
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
    box.innerHTML = tr("<p class=\"empty-note\" style=\"margin:auto\">пока нет данных</p>","<p class=\"empty-note\" style=\"margin:auto\">no data yet</p>");
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
    sys.polling ? tr(`Читаю источник: ${esc(sys.polling)}`,`Reading source: ${esc(sys.polling)}`) : esc(sys.activity)]);

  const bits = [];
  const last = ago(sys.last_publish_at);
  if (last) bits.push(tr(`Последний пост: ${last}`,`Last post: ${last}`));
  if (sys.model_cooldown) bits.push(tr(`осн. модель ещё ${sys.model_cooldown} мин`,`main model cooldown: ${sys.model_cooldown} min`));
  if (bits.length) lines.push(['inbox', bits.join(' · ')]);
  if (sys.last_error) lines.push(['alert', tr(`Последняя записанная ошибка: ${esc(sys.last_error)}`,`Last recorded error: ${esc(sys.last_error)}`)]);
  lines.push(['inbox', tr(`Память приложения: ${sys.process_mb} МБ`,`App memory: ${sys.process_mb} MB`)]);

  const ramPercent = sys.ram_percent;
  $('#system').innerHTML = `
    <div class="meter"><span class="name">CPU</span><span class="track"><span class="fill" style="width:${sys.cpu}%"></span></span><span>${sys.cpu}%</span></div>
    <div class="meter"><span class="name">RAM</span><span class="track"><span class="fill" style="width:${ramPercent}%"></span></span><span>${sys.ram_used}/${sys.ram_total} ${tr("ГБ","GB")}</span></div>
    <div class="meter"><span class="name">${tr("Диск","Disk")}</span><span class="track"><span class="fill" style="width:${sys.disk_percent}%"></span></span><span>${sys.disk_used}/${sys.disk_total} ${tr("ГБ","GB")}</span></div>
    ${lines.map(([ico, text]) => `<p>${icon(ico)}<span>${text}</span></p>`).join('')}`;
}

function setBadge(selector, count) {
  const badge = $(selector);
  badge.hidden = !count;
  badge.textContent = count;
}

/* ---------- feed ---------- */

async function loadFeed() {
  if (!channel) { if (exploring) renderExploreWorkspace(); return; }
  const current = readTicket('feed');
  const posts = await api(`/channels/${channel.id}/feed`);
  if (!current()) return;
  $('#pendingCount').textContent = posts.length;
  setBadge('#feedBadge', posts.length);
  if (!posts.length) {
    $('#feed').innerHTML = tr("<p class=\"empty-note\">Пусто — все посты разобраны.</p>","<p class=\"empty-note\">All caught up — no posts to review.</p>");
    return;
  }
  $('#feed').innerHTML = (posts.length === 30 ? tr("<p class=\"hint\">Показаны последние 30 постов. После обработки появятся остальные.</p>","<p class=\"hint\">Showing the latest 30 posts. More will appear after you review these.</p>") : '') + posts
    .map((post) => {
      const unavailable = post.business_draft && !boot?.features?.business_mode;
      const photo = post.media.find((m) => m.type === 'photo' && (m.data || (m.url || '').startsWith('http')));
      const photoUrl = photo?.data ? `data:image/jpeg;base64,${photo.data}` : photo?.url;
      const attached = !photo && post.media.length
        ? `<p class="note">${icon('clip')}<span>${tr(`${post.media.length} медиа из чата — прикрепится при публикации`,`${post.media.length} media items from chat — attached on publication`)}</span></p>` : '';
      const warn = post.fact_check && post.fact_check.ok === false
        ? `<p class="note warn">${icon('alert')}<span>${tr("Фактчек:","Fact-check:")} ${esc(post.fact_check.verdict || tr("есть замечания","issues found"))}</span></p>` : `<p class="note ${post.fact_check?.ok === true ? 'ok' : ''}">${icon('shield')}<span>${post.fact_check?.ok === true ? tr("Проверка пройдена · сверьте важные факты с оригиналом","Checks passed · verify important facts against the original") : tr("Без финального фактчека · проверьте текст перед публикацией","No final fact-check · review before publishing")}</span></p>`;
      return `<article class="post" data-id="${post.id}" data-media-revision="${esc(post.media_revision || '')}">
        <header>
          <span>${esc(post.source_title || tr("источник","source"))} · ${ago(post.created_at) || ''}</span>
          ${post.url ? `<a href="${esc(safeLink(post.url))}" target="_blank" rel="noopener noreferrer">${icon('link')}${tr('оригинал','original')}</a>` : ''}
        </header>
        ${photo ? `<img src="${esc(photoUrl)}" loading="lazy" alt="${tr('Фото к посту — проверьте перед согласованием','Post photo — review before approval')}">` : ''}
        ${attached}${warn}${post.reason ? `<p class="note warn">${esc(post.reason)}</p>` : ''}
        <div class="text">${esc(post.text_out || post.raw_text)}</div>
        ${post.business_draft && !unavailable ? `<div class="business-photo"><label class="upload-control"><span>${photo ? 'Заменить фото' : 'Загрузить фото проекта'}</span><small>JPEG, PNG или WebP · до 4 МБ</small><input type="file" class="post-photo" accept="image/jpeg,image/png,image/webp"></label><p class="hint">Только свои фото или изображения с разрешением на публикацию.</p>${photo?.source ? `<a href="${esc(safeLink(photo.source))}" target="_blank" rel="noopener noreferrer">Страница фотографии · проверьте проект</a>` : ''}${post.media.length ? '<button data-act="remove_photo">Убрать фото</button>' : '<p class="note warn">Фото не найдено — загрузите его перед согласованием.</p>'}</div>` : ''}
        ${unavailable ? '<p class="notice">Материал сохранён. Редактирование и публикация временно недоступны.</p>' : `<div class="acts">
          <button class="ok" data-act="approve" ${post.text_out?.trim() ? '' : tr("disabled title=\"Нет готового текста\"","disabled title=\"No prepared text\"")}>${icon('check')} ${post.business_draft ? tr("Согласовать и опубликовать","Approve and publish") : tr("Опубликовать","Publish")}</button>
          <button data-act="${post.business_draft ? 'edit' : 'regen'}">${icon('refresh')} ${post.business_draft ? tr("Править","Edit") : tr("Переписать","Rewrite")}</button>
          <button class="no" data-act="reject" aria-label="${tr('Отклонить пост','Reject post')}">${icon('close')}</button>
        </div>`}
        ${researchCard(post.fact_check?.research)}
      </article>`;
    })
    .join('');
}

$('#feed').addEventListener('change', async (event) => {
  if (!event.target.matches('.post-photo')) return;
  const file = event.target.files[0];
  if (!file) return;
  const article = event.target.closest('.post');
  if (file.size > 4 * 1024 * 1024) { toast(tr("Фото: максимум 4 МБ","Photo: 4 MB maximum"), true); event.target.value = ''; return; }
  article.querySelectorAll('button,input').forEach(el => el.disabled = true);
  try {
    await api(`/posts/${article.dataset.id}/photo`, {method:'POST', headers:{'Content-Type':file.type, 'X-Media-Revision':article.dataset.mediaRevision}, body:file});
    await loadFeed();
    toast(tr("Фото сохранено. Проверьте его вместе с текстом.","Photo saved. Review it together with the text."));
  } catch (error) { toast(error.message, true); }
  finally { article.querySelectorAll('button,input').forEach(el => el.disabled = false); }
});

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
      headers: {'X-Media-Revision':article.dataset.mediaRevision},
      ...(action === 'save-edit' ? {body: JSON.stringify({text:article.querySelector('.business-edit textarea').value})}
        : action === 'approve' ? {body:JSON.stringify({text:article.querySelector('.text').textContent, media_revision:article.dataset.mediaRevision || undefined})} : {}) });
    tg?.HapticFeedback?.notificationOccurred('success');
    if (action === 'regen' || action === 'save-edit' || action === 'remove_photo') {
      await loadFeed();
      toast('Черновик сохранён. Для публикации подтвердите его.');
    } else {
      article.remove();
      toast(action === 'approve' ? tr("Опубликовано","Published") : tr("Отклонено","Rejected"));
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
  if (!channel) { if (exploring) renderExploreWorkspace(); return; }
  const current = readTicket('ads');
  const offers = await api(`/channels/${channel.id}/ads?view=${adsView}`);
  if (!current()) return;
  $('#adsCount').textContent = offers.length;
  if (adsView === 'active') setBadge('#adsBadge', offers.filter((o) => o.status === 'new').length);
  if (!offers.length) {
    $('#ads').innerHTML = `<p class="empty-note">${adsView === 'archive' ? tr("Архив пуст. Здесь появятся убранные материалы и ошибочные срабатывания.","The archive is empty. Dismissed items and false positives appear here.") : tr("Нет материалов на разбор. Здесь появится возможная реклама из источников.","Nothing to review. Potential ads from sources appear here.")}</p>`;
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
          <span class="who">${esc(offer.advertiser || tr("рекламодатель не определён","advertiser unknown"))}</span>
          <span class="seen">${offer.seen_count > 1 ? offer.seen_count + '× · ' : ''}${ago(offer.last_seen_at) || tr("только что","just now")}</span>
        </header>
        <p class="note">${icon('sources')}<span>${esc(offer.source_title || tr("источник","source"))}${offer.url ? ` · <a href="${esc(safeLink(offer.url))}" target="_blank" rel="noopener noreferrer" style="color:var(--accent)">${tr("оригинал","original")}</a>` : ''}</span></p>
        ${contacts ? `<div class="contacts">${contacts}</div>` : tr("<p class=\"note\">Контактов в тексте нет — смотрите оригинал.</p>","<p class=\"note\">No contact details in the text — check the original.</p>")}
        <details class="ad-reasons"><summary>${tr("Почему материал попал сюда","Why this item was filtered")}</summary><p>${esc((offer.reasons || []).join(' · ') || tr("Причина не сохранена для старой записи","No reason was saved for this older entry"))}</p></details>
        ${offer.status === 'false_positive' ? tr("<p class=\"note\">Вы отметили: это не реклама</p>","<p class=\"note\">You marked this as not an ad.</p>") : ''}
        <div class="excerpt">${esc(offer.raw_text)}</div>
        <div class="acts">
          ${adsView === 'archive' ? tr("<button data-act=\"new\">Вернуть на разбор</button>","<button data-act=\"new\">Return to review</button>") : `
          <button data-act="${offer.status === 'contacted' ? 'new' : 'contacted'}">${offer.status === 'contacted' ? tr("Не связывался","Not contacted") : tr("Связался","Contacted")}</button>
          <button data-act="false_positive">${tr("Это не реклама","Not an ad")}</button>
          <button data-act="archived" aria-label="${tr('Убрать в архив','Archive')}">${icon('inbox')}</button>`}
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
    toast(button.dataset.act === 'false_positive' ? tr("Отмечено как ошибка. Материал доступен в архиве.","Marked as a false positive. The item is in the archive.") : tr("Статус обновлён","Status updated"));
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
  if (!/^[a-zA-Z0-9_]+$/.test(boot?.bot_username || '')) return toast(tr("Не удалось получить адрес бота","Could not load the bot address"), true);
  const url = `https://t.me/${boot.bot_username}?start=add_source`;
  if (tg?.openTelegramLink) tg.openTelegramLink(url);
  else window.open(url, '_blank', 'noopener,noreferrer');
});
$('#sourceBatchCancel').addEventListener('click', () => { sourceBatchCancelled = true; });
$('#sourceBatchAdd').addEventListener('click', async () => {
  if (!channel || sourceBatchRunning) return;
  const refs = [...new Set($('#sourceBatchInput').value.trim().split(/\s+/).filter(Boolean))];
  if (!refs.length || refs.length > 20) return toast(tr("Вставьте от 1 до 20 адресов, по одному на строку","Paste 1 to 20 addresses, one per line"), true);
  const target = channel.id;
  const results = [];
  sourceBatchRunning = true; sourceBatchCancelled = false;
  $('#sourceBatchAdd').disabled = true;
  $('#sourceBatchCancel').hidden = false;
  $('#sourceBatchStatus').textContent = tr("Проверяю источники…","Checking sources…");
  try {
    for (const ref of refs) {
      if (sourceBatchCancelled || channel?.id !== target) break;
      try {
        const result = await api(`/channels/${target}/sources`, {method:'POST', body:JSON.stringify({ref})});
        results.push({ref, ok:true, text:result.already_exists ? tr("Уже добавлен","Already added") : tr("Добавлен","Added")});
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
  $('#copyFromChannel').innerHTML = tr("<option value=\"\">Выберите канал</option>","<option value=\"\">Choose a channel</option>") +
    (boot?.channels || []).filter(c => c.id !== channel?.id).map(c => `<option value="${c.id}">${esc(c.title || c.username)}</option>`).join('');
  $('#copySourceList').innerHTML = '';
  $('#copySources').disabled = true;
  $('#copySourcesStatus').textContent = (boot?.channels || []).length < 2 ? tr("Для копирования подключите второй канал.","Connect another channel to reuse its sources.") : '';
}

$('#copyFromChannel').addEventListener('change', async () => {
  const current = readTicket('copySources');
  const origin = Number($('#copyFromChannel').value);
  $('#copySourceList').innerHTML = '';
  $('#copySources').disabled = true;
  $('#copySourcesStatus').textContent = origin ? tr("Загружаю источники…","Loading sources…") : '';
  if (!origin) return;
  try {
    const sources = await api(`/channels/${origin}/sources`);
    if (!current()) return;
    $('#copySourceList').innerHTML = sources.map(s => `<label class="copy-source"><input type="checkbox" value="${s.id}"><span><b>${esc(s.title || s.ref)}</b><small>${esc(s.kind === 'tg' ? '@' + s.ref : s.ref)}</small></span></label>`).join('');
    $('#copySourcesStatus').textContent = sources.length ? tr("Отметьте нужные источники.","Select the sources you need.") : tr("В этом канале пока нет источников.","This channel has no sources yet.");
  } catch (error) { if (current()) $('#copySourcesStatus').textContent = error.message; }
});
$('#copySourceList').addEventListener('change', () => {
  const n = $('#copySourceList').querySelectorAll('input:checked').length;
  $('#copySources').disabled = !n;
  $('#copySources').textContent = n ? tr(`Добавить выбранные · ${n}`,`Add selected · ${n}`) : tr("Добавить выбранные","Add selected");
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
    $('#copySourcesStatus').textContent = tr(`Добавлено: ${result.added}. Уже были в канале: ${result.skipped}.`,`Added: ${result.added}. Already in the channel: ${result.skipped}.`);
    $('#copySourceList').querySelectorAll('input').forEach(el => el.checked = false);
    await loadSources();
  } catch (error) { if (current()) $('#copySourcesStatus').textContent = error.message; }
  finally {
    $('#copyFromChannel').disabled = false;
    if (current()) {
      $('#copySourceList').querySelectorAll('input').forEach(el => el.disabled = false);
      $('#copySources').disabled = !$('#copySourceList').querySelectorAll('input:checked').length;
      $('#copySources').textContent = tr("Добавить выбранные","Add selected");
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
    : tr("<p class=\"empty-note\">Источников пока нет. Добавьте первый — бот начнёт проверять его каждые полторы минуты.</p>","<p class=\"empty-note\">No sources yet. Add one and the bot will check it about every 90 seconds.</p>");
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
    toast(tr("Источник добавлен","Source added"));
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
  $$('[data-business]').forEach(el => { el.value = drafts.get(`${channel.id}:business:${el.dataset.business}`) ?? profile[el.dataset.business] ?? (el.dataset.business === 'content_policy' ? 'company_only' : ''); });
  $('#businessBrief').value = drafts.get(`${channel.id}:businessBrief`) || '';
  $('#businessUrl').value = drafts.get(`${channel.id}:businessUrl`) || '';
  $('#generateBusiness').disabled = !channel.business_mode || publishingNow;
  $('#logoStatus').textContent = channel.logo_configured ? tr("Логотип сохранён.","Logo saved.") : tr("Логотип пока не загружен.","No logo uploaded yet.");
  $$('[data-field]').forEach((el) => {
    const value = drafts.get(`${channel.id}:${el.dataset.field}`) ?? channel[el.dataset.field];
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value ?? '';
    if (el.dataset.field === 'gemini_key') el.placeholder = channel.gemini_key_configured ? tr("Ключ сохранён. Введите новый для замены","Key saved. Enter a new one to replace it") : tr("Общий ключ сервиса","Shared service key");
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
  if ($$('[data-business]').some(el => el.value.trim() !== (saved[el.dataset.business] || (el.dataset.business === 'content_policy' ? 'company_only' : '')))) {
    toast('Сначала сохраните изменения досье', true); return;
  }
  publishingNow = true;
  $('#generateBusiness').disabled = true;
  $('#businessStatus').textContent = 'Готовим черновик. Это может занять несколько минут; публикации не будет.';
  try {
    await api(`/channels/${id}/business/draft`, {method:'POST', body:JSON.stringify(input)});
    toast('Черновик готов — требуется ваше согласование');
    if (channel?.id === id) {
      $('#businessStatus').textContent = 'Черновик сохранён во вкладке «Посты».';
      openPage('feed'); await switchFeed('pending');
    }
  } catch (error) {
    if (channel?.id === id) {
      $('#businessStatus').textContent = error.message;
      $('#businessStatus').scrollIntoView({behavior:'smooth', block:'center'});
    } else toast(error.message, true);
  } finally {
    publishingNow = false;
    $('#generateBusiness').disabled = !channel?.business_mode;
    await refreshStats();
  }
});

function renderPlan() {
  const user = boot?.user || {};
  const data = subscription;
  const active = data ? data.status === 'active' : !!user.has_access;
  const plan = data?.current_plan;
  const name = plan ? String(plan).toUpperCase() : user.is_admin ? tr('Администратор','Administrator') : active ? tr('Доступ по коду','Access code') : tr('Нет активного тарифа','No active plan');
  const limit = data?.ai_daily_limit ?? user.ai_daily_limit ?? user.daily_limit ?? 0;
  const used = data?.ai_used_today ?? 0;
  const until = data?.period_end || user.access_until;
  const remaining = Math.max(0, limit - used);
  $('#planCard').innerHTML = `<div class="plan-meta"><div><span class="eyebrow">${tr('ВАШ ДОСТУП','YOUR ACCESS')}</span><h3>${esc(name)}</h3></div><span class="tag">${active ? tr('Активен','Active') : tr('Не активен','Inactive')}</span></div>
    <p class="hint">${user.is_admin && !plan ? tr('Управление сервисом','Service administration') : until ? `${active ? tr('Действует до','Valid until') : tr('Истёк','Expired')} ${esc(formatDate(until))}` : tr('Подключённый доступ появится здесь','Your access will appear here')}</p>
    ${active && (!user.is_admin || plan) ? `<progress class="quota-meter" max="${Math.max(1, limit)}" value="${Math.min(used, limit)}" aria-label="${tr('Использовано постов с ИИ сегодня','AI posts used today')}"></progress><div class="quota-copy"><span>${data ? tr(`${used} из ${limit} использовано сегодня`,`${used} of ${limit} used today`) : tr(`${limit} постов с ИИ в день`,`${limit} AI posts per day`)}</span>${data ? `<span>${tr(`Осталось ${remaining}`,`${remaining} remaining`)}</span>` : ''}</div><p class="hint plan-account-limit">${tr('Общий лимит на все каналы','Shared limit across all channels')}</p>` : ''}
    ${data?.scheduled_plan ? `<p class="scheduled">${data.next_period_paid ? tr('Оплачен следующий период','Next period paid:') : tr('На следующий период выбран','Selected for the next period:')} ${esc(String(data.scheduled_plan).toUpperCase())} ${tr('с','from')} ${esc(formatDate(data.scheduled_at))}.${data.next_period_paid ? '' : tr(' Доступ на новый период пока не активирован.',' Access for the next period is not active yet.')}</p>` : ''}`;
  $('#homePlanHint').textContent = active ? user.is_admin && !plan ? tr('Доступ администратора','Administrator access') : tr(`${name} · ${limit} с ИИ в день`,`${name} · ${limit} AI posts per day`) : tr('Доступ и лимиты','Access and limits');
}

async function loadBilling() {
  if (billingLoading || !boot) return;
  billingLoading = true;
  $('#billingRefresh').disabled = true;
  $('#billingRefresh').textContent = tr('Проверяем тариф…','Checking your plan…');
  $('#billingAvailability').hidden = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  renderPlan();
  try {
    const options = {signal:controller.signal,cache:'no-store'};
    const refreshed = await api('/bootstrap',options);
    if (!refreshed?.user || !Array.isArray(refreshed.channels)) throw new Error(tr('Не удалось обновить доступ','Could not refresh access'));
    const data = await api('/billing/subscription',options);
    if (!data || !['active','inactive'].includes(data.status)) throw new Error(tr('Не удалось получить состояние доступа','Could not retrieve access status'));
    subscription = data;
    // Refresh access without replacing selected channel settings or unsaved drafts.
    boot.user = {...refreshed.user,has_access:data.status === 'active'};
    if (!channel && refreshed.channels.length) {
      boot.channels = refreshed.channels;
      channel = boot.channels[0];
      if (exploring) leaveExploreMode();
      renderChannelList(); fillSettings();
    }
    $('#nav').hidden = (!channel && !exploring) || !boot.user.has_access;
    $('#channelBar').hidden = (!channel && !exploring) || !boot.user.has_access;
    if (!channel && !boot.user.has_access && page !== 'billing') showGate();
    renderPlan();
    $('#billingAvailability').textContent = tr('Данные тарифа обновлены.','Your plan is up to date.');
    $('#billingAvailability').hidden = false;
  } catch (error) {
    const message = error.name === 'AbortError' ? tr('Сервер не ответил вовремя','The server did not respond in time') : error.message;
    $('#billingAvailability').textContent = `${message}. ${tr('Попробуйте обновить ещё раз.','Please try refreshing again.')}`;
    $('#billingAvailability').hidden = false;
  } finally {
    clearTimeout(timeout);
    billingLoading = false;
    $('#billingRefresh').disabled = false;
    $('#billingRefresh').innerHTML = `${tr('Обновить тариф','Refresh plan')} ${icon('refresh')}`;
  }
}
$('#billingRefresh').addEventListener('click', loadBilling);
$('#billingBack').addEventListener('click', () => {
  if (!boot?.user.has_access) return showGate();
  if (!channel) return exploring ? enterExploreMode(billingReturnPage) : showOnboarding();
  openPage(billingReturnPage);
});

function renderChips(selector, labels, active, onPick) {
  const box = $(selector);
  box.innerHTML = labels
    .map(([key, label, english]) => `<button class="chip${key === String(active) ? ' on' : ''}" data-key="${key}">${esc(tr(label,english))}</button>`)
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
  preview.textContent = text || tr("— без подписи —","— no signature —");
  preview.className = text ? '' : 'none';
  $('#sigUrl').placeholder = channel?.username ? tr(`пусто — на t.me/${channel.username}`,`blank — link to t.me/${channel.username}`) : tr("пусто — на этот канал","blank — link to this channel");
}

function save(body, silent = false) {
  if (!channel) return Promise.resolve();
  const id = channel.id;
  savingCount++;
  $('#saveStatus').textContent = tr("Сохраняем изменения…","Saving changes…");
  $('#channelSelect').disabled = true;
  $('#addChannel').disabled = true;
  const run = async () => {
    try {
      const updated = await api(`/channels/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
      boot.channels = boot.channels.map(c => c.id === id ? updated : c);
      if (channel?.id === id) channel = updated;
      Object.entries(body).forEach(([key, value]) => { if (drafts.get(`${id}:${key}`) === value) drafts.delete(`${id}:${key}`); });
      if (body.business_profile) Object.entries(body.business_profile).forEach(([key, value]) => {
        if (drafts.get(`${id}:business:${key}`) === value) drafts.delete(`${id}:business:${key}`);
      });
      $('#saveStatus').textContent = tr("Изменения сохранены","Changes saved");
      if (!silent) toast(tr("Сохранено","Saved"));
      if (['business_mode', 'business_auto'].some(key => key in body)) fillSettings();
      if (['paused', 'autopost', 'business_mode'].some(key => key in body)) await refreshStats();
    } catch (error) {
      $('#saveStatus').textContent = tr(`Не сохранено: ${error.message}. Повторите изменение.`,`Not saved: ${error.message}. Please retry the change.`);
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
  if (!(await ask(tr("Удалить канал из панели? Посты и источники будут стёрты.","Remove the channel from the app? Its posts and sources will be deleted.")))) return;
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
    if (file.size > 4 * 1024 * 1024 || !/\.png$/i.test(file.name)) throw new Error(tr("Выберите PNG до 4 МБ","Choose a PNG up to 4 MB"));
    await saveQueue;
    const updated = await api(`/channels/${id}/logo`, {method: 'POST', headers: {'Content-Type': 'image/png'}, body: file});
    if (channel?.id === id) {
      channel.logo_configured = updated.logo_configured;
      channel.watermark = updated.watermark;
      fillSettings();
      toast(tr("PNG сохранён, водяной знак включён","PNG saved; watermark enabled"));
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    event.target.disabled = false;
    event.target.value = '';
  }
});

/* ---------- admin ---------- */

function renderApiLoad(load) {
  const target = $('#apiLoad');
  if (!load) { target.innerHTML = `<p class="hint">${tr('Показатели API пока недоступны.','API metrics are not available yet.')}</p>`; return; }
  const recent = load.windows['15'], hour = load.windows['60'];
  const peak = Math.max(1, ...load.series.map(item => item.requests));
  const n = value => Number(value) || 0;
  const countLabel = (requests,errors) => tr(`${n(requests)} запросов, ${n(errors)} ошибок`,`${n(requests)} requests, ${n(errors)} errors`);
  target.innerHTML = `<b>${tr(`Сейчас: ${n(load.active)} из ${n(load.concurrency)} запросов · ждут слот: ${n(load.waiting)}`,`Now: ${n(load.active)} of ${n(load.concurrency)} requests · waiting for a slot: ${n(load.waiting)}`)}</b>
    <progress class="api-capacity" max="${Math.max(1,n(load.concurrency))}" value="${n(load.active)}" aria-label="${tr('Занятые слоты Gemini','Gemini slots in use')}"></progress>
    <p class="hint">${tr('Запросы по минутам · последние 15 минут. Красная часть — ошибки HTTP, сети и прерывания.','Requests per minute for the last 15 minutes. Red shows HTTP errors, network errors and interruptions.')}</p>
    <div class="api-chart" role="img" aria-label="${tr('За последние 15 минут:','Over the last 15 minutes:')} ${countLabel(recent.requests,recent.errors)}">${load.series.map((item,i) => `<div class="api-bar" title="${14-i} ${tr('мин. назад:','min ago:')} ${countLabel(item.requests,item.errors)}" style="height:${Math.max(2,100*n(item.requests)/peak)}%"><span style="height:${item.requests ? 100*n(item.errors)/item.requests : 0}%"></span></div>`).join('')}</div>
    <div class="grid">${[[recent.requests,tr('HTTP-попыток / 15 мин','HTTP attempts / 15 min')],[recent.errors,tr('ошибок / 15 мин','errors / 15 min')],[recent.quota,tr('отказов квоты 429','quota errors 429')],[recent.server,tr('ошибок сервера 5xx','server errors 5xx')],[recent.transport,tr('сетевых ошибок','network errors')],[recent.interrupted,tr('прерванных запросов','interrupted requests')],[recent.fallbacks,tr('успешных резервов','successful fallbacks')],[recent.average_ms == null ? '—' : (recent.average_ms/1000).toFixed(1)+tr(' с',' s'),tr('средний HTTP-ответ','average HTTP response')]].map(([value,label]) => `<div class="stat"><b>${esc(String(value))}</b><i>${label}</i></div>`).join('')}</div>
    <p class="hint">${tr(`За 60 минут: ${n(hour.requests)} попыток, ${n(hour.errors)} ошибок, ${n(hour.fallbacks)} успешных резервов.`,`Over 60 minutes: ${n(hour.requests)} attempts, ${n(hour.errors)} errors, ${n(hour.fallbacks)} successful fallbacks.`)}</p>
    <p class="hint">${tr('Учёт с','Tracked since')} ${esc(new Date(load.started_at).toLocaleString(uiLocale()))}. ${tr('Обнуляется при перезапуске. Это HTTP-нагрузка, не число постов и не процент квоты Google. Ожидание слота не включает ожидание ключа или отложенные материалы; HTTP 200 не гарантирует прохождение фактчека.','Resets on restart. These are HTTP metrics, not post counts or Google quota percentages. Waiting excludes key cooldown and deferred content; HTTP 200 does not guarantee a passed fact-check.')}</p>`;
}

async function refreshAdminMonitoring() {
  if (!adminOpen || monitoringBusy || document.hidden) return;
  monitoringBusy = true;
  try {
    const data = await api('/admin/monitoring');
    if (!adminOpen) return;
    renderSystem(data.system);
    renderApiLoad(data.api_load);
    $('#adminEvents').innerHTML = (data.events || []).map(e => `<div class="card"><b>${e.emergency ? '🚨 ' : ''}${esc(e.title)}</b><p>${esc(e.explanation)}</p><p class="hint">${esc(e.channel?.title || tr("Сервис","Service"))} · ${esc(new Date(e.time).toLocaleString(uiLocale()))}</p><details><summary>${tr("Технические детали","Technical details")}</summary><p class="hint">${esc(e.detail)}</p></details></div>`).join('') || tr("<p class=\"hint\">Событий пока нет.</p>","<p class=\"hint\">No events yet.</p>");
    const c = data.capacity, q = data.queue;
    $('#adminMonitoring').innerHTML = [
      [c.users, tr("пользователей","users")], [c.channels, tr("каналов","channels")],
      [c.active_channels, tr("каналов без паузы","unpaused channels")],
      [Math.round(c.database_bytes / 1024 ** 2), tr("МБ в базе","MB in database")],
      [q.new, tr("ожидают обработки","waiting to process")], [q.pending, tr("на модерации","awaiting review")],
      [q.ready, tr("готовы / в дайджесте","ready / in digest")], [q.publishing, tr("публикуются","publishing")],
      [q.attention, tr("требуют проверки доставки","need delivery verification")],
    ].map(([v, label]) => `<div class="stat"><b>${Number(v) || 0}</b><i>${label}</i></div>`).join('');
    $('#channelAudit').innerHTML = (data.channels || []).map(c => `<div class="card"><b>${esc(c.title || c.id)}</b><p class="hint">${esc(c.status)}</p><p class="hint">${tr("Последняя публикация:","Last published:")} ${esc(ago(c.last_published) || tr("не было","never"))} · ${tr("Проверка источников:","Source check:")} ${esc(ago(c.checked_at) || tr("не было","never"))}</p></div>`).join('') || tr("<p class=\"hint\">Каналов пока нет.</p>","<p class=\"hint\">No channels yet.</p>");
    $('#adminUpdated').textContent = tr(`Обновлено: ${new Date().toLocaleTimeString(uiLocale())}`,`Updated: ${new Date().toLocaleTimeString(uiLocale())}`);
  } catch (error) {
    if (adminOpen) $('#adminUpdated').textContent = tr(`Не удалось обновить показатели: ${error.message}. Показаны последние полученные данные.`,`Could not refresh metrics: ${error.message}. Showing the last available data.`);
  } finally {
    monitoringBusy = false;
  }
}

async function loadAdmin() {
  await loadApiKeys();
  const data = await api('/admin/overview');
  const { totals } = data;
  $('#adminTotals').innerHTML = [
    ['', totals.posts, tr("постов в базе","posts in database")],
    ['ok', totals.published, tr("опубликовано в базе","published in database")],
    ['accent-t', totals.ai_today, tr("запросов ИИ","AI requests")],
  ].map(([cls, v, l]) => `<div class="stat ${cls}"><b>${v ?? 0}</b><i>${l}</i></div>`).join('');

  $('#adminUsers').innerHTML = data.users.map((u) => `
    <div class="adminrow client-access" data-client="${u.tg_id}">
      <span>${esc(u.first_name || u.tg_id)}${u.username ? ` @${esc(u.username)}` : ''}
        <div class="sub">id ${u.tg_id} · ${tr("каналов","channels")} ${u.channels}${u.promo_code ? ' · ' + esc(u.promo_code) : ''}</div></span>
      <div class="access-grid">
      <label>${tr("Тариф","Plan")}<select data-field="plan">${Object.entries(ACCESS_PLANS).map(([key,p]) => `<option value="${key}" ${key===(u.plan||'custom')?'selected':''}>${p.name}</option>`).join('')}</select></label>
      <label>${tr("Постов/день","Posts/day")}<input type="number" min="0" max="100000" value="${u.daily_limit}" data-field="daily_limit"></label>
      <label>${tr("Лимит каналов","Channel limit")}<input type="number" min="1" max="120" value="${u.max_channels}" data-field="max_channels"></label>
      <label>${tr("Продлить на дней","Extra access days")}<input type="number" min="0" max="3650" value="0" data-field="extend_days"></label>
      </div>
      <p class="hint">${tr(`Доступ до ${formatDate(u.access_until)}. Смена тарифа сама по себе не продлевает доступ и не удаляет каналы.`,`Access until ${formatDate(u.access_until)}. Changing the plan alone does not extend access or remove channels.`)}</p>
      <button class="accent wide" data-save-client>${tr("Сохранить изменения","Save changes")}</button>
    </div>`).join('') || tr("<p class=\"empty-note\">Пользователей пока нет.</p>","<p class=\"empty-note\">No users yet.</p>");

  $('#adminChannels').innerHTML = data.channels.map((c) => `
    <div class="adminrow">
      <span>${esc(c.title || c.username)}
        <div class="sub">${tr("владелец","owner")} ${c.owner_id} · ${tr("источников","sources")} ${c.sources}</div></span>
      <span class="tag${c.paused ? '' : ' live'}">${c.paused ? tr("пауза","paused") : c.autopost ? tr("авто","auto") : tr("модерация","review")}</span>
    </div>`).join('') || tr("<p class=\"empty-note\">Каналов пока нет.</p>","<p class=\"empty-note\">No channels yet.</p>");

  $('#adminUsers').querySelectorAll('[data-client]').forEach((row) => {
    row.querySelector('[data-field="plan"]').addEventListener('change', event => {
      const p = ACCESS_PLANS[event.target.value];
      if (event.target.value !== 'custom') {
        row.querySelector('[data-field="daily_limit"]').value=p.daily;
        row.querySelector('[data-field="max_channels"]').value=p.channels;
      }
    });
    row.querySelector('[data-save-client]').addEventListener('click', async (event) => {
      const button=event.currentTarget;
      const inputs=[...row.querySelectorAll('[data-field]')];
      if (!inputs.every(input=>input.reportValidity())) return;
      const payload=Object.fromEntries(inputs.map(input=>[input.dataset.field,input.dataset.field==='plan'?input.value:Number(input.value)]));
      if (!(await ask(tr("Сохранить тариф и лимиты клиента? Продление прибавится к текущему сроку.","Save this customer’s plan and limits? Extra days will be added to the current expiry.")))) return;
      button.disabled=true;
      try {
        await api(`/admin/users/${row.dataset.client}`, {
          method: 'POST', body: JSON.stringify(payload),
        });
        row.querySelector('[data-field="extend_days"]').value=0;
        toast(tr("Доступ клиента обновлён","Customer access updated"));
        await loadAdmin();
      } catch (error) {
        toast(error.message, true);
      } finally {
        button.disabled=false;
      }
    });
  });

  await loadPromoCodes();
}

async function loadApiKeys() {
  const data = await api('/admin/api-keys');
  $('#apiKeyStatus').textContent = `${tr("Сохранено","Saved")} ${data.keys.length}/${data.limit}. ${tr("Ключ из настроек сервера:","Server-configured key:")} ${data.server_key_configured ? tr("есть, используется как резерв","available, used as fallback") : tr("не задан","not set")}.`;
  $('#apiKeyList').innerHTML = data.keys.map(k => {
    const cooling = k.cooldown_until && new Date(k.cooldown_until) > new Date();
    const state = !k.enabled ? tr("Отключён","Disabled") : cooling ? tr(`Пауза до ${new Date(k.cooldown_until).toLocaleTimeString(uiLocale())}`,`Paused until ${new Date(k.cooldown_until).toLocaleTimeString(uiLocale())}`) : k.last_success_at ? tr("Доступен для запросов","Available for requests") : tr("Ожидает первого успешного запроса","Awaiting first successful request");
    return `<div class="card key-card"><b>${esc(k.label)}</b><p class="hint">${esc(state)}${k.last_error ? ' · ' + esc(k.last_error) : ''}</p><div class="form-actions"><button class="ghost" data-key-toggle="${k.id}" data-enabled="${k.enabled}">${k.enabled ? tr("Отключить","Disable") : tr("Включить","Enable")}</button><button class="danger" data-key-delete="${k.id}">${tr("Удалить","Delete")}</button></div></div>`;
  }).join('');
  $('#apiKeyList').querySelectorAll('button').forEach(button => button.addEventListener('click', async () => {
    const remove = button.dataset.keyDelete;
    if (remove && !window.confirm(tr("Удалить этот ключ из пула? Для возврата потребуется вставить его заново.","Remove this key from the pool? You will need to enter it again to restore it."))) return;
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
    toast(tr(`Добавлено ключей: ${result.added}`,`Keys added: ${result.added}`));
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$('#apiKeyRefresh').addEventListener('click', () => loadApiKeys().catch(error => toast(error.message, true)));

async function loadPromoCodes() {
  const codes = await api('/admin/promo');
  $('#promoList').innerHTML = codes.map((c) => `
    <div class="adminrow">
      <span class="codechip${c.used_by ? ' used' : ''}">${esc(c.code)}
        <div class="sub">${esc(c.plan)} · ${c.daily_limit}/${tr("день","day")} · ${c.max_channels} ${tr("кан.","channels")} · ${c.days} ${tr("дн.","days")}${
          c.used_by ? ` · ${tr("активировал","activated by")} ${esc(c.first_name || c.used_by)}` : ''}${c.assigned_to ? tr(" · для ID "," · for ID ")+esc(c.assigned_to) : ''}${c.expires_at ? tr(" · активация до "," · activate by ")+formatDate(c.expires_at) : ''}${c.note ? ' · ' + esc(c.note) : ''}</div></span>
      ${c.used_by ? tr("<span class=\"tag\">занят</span>","<span class=\"tag\">used</span>")
        : `<span class="pair"><button class="ghost square" data-copy="${esc(c.code)}">${icon('copy')}</button>
           <button class="ghost square" data-drop="${esc(c.code)}">${icon('trash')}</button></span>`}
    </div>`).join('') || tr("<p class=\"empty-note\">Кодов пока нет — выпустите первый.</p>","<p class=\"empty-note\">No codes yet — create your first one.</p>");
}

$('#promoList').addEventListener('click', async (event) => {
  const copy = event.target.closest('[data-copy]');
  if (copy) {
    try {
      await navigator.clipboard.writeText(copy.dataset.copy);
      toast(tr(`Код ${copy.dataset.copy} скопирован`,`Code ${copy.dataset.copy} copied`));
    } catch {
      toast(copy.dataset.copy);
    }
    return;
  }
  const drop = event.target.closest('[data-drop]');
  if (!drop) return;
  if (!(await ask(tr(`Удалить код ${drop.dataset.drop}?`,`Delete code ${drop.dataset.drop}?`)))) return;
  try {
    await api(`/admin/promo/${drop.dataset.drop}`, { method: 'DELETE' });
    await loadPromoCodes();
  } catch (error) {
    toast(error.message, true);
  }
});

$('#promoCreate').addEventListener('click', async () => {
  if (!['promoCount','promoDaily','promoChannels','promoDays','promoExpiry'].every(id=>$('#'+id).reportValidity())) return;
  $('#promoCreate').disabled = true;
  try {
    const result = await api('/admin/promo', {
      method: 'POST',
      body: JSON.stringify({
        count: Number($('#promoCount').value) || 1,
        plan: $('#promoPlan').value,
        note: $('#promoNote').value,
        overrides: {daily_limit:Number($('#promoDaily').value),max_channels:Number($('#promoChannels').value),days:Number($('#promoDays').value)},
        activation_days: $('#promoExpiry').value ? Number($('#promoExpiry').value) : null,
        assigned_to: $('#promoRecipient').value.trim() ? Number($('#promoRecipient').value) : null,
      }),
    });
    $('#promoNote').value = '';
    toast(tr(`Выпущено кодов: ${result.codes.length}`,`Codes created: ${result.codes.length}`));
    await loadPromoCodes();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('#promoCreate').disabled = false;
  }
});
$('#promoPlan').addEventListener('change', event => {
  const p=ACCESS_PLANS[event.target.value];
  if (event.target.value==='custom') return;
  $('#promoDaily').value=p.daily;
  $('#promoChannels').value=p.channels;
  $('#promoDays').value=p.days;
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
  if (!channel) return exploring ? enterExploreMode(page) : showOnboarding();
  $('#nav').hidden = false;
  $('#channelBar').hidden = false;
  openPage(page);
});

/* ---------- promo gate ---------- */

$('#promoSubmit').addEventListener('click', async () => {
  const code = $('#promoInput').value.trim();
  if (!code) return toast(tr("Введите код","Enter a code"), true);
  $('#promoSubmit').disabled = true;
  try {
    await api('/promo/redeem', { method: 'POST', body: JSON.stringify({ code }) });
    toast(tr("Код принят","Code accepted"));
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
  if (!ref) return toast(tr("Введите @username канала","Enter the channel @username"), true);
  button.disabled = true;
  try {
    const created = await api('/channels', { method: 'POST', body: JSON.stringify({ ref }) });
    boot.channels.push(created);
    channel = created;
    input.value = '';
    $('#addChannelRow').hidden = true;
    enterNormalMode();
    toast(tr("Канал добавлен","Channel added"));
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

$('#addChannel').addEventListener('click', () => {
  if (!channel) return showOnboarding();
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
  if (channel.business_mode || publishingNow) return;
  publishingNow = true;
  $('#publishNow').disabled = true;
  $('#publishHint').textContent = tr("Проверяю источники и готовлю один свежий пост…","Checking sources and preparing one fresh post…");
  try {
    await api(`/channels/${channel.id}/publish_now`, { method: 'POST' });
    toast(tr("Свежий пост опубликован","Fresh post published"));
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

const PAGE_LOADERS = { home: refreshStats, feed: () => feedView === 'pending' ? loadFeed() : loadHistory(), ads: loadAds, sources: loadSources, billing: loadBilling };

function openPage(name) {
  if (!['home', 'feed', 'ads', 'sources', 'settings', 'billing'].includes(name)) return;
  if (!boot?.user.has_access && name !== 'billing') return showGate();
  if (!channel && name !== 'billing' && !exploring) return;
  if (name === 'billing' && page !== 'billing') billingReturnPage = page;
  page = name;
  $$('.nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === (name === 'billing' ? 'settings' : name === 'ads' ? 'feed' : name)));
  $$('.page').forEach((s) => (s.hidden = s.id !== `page-${name}`));
  if (exploring && !channel) renderExploreWorkspace();
  const load = PAGE_LOADERS[name];
  if (load && (channel || name === 'billing')) Promise.resolve(load()).catch((e) => toast(e.message, true));
}

$$('.nav button').forEach((b) => b.addEventListener('click', () => openPage(b.dataset.page)));

function showOnly(id) {
  $$('.page').forEach((s) => (s.hidden = s.id !== id));
  $('#nav').hidden = true;
  $('#channelBar').hidden = true;
  $('#addChannelRow').hidden = true;
}

function showGate() {
  exploring = false;
  document.body.classList.remove('exploring');
  channel = null;
  $('#status').textContent = tr("доступ не активирован","access not active");
  $('#gateHint').textContent = boot.user.access_until
    ? tr(`Прошлый доступ закончился ${formatDate(boot.user.access_until)}.`,`Your previous access ended on ${formatDate(boot.user.access_until)}.`) : '';
  showOnly('gate');
}

function showOnboarding() {
  if (!boot?.user.has_access) return showGate();
  channel = null;
  $('#status').textContent = tr("канал ещё не подключён","no channel connected yet");
  $('#botName').textContent = boot.bot_username ? `@${boot.bot_username}` : tr("бота","the bot");
  showOnly('onboarding');
}

function leaveExploreMode() {
  if (exploreDisabled.size) $('#saveStatus').innerHTML = `<span data-i18n="Переключатели сохраняются сразу." data-i18n-en="Changes to switches are saved immediately.">${tr('Переключатели сохраняются сразу.','Changes to switches are saved immediately.')}</span>`;
  exploring = false;
  document.body.classList.remove('exploring');
  for (const [el,disabled] of exploreDisabled) el.disabled = disabled;
  exploreDisabled.clear();
  $('#exploreHome').hidden = true;
  $('#homeConnected').hidden = false;
  $$('[data-explore-notice]').forEach(el => el.hidden = true);
  $('#channelSelect').disabled = false;
}

function enterNormalMode() {
  leaveExploreMode();
  $('#gate').hidden = true;
  $('#onboarding').hidden = true;
  $('#nav').hidden = false;
  $('#channelBar').hidden = false;
  renderChannelList();
  fillSettings();
  openPage('home');
  loadAds().catch(() => {});
}

/* An account can explore the real workspace without creating a channel. */
function exploreWasChosen() {
  try { return localStorage.getItem(`neyro:explore:${boot.user.tg_id}`) === '1'; } catch { return false; }
}
function enterExploreMode(name = 'home') {
  if (!boot?.user.has_access) return showGate();
  if (channel) return openPage(name);
  exploring = true;
  try { localStorage.setItem(`neyro:explore:${boot.user.tg_id}`, '1'); } catch {}
  document.body.classList.add('exploring');
  $('#nav').hidden = false;
  $('#channelBar').hidden = false;
  $('#addChannelRow').hidden = true;
  openPage(name);
}
function renderExploreWorkspace() {
  if (channel || !boot?.user.has_access) return;
  $('#homeConnected').hidden = true;
  $('#exploreHome').hidden = false;
  $('#status').textContent = tr('Ваша редакция','Your workspace');
  $('#channelSelect').innerHTML = `<option>${tr('Канал пока не подключён','No channel connected yet')}</option>`;
  $('#channelSelect').disabled = true;
  $('#exploreHome').innerHTML = `
    <div class="explore-intro"><div><span class="eyebrow">${tr('СНАЧАЛА ОСМОТРИТЕСЬ','MAKE YOURSELF AT HOME')}</span><h2>${tr('Ваша новая<br>редакция.','Your new<br>workspace.')}</h2><p>${tr('Здесь материалы превращаются в публикации. Изучите инструменты в своём темпе — канал подключите, когда будете готовы.','This is where source material becomes a published post. Explore the tools at your own pace, then connect a channel when you are ready.')}</p><div class="explore-actions"><button class="accent" data-connect-channel>${tr('Подключить канал','Connect a channel')} ${icon('plus')}</button><button class="text-link" data-explore-tutorial>${tr('Как всё работает','How it works')} ${icon('back')}</button></div></div><div class="editorial-index" aria-hidden="true"><span>N / P</span><div></div><small>${tr('МАТЕРИАЛ → ПОСТ','SOURCE → POST')}</small></div></div>
    <div class="explore-section-head"><h3>${tr('От источника до публикации','From source to publication')}</h3><span>01 — 03</span></div>
    <div class="workflow-list">${[
      ['sources','01',tr('Соберите свои источники','Bring your sources'),tr('Telegram-каналы и RSS. Вы решаете, откуда брать материалы.','Telegram channels and RSS. You decide where content comes from.'),'sources'],
      ['settings','02',tr('Задайте голос канала','Set your channel’s voice'),tr('Темы, стиль, язык и расписание — под вашу редакционную задачу.','Topics, tone, language and schedule — shaped around your editorial needs.'),'settings'],
      ['feed','03',tr('Проверьте и опубликуйте','Review and publish'),tr('Читайте черновики, вносите правки и выбирайте, что увидят подписчики.','Read drafts, make changes and choose what your subscribers see.'),'inbox']
    ].map(([go,num,title,copy,ico])=>`<button class="workflow-row" data-go="${go}"><span class="workflow-number">${num}</span><span class="workflow-copy"><b>${title}</b><small>${copy}</small></span>${icon(ico)}${icon('back')}</button>`).join('')}</div>
    <button class="explore-access" data-go="billing"><span>${icon('key')} ${tr('Ваш доступ уже активен','Your access is active')}</span><span>${tr('Тариф и лимиты','Plan and limits')} ${icon('back')}</span></button>`;
  const descriptions = {
    feed:tr('Здесь появятся черновики и история публикаций. Для начала подключите канал и добавьте источники.','Drafts and publishing history will appear here. Connect a channel and add sources to get started.'),
    sources:tr('Источники подключаются к конкретному каналу. Пока можно посмотреть, как они добавляются.','Sources belong to a specific channel. You can explore the available ways to add them.'),
    settings:tr('Изучите настройки канала. Их можно будет изменить после подключения. Тему и язык интерфейса меняйте уже сейчас в шапке.','Explore the channel settings. Connect a channel to edit them. You can already change the interface theme and language in the header.'),
    ads:tr('Здесь будут материалы, которые фильтр посчитал рекламой. Решение можно проверить вручную.','Content flagged as advertising will appear here for your review.')
  };
  $$('[data-explore-notice]').forEach(el=>{el.hidden=false;el.innerHTML=`<p>${descriptions[el.dataset.exploreNotice]}</p><button class="text-link" data-connect-channel>${tr('Подключить канал','Connect a channel')} ${icon('plus')}</button>`;});
  const empty = (title,copy) => `<div class="workspace-empty">${icon('inbox')}<h3>${title}</h3><p>${copy}</p></div>`;
  $('#feed').innerHTML=empty(tr('Место для хороших материалов','A place for good stories'),tr('После подключения здесь будут посты, подготовленные для вашей проверки.','Once connected, posts prepared for your review will appear here.'));
  $('#history').innerHTML=empty(tr('История начнётся с первого поста','Your story starts with the first post'),tr('Статус обработки и результат публикации сохранятся здесь.','Processing status and publishing results will be recorded here.'));
  $('#ads').innerHTML=empty(tr('Пока нет материалов','No content yet'),tr('Сначала подключите канал и источники.','Connect a channel and sources first.'));
  $('#sources').innerHTML='';
  $('#pendingCount').textContent='0';
  $('#sourcesCount').textContent='0';
  $('#adsCount').textContent='0';
  setBadge('#feedBadge',0);setBadge('#adsBadge',0);
  $('#saveStatus').textContent=tr('Обзор настроек · подключите канал, чтобы сохранять изменения.','Settings preview · connect a channel to save changes.');
  $('#qualityTitle').textContent=tr('Качество обработки','Processing quality');
  $('#qualityHint').textContent=tr('От быстрого рерайта до глубокой проверки фактов. Выберите режим после подключения канала.','From a quick rewrite to an additional fact check. Choose a mode after connecting your channel.');
  $('#delayChips').innerHTML=DELAY_LABELS.map(([,ru,en])=>`<span class="preview-chip">${tr(ru,en)}</span>`).join('');
  $('#paceChips').innerHTML=PACE_LABELS.map(([,ru,en])=>`<span class="preview-chip">${tr(ru,en)}</span>`).join('');
  $$('#page-settings input,#page-settings select,#page-settings textarea,#page-settings button:not([data-go]):not([data-tutorial]):not([data-connect-channel]),#page-sources input,#page-sources select,#page-sources textarea,#page-sources button:not([data-go]):not([data-connect-channel]):not(#sourceBatchOpen):not(#sourceCopyOpen)').forEach(el=>{
    if(!exploreDisabled.has(el)) exploreDisabled.set(el,el.disabled);
    el.disabled=true;
  });
}
$('#skipChannel').addEventListener('click',()=>enterExploreMode());
$('#app').addEventListener('click',event=>{
  if(event.target.closest('[data-connect-channel]')) showOnboarding();
  if(event.target.closest('[data-explore-tutorial]')) setupTour();
});

/* ---------- guided tutorial ---------- */

const TOUR_KEY = 'neyro:tour_seen';
const TUTORIAL_PROGRESS_KEY = 'neyro:tutorial_v2';
const TUTORIAL_COPY = {
  ru: {
    brand: 'Учебник Нейропостинга', close: 'Закрыть', next: 'Дальше', done: 'Готово',
    previous: 'Предыдущая тема', section: 'Тема', topics: 'Перейти к теме', restart: 'Начать сначала',
    saved: 'Место сохранится — продолжите обучение с этой темы.',
    complete: 'Обучение пройдено. К любой теме можно вернуться через «Как работает Нейропостинг».',
    connectFirst: 'Сначала подключите канал. Затем вернитесь к этому шагу через кнопку обучения.',
    activateFirst: 'Сначала проверьте доступ в разделе «Мой тариф». Обучение доступно в любой момент.',
    botUnavailable: 'Адрес бота пока не загружен. Откройте чат, из которого запустили приложение.',
    steps: [
      {id:'overview', icon:'home', short:'Начало', title:'От источника до вашего канала',
        intro:'Сначала освоим публикацию с проверкой. Автопостинг можно включить позже.',
        items:['Подключите свой канал и выберите источники материалов.', 'ИИ подготовит текст по вашим инструкциям. Проверьте факты, ссылки и право использовать медиа.', 'Одобрите готовый пост или настройте публикацию по расписанию.'],
        note:'Кнопки в этом учебнике только открывают нужный раздел. Они не публикуют посты и не меняют настройки.', action:'channel', label:'К подключению канала'},
      {id:'channel', icon:'sources', short:'Канал', title:'Подключите Telegram-канал',
        intro:'Нужен доступ к управлению вашим каналом в Telegram.',
        items:['Добавьте {bot} в администраторы канала и разрешите публиковать сообщения.', 'В приложении введите @username канала и нажмите «Добавить канал».', 'Если каналов несколько, выбирайте нужный в списке сверху. Источники и настройки относятся к выбранному каналу.'],
        note:'Если канал не подключается, проверьте @username, права бота и свой доступ к управлению каналом.', action:'channel', label:'Открыть подключение'},
      {id:'sources', icon:'link', short:'Источники', title:'Выберите, откуда брать материалы',
        intro:'Откройте «Источники»: подойдут публичный @канал, ссылка t.me или RSS-лента.',
        items:['Добавьте один адрес или нажмите «Добавить списком»: до 20 адресов за раз.', 'Можно переслать пост из публичного канала боту и выбрать добавление источника.', 'После подключения дождитесь новых подходящих материалов. Реклама, повторы, старые новости и неподходящие темы могут отсеиваться.'],
        note:'Пустая очередь не означает поломку. Причины ожидания видны на главной, а ошибочно отсеянную рекламу можно проверить в разделе «Посты».', action:'sources', label:'Открыть источники'},
      {id:'writing', icon:'settings', short:'Стиль', title:'Объясните ИИ, какой текст нужен',
        intro:'В «Настройках» откройте «Тексты и отбор материалов».',
        items:['Выберите качество: «Быстро» — простой рерайт; «Баланс» — отбор и рерайт; «Суперпостинг» — дополнительная проверка.', 'Опишите тему, тон, длину и запреты в инструкциях. Нажмите «Сохранить инструкции». Для стоп-слов есть отдельное сохранение.', 'Выберите язык постов. При необходимости включите «Голос канала», фильтры медиа и популярности; подпись и логотип находятся в «Оформлении».'],
        note:'Свой ключ ИИ необязателен и не отменяет лимит тарифа. Даже после проверки ИИ важные факты нужно сверять с оригиналом.', action:'settings', label:'Открыть настройки'},
      {id:'review', icon:'inbox', short:'Проверка', title:'Проверьте первый черновик',
        intro:'Оставьте «Автопостинг» выключенным. Подготовленные материалы попадут в «Посты» → «На проверке».',
        items:['Прочитайте текст, замечания проверки и оригинал. «Переписать» подготовит другой вариант; крестик отклонит материал.', '«Опубликовать» отправляет выбранный пост в канал. Свой текст, ссылку или фото с подписью можно отправить боту — черновик появится здесь.', 'Результат смотрите во вкладке «История». Отфильтрованная реклама проверяется отдельно: «Это не реклама» не публикует исходный пост.'],
        note:'На главной «Опубликовать сейчас» сразу подготавливает и отправляет один свежий пост вне расписания. Для предварительной проверки используйте черновики.', action:'feed', label:'Открыть посты'},
      {id:'schedule', icon:'settings', short:'Расписание', title:'Переходите к автоматической работе',
        intro:'Когда качество черновиков вас устраивает, откройте «Расписание и публикация».',
        items:['Проверьте часовой пояс и окно публикации. Например, окно 9–21 работает по времени выбранного канала.', 'Выберите задержку и темп, затем включите «Автопостинг». Темп распределяет подходящие посты по окну, но не гарантирует их количество.', '«Пауза» останавливает проверку источников и автоматическую публикацию. «Вечерний дайджест» собирает короткие новости в сводку; для автоотправки нужен включённый автопостинг.'],
        note:'Материалы, требующие проверки, могут оставаться в черновиках даже при автопостинге. Ручная кнопка «Опубликовать сейчас» работает отдельно от расписания.', action:'settings', label:'Открыть расписание'},
      {id:'limits', icon:'bolt', short:'Доступ', title:'Один дневной лимит на аккаунт',
        intro:'Раздел «Мой тариф» показывает подключённый доступ, остаток и дату окончания.',
        items:['Лимит публикаций общий для всех ваших каналов. Например, при лимите 5 и трёх публикациях в одном канале на остальные остаётся 2.', 'Новый день для лимита начинается в 00:00 UTC — это 03:00 по Москве. Часовой пояс расписания этот момент не меняет.', 'Продление ручное, без автосписаний. Повышение тарифа действует после доплаты за остаток периода; понижение — со следующего периода.'],
        note:'Mini App показывает уже приобретённый доступ. Если он изменился, нажмите «Обновить тариф». Отправляемый пост или отправка с неясным результатом могут временно занимать место в лимите.', action:'billing', label:'Посмотреть мой доступ'},
      {id:'recovery', icon:'shield', short:'Помощь', title:'Если пост не появился',
        intro:'Проверяйте причину по порядку — повторная отправка не всегда нужна.',
        items:['На главной проверьте доступ, дневной остаток, паузу, источники и окно публикации. В «Постах» может ждать черновик.', 'Откройте «Историю»: там видны отсев, ошибка или ожидание. При статусе «Нужна сверка с каналом» или частичной отправке сначала проверьте сам Telegram-канал.', 'Подтверждайте результат сверки только после проверки текста и медиа. Если причина неясна, обратитесь в поддержку из настроек и укажите канал, время и текст ошибки.'],
        note:'Не отправляйте один материал повторно вслепую: Telegram мог принять пост, даже если ответ задержался. Пароли, ключи и данные карты для обращения в поддержку не нужны.', action:'history', label:'Открыть историю'},
    ],
  },
  en: {
    brand:'NeuroPost guide', close:'Close', next:'Next', done:'Done', previous:'Previous topic',
    section:'Topic', topics:'Jump to a topic', restart:'Start again',
    saved:'Your place is saved. Reopen the guide to continue here.',
    complete:'Guide complete. Revisit any topic from “How NeuroPost works”.',
    connectFirst:'Connect a channel first, then return to this step using the guide button.',
    activateFirst:'Check your access in “My plan” first. You can open this guide at any time.',
    botUnavailable:'The bot address is not available yet. Open the chat where you launched this app.',
    steps:[
      {id:'overview',icon:'home',short:'Start',title:'From a source to your channel',
        intro:'Start by reviewing each post. You can enable automatic publishing later.',
        items:['Connect your channel and choose content sources.', 'AI prepares text using your instructions. Check facts, links and permission to use media.', 'Approve a finished draft or set up scheduled publishing.'],
        note:'Buttons in this guide only open app sections. They never publish posts or change settings.',action:'channel',label:'Connect a channel'},
      {id:'channel',icon:'sources',short:'Channel',title:'Connect your Telegram channel',
        intro:'You need permission to manage your channel in Telegram.',
        items:['Add {bot} as a channel administrator and allow it to post messages.', 'Enter the channel @username in the app and select “Add channel”.', 'If you have several channels, choose one at the top. Sources and settings apply to the selected channel.'],
        note:'If connection fails, check the @username, the bot’s permissions and your own channel access.',action:'channel',label:'Open channel setup'},
      {id:'sources',icon:'link',short:'Sources',title:'Choose where content comes from',
        intro:'Open “Sources”: use a public @channel, a t.me link or an RSS feed.',
        items:['Add one address or choose “Add a list” for up to 20 addresses at once.', 'You can also forward a public channel post to the bot and choose to add its source.', 'Wait for new suitable content. Ads, duplicates, old news and off-topic stories may be filtered out.'],
        note:'An empty queue is not necessarily an error. The home screen explains waiting; review filtered ads under “Posts”.',action:'sources',label:'Open sources'},
      {id:'writing',icon:'settings',short:'Style',title:'Tell AI what to write',
        intro:'In “Settings”, open “Writing and content filters”.',
        items:['Choose quality: “Fast” rewrites text; “Balanced” filters and rewrites; “Superposting” adds another check.', 'Describe topics, tone, length and restrictions, then save the instructions. Stop words have a separate save button.', 'Choose the language of your posts. Channel voice, media and popularity filters are optional. Find signatures and logos under “Appearance”.'],
        note:'Your own AI key is optional and does not remove plan limits. Verify important facts against the original even after AI checks.',action:'settings',label:'Open settings'},
      {id:'review',icon:'inbox',short:'Review',title:'Review your first draft',
        intro:'Keep “Autoposting” off. Prepared content appears in “Posts” → “To review”.',
        items:['Read the text, check notes and original source. “Rewrite” prepares another version; the cross rejects a draft.', '“Publish” sends that post to the channel. Send your own text, a link or a photo with a caption to the bot to create a draft here.', 'Check the outcome in “History”. Filtered ads are separate: marking “Not an ad” does not publish the original.'],
        note:'“Publish now” on the home screen prepares and sends one fresh post immediately outside the schedule. Use drafts when you want to review first.',action:'feed',label:'Open posts'},
      {id:'schedule',icon:'settings',short:'Schedule',title:'Set up automatic publishing',
        intro:'When you are happy with your drafts, open “Schedule and publishing”.',
        items:['Check the time zone and publishing window. A 9–21 window uses your selected channel’s local time.', 'Choose a delay and pace, then enable autoposting. Pace spreads suitable posts across the window; it does not guarantee a number of posts.', '“Pause” stops source checks and automatic publishing. The evening digest groups short news into one summary and needs autoposting enabled to send automatically.'],
        note:'Posts that need review may stay as drafts even with autoposting on. The manual “Publish now” button works separately from the schedule.',action:'settings',label:'Open schedule'},
      {id:'limits',icon:'bolt',short:'Access',title:'One daily limit for your account',
        intro:'“My plan” shows your existing access, remaining allowance and expiry date.',
        items:['All your channels share the daily publishing limit. With a limit of 5, publishing 3 posts in one channel leaves 2 for the others.', 'The limit resets at 00:00 UTC, or 03:00 in Moscow. The scheduling time zone does not change this reset.', 'Renewals are manual, with no automatic charges. An upgrade starts after the prorated payment; a downgrade starts next period.'],
        note:'The Mini App shows access already acquired. Select “Refresh plan” after a change. Posts being sent or awaiting delivery verification may reserve a daily slot.',action:'billing',label:'View my access'},
      {id:'recovery',icon:'shield',short:'Help',title:'If a post has not appeared',
        intro:'Check the cause in order. Sending again is not always the answer.',
        items:['On the home screen, check access, remaining allowance, pause, sources and the publishing window. A draft may be waiting in “Posts”.', 'Open “History” for filters, errors or waiting. If delivery is uncertain or partial, check the actual Telegram channel first.', 'Confirm delivery only after checking both text and media. If unclear, contact support from Settings with the channel, time and error message.'],
        note:'Do not resend blindly: Telegram may have accepted the post even if its response was delayed. Support does not need your passwords, API keys or card details.',action:'history',label:'Open history'},
    ],
  },
};

function tutorialCopy() {
  return TUTORIAL_COPY[window.NeyroPrefs?.language || document.documentElement?.lang] || TUTORIAL_COPY.ru;
}
function tourSeen() {
  try { return localStorage.getItem(TOUR_KEY) === '1'; } catch { return false; }
}
function tutorialProgress() {
  try {
    const value = JSON.parse(localStorage.getItem(TUTORIAL_PROGRESS_KEY) || '{}');
    return {index:Number.isInteger(value.index) ? Math.max(0,Math.min(value.index,TUTORIAL_COPY.ru.steps.length-1)) : 0, complete:value.complete === true};
  } catch { return {index:0,complete:false}; }
}
let tourReturnFocus = null;
let tutorialIndex = 0;
let tutorialComplete = false;
let tutorialKeyHandler = null;

function saveTutorialProgress() {
  try { localStorage.setItem(TUTORIAL_PROGRESS_KEY, JSON.stringify({index:tutorialIndex,complete:tutorialComplete})); } catch { /* Private mode can block storage. */ }
}
function closeTour(completed = false) {
  if (completed === true) tutorialComplete = true;
  saveTutorialProgress();
  try { localStorage.setItem(TOUR_KEY, '1'); } catch { /* Storage may be unavailable. */ }
  $('#tour').hidden = true;
  $('#app').inert = false;
  document.body.classList.remove('tour-open');
  if (tutorialKeyHandler) document.removeEventListener('keydown', tutorialKeyHandler);
  tutorialKeyHandler = null;
  if (tourReturnFocus?.isConnected && tourReturnFocus !== document.body) tourReturnFocus.focus();
}
function renderTutorial() {
  const copy = tutorialCopy();
  const step = copy.steps[tutorialIndex];
  const bot = /^[A-Za-z0-9_]{5,32}$/.test(boot?.bot_username || '') ? '@'+boot.bot_username : (copy === TUTORIAL_COPY.en ? 'the NeuroPost bot' : 'бота Нейропостинга');
  $('#tourBrand').textContent = copy.brand;
  $('#tourSkip').innerHTML = `${esc(copy.close)} ${icon('close')}`;
  $('#tourTopicsLabel').textContent = copy.topics;
  $('#tourTopics').innerHTML = copy.steps.map((item,index)=>`<option value="${index}"${index===tutorialIndex?' selected':''}>${index+1}. ${esc(item.short)}</option>`).join('');
  $('#tourTrack').innerHTML = `<section class="slide tutorial-section" data-topic="${step.id}"><div class="tour-visual" aria-hidden="true"><span class="tour-number">${String(tutorialIndex+1).padStart(2,'0')}</span>${icon(step.icon)}<span>${esc(step.short)}</span></div><div class="tour-copy"><span class="eyebrow">${esc(copy.section)} ${tutorialIndex+1} / ${copy.steps.length}</span><h2 id="tourTitle">${esc(step.title)}</h2><p>${esc(step.intro)}</p><ol class="steps tutorial-step-list">${step.items.map(item=>`<li>${esc(item.replaceAll('{bot}',bot))}</li>`).join('')}</ol><p class="hint tutorial-note">${esc(step.note)}</p><button class="ghost tutorial-action" data-tutorial-action="${step.action}">${esc(step.label)} ${icon('back')}</button></div></section>`;
  $('#tour').setAttribute('aria-labelledby','tourTitle');
  $('#tourCounter').textContent = `${String(tutorialIndex+1).padStart(2,'0')} / ${String(copy.steps.length).padStart(2,'0')}`;
  $('#tourPrev').disabled = tutorialIndex === 0;
  $('#tourPrev').setAttribute('aria-label',copy.previous);
  $('#tourNext').innerHTML = tutorialIndex === copy.steps.length-1 ? `${esc(copy.done)} ${icon('check')}` : `${esc(copy.next)} ${icon('back')}`;
  $('#tourRestart').textContent = copy.restart;
  $('#tourSaved').textContent = tutorialComplete ? copy.complete : copy.saved;
  saveTutorialProgress();
}
function showTutorialTopic(index) {
  tutorialIndex = Math.max(0,Math.min(Number(index)||0,tutorialCopy().steps.length-1));
  renderTutorial();
  $('#tourTrack').scrollTop = 0;
}
function openTutorialSection(action) {
  if (!['channel','sources','settings','feed','history','billing'].includes(action)) return;
  const copy = tutorialCopy();
  closeTour();
  if (action === 'billing') { openPage('billing'); return; }
  if (!boot?.user.has_access) { showGate(); toast(copy.activateFirst); return; }
  if (!channel) {
    if (action === 'channel') { showOnboarding(); $('#firstChannelInput').focus(); return; }
    enterExploreMode(action === 'history' ? 'feed' : action);
    if (action === 'history') switchFeed('history');
    return;
  }
  if (action === 'channel') { $('#channelSelect').focus(); return; }
  if (action === 'history') { openPage('feed'); switchFeed('history'); return; }
  openPage(action);
  if (action === 'settings') {
    const schedule = $('[data-field="autopost"]')?.closest('details');
    if (schedule && tutorialCopy().steps[tutorialIndex].id === 'schedule') { schedule.open = true; schedule.scrollIntoView({block:'nearest'}); }
  }
}
function setupTour() {
  const tour = $('#tour');
  if (!tour.hidden) return;
  tourReturnFocus = document.activeElement;
  const progress = tutorialProgress();
  tutorialIndex = progress.complete ? 0 : progress.index;
  tutorialComplete = progress.complete;
  tour.hidden = false;
  $('#app').inert = true;
  document.body.classList.add('tour-open');
  renderTutorial();
  $('#tourNext').onclick = () => tutorialIndex === tutorialCopy().steps.length-1 ? closeTour(true) : showTutorialTopic(tutorialIndex+1);
  $('#tourPrev').onclick = () => showTutorialTopic(tutorialIndex-1);
  $('#tourSkip').onclick = () => closeTour();
  $('#tourTopics').onchange = event => showTutorialTopic(event.target.value);
  $('#tourRestart').onclick = () => { tutorialComplete = false; showTutorialTopic(0); };
  $('#tourTrack').onclick = event => { const button=event.target.closest('[data-tutorial-action]'); if(button) openTutorialSection(button.dataset.tutorialAction); };
  tutorialKeyHandler = event => {
    if (tour.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeTour(); return; }
    if (event.target?.matches('select,input,textarea')) return;
    if (event.key === 'ArrowRight') { event.preventDefault(); showTutorialTopic(tutorialIndex+1); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); showTutorialTopic(tutorialIndex-1); }
    if (event.key === 'Tab') {
      const controls = [...tour.querySelectorAll('button:not(:disabled),select:not(:disabled),a[href]')].filter(el=>!el.closest('[hidden]'));
      const first=controls[0],last=controls[controls.length-1];
      if (!first) return;
      if (!tour.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
      else if(event.shiftKey && document.activeElement===first) { event.preventDefault(); last.focus(); }
      else if(!event.shiftKey && document.activeElement===last) { event.preventDefault(); first.focus(); }
    }
  };
  document.addEventListener('keydown',tutorialKeyHandler);
  $('#tourSkip').focus();
}
$$('[data-tutorial]').forEach(button=>button.addEventListener('click',setupTour));
window.NeuroTutorial = Object.freeze({refreshLanguage:()=>{if(!$('#tour').hidden) renderTutorial();},open:setupTour});
document.addEventListener?.('neyro:preferences',window.NeuroTutorial.refreshLanguage);


function refreshLanguageOptions() {
  const select = $('#langSelect');
  const selected = select.value;
  let names = null;
  try { names = new Intl.DisplayNames([uiLocale()], {type:'language'}); } catch { /* Older webviews keep the supplied labels. */ }
  fillOptions(select,Object.entries(boot?.languages || {}).map(([code,label])=>{
    let translated = label;
    try { translated = names?.of(code) || label; } catch { /* Keep unusual provider codes. */ }
    return [code,translated];
  }));
  select.value = selected;
}
function refreshInterfaceLanguage() {
  window.NeyroPrefs?.apply(document);
  if (!boot) return;
  renderPlan();
  if (!channel) {
    if (exploring && boot.user.has_access) renderExploreWorkspace();
    else $('#status').textContent = boot.user.has_access ? tr('канал ещё не подключён','no channel connected yet') : tr('доступ не активирован','access not active');
    $('#botName').textContent = boot.bot_username ? `@${boot.bot_username}` : tr('бота','the bot');
    if (boot.user.access_until) $('#gateHint').textContent = tr(`Прошлый доступ закончился ${formatDate(boot.user.access_until)}.`,`Your previous access ended on ${formatDate(boot.user.access_until)}.`);
    return;
  }
  refreshLanguageOptions();
  // Labels only: text areas, selected channel and the post language are untouched.
  updateQualityLabel();
  updateSignaturePreview();
  for (const [selector,labels] of [['#delayChips',DELAY_LABELS],['#paceChips',PACE_LABELS]]) {
    $(selector).querySelectorAll('[data-key]').forEach(button=>{
      const entry=labels.find(([key])=>key===button.dataset.key);
      if(entry) button.textContent=tr(entry[1],entry[2]);
    });
  }
  $('#logoStatus').textContent = channel.logo_configured ? tr('Логотип сохранён.','Logo saved.') : tr('Логотип пока не загружен.','No logo uploaded yet.');
  $('[data-field="gemini_key"]').placeholder = channel.gemini_key_configured ? tr('Ключ сохранён. Введите новый для замены','Key saved. Enter a new one to replace it') : tr('Общий ключ сервиса','Shared service key');
  if(lastRenderedStats?.channelId===channel.id) renderStats(lastRenderedStats.stats);
  if(adminOpen) { refreshAdminMonitoring(); return; }
  // Existing draft editors and in-flight post actions are never replaced.
  const editing = $('#feed').querySelector('textarea,.business-edit,button:disabled');
  if(page==='feed' && !editing) (feedView==='history' ? loadHistory() : loadFeed()).catch(error=>toast(error.message,true));
  if(page==='ads' && !$('#ads').querySelector('button:disabled')) loadAds().catch(error=>toast(error.message,true));
  if(page==='sources' && !sourceBatchRunning) loadSources().catch(error=>toast(error.message,true));
}
document.addEventListener?.('neyro:preferences',refreshInterfaceLanguage);

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
    $('#boot').innerHTML = `<div class="card hero"><h2>${tr("Не удалось открыть редакцию","Could not open the workspace")}</h2><p class="muted">${esc(error.message)}</p><p>${tr("Откройте приложение через кнопку в Telegram-боте.","Open the app using the button in the Telegram bot.")}</p><button class="accent" id="retryBoot">${tr("Попробовать снова","Try again")}</button></div>`;
    $('#retryBoot').onclick = start;
    return;
  }

  refreshLanguageOptions();
  fillOptions($('#tzSelect'), boot.timezones.map((t) => [t, t.split('/').pop().replace('_', ' ')]));
  fillOptions($('#digestTime'), Array.from({ length: 48 }, (_, i) => {
    const value = `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 ? '30' : '00'}`;
    return [value, value];
  }));
  fillOptions($('#promoPlan'), Object.entries(ACCESS_PLANS).map(([k,p])=>[k,p.name]));
  $('#promoPlan').value='pro';

  $('#boot').hidden = true;
  $('#app').hidden = false;
  $('#openAdmin').hidden = !boot.user.is_admin;
  $('#profileInitial').textContent = (boot.user.first_name || 'Н').slice(0, 1).toUpperCase();
  renderPlan();
  loadBilling();
  if (!tourSeen()) setupTour();

  setInterval(() => {
    if (document.hidden) return;
    if (adminOpen) refreshAdminMonitoring();
    else if (page === 'home' && channel) refreshStats();
  }, 15000);

  if (!boot.user.has_access) return showGate();
  if (!boot.channels.length) return exploreWasChosen() ? enterExploreMode() : showOnboarding();

  channel = boot.channels[0];
  enterNormalMode();
}

$('#app').addEventListener('click', e => { const button = e.target.closest('[data-go]'); if (button) openPage(button.dataset.go); });
const HISTORY_LABELS = { get expired(){return tr("Устарел","Expired");}, get published(){return tr("Опубликован","Published");}, get failed(){return tr("Ошибка публикации","Publishing error");}, get filtered(){return tr("Отфильтрован","Filtered");}, get duplicate(){return tr("Дубликат","Duplicate");}, get rejected(){return tr("Отклонён","Rejected");}, get approved(){return tr("В очереди","Queued");}, get new(){return tr("Обрабатывается","Processing");}, get digest(){return tr("В дайджесте","In digest");}, get publishing(){return tr("Отправляется","Sending");}, get uncertain(){return tr("Нужна сверка с каналом","Verify delivery in channel");}, get partial(){return tr("Отправлена только часть","Partially delivered");}, get digest_item(){return tr("Включён в дайджест","Included in digest");} };
async function loadHistory() {
  if (!channel) { if (exploring) renderExploreWorkspace(); return; }
  const current = readTicket('history');
  const rows = await api(`/channels/${channel.id}/history`);
  if (!current()) return;
  $('#history').innerHTML = rows.length ? tr("<p class=\"hint\">Последние 50 событий обработки.</p>","<p class=\"hint\">Latest 50 processing events.</p>") + rows.map(row => `<article class="post history-item"><header><span>${esc(row.source_title || tr("Ручной пост","Manual post"))}</span><span class="tag">${esc(HISTORY_LABELS[row.status] || row.status)}</span></header><p>${esc(row.preview)}</p>${row.reason ? `<p class="hint">${esc(row.reason)}</p>` : ''}${row.delivery_receipts?.length ? `<p class="hint">${tr("Telegram подтвердил сообщения:","Telegram acknowledged messages:")} ${esc(row.delivery_receipts.flatMap(step => step.message_ids).map(id => '#' + id).join(', '))}</p>` : ''}<span class="hint">${esc(ago(row.published_at || row.created_at))}</span>${['partial','uncertain'].includes(row.status) ? `<div class="acts"><button data-reconcile="confirm_sent" data-id="${row.id}">${tr("Проверено: пост опубликован полностью","Verified: the entire post is published")}</button>${row.status === 'uncertain' ? `<button data-reconcile="confirm_absent" data-id="${row.id}">${tr("Проверено: в канале ничего нет","Verified: nothing appeared in the channel")}</button>` : ''}</div>` : ''}</article>`).join('') : tr("<div class=\"empty-note\">Здесь будет история обработки и публикаций.</div>","<div class=\"empty-note\">Processing and publishing history will appear here.</div>");
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
  $('#ads').innerHTML = tr("<p class=\"empty-note\" role=\"status\">Загружаем материалы…</p>","<p class=\"empty-note\" role=\"status\">Loading content…</p>");
  $('#adsActive').classList.toggle('on', view === 'active');
  $('#adsArchive').classList.toggle('on', view === 'archive');
  try { await loadAds(); } catch (error) { $('#ads').innerHTML = tr("<p class=\"empty-note\">Не удалось загрузить материалы. Нажмите вкладку, чтобы повторить.</p>","<p class=\"empty-note\">Could not load content. Select the tab to try again.</p>"); toast(error.message, true); }
}
$('#adsActive').onclick = () => switchAds('active');
$('#adsArchive').onclick = () => switchAds('archive');
$('#history').addEventListener('click', async event => {
  const button = event.target.closest('[data-reconcile]');
  if (!button) return;
  const action = button.dataset.reconcile;
  const question = action === 'confirm_sent' ? tr("Вы проверили канал и видите весь пост, включая текст и медиа? Это отметит публикацию завершённой.","Have you checked the channel and found the entire post, including text and media? This marks delivery complete.") : tr("Вы проверили канал и убедились, что ни текст, ни медиа не появились? Пост вернётся на ручную проверку. Сейчас ничего не отправится.","Have you checked the channel and confirmed that neither text nor media appeared? The post will return to review. Nothing will be sent now.");
  if (!(await ask(question))) return;
  button.disabled = true;
  try {
    await api(`/posts/${button.dataset.id}/${action}`, {method:'POST'});
    await loadHistory();
    toast(tr("Результат сверки сохранён","Verification saved"));
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
start();
