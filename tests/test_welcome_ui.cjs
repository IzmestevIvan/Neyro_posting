// First-visit animation and optional guide, using offline API fixtures only.
const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||(fs.existsSync(chrome)?chrome:undefined)});
 const out=process.env.VISUAL_OUTPUT||'/private/tmp/neyro-welcome';fs.mkdirSync(out,{recursive:true});
 let userId=810,access=true,connected=false,captures=0;
 const channelData={id:9,title:"Existing channel",username:"existing",quality:"balanced",lang:"ru",tz:"Europe/Moscow",pace:"6",delay_mode:"10-30",autopost:0,paused:0,window_start:8,window_end:23,digest_enabled:0,digest_time:"21:00"};
 const errors=[],requests=[];
 try{
  const p=await browser.newPage({viewport:{width:390,height:844},locale:'ru-RU',colorScheme:'dark'});
  p.on('pageerror',e=>errors.push(e.message));
  await p.route('**/*',route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/miniapp/index.html','utf8')});
   if(path.startsWith('/static/')&&/^[\w.-]+$/.test(path.slice(8))){
    const file='app/miniapp/'+path.slice(8);
    if(fs.existsSync(file))return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file,'utf8')});
   }
   if(!path.startsWith('/api/'))return route.fulfill({contentType:'text/javascript',body:''});
   requests.push({path,method:route.request().method()});
   const data=path==='/api/bootstrap'?{channels:connected?[channelData]:[],features:{business_mode:false},user:{id:userId,first_name:'Анна',has_access:access,is_admin:false,max_channels:3,daily_limit:10},languages:{ru:'Русский',en:'English'},timezones:['Europe/Moscow'],bot_username:'fixture_bot'}
    :path==='/api/billing/subscription'?{status:access?'active':'inactive',current_plan:null,ai_daily_limit:10,ai_used_today:0}
    :path.endsWith('/stats')?{today:0,pending:0,remaining:10,sources:0,queued:0,mode:'review',blockers:[],posts_chart:[],subscribers_chart:[],waiting:{}}:[];
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await p.goto('https://welcome.test/');
  await p.locator('#welcome').waitFor({state:'visible'});
  assert.equal(await p.locator('#tour').isVisible(),false);
  assert.equal(await p.locator('#welcomeStart').isEnabled(),true);
  assert.equal(await p.locator('#app').evaluate(el=>el.inert),true);
  assert.equal(await p.locator('.welcome-steps li').count(),4);
  assert.equal(await p.evaluate(()=>localStorage.getItem('neyro:tutorial_v2')),null);
  // Count the first display: closing Telegram without pressing a button must not repeat it.
  assert.equal(await p.evaluate(()=>localStorage.getItem('neyro:welcome:v1:810')),'1');
  assert.equal(await p.evaluate(()=>localStorage.getItem('neyro:welcome:v1:guest')),null);
  await p.reload();await p.locator('#onboarding').waitFor({state:'visible'});
  assert.equal(await p.locator('#welcome').isVisible(),false);
  await p.evaluate(()=>openWelcome());
  // Pausing keeps the current stage; completion never advances into the app by itself.
  await p.locator('#welcomeMotion').click();
  const paused=await p.locator('#welcome').getAttribute('data-step');
  await p.waitForTimeout(1800);
  assert.equal(await p.locator('#welcome').getAttribute('data-step'),paused);
  await p.locator('#welcomeMotion').click();
  await p.waitForFunction(()=>welcomeFinished,null,{timeout:9000});
  assert.equal(await p.locator('#welcome').getAttribute('data-step'),'3');
  assert.equal(await p.locator('#welcome').isVisible(),true);
  assert.equal(await p.locator('#welcome').getAttribute('data-playing'),'false');
  await p.locator('#welcomeMotion').click();
  assert.equal(await p.locator('#welcome').getAttribute('data-step'),'0');
  await p.locator('#welcomeMotion').click();
  // Focus is contained, with a working Escape exit and account-scoped acknowledgement.
  await p.locator('#welcomeClose').focus();await p.keyboard.press('Shift+Tab');
  assert.equal(await p.locator('#welcomeLearn').evaluate(el=>el===document.activeElement),true);
  await p.keyboard.press('Tab');
  assert.equal(await p.locator('#welcomeClose').evaluate(el=>el===document.activeElement),true);
  await p.keyboard.press('Escape');
  assert.equal(await p.locator('#welcome').isVisible(),false);
  assert.equal(await p.locator('#app').evaluate(el=>el.inert),false);
  assert.equal(await p.locator('#onboarding').isVisible(),true);
  assert.equal(await p.evaluate(()=>localStorage.getItem('neyro:welcome:v1:810')),'1');
  await p.reload();await p.locator('#onboarding').waitFor({state:'visible'});
  assert.equal(await p.locator('#welcome').isVisible(),false);
  assert.equal(await p.locator('#tour').isVisible(),false);
  // A learn button opens the complete existing guide; the skip button opens workspace.
  assert.equal(await p.locator('#onboarding [data-tutorial]').textContent(),'Изучить приложение');
  await p.locator('#onboarding [data-tutorial]').click();
  assert.equal(await p.locator('#tourTopics option').count(),8);
  await p.locator('#tourTopics').selectOption('3');await p.keyboard.press('Escape');
  const progress=await p.evaluate(()=>localStorage.getItem('neyro:tutorial_v2'));
  await p.locator('#skipChannel').click();
  assert.equal(await p.locator('#exploreHome').isVisible(),true);
  assert.equal(await p.locator('#tour').isVisible(),false);
  // Another account sees the introduction; choosing Learn closes it before the guide.
  userId=811;await p.reload();await p.locator('#welcome').waitFor({state:'visible'});
  await p.locator('#welcomeLearn').click();
  assert.equal(await p.locator('#welcome').isVisible(),false);
  assert.equal(await p.locator('#tour').isVisible(),true);
  assert.equal(await p.locator('#tourTopics').inputValue(),'3');
  assert.equal(await p.evaluate(()=>localStorage.getItem('neyro:tutorial_v2')),progress);
  await p.keyboard.press('Escape');
  // 4 viewports, RU/EN, light/dark, four phases. Freeze animation for stable captures.
  for(const [width,height] of [[320,568],[390,844],[812,375],[1280,900]]){
   await p.setViewportSize({width,height});
   for(const [language,theme] of [['ru','light'],['ru','dark'],['en','light'],['en','dark']]){
    await p.evaluate(({language,theme})=>{NeyroPrefs.setLanguage(language);NeyroPrefs.setTheme(theme);openWelcome();pauseWelcomeAnimation();},{language,theme});
    for(let step=0;step<4;step++){
     await p.evaluate(step=>{welcomeStep=step;renderWelcomeMotion();},step);
     await p.waitForTimeout(1300);
     await p.screenshot({path:`${out}/${width}-${language}-${theme}-${step}.png`});captures++;
     const issues=await p.evaluate(()=>{
      const issues=[];const dialog=$('.welcome-shell').getBoundingClientRect(),foot=$('.welcome-foot').getBoundingClientRect();
      if(dialog.left<0||dialog.right>innerWidth||dialog.top<0||dialog.bottom>innerHeight+1)issues.push('dialog outside viewport');
      if(foot.bottom>innerHeight+1)issues.push('footer outside viewport');
      const body=$('.welcome-body');if(body.scrollWidth>body.clientWidth)issues.push('body horizontal overflow');
      if(innerHeight>=568 && $('.welcome-steps').getBoundingClientRect().bottom>body.getBoundingClientRect().bottom)issues.push('four steps need scrolling');
      for(const el of $('#welcome').querySelectorAll('button')){if(!el.checkVisibility())continue;const r=el.getBoundingClientRect();if(r.height<44||r.left<0||r.right>innerWidth)issues.push('button size/bounds');}
      return issues;
     });assert.deepEqual(issues,[],`${width} ${language} ${theme} ${step}`);
    }
    await p.locator('.welcome-steps li').last().scrollIntoViewIfNeeded();
    await p.locator('#welcomeStart').click();
    assert.equal(await p.locator('#welcome').isVisible(),false);
   }
  }
  // Reduced motion is respected at first render and when changed live.
  await p.emulateMedia({reducedMotion:'reduce'});
  await p.evaluate(()=>openWelcome());
  assert.equal(await p.locator('#welcome').getAttribute('data-step'),'3');
  assert.equal(await p.locator('#welcomeMotion').isVisible(),false);
  assert.equal(await p.evaluate(()=>welcomeTimer),null);
  assert.equal(await p.locator('.engine-ring').evaluate(el=>getComputedStyle(el).animationName),'none');
  await p.emulateMedia({reducedMotion:'no-preference'});
  assert.equal(await p.locator('#welcome').getAttribute('data-playing'),'false');
  await p.locator('#welcomeMotion').click();
  await p.emulateMedia({reducedMotion:'reduce'});
  await p.waitForFunction(()=>welcomeMotionPreference.matches && welcomeStep===3 && !welcomePlaying);
  assert.equal(await p.locator('#welcome').getAttribute('data-step'),'3');
  assert.equal(await p.locator('#welcome').getAttribute('data-playing'),'false');
  // Introduction never unlocks access. Blocked storage does not prevent closing it.
  await p.locator('#welcomeClose').click();
  userId=812;access=false;await p.reload();await p.locator('#welcome').waitFor({state:'visible'});
  await p.evaluate(()=>{Storage.prototype.setItem=()=>{throw new Error('blocked')};});
  await p.locator('#welcomeStart').click();
  assert.equal(await p.locator('#gate').isVisible(),true);
  assert.equal(await p.locator('#tour').isVisible(),false);
  assert.ok(requests.every(r=>r.method==='GET' && !r.path.includes('/channels')));
  // Real bootstrap uses user.id. Existing channels suppress welcome even on a fresh device.
  userId=813;connected=true;access=true;
  await p.addInitScript(()=>{Storage.prototype.getItem=()=>{throw new Error('blocked')};Storage.prototype.setItem=()=>{throw new Error('blocked')};});
  await p.reload();await p.locator('#homeConnected').waitFor({state:'visible'});
  await p.waitForFunction(()=>!billingLoading);
  assert.equal(await p.locator('#welcome').isVisible(),false);
  await p.evaluate(()=>openWelcome());
  assert.equal(await p.locator('#welcome').isVisible(),false);
  await p.reload();await p.locator('#homeConnected').waitFor({state:'visible'});
  assert.equal(await p.locator('#welcome').isVisible(),false);
  assert.ok(requests.every(r=>r.method==='GET'));
  assert.deepEqual(errors,[]);
  console.log(`Welcome UI: first visit, manual guide, replay/pause, focus, persistence, reduced motion, access gate, blocked storage and ${captures} phase captures passed`);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
