// Browser account state regressions with mocked API and DOM; never creates real payments.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector,{value:'reader@example.ru',disabled:false,hidden:false,dataset:{},
    textContent:'',innerHTML:'',events:{},addEventListener(event,handler){this.events[event]=handler},
    reportValidity(){return true},focus(){},scrollIntoView(){}});
  return elements.get(selector);
}
const requests=[];
const redirects=[];
const storage=new Map();
const documentEvents=new Map();
const context=vm.createContext({URL,URLSearchParams,Intl,Date,Set,Number,console,assert,crypto:{randomUUID},
  location:{search:''},window:{location:{assign(url){redirects.push(url)}}},
  document:{querySelector:element,hidden:false,addEventListener(name,callback){documentEvents.set(name,callback)}},
  setTimeout(){return 1},clearTimeout(){},sessionStorage:{setItem(k,v){storage.set(k,v)},removeItem(k){storage.delete(k)},getItem(k){return storage.get(k)}},
  fetch:async (url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({ok:true})}},
});
vm.runInContext(readFileSync('app/miniapp/account.js','utf8').replace(/^startAccount\(\);$/m,''),context);
(async()=>{
  await vm.runInContext(`(async()=>{
    state.session={authenticated:true,csrf_token:'csrf-token',user:{first_name:'Анна'}};
    await api('/billing/quote',{method:'POST',body:'{"plan":"plus"}'});
    assert.equal(confirmationLink('javascript:alert(1)'),'');
    assert.equal(confirmationLink('https://yookassa.ru.evil.example/path'),'');
    assert.equal(confirmationLink('https://user:secret@yookassa.ru/path'),'');
    assert.equal(confirmationLink('https://yoomoney.ru/checkout/1'),'https://yoomoney.ru/checkout/1');
    assert.equal(FALLBACK_PLANS.length,5); assert.equal(PLAN_CODES.has('mini'),false);
    state.catalog={plans:FALLBACK_PLANS,checkout_available:false,provider:'yookassa'};
    let calls=0;
    api=async()=>{calls++;return {}};
    state.quote={quote_id:'q1',plan:'pro',checkout_available:false};
    await checkout();assert.equal(calls,0);
    state.quote={quote_id:'q1',plan:'pro',checkout_available:true,change_type:'new'};
    state.requestId='stable-id';
    const bodies=[];
    api=async(url,options)=>{if(url==='/billing/checkout'){bodies.push(JSON.parse(options.body));throw Error('offline')}return {}};
    await checkout();await checkout();
    assert.equal(bodies.length,2);assert.equal(bodies[0].request_id,bodies[1].request_id);
    assert.equal(state.busy,false);assert.equal($('#accountCheckout').disabled,false);
    let release;
    const pending=new Promise(resolve=>release=resolve);
    api=async()=>{calls++;await pending;throw Error('offline')};
    const first=checkout();const second=checkout();release();await Promise.all([first,second]);
    assert.equal(calls,1);
    const downgradePayload=[];
    api=async(url,options)=>{
      if(url==='/billing/checkout'){downgradePayload.push(JSON.parse(options.body));return {status:'scheduled'}}
      if(url==='/billing/subscription')return {status:'active',current_plan:'pro',ai_daily_limit:10,ai_used_today:2,scheduled_plan:'plus',scheduled_at:'2026-11-03'};
      if(url==='/billing/orders')return {orders:[]};
    };
    state.quote={quote_id:'q2',plan:'plus',amount_rub:0,change_type:'downgrade',checkout_available:true};
    renderQuote();assert.equal($('#receiptField').hidden,true);
    assert.ok($('#quoteSummary').innerHTML.includes('вручную оплатить'));
    await checkout();assert.equal(downgradePayload[0].receipt_email,null);assert.equal(state.quote,null);
    assert.ok($('#accountNotice').textContent.includes('вручную'));
    assert.ok($('#accountPlan').innerHTML.includes('потребуется оплата'));
    api=async(url)=>url.startsWith('/billing/orders/') ? {status:'pending'} : {orders:[]};
    rememberOrder('mock-order');await checkOrder();
    assert.equal(state.pendingOrder,'mock-order');assert.ok($('#paymentStatus').textContent.includes('Ожидаем'));
    api=async(url)=>url.startsWith('/billing/orders/') ? {status:'test_succeeded'} : {orders:[]};
    await checkOrder();assert.equal(state.pendingOrder,null);assert.ok($('#paymentStatus').textContent.includes('не меняется'));
    rememberOrder('mock-order');
    api=async(url)=>url.startsWith('/billing/orders/') ? {status:'succeeded'} : url==='/billing/orders' ? {orders:[]} : {status:'active',current_plan:'pro',ai_daily_limit:10,ai_used_today:2};
    await checkOrder();assert.equal(state.pendingOrder,null);assert.ok($('#paymentStatus').textContent.includes('подтверждена'));
    let callback;
    window.Telegram={Login:{auth:(_options,handler)=>{callback=handler}}};
    state.session={authenticated:false,telegram_client_id:'123',login_nonce:'expired'};
    api=async()=>({authenticated:false,telegram_client_id:'123',login_nonce:'fresh'});
    $('#telegramLogin').events.click();
    await callback({error:'expired nonce'});
    assert.equal(state.session.login_nonce,'fresh');assert.equal(state.busy,false);
    assert.equal($('#telegramLogin').disabled,false);
    // Receipt status is independent of payment success: only an attached NPD link means issued.
    const receiptUrl='https://lknpd.nalog.ru/api/v1/receipt/123456789012/abc123/print';
    assert.equal(npdReceiptLink(receiptUrl),receiptUrl);
    for(const bad of ['javascript:alert(1)','https://lknpd.nalog.ru.evil.example/api/v1/receipt/1/a/print','https://u:p@lknpd.nalog.ru/api/v1/receipt/1/a/print',receiptUrl+'?token=x',receiptUrl+'#x','https://lknpd.nalog.ru/other'])assert.equal(npdReceiptLink(bad),'');
    const manualOrder={status:'succeeded',receipt_mode:'npd_manual'};
    assert.equal(receiptMarkup(manualOrder),'<small>Чек готовится</small>');
    assert.ok(receiptMarkup({...manualOrder,npd_receipt_url:receiptUrl}).includes('Открыть чек'));
    assert.ok(!receiptMarkup({...manualOrder,npd_receipt_url:receiptUrl}).includes('готовится'));
    assert.equal(receiptMarkup({...manualOrder,status:'pending'}),'');
    assert.equal(receiptMarkup({...manualOrder,status:'test_succeeded'}),'');
    assert.equal(receiptMarkup({...manualOrder,receipt_mode:'yookassa_54fz'}),'');
    assert.equal(receiptMarkup({...manualOrder,npd_receipt_url:'javascript:alert(1)'}),'<small>Чек готовится</small>');
    state.catalog={plans:FALLBACK_PLANS,receipt_mode:'npd_manual'};renderPlans();
    assert.equal($('#receiptNotice').hidden,false);
    state.catalog.receipt_mode='yookassa_54fz';renderPlans();assert.equal($('#receiptNotice').hidden,true);
    // A customer never loads or sees the admin queue; losing the session clears its contents.
    let receiptCalls=0;
    api=async()=>{receiptCalls++;return {orders:[]}};
    state.session={authenticated:true,user:{is_admin:false}};
    renderSession();await loadManualReceipts();await attachManualReceipt('order',receiptUrl);
    assert.equal(receiptCalls,0);assert.equal($('#adminReceipts').hidden,true);
    const attachments=[];
    state.session={authenticated:true,user:{is_admin:true},csrf_token:'csrf-token'};
    api=async(path,options)=>{
      receiptCalls++;
      if(options?.method==='POST'){attachments.push({path,body:JSON.parse(options.body)});return {}}
      return {orders:path==='/billing/admin/receipts' ? [{order_id:'order-1',plan:'plus',amount_rub:390,receipt_email:'<buyer@example.ru>',description:'<script>unsafe</script>',created_at:'2026-10-03'}] : []};
    };
    renderSession();await loadManualReceipts();
    assert.equal($('#adminReceipts').hidden,false);
    assert.ok($('#adminReceiptList').innerHTML.includes('&lt;script&gt;unsafe&lt;/script&gt;'));
    assert.ok($('#adminReceiptList').innerHTML.includes('&lt;buyer@example.ru&gt;'));
    await attachManualReceipt('order-1','https://evil.example/');assert.equal(attachments.length,0);
    await attachManualReceipt('order-1',receiptUrl);
    assert.equal(attachments.length,1);assert.equal(attachments[0].path,'/billing/admin/receipts/order-1');
    assert.equal(attachments[0].body.receipt_url,receiptUrl);
    assert.ok($('#adminReceiptStatus').textContent.includes('доступен покупателю'));
    let finishReceipts;
    api=async()=>await new Promise(resolve=>{finishReceipts=resolve});
    const adminRefresh=loadManualReceipts();
    state.session=null;renderSession();
    finishReceipts({orders:[{description:'must not appear'}]});await adminRefresh;
    assert.equal($('#adminReceipts').hidden,true);assert.equal($('#adminReceiptList').innerHTML,'');
    // Translate interface copy, never user names, email or issued receipt descriptions.
    window.NeyroPrefs={language:'en',apply(){},t(ru,en,values={}){return String(this.language==='en' ? en ?? ru : ru).replace(/\\{(\\w+)\\}/g,(match,key)=>key in values ? String(values[key]) : match)}};
    state.session={authenticated:true,user:{first_name:'Анна',is_admin:true},telegram_client_id:'123',login_nonce:'fresh'};
    state.subscription={status:'active',current_plan:'pro',ai_daily_limit:10,ai_used_today:2,period_end:'2026-11-03',scheduled_plan:'plus',scheduled_at:'2026-11-03'};
    state.catalog={plans:FALLBACK_PLANS,checkout_available:true,receipt_mode:'npd_manual'};
    state.quote={quote_id:'q3',plan:'pro',amount_rub:100,change_type:'upgrade',checkout_available:true};
    renderSession();renderPlan();renderPlans();renderQuote();
    assert.equal($('#accountName').textContent,'Анна');
    assert.ok($('#accountPlan').innerHTML.includes('2 of 10 used today'));
    assert.ok($('#accountPlan').innerHTML.includes('Payment is required to renew'));
    assert.ok(!/[А-Яа-яЁё]/.test($('#accountPlan').innerHTML));
    assert.ok(!/[А-Яа-яЁё]/.test($('#accountPlans').innerHTML));
    assert.equal($('#accountCheckout').textContent,'Pay with YooKassa');
    assert.ok($('#quoteSummary').innerHTML.includes('remaining time'));
    notice('#paymentStatus','Оплата подтверждена. Доступ обновлён.');
    assert.equal($('#paymentStatus').textContent,'Payment confirmed. Your access has been updated.');
    assert.equal(receiptMarkup(manualOrder),'<small>Receipt pending</small>');
    assert.equal(tr('Новая непереведённая ошибка'),'Could not complete the request. Please try again.');
    api=async()=>({orders:[{order_id:'1',plan:'plus',amount_rub:390,receipt_email:'ivan@example.ru',description:'Доступ Нейропостинг',created_at:'2026-10-03'}]});
    await loadManualReceipts();
    assert.ok($('#adminReceiptList').innerHTML.includes('ivan@example.ru'));
    assert.ok($('#adminReceiptList').innerHTML.includes('Доступ Нейропостинг'));
    assert.ok($('#adminReceiptList').innerHTML.includes('My Tax receipt link'));
    api=async()=>({orders:[],refunds:[{order_id:'returned-order',plan:'plus',amount_rub:390,refunded_amount_rub:390,status:'refunded',receipt_email:'ivan@example.ru',npd_receipt_url:receiptUrl,created_at:'2026-10-03'}]});
    await loadManualReceipts();
    assert.ok($('#adminReceiptList').innerHTML.includes('Refunded'));
    assert.ok($('#adminReceiptList').innerHTML.includes('Reconcile the refund'));
    assert.ok(!$('#adminReceiptList').innerHTML.includes('data-receipt-order'));
    assert.ok($('#adminReceiptList').innerHTML.includes('View receipt'));
    assert.ok(receiptMarkup({...manualOrder,status:'refunded',npd_receipt_url:receiptUrl}).includes('View receipt'));
    window.NeyroPrefs.language='ru';renderPlan();
    assert.ok($('#accountPlan').innerHTML.includes('2 из 10 использовано сегодня'));
  })()`,context);
  assert.equal(requests[0].options.credentials,'same-origin');
  assert.equal(requests[0].options.headers['X-CSRF-Token'],'csrf-token');
  assert.equal(redirects.length,0);
  console.log('Account UI: payment state, CSRF, retries, NPD receipt status, safe links and admin-only receipt queue passed');
})().catch(error=>{console.error(error);process.exitCode=1});
