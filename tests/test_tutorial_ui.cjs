// Run with Playwright available through NODE_PATH. All traffic is local fixtures.
// No Telegram messages, AI generation, publishing or checkout calls are made.
const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
(async () => {
  const macChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || (fs.existsSync(macChrome) ? macChrome : undefined)});
  const failures=[];
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}});
    page.on('pageerror',error=>failures.push(error.message));
    await page.route('**/*',route=>{
      const path = new URL(route.request().url()).pathname;
      if(path==='/') return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/miniapp/index.html','utf8')});
      if(path.startsWith('/static/') && /^[\w.-]+$/.test(path.slice(8))) {
        const file='app/miniapp/'+path.slice(8);
        if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file,'utf8').replace(/^start\(\);$/m,'')});
      }
      return route.fulfill({contentType:'text/javascript',body:''});
    });
    await page.goto('https://tutorial.test/');
    await page.evaluate(()=>{
      boot={channels:[],features:{business_mode:false},user:{has_access:true,tg_id:1},bot_username:'fixture_bot'};
      window.requested=[];
      api=async (path,options={})=>{requested.push({path,method:options.method||'GET'});return [];};
      $('#boot').hidden=true;$('#app').hidden=false;showOnboarding();
      localStorage.clear();
      if(window.NeyroPrefs) window.NeyroPrefs.setLanguage('ru');
      else document.documentElement.lang='ru';
    });
    // Available before a channel exists; reopening resumes the last topic.
    await page.locator('#onboarding [data-tutorial]').click();
    assert.equal(await page.locator('#tour').isVisible(),true);
    assert.equal(await page.locator('#tourTopics option').count(),8);
    assert.equal(await page.locator('#app').evaluate(el=>el.inert),true);
    await page.locator('#tourNext').click();
    await page.locator('#tourNext').click();
    assert.equal(await page.locator('.tutorial-section').getAttribute('data-topic'),'sources');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#tour').isVisible(),false);
    assert.equal(await page.locator('#app').evaluate(el=>el.inert),false);
    assert.equal(await page.locator('#onboarding [data-tutorial]').evaluate(el=>el===document.activeElement),true);
    await page.locator('#openTutorial').click();
    assert.equal(await page.locator('.tutorial-section').getAttribute('data-topic'),'sources');
    await page.locator('.tutorial-action').click();
    assert.equal(await page.locator('#onboarding').isVisible(),true);
    assert.equal(await page.locator('#tour').isVisible(),false);
    // Gate remains accessible; the guide never grants access or mutates a channel.
    await page.evaluate(()=>{boot.user.has_access=false;showGate();});
    await page.locator('#gate [data-tutorial]').click();
    await page.locator('.tutorial-action').click();
    assert.equal(await page.locator('#gate').isVisible(),true);
    assert.deepEqual(await page.evaluate(()=>requested),[]);
    // Check all topics and both languages; changing language retains position.
    await page.locator('#openTutorial').click();
    const ruTitles=[];
    for(let index=0;index<8;index++) {
      await page.locator('#tourTopics').selectOption(String(index));
      ruTitles.push(await page.locator('#tourTitle').textContent());
      assert.equal(await page.locator('.tutorial-step-list li').count(),3);
    }
    assert.equal(new Set(ruTitles).size,8);
    await page.evaluate(()=>{
      if(window.NeyroPrefs) window.NeyroPrefs.setLanguage('en');
      else {document.documentElement.lang='en';document.dispatchEvent(new CustomEvent('neyro:preferences'));}
    });
    assert.equal(await page.locator('#tourTitle').textContent(),'If a post has not appeared');
    await page.locator('#tourTopics').selectOption('6');
    const english = await page.locator('#tourTrack').textContent();
    assert.ok(english.includes('00:00 UTC'));
    assert.ok(english.includes('All your channels'));
    assert.ok(english.includes('no automatic charges'));
    assert.ok(!/[А-Яа-яЁё]/.test(english));
    await page.locator('#tourTopics').selectOption('4');
    assert.match(await page.locator('#tourTrack').textContent(),/prepares and sends one fresh post immediately/);
    // Escape does not accumulate keyboard listeners on repeated openings.
    for(let n=0;n<3;n++){await page.keyboard.press('Escape');await page.locator('#openTutorial').click();}
    await page.locator('#tourRestart').click();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tourTopics').inputValue(),'1');
    // Tab stays inside the modal and does not strand focus on hidden content.
    await page.locator('#tourSkip').focus();await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('#tourNext').evaluate(el=>el===document.activeElement),true);
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#tourSkip').evaluate(el=>el===document.activeElement),true);
    // Completion is persisted, and a completed guide reopens at the beginning.
    await page.locator('#tourTopics').selectOption('7');await page.locator('#tourNext').click();
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('neyro:tutorial_v2')).complete),true);
    await page.locator('#openTutorial').click();
    assert.equal(await page.locator('#tourTopics').inputValue(),'0');
    // Read-only section shortcuts work with an active channel.
    await page.evaluate(()=>{boot.user.has_access=true;channel={id:7};boot.channels=[channel];});
    await page.locator('#tourTopics').selectOption('7');await page.locator('.tutorial-action').click();
    assert.equal(await page.locator('#page-feed').isVisible(),true);
    assert.equal(await page.locator('#history').isVisible(),true);
    assert.ok((await page.evaluate(()=>requested)).every(r=>r.method==='GET'));
    // Invalid persisted data is safely bounded, including blocked storage.
    assert.equal(await page.evaluate(()=>{localStorage.setItem('neyro:tutorial_v2','{"index":999}');return tutorialProgress().index;}),7);
    assert.equal(await page.evaluate(()=>{localStorage.setItem('neyro:tutorial_v2','broken');return tutorialProgress().index;}),0);
    // Language switching translates interface labels, never the user's content or drafts.
    await page.evaluate(async()=>{
      channel={id:7,quality:'super',lang:'ru',delay_mode:'instant',pace:'6',username:'fixture',logo_configured:false};
      boot.channels=[channel];
      const safeApi=api;
      api=async(path,options={})=>{
        requested.push({path,method:options.method||'GET'});
        if(path.includes('/feed'))return [{id:17,media:[],text_out:'Текст пользователя без перевода',source_title:'Источник пользователя',created_at:new Date().toISOString()}];
        if(path.includes('/history'))return [{id:17,status:'uncertain',preview:'Сохранённый текст',created_at:new Date().toISOString(),delivery_receipts:[{message_ids:[1]}]}];
        return [];
      };
      $('#quality').value='2';
      $('[data-field="instructions"]').value='Мой незавершённый текст';
      $('[data-field="signature_text"]').value='Моя подпись';
      renderChips('#delayChips',DELAY_LABELS,'instant',()=>{});
      renderChips('#paceChips',PACE_LABELS,'6',()=>{});
      page='settings';
      NeyroPrefs.setLanguage('en');
      refreshInterfaceLanguage();
      renderStats({today:1,pending:1,remaining:4,sources:1,waiting:{},posts_chart:[],subscribers_chart:[],mode:'автопостинг'});
      await loadFeed();await loadHistory();
    });
    assert.equal(await page.locator('#qualityTitle').textContent(),'Superposting');
    assert.equal(await page.locator('#delayChips [data-key="instant"]').textContent(),'Instant');
    assert.equal(await page.locator('[data-field="instructions"]').inputValue(),'Мой незавершённый текст');
    assert.equal(await page.locator('[data-field="signature_text"]').inputValue(),'Моя подпись');
    assert.equal(await page.evaluate(()=>channel.lang),'ru');
    assert.ok((await page.locator('#feed').textContent()).includes('Текст пользователя без перевода'));
    assert.ok((await page.locator('#feed').textContent()).includes('Rewrite'));
    assert.ok((await page.locator('#history').textContent()).includes('Verify delivery in channel'));
    assert.ok((await page.locator('#history').textContent()).includes('Verified: nothing appeared'));
    assert.ok((await page.locator('#history').textContent()).includes('Сохранённый текст'));
    assert.ok(!/[А-Яа-яЁё]/.test(await page.locator('#statsGrid').textContent()));
    assert.equal(await page.locator('#publishNow').textContent(),'Publish now ');
    await page.evaluate(()=>{NeyroPrefs.setLanguage('ru');refreshInterfaceLanguage();});
    assert.equal(await page.locator('#qualityTitle').textContent(),'Суперпостинг');
    assert.equal(await page.locator('[data-field="instructions"]').inputValue(),'Мой незавершённый текст');
    assert.ok((await page.evaluate(()=>requested)).every(r=>r.method==='GET'));
    assert.deepEqual(failures,[]);
    console.log('Tutorial UI: entry, resume, 8 bilingual topics, focus, completion and read-only navigation passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
