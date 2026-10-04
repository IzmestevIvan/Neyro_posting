// Offline interaction/geometry checks. No live accounts, payments or publications.
const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||(fs.existsSync(chrome)?chrome:undefined)});
 const out=process.env.VISUAL_OUTPUT||'/private/tmp/neyro-mobile-layout';fs.mkdirSync(out,{recursive:true});
 const errors=[];let captures=0;
 try {
  const p=await browser.newPage({viewport:{width:390,height:844},hasTouch:true});
  p.on('pageerror',e=>errors.push(e.message));
  await p.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/miniapp/index.html','utf8')});
   if(path.startsWith('/static/')&&/^[\w.-]+$/.test(path.slice(8))){
    const file='app/miniapp/'+path.slice(8);
    if(fs.existsSync(file))return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file,'utf8').replace(/^start\(\);$/m,'')});
   }
   return route.fulfill({contentType:'text/javascript',body:''});
  });
  await p.goto('https://mobile.test/');
  await p.evaluate(()=>{
   channel={id:1,title:'Очень длинное название канала с новостями и материалами редакции',username:'channel_with_a_long_username',autopost:1,paused:0,pace:'6',delay_mode:'10-30',quality:'super',lang:'ru',tz:'America/Argentina/Buenos_Aires',window_start:8,window_end:23,digest_enabled:1,digest_time:'21:00',signature_text:'Подписаться',watermark:1};
   boot={channels:[channel],features:{business_mode:false},user:{tg_id:1,first_name:'Анна',has_access:true,is_admin:true,max_channels:5,daily_limit:10},bot_username:'test_bot',languages:{ru:'Русский',en:'English'}};
   api=async(path,opts={})=>{
    if(opts.method&&opts.method!=='GET')throw new Error('Unexpected mutation in layout check');
    if(path==='/bootstrap')return boot;
    if(path==='/billing/subscription')return {status:'active',current_plan:'pro',ai_daily_limit:10,ai_used_today:2};
    if(path.includes('/stats'))return {today:2,pending:1,remaining:8,sources:2,queued:0,mode:'автопостинг',blockers:[],posts_chart:[],subscribers_chart:[],waiting:{}};
    if(path.includes('/feed'))return [{id:1,source_title:'Длинный заголовок источника с новостями города',media:[],text_out:'Здесь будет текст публикации. '.repeat(20),created_at:new Date().toISOString(),url:'https://example.com/post'}];
    if(path.includes('/sources'))return [{id:1,kind:'tg',ref:'channel_with_a_long_username',title:'Источник с очень длинным заголовком',enabled:1}];
    return [];
   };
   $('#boot').hidden=true;$('#app').hidden=false;$('#openAdmin').hidden=false;
   fillOptions($('#langSelect'),[['ru','Русский'],['en','English']]);
   fillOptions($('#tzSelect'),[['America/Argentina/Buenos_Aires','America/Argentina/Buenos_Aires']]);
   fillOptions($('#digestTime'),[['21:00','21:00']]);
   enterNormalMode();
  });
  const frameCheck=async(mobile)=>{
   const problems=await p.evaluate(mobile=>{
    const issues=[];const rect=s=>document.querySelector(s).getBoundingClientRect();
    const app=rect('#app'),pane=rect('#workspaceScroll'),nav=rect('#nav'),head=rect('#app>.head');
    if(document.documentElement.scrollWidth>innerWidth)issues.push('document horizontal overflow');
    if(app.top < -1 ||app.bottom>innerHeight+1)issues.push('frame outside visible viewport');
    if(mobile&&pane.bottom>nav.top+1)issues.push('navigation covers content pane');
    if(pane.top<head.bottom-1)issues.push('header covers content pane');
    if(mobile&&head.height>85)issues.push('header proportions too tall');
    for(const el of document.querySelectorAll('#app button,#app input,#app select,#app textarea,#app summary')){
     if(!el.checkVisibility())continue;
     const r=el.getBoundingClientRect();if(!r.width||!r.height)continue;
     if(r.left < -1||r.right>innerWidth+1)issues.push(`offscreen ${el.id||el.className}`);
    }
    const avatar=document.querySelector('.post-avatar');
    if(avatar?.checkVisibility()){const r=avatar.getBoundingClientRect();if(Math.abs(r.width-r.height)>1||r.width>40)issues.push('stretched post avatar');}
    return issues;
   },mobile);assert.deepEqual(problems,[]);
  };
  // Scroll an actual target into view, then ensure a tap reaches that target.
  const reachable=async(selector)=>{
   const target=p.locator(selector).first();await target.scrollIntoViewIfNeeded();
   assert.equal(await target.evaluate(el=>{
    const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    const pane=document.querySelector('#workspaceScroll').getBoundingClientRect();
    return r.top>=pane.top-1&&r.bottom<=pane.bottom+1&&(hit===el||el.contains(hit));
   }),true,`obscured target: ${selector}`);
  };
  for(const [width,height] of [[320,568],[360,740],[390,844],[430,932],[768,1024],[812,375],[1280,900]]){
   await p.setViewportSize({width,height});
   await p.waitForFunction(()=>Math.abs(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-height'))-innerHeight)<2);
   for(const [language,theme] of [['ru','dark'],['en','light']]){
    await p.evaluate(({language,theme})=>{NeyroPrefs.setLanguage(language);NeyroPrefs.setTheme(theme);},{language,theme});
    for(const view of ['home','feed','settings']){
     await p.locator(`#nav [data-page="${view}"]`).click();
     await p.evaluate(async view=>{if(PAGE_LOADERS[view])await PAGE_LOADERS[view]();},view);
     await frameCheck(width<1080);
     if(view==='home')await reachable('#publishNow');
     if(view==='feed')await reachable('#feed [data-act="approve"]');
     if(view==='settings'){
      await p.evaluate(()=>document.querySelectorAll('#page-settings>details:not([hidden])').forEach(d=>d.open=true));
      await frameCheck(width<1080);
      await reachable('#deleteChannel');
     }
     await p.screenshot({path:`${out}/${width}x${height}-${language}-${theme}-${view}.png`});captures++;
    }
    // Preferences stay within the screen; Escape/outside click closes the disclosure.
    await p.locator('#appearancePreferences>summary').click();
    const box=await p.locator('#appearancePreferences .preferences').boundingBox();
    assert.ok(box.x>=0&&box.x+box.width<=width&&box.y+box.height<=height);
    await p.locator('[data-pref-language]').selectOption(language);
    await p.keyboard.press('Escape');
    assert.equal(await p.locator('#appearancePreferences').evaluate(el=>el.open),false);
    await p.locator('#appearancePreferences>summary').click();
    await p.locator('#nav [data-page="home"]').click();
    assert.equal(await p.locator('#appearancePreferences').evaluate(el=>el.open),false);
    assert.equal(await p.locator('#workspaceScroll').evaluate(el=>el.scrollTop),0);
   }
  }
  // Simulated keyboard viewport + safe-area offsets. Native IME behavior is a device check.
  await p.setViewportSize({width:390,height:380});
  await p.locator('#nav [data-page="settings"]').click();
  await p.locator('[data-field="instructions"]').fill('Несохранённый текст пользователя');
  await reachable('[data-field="instructions"]');
  await frameCheck(true);
  await p.evaluate(()=>document.documentElement.style.setProperty('--tg-content-safe-area-inset-bottom','34px'));
  await p.setViewportSize({width:390,height:844});
  await frameCheck(true);await reachable('#deleteChannel');
  assert.equal(await p.locator('[data-field="instructions"]').inputValue(),'Несохранённый текст пользователя');
  assert.deepEqual(errors,[]);
  console.log(`Mobile layout: ${captures} captures, 7 viewport sizes, dock separation, tap reachability, proportions, expanded forms, preferences and keyboard-sized viewport passed`);
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
