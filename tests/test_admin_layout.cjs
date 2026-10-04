// Rich admin fixtures: verify borders, field alignment and tap reachability, without mutations.
const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||(fs.existsSync(chrome)?chrome:undefined)});
 const out=process.env.VISUAL_OUTPUT||'/private/tmp/neyro-admin-layout';fs.mkdirSync(out,{recursive:true});
 const errors=[];let captures=0;
 try{
  const p=await browser.newPage({viewport:{width:390,height:844},locale:'ru-RU'});
  p.on('pageerror',e=>errors.push(e.message));
  await p.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/miniapp/index.html','utf8')});
   if(path.startsWith('/static/')&&/^[\w.-]+$/.test(path.slice(8))){const file='app/miniapp/'+path.slice(8);if(fs.existsSync(file))return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file,'utf8').replace(/^start\(\);$/m,'')});}
   return route.fulfill({contentType:'text/javascript',body:''});
  });
  await p.goto('https://admin.test/');
  await p.evaluate(()=>{
   const now=new Date().toISOString();
   boot={user:{id:1,is_admin:true,has_access:true},channels:[],features:{business_mode:false}};
   window.adminRequests=[];
   api=async(path,options={})=>{
    adminRequests.push({path,method:options.method||'GET'});
    if(options.method&&options.method!=='GET')throw Error('Admin layout must not write');
    if(path==='/bootstrap')return boot;
    if(path==='/billing/subscription')return {status:'active',current_plan:'pro',ai_daily_limit:10};
    if(path==='/admin/api-keys')return {keys:[{id:1,label:'Ключ редакции — проект с длинным названием',enabled:true,last_error:'Quota temporarily unavailable; retry scheduled',last_success_at:now},{id:2,label:'Резерв редакции',enabled:false}],limit:20,server_key_configured:true};
    if(path==='/admin/overview')return {totals:{posts:120000,published:20000,ai_today:809},users:[{tg_id:1234567890,first_name:'Пользователь с длинным именем и фамилией',username:'long_account_username',channels:4,daily_limit:100000,max_channels:120,plan:'custom',access_until:now}],channels:[{title:'Редакция с очень длинным названием канала',owner_id:1234567890,sources:42,autopost:true}]};
    if(path==='/admin/promo')return [{code:'ABCD1234',plan:'custom',daily_limit:1000,max_channels:10,days:365,assigned_to:1234567890,expires_at:now,note:'Длинный комментарий для владельца канала'},{code:'EFGH5678',plan:'pro',daily_limit:500,max_channels:3,days:30,used_by:1234567890,first_name:'Анна'}];
    if(path==='/admin/monitoring')return {
     system:{cpu:99.5,ram_percent:80,ram_used:1.6,ram_total:2,disk_percent:70,disk_used:17,disk_total:25,process_mb:180,activity:'Проверка источников',last_error:'Длинное описание ошибки с адресом example.com/source/channel'},
     capacity:{users:3000,channels:12000,active_channels:10000,database_bytes:1000000000},queue:{new:2000,pending:300,ready:44,publishing:10,attention:120},
     api_load:{active:4,concurrency:5,waiting:10,started_at:now,windows:{'15':{requests:100,errors:20,quota:3,server:4,transport:5,interrupted:8,fallbacks:15,average_ms:1234},'60':{requests:400,errors:50,fallbacks:40}},series:Array.from({length:15},(_,i)=>({requests:i+2,errors:i%3}))},
     events:[{title:'Повторная проверка доставки',explanation:'Проверьте доставку сообщения в Telegram-канале.',time:now,detail:'Network timeout while receiving confirmation',channel:{title:'Канал редакции'}}],
     channels:[{id:9,title:'Канал редакции',status:'Подключён, ожидает новые материалы',last_published:now,checked_at:now}]};
    return [];
   };
   $('#boot').hidden=true;$('#app').hidden=false;$('#openAdmin').hidden=false;
   fillOptions($('#promoPlan'),Object.entries(ACCESS_PLANS).map(([key,p])=>[key,p.name]));
   $('#openAdmin').click();
  });
  await p.locator('.client-access').waitFor({state:'visible'});
  for(const [width,height] of [[320,568],[390,844],[768,1024],[1280,900]]){
   await p.setViewportSize({width,height});
   for(const [language,theme] of [['ru','light'],['ru','dark'],['en','light'],['en','dark']]){
    await p.evaluate(async({language,theme})=>{NeyroPrefs.setLanguage(language);NeyroPrefs.setTheme(theme);await loadAdmin();await refreshAdminMonitoring();document.querySelectorAll('#page-admin>details').forEach(el=>el.open=true);},{language,theme});
    const issues=await p.evaluate(()=>{
     const issues=[];const rect=el=>el.getBoundingClientRect();
     if(document.documentElement.scrollWidth>innerWidth)issues.push('horizontal overflow');
     for(const el of document.querySelectorAll('#page-admin .card,#page-admin .adminrow,#page-admin .stat,#page-admin input,#page-admin select,#page-admin button')){
      if(!el.checkVisibility())continue;const r=rect(el);
      if(r.left<0||r.right>innerWidth+1)issues.push('offscreen '+(el.id||el.className));
      if(el.tagName==='BUTTON'&&r.height<44)issues.push('short button');
     }
     for(const grid of document.querySelectorAll('#page-admin .access-grid')){
      const labels=[...grid.querySelectorAll('label')];
      labels.forEach(label=>{const field=label.querySelector('input,select');if(Math.abs(rect(label).width-rect(field).width)>1)issues.push('narrow field');});
      for(let i=0;i<labels.length;i+=2){if(labels[i+1]&&Math.abs(rect(labels[i].querySelector('input,select')).top-rect(labels[i+1].querySelector('input,select')).top)>1)issues.push('unaligned fields');}
     }
     const promo=rect(document.querySelector('.promo-form'));
     if(rect(document.querySelector('#promoCount')).right>promo.right+1)issues.push('promo field breaks card padding');
     for(const grid of document.querySelectorAll('#page-admin .grid')){
      if(getComputedStyle(grid).borderWidth!=='0px')issues.push('double metric frame');
     }
     for(const value of document.querySelectorAll('#page-admin .stat b')){
      if(value.scrollWidth>value.clientWidth+1)issues.push('overflowing metric');
      if(rect(value).height>parseFloat(getComputedStyle(value).lineHeight)+1)issues.push('wrapped metric');
     }
     for(const el of document.querySelectorAll('#page-admin>.card,#adminUsers>.adminrow,#promoList>.adminrow')){
      if(getComputedStyle(el).borderRadius!=='16px')issues.push('inconsistent card radius');
     }
     for(const el of document.querySelectorAll('#apiKeyList .key-card')){
      if(getComputedStyle(el).borderLeftWidth!=='0px'||getComputedStyle(el).borderRadius!=='0px')issues.push('nested key frame');
     }
     return issues;
    });assert.deepEqual(issues,[],`${width} ${language} ${theme}`);
    for(const [name,selector] of [['overview','#adminTotals'],['api','#apiLoad'],['keys','#apiKeyList'],['promo','#promoPlan'],['users','.client-access']]){
     await p.locator(selector).scrollIntoViewIfNeeded();
     await p.screenshot({path:`${out}/${width}-${language}-${theme}-${name}.png`});captures++;
    }
    const save=p.locator('[data-save-client]');await save.scrollIntoViewIfNeeded();
    assert.equal(await save.evaluate(el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===el||el.contains(hit);}),true);
   }
  }
  assert.ok(await p.evaluate(()=>adminRequests.every(r=>r.method==='GET')));
  assert.deepEqual(errors,[]);
  console.log(`Admin layout: aligned fields, consistent frames, populated keys/codes/metrics, tap reachability and ${captures} captures passed`);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
