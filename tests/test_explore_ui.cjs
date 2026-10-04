// Full browser lifecycle using offline API fixtures, including promo activation/reload.
const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
(async()=>{
  const macChrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||(fs.existsSync(macChrome)?macChrome:undefined)});
  const out=process.env.VISUAL_OUTPUT||'/private/tmp/neyro-explore';
  fs.mkdirSync(out,{recursive:true});
  const errors=[],requests=[];
  let access=false,connected=false,userId=700,screenshots=0;
  const channelData={id:9,title:'Моя редакция',username:'my_editorial',autopost:0,paused:0,pace:'6',delay_mode:'10-30',quality:'balanced',lang:'ru',tz:'Europe/Moscow',window_start:8,window_end:23,digest_enabled:0,digest_time:'21:00',instructions:'',stopwords:'',signature_text:'',watermark:0};
  try {
    const page=await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=>{for(const id of [700,701]) localStorage.setItem(`neyro:welcome:v1:${id}`,'1');});
    await page.route('**/*',async route=>{
      const path=new URL(route.request().url()).pathname;
      if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/miniapp/index.html','utf8')});
      if(path.startsWith('/static/')&&/^[\w.-]+$/.test(path.slice(8))){
        const file='app/miniapp/'+path.slice(8);
        if(fs.existsSync(file))return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file,'utf8')});
      }
      if(!path.startsWith('/api/'))return route.fulfill({contentType:'text/javascript',body:''});
      const method=route.request().method();requests.push({path,method});
      let data={};
      if(path==='/api/bootstrap')data={channels:connected?[channelData]:[],features:{business_mode:false},user:{tg_id:userId,first_name:'Анна',has_access:access,is_admin:false,max_channels:3,daily_limit:10},languages:{ru:'Русский',en:'English'},timezones:['Europe/Moscow'],bot_username:'fixture_bot'};
      else if(path==='/api/billing/subscription')data={status:access?'active':'inactive',current_plan:null,ai_daily_limit:10,ai_used_today:0};
      else if(path==='/api/promo/redeem'){assert.equal(method,'POST');access=true;}
      else if(path==='/api/channels'&&method==='POST'){connected=true;data=channelData;}
      else if(path==='/api/channels/9/stats')data={today:0,pending:0,remaining:10,sources:0,queued:0,mode:'на проверку',blockers:[],posts_chart:[],subscribers_chart:[],waiting:{}};
      else data=[];
      return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto('https://explore.test/');
    await page.locator('#gate').waitFor({state:'visible'});
    // Access still requires a valid code; calling preview cannot bypass the gate.
    await page.evaluate(()=>enterExploreMode());
    assert.equal(await page.locator('#gate').isVisible(),true);
    await page.locator('#promoInput').fill('ABCD1234');
    await page.locator('#promoSubmit').click();
    await page.locator('#skipChannel').waitFor({state:'visible'});
    await page.waitForFunction(()=>!billingLoading);
    assert.equal(await page.locator('#onboarding').isVisible(),true);
    await page.locator('#skipChannel').click();
    await page.locator('#exploreHome').waitFor({state:'visible'});
    assert.equal(await page.locator('#nav button').count(),4);
    assert.equal(await page.locator('#channelSelect').isDisabled(),true);
    assert.equal(await page.locator('#homeConnected').isVisible(),false);
    // All real sections are reachable with no channel-scoped read or mutation.
    for(const view of ['sources','settings','feed','home']){
      await page.locator(`#nav [data-page="${view}"]`).click();
      assert.equal(await page.locator(`#page-${view}`).isVisible(),true);
    }
    await page.locator('#nav [data-page="settings"]').click();
    await page.locator('#page-settings details:visible').first().locator('summary').click();
    assert.equal(await page.locator('#quality').isDisabled(),true);
    await page.locator('#nav [data-page="feed"]').click();
    await page.locator('#showHistory').click();
    assert.equal(await page.locator('#history .workspace-empty').count(),1);
    await page.locator('#nav [data-page="home"]').click();
    await page.locator('#exploreHome [data-go="billing"]').click();
    await page.waitForFunction(()=>!billingLoading);
    assert.equal(await page.locator('#billingChangePlan').getAttribute('href'),'/account#plans');
    await page.evaluate(()=>{billingCheckedAt=0;return refreshBillingAutomatically();});
    await page.waitForFunction(()=>!billingLoading);
    await page.locator('#billingBack').click();
    assert.equal(await page.locator('#exploreHome').isVisible(),true);
    await page.reload();
    await page.locator('#exploreHome').waitFor({state:'visible'});
    // Visual checks cover both languages/themes and all page shapes at four widths.
    for(const width of [320,390,768,1280]){
      await page.setViewportSize({width,height:844});
      for(const [language,theme] of [['ru','light'],['ru','dark'],['en','light'],['en','dark']]){
        await page.evaluate(({language,theme})=>{NeyroPrefs.setLanguage(language);NeyroPrefs.setTheme(theme);},{language,theme});
        for(const view of ['home','sources','settings','feed']){
          await page.locator(`#nav [data-page="${view}"]`).click();
          await page.evaluate(()=>window.scrollTo(0,0));
          await page.screenshot({path:`${out}/${width}-${language}-${theme}-${view}.png`,fullPage:true});screenshots++;
          const problems=await page.evaluate(()=>{
            const issues=[];
            if(document.documentElement.scrollWidth>innerWidth)issues.push('page overflow');
            document.querySelectorAll('button,input,select,textarea').forEach(el=>{
              const r=el.getBoundingClientRect();if(!r.width||!r.height||!el.checkVisibility())return;
              if(r.left < -1||r.right>innerWidth+1)issues.push(`offscreen ${el.id||el.className}`);
              if(el.tagName==='BUTTON'&&r.height<42)issues.push(`small button ${el.id||el.className}`);
            });return issues;
          });
          assert.deepEqual(problems,[],`${width} ${language} ${theme} ${view}`);
        }
      }
    }
    assert.ok(!requests.some(r=>r.path.startsWith('/api/channels')),JSON.stringify(requests));
    // Each contextual connection link works, including in the read-only settings.
    for(const view of ['settings','sources','feed']){
      await page.locator(`#nav [data-page="${view}"]`).click();
      await page.locator(`#page-${view} [data-connect-channel]`).click();
      assert.equal(await page.locator('#onboarding').isVisible(),true);
      await page.locator('#skipChannel').click();
    }
    // Source entry panels can be inspected without enabling any mutation.
    await page.locator('#nav [data-page="sources"]').click();
    await page.locator('#sourceBatchOpen').click();
    assert.equal(await page.locator('#sourceBatchPanel').isVisible(),true);
    assert.equal(await page.locator('#sourceBatchInput').isDisabled(),true);
    // Connecting later restores real controls, renders statistics and removes preview.
    await page.locator('#addChannel').click();
    await page.locator('#firstChannelInput').fill('@my_editorial');
    await page.locator('#firstChannelSubmit').click();
    await page.locator('#homeConnected').waitFor({state:'visible'});
    assert.equal(await page.locator('#exploreHome').isVisible(),false);
    assert.equal(await page.locator('#channelSelect').isDisabled(),false);
    assert.equal(await page.locator('#quality').isDisabled(),false);
    assert.equal(await page.locator('#sourceInput').isDisabled(),false);
    assert.match(await page.locator('#saveStatus').textContent(),/saved immediately/);
    await page.reload();await page.locator('#homeConnected').waitFor({state:'visible'});
    // The remembered choice is scoped to the account, never shared between users.
    userId=701;connected=false;
    await page.reload();await page.locator('#onboarding').waitFor({state:'visible'});
    await page.locator('#skipChannel').click();
    // A channel connected elsewhere is adopted by refresh; preview controls recover.
    connected=true;
    await page.locator('#exploreHome [data-go="billing"]').click();
    await page.waitForFunction(()=>!billingLoading);
    await page.locator('#billingBack').click();
    assert.equal(await page.locator('#homeConnected').isVisible(),true);
    assert.equal(await page.locator('#quality').isDisabled(),false);
    assert.equal(await page.locator('#sourceInput').isDisabled(),false);
    access=false;
    await page.reload();await page.locator('#gate').waitFor({state:'visible'});
    await page.evaluate(()=>enterExploreMode());
    assert.equal(await page.locator('#gate').isVisible(),true);
    assert.deepEqual(requests.filter(r=>r.method!=='GET').map(r=>r.path),['/api/promo/redeem','/api/channels']);
    assert.deepEqual(errors,[]);
    console.log(`Explore UI: promo, skip, read-only navigation, saved choice, connection, remote connection, expiry and ${screenshots} visual checks passed`);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
