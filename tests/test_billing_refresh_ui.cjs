const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
function el(selector) {
  if (!elements.has(selector)) elements.set(selector,{value:'',textContent:'',innerHTML:'',disabled:false,hidden:false,dataset:{},
    addEventListener(){},setAttribute(){},classList:{toggle(){}},querySelectorAll(){return []},appendChild(){},remove(){}});
  return elements.get(selector);
}
let timer;
const context = vm.createContext({URL,URLSearchParams,AbortController,location:{search:''},window:{},assert,console,
  document:{querySelector:el,querySelectorAll:()=>[],createElement:()=>el('toast'),body:el('body')},
  setTimeout:(fn)=>{timer=fn;return 1},clearTimeout(){timer=null},
});
vm.runInContext(readFileSync('app/miniapp/app.js','utf8').replace(/^start\(\);$/m,''),context);
(async()=>{
 await vm.runInContext(`(async()=>{
   const original={id:4,username:'my_channel',instructions:'saved'};
   channel=original;boot={user:{has_access:true,is_admin:false},channels:[original]};
   drafts.set('4:instructions','unsaved');
   let calls=[];
   api=async(path,options)=>{calls.push({path,options});return path==='/bootstrap'
     ? {user:{has_access:false,is_admin:false},channels:[{id:4,instructions:'stale'}]}
     : {status:'inactive',current_plan:null,ai_daily_limit:0};};
   await loadBilling();
   assert.equal(boot.user.has_access,false);
   assert.equal(channel,original);assert.equal(drafts.get('4:instructions'),'unsaved');
   assert.ok($('#planCard').innerHTML.includes('Нет активного тарифа'));
   assert.equal($('#nav').hidden,true);
   assert.ok($('#billingAvailability').textContent.includes('обновлены'));
   assert.equal($('#billingRefresh').disabled,false);
   assert.deepEqual(calls.map(x=>x.path),['/bootstrap','/billing/subscription']);
   assert.ok(calls.every(x=>x.options.cache==='no-store' && x.options.signal));
   api=async(path)=>path==='/bootstrap'?{user:{is_admin:true,has_access:true},channels:[original]}
     :{status:'active',current_plan:'plus',ai_daily_limit:5,ai_used_today:1,period_end:'2026-11-03'};
   await loadBilling();
   assert.ok($('#planCard').innerHTML.includes('PLUS'));
   assert.ok(!$('#planCard').innerHTML.includes('<h3>Администратор</h3>'));
   assert.equal($('#nav').hidden,false);
   assert.equal(boot.user.has_access,true);
   api=async()=>{throw new Error('offline')};
   await loadBilling();
   assert.ok($('#billingAvailability').textContent.includes('offline'));
   assert.equal($('#billingRefresh').disabled,false);
   assert.equal(subscription.current_plan,'plus');
 })()`,context);
 const pending=vm.runInContext(`api=async(path,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('timeout'),{name:'AbortError'}))));loadBilling()`,context);
 assert.equal(el('#billingRefresh').disabled,true);
 assert.ok(timer);timer();await pending;
 assert.equal(el('#billingRefresh').disabled,false);
 assert.ok(el('#billingAvailability').textContent.includes('не ответил вовремя'));
 await vm.runInContext(`api=async(path)=>path==='/bootstrap'?{user:{has_access:true},channels:[]}:{status:'active',current_plan:'pro',ai_daily_limit:10};loadBilling()`,context);
 assert.ok(el('#planCard').innerHTML.includes('PRO'));
 console.log('Billing refresh: expiry, activation, paid admin plan, preserved drafts, timeout and retry passed');
})().catch(error=>{console.error(error);process.exitCode=1});
