// Offline browser proof: plan choice is navigation; access updates without a refresh button.
const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||(fs.existsSync(chrome)?chrome:undefined)});
 const out=process.env.VISUAL_OUTPUT||'/private/tmp/neyro-plan-change';fs.mkdirSync(out,{recursive:true});
 let active=true,plan='plus',accountAuthenticated=true;const errors=[],requests=[];
 const sub=()=>({status:active?'active':'inactive',current_plan:active?plan:null,ai_daily_limit:plan==='plus'?5:10,ai_used_today:2});
 const plans=[['plus',390,5],['pro',490,10],['expert',590,15],['creator',990,25],['unlimited',1490,50]].map(([code,price_rub,ai_daily_limit])=>({code,name:code.toUpperCase(),price_rub,ai_daily_limit}));
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},locale:'ru-RU',colorScheme:'dark'});
  context.on('page',page=>page.on('pageerror',e=>errors.push(e.message)));
  await context.addInitScript(()=>{try{localStorage.setItem('neyro:welcome:v1:820','1');localStorage.setItem('neyro:explore:820','1');}catch{/* about:blank popup has no origin yet */}});
  await context.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==='/'||path==='/account')return route.fulfill({contentType:'text/html',body:fs.readFileSync(`app/miniapp/${path==='/'?'index':'account'}.html`,'utf8')});
   if(path.startsWith('/static/')&&/^[\w.-]+$/.test(path.slice(8))){
    const file='app/miniapp/'+path.slice(8);
    if(fs.existsSync(file))return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file,'utf8')});
   }
   if(!path.startsWith('/api/'))return route.fulfill({contentType:'text/javascript',body:''});
   requests.push({path,method:route.request().method()});
   const data=path==='/api/bootstrap'?{channels:[],features:{business_mode:false},user:{tg_id:820,first_name:'Анна',has_access:active,is_admin:false,max_channels:3,daily_limit:10},languages:{ru:'Русский',en:'English'},timezones:['Europe/Moscow'],bot_username:'fixture_bot'}
    :path==='/api/billing/subscription'?sub()
    :path==='/api/account/session'?{authenticated:accountAuthenticated,telegram_client_id:'12345',login_nonce:'fixture',csrf_token:'fixture',user:{first_name:'Анна',is_admin:false}}
    :path==='/api/billing/catalog'?{provider:'yookassa',plans,checkout_available:false,receipt_mode:'npd_manual'}
    :path==='/api/billing/orders'?{orders:[]}:[];
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  const p=await context.newPage();await p.clock.install();
  await p.goto('https://plan.test/');await p.locator('#exploreHome').waitFor({state:'visible'});
  await p.locator('#exploreHome [data-go="billing"]').click();await p.waitForFunction(()=>!billingLoading);
  assert.equal(await p.locator('#billingRefresh').count(),0);
  assert.equal(await p.locator('#billingChangePlan').innerText(),'Сменить тариф');
  assert.match(await p.locator('#planCard').innerText(),/PLUS/);
  const popupEvent=p.waitForEvent('popup');await p.locator('#billingChangePlan').click();
  const account=await popupEvent;await account.waitForLoadState();
  assert.equal(account.url(),'https://plan.test/account#plans');
  await account.locator('[data-plan]').last().waitFor({state:'visible'});
  assert.equal(await account.locator('[data-plan]').count(),5);
  assert.equal(await account.locator('#accountPlans [data-plan="pro"]').isEnabled(),true);
  await account.close();
  accountAuthenticated=false;
  const login=await context.newPage();await login.goto('https://plan.test/account#plans');
  await login.waitForFunction(()=>document.activeElement===document.querySelector('#telegramLogin'));
  assert.equal(await login.locator('#telegramLogin').isEnabled(),true);
  const loginBounds=await login.locator('#telegramLogin').boundingBox();
  assert.ok(loginBounds.y>=0 && loginBounds.y+loginBounds.height<844);
  await login.close();accountAuthenticated=true;
  // Existing access can change in another session; the visible app polls every minute.
  plan='pro';await p.clock.runFor(61000);
  await p.waitForFunction(()=>subscription?.current_plan==='pro'&&!billingLoading);
  assert.match(await p.locator('#planCard').innerText(),/PRO/);
  assert.equal(await p.locator('#billingAvailability').isVisible(),false);
  // Returning from the web account triggers a read immediately, without waiting a minute.
  plan='plus';await p.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await p.waitForFunction(()=>subscription?.current_plan==='plus'&&!billingLoading);
  await p.locator('#billingBack').click();
  active=false;await p.clock.runFor(61000);
  await p.locator('#gate').waitFor({state:'visible'});
  active=true;await p.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await p.locator('#exploreHome').waitFor({state:'visible'});
  // Layout and translation remain usable at narrow widths and in both themes.
  await p.locator('#exploreHome [data-go="billing"]').click();
  let captures=0;
  for(const [width,height] of [[320,568],[390,844],[812,375],[1280,900]]){
   await p.setViewportSize({width,height});
   for(const [language,theme] of [['ru','light'],['ru','dark'],['en','light'],['en','dark']]){
    await p.evaluate(({language,theme})=>{NeyroPrefs.setLanguage(language);NeyroPrefs.setTheme(theme);},{language,theme});
    const link=p.locator('#billingChangePlan');await link.scrollIntoViewIfNeeded();
    assert.equal(await link.innerText(),language==='ru'?'Сменить тариф':'Change plan');
    assert.equal(await link.evaluate(el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.height>=44&&r.left>=0&&r.right<=innerWidth&&(hit===el||el.contains(hit));}),true);
    await p.screenshot({path:`${out}/${width}-${language}-${theme}.png`});captures++;
   }
  }
  // Native Telegram clients use openLink without requesting or changing a plan.
  const t=await context.newPage();
  await t.addInitScript(()=>{window.opened=[];window.Telegram={WebApp:{initData:'fixture',ready(){},expand(){},onEvent(){},openLink(url){window.opened.push(url)}}};});
  await t.goto('https://plan.test/');await t.locator('#exploreHome').waitFor({state:'visible'});
  await t.locator('#exploreHome [data-go="billing"]').click();await t.waitForFunction(()=>!billingLoading);
  const before=requests.length;await t.locator('#billingChangePlan').click();
  assert.deepEqual(await t.evaluate(()=>window.opened),['https://plan.test/account#plans']);
  assert.equal(requests.length,before);
  assert.ok(requests.every(r=>r.method==='GET'));
  assert.deepEqual(errors,[]);
  console.log(`Plan change: web choice, Telegram external link, automatic polling/resume, access recovery, no mutations and ${captures} layouts passed`);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
