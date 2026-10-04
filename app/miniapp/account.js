'use strict';
const $ = selector => document.querySelector(selector);
const ACCOUNT_EN = {
  "Некорректный номер операции": "Invalid operation reference",
  "Пользователь недоступен": "Account is unavailable",
  "Выберите доступный тариф": "Choose an available plan",
  "Изменение подписки временно требует проверки поддержки.": "Changing this subscription currently requires a support review.",
  "Следующий период уже оплачен. Смена тарифа требует сверки с поддержкой.": "Your next period is already paid. Contact support to review a plan change.",
  "Номер попытки уже относится к другому расчёту": "This attempt belongs to another quote",
  "Расчёт не найден": "Quote not found",
  "Расчёт устарел. Рассчитайте стоимость снова.": "This quote has expired. Choose the plan again for an updated price.",
  "Период закончился. Рассчитайте стоимость снова.": "The period has ended. Choose the plan again for an updated price.",
  "Предыдущий платёж ещё проверяется. Откройте его в истории.": "Your previous payment is still being checked. Open it in your payment history.",
  "Оплата пока не подключена": "Payments are not connected yet",
  "Укажите email для чека": "Enter your receipt email address",
  "Платёж не найден": "Payment not found",
  "Войдите через Telegram ещё раз.": "Sign in with Telegram again.",
  "Вход на сайте пока не настроен.": "Website sign-in is not set up yet.",
  "Откройте личный кабинет на сайте сервиса.": "Open your account on the service website.",
  "Telegram временно недоступен. Повторите вход позже.": "Telegram is temporarily unavailable. Try signing in later.",
  "Вход через Telegram пока не настроен.": "Telegram sign-in is not set up yet.",
  "Доступ заблокирован.": "Access is blocked.",
  "Обновите страницу и повторите действие.": "Refresh the page and try again.",
  "Слишком много попыток входа. Повторите через минуту.": "Too many sign-in attempts. Try again in a minute.",
  "Доступ по коду": "Code access",
  "Мой аккаунт": "My account",
  "Используйте тот же Telegram-аккаунт, что и в боте.": "Use the same Telegram account you use with the bot.",
  "Вход пока недоступен. Попробуйте чуть позже.": "Sign-in is temporarily unavailable. Please try again later.",
  "Не удалось выполнить запрос. Попробуйте ещё раз.": "Could not complete the request. Please try again.",
  "Оплата пока недоступна. Тарифы появятся в кабинете после подключения сервиса оплаты.": "Payments are not available yet. Plans will become available after the payment service is connected.",
  "Оплата временно недоступна. Вы можете посмотреть тарифы.": "Payments are temporarily unavailable. You can still browse the plans.",
  "Тестовый режим оплаты. Тестовый платёж не меняет действующий доступ.": "Test payment mode. Test payments do not change your current access.",
  "Войдите через Telegram, чтобы подключить тариф.": "Sign in with Telegram to choose a plan.",
  "Оплата пока недоступна. Попробуйте позже.": "Payments are unavailable. Please try again later.",
  "Рассчитываем стоимость…": "Calculating the price…",
  "Не удалось получить стоимость. Повторите выбор тарифа.": "Could not calculate the price. Please choose the plan again.",
  "Готовим подтверждение…": "Preparing your payment…",
  "Выбор сохранён. До конца периода действует текущий тариф. Затем продлите доступ вручную на выбранном тарифе.": "Choice saved. Your current plan remains until the end of the period. Then renew manually with the selected plan.",
  "Не удалось получить номер оплаты. Повторите попытку.": "Could not get the payment reference. Please try again.",
  "Ссылка на оплату пока недоступна. Обновите статус в разделе «Мои оплаты».": "The payment link is not available yet. Refresh the status under My payments.",
  "Открываем защищённую страницу ЮKassa…": "Opening the secure YooKassa payment page…",
  "Ожидает оплаты": "Awaiting payment",
  "Обрабатывается": "Processing",
  "Оплачено": "Paid",
  "Отменено": "Canceled",
  "Не завершено": "Incomplete",
  "Запланировано": "Scheduled",
  "Готовится": "Preparing",
  "Проверяем статус": "Checking status",
  "Проверяется": "Under review",
  "Тестовая оплата": "Test payment",
  "Возвращено": "Refunded",
  "Укажите полную ссылку на чек из «Мой налог» на lknpd.nalog.ru.": "Enter the full My Tax receipt link from lknpd.nalog.ru.",
  "Ссылка добавлена. Чек доступен покупателю в кабинете.": "Link added. The customer can view the receipt in their account.",
  "Оплата подтверждена. Доступ обновлён.": "Payment confirmed. Your access has been updated.",
  "Тестовая оплата подтверждена. Действующий доступ не меняется.": "Test payment confirmed. Your current access is unchanged.",
  "Оплата проверяется. Обновите статус позже.": "Payment is under review. Refresh the status later.",
  "По этой оплате оформлен возврат.": "This payment has been refunded.",
  "Оплата не завершена. Можно выбрать тариф и попробовать снова.": "Payment was not completed. You can choose a plan and try again.",
  "Ожидаем подтверждение оплаты. Доступ обновится после проверки.": "Waiting for payment confirmation. Your access will update after verification.",
  "Не удалось загрузить вход Telegram. Обновите страницу.": "Telegram sign-in did not load. Please refresh the page.",
  "Вход не завершён. Попробуйте ещё раз.": "Sign-in was not completed. Please try again.",
  "Вход отменён.": "Sign-in canceled.",
  "Не удалось подтвердить вход. Попробуйте ещё раз.": "Could not verify sign-in. Please try again.",
  "Не удалось открыть вход Telegram. Попробуйте снова.": "Could not open Telegram sign-in. Please try again.",
  "Оплата ещё не подключена": "Payments are not connected yet",
  "Оплата временно недоступна": "Payments are temporarily unavailable",
  "Обновите страницу и войдите снова": "Refresh the page and sign in again",
  "Сессия истекла. Войдите снова": "Your session expired. Sign in again"
};
const language = () => window.NeyroPrefs?.language || 'ru';
const locale = () => language() === 'en' ? 'en-GB' : 'ru-RU';
function tr(ru, en, values = {}) {
  const translation = en ?? (Object.prototype.hasOwnProperty.call(ACCOUNT_EN, ru) ? ACCOUNT_EN[ru] : undefined);
  if (window.NeyroPrefs) return window.NeyroPrefs.t(ru, translation ?? (/[А-Яа-яЁё]/.test(ru) ? 'Could not complete the request. Please try again.' : ru), values);
  return String(ru).replace(/\{(\w+)\}/g, (match, key) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match);
}
const noticeCopies = new Map();
const FALLBACK_PLANS = [
  {code:'plus', name:'PLUS', price_rub:390, ai_daily_limit:5},
  {code:'pro', name:'PRO', price_rub:490, ai_daily_limit:10},
  {code:'expert', name:'EXPERT', price_rub:590, ai_daily_limit:15},
  {code:'creator', name:'CREATOR', price_rub:990, ai_daily_limit:25},
  {code:'unlimited', name:'UNLIMITED', price_rub:1490, ai_daily_limit:50},
];
const PLAN_CODES = new Set(FALLBACK_PLANS.map(plan => plan.code));
const state = {session:null, catalog:null, subscription:null, quote:null, requestId:null, busy:false, checking:false, pendingOrder:null, orderTimer:null};
const esc = text => String(text ?? '').replace(/[&<>"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[char]));
const money = value => new Intl.NumberFormat(locale(), {style:'currency', currency:'RUB', maximumFractionDigits:2}).format(Number(value));
const date = value => value ? new Date(value).toLocaleDateString(locale(), {day:'numeric', month:'long', year:'numeric'}) : '—';
const planName = code => PLAN_CODES.has(code) ? code.toUpperCase() : tr('Доступ по коду');
const authenticated = () => !!state.session?.authenticated;
const receiptAdmin = () => authenticated() && state.session?.user?.is_admin === true;
function notice(selector, text) { noticeCopies.set(selector, text); const el = $(selector); el.textContent = text ? tr(text) : ''; el.hidden = !text; }
async function api(path, options = {}) {
  const method = options.method || 'GET';
  const headers = {'Content-Type':'application/json', ...(options.headers || {})};
  if (method !== 'GET' && state.session?.csrf_token) headers['X-CSRF-Token'] = state.session.csrf_token;
  const response = await fetch(`/api${path}`, {...options, method, headers, credentials:'same-origin'});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос. Попробуйте ещё раз.');
    error.status = response.status;
    if (response.status === 401 && path !== '/account/login') { state.session = null; state.quote = null; renderSession(); renderPlans(); }
    throw error;
  }
  return data;
}
function renderSession() {
  const loggedIn = authenticated();
  $('#accountAuth').hidden = loggedIn;
  $('#accountAccess').hidden = !loggedIn;
  $('#accountOrders').hidden = !loggedIn;
  $('#logout').hidden = !loggedIn;
  $('#accountName').hidden = !loggedIn;
  $('#accountName').textContent = state.session?.user?.first_name || state.session?.user?.username || tr('Мой аккаунт');
  $('#adminReceipts').hidden = !receiptAdmin();
  if (!receiptAdmin()) { state.receipts = null; state.refunds = null; $('#adminReceiptList').innerHTML = ''; notice('#adminReceiptStatus', ''); }
  if (!loggedIn) $('#accountQuote').hidden = true;
  const configured = Number(state.session?.telegram_client_id) > 0 && !!state.session?.login_nonce;
  $('#telegramLogin').disabled = !configured || state.busy;
  $('#loginStatus').textContent = tr(configured ? 'Используйте тот же Telegram-аккаунт, что и в боте.' : 'Вход пока недоступен. Попробуйте чуть позже.');
}
function renderPlan() {
  const sub = state.subscription;
  if (!sub) { $('#accountPlan').innerHTML = `<h3>${tr('Проверяем доступ', 'Checking access')}</h3><p class="hint">${tr('Обновите страницу, если сведения не появились.', 'Refresh the page if your details do not appear.')}</p>`; return; }
  const active = sub.status === 'active';
  const limit = Math.max(0, Number(sub.ai_daily_limit) || 0);
  const used = Math.max(0, Number(sub.ai_used_today) || 0);
  const title = sub.current_plan ? planName(sub.current_plan) : active ? tr('Доступ по коду') : tr('Выберите тариф', 'Choose a plan');
  const period = sub.period_end ? `${active ? tr('Действует до', 'Active until') : tr('Период закончился', 'Period ended')} ${date(sub.period_end)}` : tr('Начните с подходящего объёма постов', 'Start with the right posting allowance');
  const scheduled = sub.next_period_paid ? tr('Оплачен следующий период', 'Next period is paid') : tr('На следующее продление выбран', 'Selected for next renewal');
  $('#accountPlan').innerHTML = `<div class="plan-meta"><div><span class="eyebrow">${tr('ВАШ ДОСТУП', 'YOUR ACCESS')}</span><h3>${esc(title)}</h3></div><span class="tag">${active ? tr('Активен', 'Active') : tr('Не активен', 'Inactive')}</span></div><p class="hint">${esc(period)}</p>${active ? `<progress class="quota-meter" max="${Math.max(1,limit)}" value="${Math.min(used,limit)}" aria-label="${tr('Использовано постов с ИИ сегодня', 'AI posts used today')}"></progress><div class="quota-copy"><span>${tr('{used} из {limit} использовано сегодня', '{used} of {limit} used today', {used,limit})}</span><span>${tr('Осталось {count}', '{count} remaining', {count:Math.max(0,limit-used)})}</span></div>` : ''}${sub.scheduled_plan ? `<p class="scheduled">${scheduled}: ${esc(planName(sub.scheduled_plan))} ${tr('с', 'from')} ${esc(date(sub.scheduled_at))}.${sub.next_period_paid ? '' : ' '+tr('Для продления потребуется оплата.', 'Payment is required to renew.')}</p>` : ''}`;
}
function renderPlans() {
  const plans = state.catalog?.plans || FALLBACK_PLANS;
  $('#receiptNotice').hidden = state.catalog?.receipt_mode !== 'npd_manual';
  $('#accountPlans').innerHTML = plans.filter(plan => PLAN_CODES.has(plan.code)).map(plan => {
    const current = state.subscription?.status === 'active' && state.subscription?.current_plan === plan.code;
    const selected = state.quote?.plan === plan.code;
    const disabled = !authenticated() || !state.catalog || state.busy;
    return `<article class="tariff${current ? ' current' : ''}${selected ? ' selected' : ''}"><span class="plan-name">${esc(plan.name)}</span><div class="plan-price">${esc(Number(plan.price_rub).toLocaleString(locale()))} <small>₽</small></div><span class="plan-period">${tr('за месяц', 'per month')}</span><p class="plan-limit">${esc(plan.ai_daily_limit)} ${tr('постов с ИИ', 'AI posts')}<br>${tr('в день', 'per day')}</p><p class="plan-feature">${tr('Общий лимит', 'Shared allowance')}<br>${tr('для всех каналов', 'across all channels')}</p><button class="${selected ? 'accent' : 'ghost'}" data-plan="${esc(plan.code)}" ${disabled ? 'disabled' : ''}>${selected ? tr('Выбран', 'Selected') : current ? tr('Продлить', 'Renew') : tr('Выбрать', 'Choose')}</button><span class="current-label">${current ? tr('Текущий тариф', 'Current plan') : '&nbsp;'}</span></article>`;
  }).join('');
  if (!state.catalog) notice('#paymentAvailability', 'Оплата пока недоступна. Тарифы появятся в кабинете после подключения сервиса оплаты.');
  else if (!state.catalog.checkout_available) notice('#paymentAvailability', state.catalog.unavailable_reason || 'Оплата временно недоступна. Вы можете посмотреть тарифы.');
  else notice('#paymentAvailability', state.catalog.test ? 'Тестовый режим оплаты. Тестовый платёж не меняет действующий доступ.' : authenticated() ? '' : 'Войдите через Telegram, чтобы подключить тариф.');
}
function renderQuote() {
  const quote = state.quote;
  $('#accountQuote').hidden = !quote;
  if (!quote) return;
  const downgrade = quote.change_type === 'downgrade';
  $('#quoteTitle').textContent = downgrade ? tr('Тариф для следующего продления', 'Plan for your next renewal') : quote.change_type === 'upgrade' ? tr('Переход на тариф выше', 'Upgrade your plan') : quote.change_type === 'renewal' ? tr('Продление тарифа', 'Renew your plan') : tr('Подключение тарифа', 'Start your plan');
  const explanation = downgrade ? tr('Тариф выбран на период с {date}. Сейчас оплаты нет; для продления потребуется вручную оплатить новый период.', 'Selected for the period starting {date}. No payment now; pay for the new period manually when you renew.', {date:date(quote.effective_at)}) : quote.change_type === 'upgrade' ? tr('Доплата за оставшееся время. Новый лимит включится после подтверждения оплаты.', 'Pay the difference for the remaining time. Your new allowance starts after payment is confirmed.') : tr('Один месяц доступа{start}. Следующее продление — вручную.', 'One month of access{start}. Renew manually for the next period.', {start:quote.effective_at ? ' '+tr('с', 'from')+' '+date(quote.effective_at) : ''});
  $('#quoteSummary').innerHTML = `<b>${esc(planName(quote.plan))}</b><div class="quote-price">${esc(money(quote.amount_rub))}</div><p class="hint">${esc(explanation)}</p>`;
  $('#receiptField').hidden = downgrade;
  $('#accountCheckout').textContent = downgrade ? tr('Сохранить выбор', 'Save choice') : tr('Оплатить через ЮKassa', 'Pay with YooKassa');
  $('#accountCheckout').disabled = state.busy || !quote.checkout_available || !authenticated();
  $('#cancelQuote').disabled = state.busy;
  if (!quote.checkout_available) notice('#checkoutStatus', quote.unavailable_reason || 'Оплата пока недоступна. Попробуйте позже.');
}
async function selectPlan(code) {
  if (state.busy || !authenticated() || !PLAN_CODES.has(code)) return;
  state.busy = true; state.quote = null;
  renderPlans(); renderQuote(); notice('#accountNotice', 'Рассчитываем стоимость…');
  try {
    const quote = await api('/billing/quote', {method:'POST', body:JSON.stringify({plan:code})});
    if (!quote.quote_id || !PLAN_CODES.has(quote.plan) || !Number.isFinite(Number(quote.amount_rub))) throw new Error('Не удалось получить стоимость. Повторите выбор тарифа.');
    state.quote = quote; state.requestId = crypto.randomUUID();
    notice('#checkoutStatus', ''); notice('#accountNotice', '');
  } catch (error) { notice('#accountNotice', error.message); }
  finally { state.busy = false; renderPlans(); renderQuote(); }
  if (state.quote) { $('#accountQuote').scrollIntoView({behavior:'smooth', block:'center'}); $('#accountQuote').focus({preventScroll:true}); }
}
function confirmationLink(value) {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password && ['yookassa.ru','yoomoney.ru'].some(host => url.hostname === host || url.hostname.endsWith(`.${host}`)) ? url.href : ''; }
  catch { return ''; }
}
function rememberOrder(id) { state.pendingOrder = id; try { sessionStorage.setItem('neyro:pending-order', id); } catch {} }
function forgetOrder() { state.pendingOrder = null; try { sessionStorage.removeItem('neyro:pending-order'); } catch {} clearTimeout(state.orderTimer); }
async function checkout() {
  const quote = state.quote;
  if (state.busy || !authenticated() || !quote?.checkout_available) return;
  if (quote.change_type !== 'downgrade' && !$('#receiptEmail').reportValidity()) return;
  state.busy = true; renderPlans(); renderQuote(); notice('#checkoutStatus', 'Готовим подтверждение…');
  try {
    const order = await api('/billing/checkout', {method:'POST', body:JSON.stringify({quote_id:quote.quote_id, receipt_email:quote.change_type === 'downgrade' ? null : $('#receiptEmail').value.trim(), request_id:state.requestId})});
    if (order.status === 'scheduled') {
      state.quote = null; notice('#accountNotice', 'Выбор сохранён. До конца периода действует текущий тариф. Затем продлите доступ вручную на выбранном тарифе.');
      await refreshAccess(); return;
    }
    if (!order.order_id) throw new Error('Не удалось получить номер оплаты. Повторите попытку.');
    rememberOrder(order.order_id);
    if (order.status === 'succeeded') { await checkOrder(); state.quote = null; return; }
    const url = confirmationLink(order.confirmation_url);
    if (!url) throw new Error('Ссылка на оплату пока недоступна. Обновите статус в разделе «Мои оплаты».');
    notice('#checkoutStatus', 'Открываем защищённую страницу ЮKassa…');
    window.location.assign(url);
  } catch (error) { notice('#checkoutStatus', error.message); }
  finally { state.busy = false; renderPlans(); renderQuote(); }
}
const ORDER_LABELS = {pending:'Ожидает оплаты', waiting_for_capture:'Обрабатывается', succeeded:'Оплачено', canceled:'Отменено', failed:'Не завершено', scheduled:'Запланировано', creating:'Готовится', unknown:'Проверяем статус', review:'Проверяется', test_succeeded:'Тестовая оплата', refunded:'Возвращено'};
function npdReceiptLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'lknpd.nalog.ru' && !url.username && !url.password && !url.port && !url.search && !url.hash && /^\/api\/v1\/receipt\/\d{12}\/[a-zA-Z0-9]+\/print$/.test(url.pathname) ? url.href : '';
  } catch { return ''; }
}
function receiptMarkup(order) {
  if (order.receipt_mode !== 'npd_manual' || !['succeeded', 'review', 'refunded'].includes(order.status)) return '';
  const url = npdReceiptLink(order.npd_receipt_url);
  return url ? `<a class="order-resume" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${tr('Открыть чек', 'View receipt')}</a>` : order.status === 'succeeded' ? `<small>${tr('Чек готовится', 'Receipt pending')}</small>` : '';
}
async function loadOrders() {
  const data = await api('/billing/orders');
  state.orders = Array.isArray(data.orders) ? data.orders : [];
  renderOrders();
}
function renderOrders() {
  const orders = state.orders || [];
  $('#orderHistory').innerHTML = orders.length ? orders.map(order => `<div class="order-row"><div><b>${esc(planName(order.plan))} · ${esc(money(order.amount_rub))}</b><small>${esc(date(order.created_at))}</small></div><span class="order-status">${esc(tr(ORDER_LABELS[order.status] || 'Проверяем статус'))}${receiptMarkup(order)}${order.status === 'pending' && confirmationLink(order.confirmation_url) ? `<a class="order-resume" href="${esc(confirmationLink(order.confirmation_url))}">${tr('Продолжить оплату', 'Continue payment')}</a>` : ''}</span></div>`).join('') : `<p class="empty-note">${tr('Здесь появятся ваши оплаты.', 'Your payments will appear here.')}</p>`;
}
function renderManualReceipts() {
  if (!receiptAdmin() || !state.receipts) return;
  const container = $('#adminReceiptList');
  const existing = new Map(Array.from(container.querySelectorAll?.('[name="receipt_url"]') || []).map(input => [input.id, input.value]));
  const status = order => `<span class="tag">${esc(tr(ORDER_LABELS[order.status] || 'Проверяем статус'))}</span>`;
  const rows = state.receipts.map(order => `<form class="card" data-receipt-order="${esc(order.order_id)}"><div class="row"><b>${esc(planName(order.plan))} · ${esc(money(order.amount_rub))}</b>${status(order)}</div><p class="hint">${esc(date(order.created_at))} · ${esc(order.receipt_email)}</p><p>${esc(order.description)}</p>${order.status === 'review' ? `<p class="notice">${tr('Доступ проверяется. Сверьте подтверждённую оплату и чек перед добавлением.', 'Access is under review. Verify the confirmed payment and receipt before adding its link.')}</p>` : ''}<label class="label" for="receipt-${esc(order.order_id)}">${tr('Ссылка на чек из «Мой налог»', 'My Tax receipt link')}</label><input id="receipt-${esc(order.order_id)}" name="receipt_url" type="url" required placeholder="https://lknpd.nalog.ru/api/v1/receipt/…" autocomplete="off" value="${esc(existing.get('receipt-'+order.order_id) || '')}"><button class="ghost" type="submit">${tr('Добавить чек', 'Add receipt')}</button></form>`);
  for (const refund of state.refunds || []) {
    const receipt = npdReceiptLink(refund.npd_receipt_url);
    rows.push(`<div class="card receipt-review"><div class="row"><b>${esc(planName(refund.plan))} · ${esc(money(refund.amount_rub))}</b>${status(refund)}</div><p class="hint">${esc(date(refund.created_at))} · ${esc(refund.receipt_email)}</p><p>${tr('Возвращено: {amount}', 'Refunded: {amount}', {amount:esc(money(refund.refunded_amount_rub))})}</p><p class="hint">${tr('Сначала сверьте возврат и чек в «Мой налог». При необходимости исправьте или аннулируйте чек; повторный чек здесь не создаётся.', 'Reconcile the refund and receipt in My Tax first. Correct or cancel the receipt if necessary; no duplicate receipt is created here.')}</p>${receipt ? `<a class="order-resume" href="${esc(receipt)}" target="_blank" rel="noopener noreferrer">${tr('Открыть чек', 'View receipt')}</a>` : ''}</div>`);
  }
  container.innerHTML = rows.length ? rows.join('') : `<p class="empty-note">${tr('Нет оплат, ожидающих чека.', 'No payments awaiting a receipt.')}</p>`;
}
async function loadManualReceipts() {
  if (!receiptAdmin()) return;
  const session = state.session;
  try {
    const data = await api('/billing/admin/receipts');
    if (!receiptAdmin() || state.session !== session) return;
    state.receipts = Array.isArray(data.orders) ? data.orders : [];
    state.refunds = Array.isArray(data.refunds) ? data.refunds : [];
    $('#adminReceipts').hidden = false;
    renderManualReceipts();
  } catch (error) { if (receiptAdmin() && state.session === session) notice('#adminReceiptStatus', error.message); }
}
async function attachManualReceipt(orderId, value) {
  if (!receiptAdmin()) return;
  const url = npdReceiptLink(value.trim());
  if (!url) { notice('#adminReceiptStatus', 'Укажите полную ссылку на чек из «Мой налог» на lknpd.nalog.ru.'); return; }
  try {
    await api(`/billing/admin/receipts/${encodeURIComponent(orderId)}`, {method:'POST', body:JSON.stringify({receipt_url:url})});
    if (!receiptAdmin()) return;
    notice('#adminReceiptStatus', 'Ссылка добавлена. Чек доступен покупателю в кабинете.');
    await Promise.all([loadManualReceipts(), loadOrders()]);
  } catch (error) { if (receiptAdmin()) notice('#adminReceiptStatus', error.message); }
}
async function refreshAccess() {
  const results = await Promise.allSettled([api('/billing/subscription'), loadOrders(), loadManualReceipts()]);
  if (results[0].status === 'fulfilled') state.subscription = results[0].value;
  else notice('#accountNotice', results[0].reason.message);
  if (results[1].status === 'rejected') $('#orderHistory').textContent = tr(results[1].reason.message);
  renderPlan(); renderPlans();
}
async function checkOrder() {
  if (!state.pendingOrder || state.checking || !authenticated()) return;
  state.checking = true; clearTimeout(state.orderTimer);
  try {
    const order = await api(`/billing/orders/${encodeURIComponent(state.pendingOrder)}`);
    if (order.status === 'succeeded') { notice('#paymentStatus', 'Оплата подтверждена. Доступ обновлён.'); forgetOrder(); await refreshAccess(); }
    else if (order.status === 'test_succeeded') { notice('#paymentStatus', 'Тестовая оплата подтверждена. Действующий доступ не меняется.'); forgetOrder(); await loadOrders(); }
    else if (order.status === 'review') { notice('#paymentStatus', 'Оплата проверяется. Обновите статус позже.'); forgetOrder(); await loadOrders(); }
    else if (order.status === 'refunded') { notice('#paymentStatus', 'По этой оплате оформлен возврат.'); forgetOrder(); await refreshAccess(); }
    else if (['canceled','failed'].includes(order.status)) { notice('#paymentStatus', 'Оплата не завершена. Можно выбрать тариф и попробовать снова.'); forgetOrder(); await loadOrders(); }
    else { notice('#paymentStatus', 'Ожидаем подтверждение оплаты. Доступ обновится после проверки.'); state.orderTimer = setTimeout(() => { if (!document.hidden) checkOrder(); }, 10000); }
  } catch (error) { notice('#paymentStatus', `${tr(error.message)} ${tr('Нажмите «Обновить», чтобы проверить снова.', 'Select Refresh to check again.')}`); }
  finally { state.checking = false; }
}
async function loadCatalog() {
  try {
    const catalog = await api('/billing/catalog');
    if (!Array.isArray(catalog.plans) || catalog.provider !== 'yookassa') throw new Error('Каталог пока недоступен');
    state.catalog = catalog;
  } catch { state.catalog = null; }
  renderPlans();
}
async function startAccount() {
  renderPlans();
  try { state.session = await api('/account/session'); notice('#accountNotice', ''); }
  catch (error) { notice('#accountNotice', error.message); }
  renderSession(); await loadCatalog();
  if (authenticated()) {
    await refreshAccess();
    const query = new URLSearchParams(location.search);
    const fromUrl = query.get('order_id') || query.get('order');
    let saved = null; try { saved = sessionStorage.getItem('neyro:pending-order'); } catch {}
    const pending = fromUrl || saved;
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pending || '')) { rememberOrder(pending); await checkOrder(); }
  }
  revealRequestedPlans();
}
let requestedPlanHandled = false;
async function revealRequestedPlans() {
  if (location.hash !== '#plans') return;
  const loggedIn = authenticated();
  const requested = new URLSearchParams(location.search).get('plan');
  if (loggedIn && PLAN_CODES.has(requested) && !requestedPlanHandled) {
    requestedPlanHandled = true;
    await selectPlan(requested);
    if (state.quote) return;
  }
  $(loggedIn ? '#plans' : '#accountAuth').scrollIntoView({block:'start'});
  $(loggedIn ? '#accountPlans [data-plan]' : '#telegramLogin')?.focus({preventScroll:true});
}
$('#accountPlans').addEventListener('click', event => { const button = event.target.closest('[data-plan]'); if (button) selectPlan(button.dataset.plan); });
$('#accountCheckout').addEventListener('click', checkout);
$('#cancelQuote').addEventListener('click', () => { if (state.busy) return; state.quote = null; state.requestId = null; renderPlans(); renderQuote(); });
$('#refreshOrders').addEventListener('click', async () => { $('#refreshOrders').disabled = true; try { await refreshAccess(); await checkOrder(); await loadCatalog(); } finally { $('#refreshOrders').disabled = false; } });
$('#refreshAdminReceipts').addEventListener('click', async () => {
  $('#refreshAdminReceipts').disabled = true;
  try { await loadManualReceipts(); } finally { $('#refreshAdminReceipts').disabled = false; }
});
$('#adminReceiptList').addEventListener('submit', async event => {
  const form = event.target.closest('[data-receipt-order]');
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector('button');
  if (button.disabled) return;
  button.disabled = true;
  try { await attachManualReceipt(form.dataset.receiptOrder, form.querySelector('[name="receipt_url"]').value); }
  finally { button.disabled = false; }
});
$('#telegramLogin').addEventListener('click', () => {
  if (state.busy || !state.session?.login_nonce) return;
  if (!window.Telegram?.Login?.auth) { notice('#loginStatus', 'Не удалось загрузить вход Telegram. Обновите страницу.'); return; }
  state.busy = true; renderSession();
  try {
    window.Telegram.Login.auth({client_id:Number(state.session.telegram_client_id), scope:['profile'], lang:language(), nonce:state.session.login_nonce}, async result => {
      try {
        if (!result?.id_token) throw new Error(result?.error ? 'Вход не завершён. Попробуйте ещё раз.' : 'Вход отменён.');
        await api('/account/login', {method:'POST', body:JSON.stringify({id_token:result.id_token, login_nonce:state.session.login_nonce})});
        state.session = await api('/account/session');
        if (!authenticated()) throw new Error('Не удалось подтвердить вход. Попробуйте ещё раз.');
        notice('#accountNotice', ''); await refreshAccess();
      } catch (error) {
        notice('#accountNotice', error.message);
        try { state.session = await api('/account/session'); } catch {}
      }
      finally { state.busy = false; renderSession(); renderPlans(); revealRequestedPlans(); }
    });
  } catch { state.busy = false; renderSession(); notice('#loginStatus', 'Не удалось открыть вход Telegram. Попробуйте снова.'); }
});
$('#logout').addEventListener('click', async () => {
  if (state.busy) return;
  state.busy = true; $('#logout').disabled = true;
  try { await api('/account/logout', {method:'POST', body:'{}'}); forgetOrder(); state.session = null; state.subscription = null; state.quote = null; await startAccount(); }
  catch (error) { notice('#accountNotice', error.message); }
  finally { state.busy = false; $('#logout').disabled = false; renderSession(); renderPlans(); }
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkOrder(); });
document.addEventListener('neyro:preferences', () => {
  for (const [selector, text] of noticeCopies) notice(selector, text);
  renderSession(); renderPlan(); renderPlans(); renderQuote(); renderOrders(); renderManualReceipts();
  window.NeyroPrefs?.apply();
});
startAccount();
