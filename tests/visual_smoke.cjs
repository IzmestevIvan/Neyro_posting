// Offline browser fixtures: all requests intercepted, no production data or payments.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const macChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || (fs.existsSync(macChrome) ? macChrome : undefined)});
  const out = process.env.VISUAL_OUTPUT || '/private/tmp/neyro-visual';
  fs.mkdirSync(out, {recursive:true});
  const failures = [];
  let screenshots = 0;
  const routeAssets = async (page, account = false) => {
    // The host browser can be English/dark. Baseline fixtures are explicitly RU/light.
    await page.addInitScript(() => {
      localStorage.setItem('neyro:language','ru');
      localStorage.setItem('neyro:theme','light');
    });
    page.on('pageerror', error => failures.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      const name = url.pathname;
      if (name === '/' || name === '/account') return route.fulfill({contentType:'text/html', body:fs.readFileSync(`app/miniapp/${account ? 'account' : 'index'}.html`, 'utf8')});
      const file = {'/static/style.css':'style.css','/static/app.js':'app.js','/static/account.js':'account.js','/static/preferences.js':'preferences.js'}[name];
      if (file) return route.fulfill({contentType:file.endsWith('.css') ? 'text/css' : 'text/javascript', body:fs.readFileSync(`app/miniapp/${file}`, 'utf8').replace(/^(start|startAccount)\(\);$/m, '')});
      return route.fulfill({contentType:'text/javascript', body:''});
    });
  };
  const capture = async (page, width, name) => {
    await page.evaluate(() => { document.querySelectorAll('.toast').forEach(el => el.remove()); window.scrollTo(0,0); });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({path:path.join(out, `${width}-${name}.png`), fullPage:!name.includes('tour-')});
    screenshots++;
    const problems = await page.evaluate(() => {
      const issues = [];
      if (document.documentElement.scrollWidth > innerWidth) issues.push('page overflow');
      for (const el of document.querySelectorAll('button,input,select,textarea,.post,.ad,.adminrow,.tariff')) {
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height || !el.checkVisibility()) continue;
        if (r.left < -1 || r.right > innerWidth + 1) issues.push(`offscreen ${el.id || el.className || el.tagName}`);
        if (el.tagName === 'BUTTON' && r.height < 42 && !el.closest('.dots')) issues.push(`small button ${el.id || el.textContent}`);
      }
      return issues;
    });
    failures.push(...problems.map(problem => `${width}px ${name}: ${problem}`));
  };
  const preference = async (page, language, theme) => {
    await page.evaluate(({language,theme}) => {
      window.NeyroPrefs.setLanguage(language);
      window.NeyroPrefs.setTheme(theme);
    },{language,theme});
    await page.waitForFunction(({language,theme}) => document.documentElement.lang === language && document.documentElement.dataset.theme === theme,{language,theme});
  };
  try {
    for (const width of [320,390,768,1280]) {
      const page = await browser.newPage({viewport:{width,height:844},deviceScaleFactor:1});
      await routeAssets(page);
      await page.goto('https://visual.test/');
      await page.evaluate(() => {
        channel={id:1,title:'Мой канал — длинное название для проверки',username:'test',autopost:1,paused:0,pace:'6',delay_mode:'10-30',quality:'super',lang:'ru',tz:'Europe/Moscow',window_start:8,window_end:23,digest_enabled:0,digest_time:'21:00',instructions:'',stopwords:'',signature_text:'Подписаться',signature_url:'',watermark:1,watermark_position:'bottom-right',logo_configured:true};
        boot={channels:[channel],features:{business_mode:false},user:{is_admin:false,has_access:true,tg_id:1,first_name:'Анна',max_channels:5,daily_limit:10},bot_username:'testbot'};
        window.fixtureStats={today:3,pending:2,remaining:7,sources:2,queued:0,mode:'автопостинг',blockers:[],posts_chart:[],subscribers_chart:[],waiting:{}};
        window.fixtureSubscription={current_plan:'pro',status:'active',period_end:'2026-11-03T00:00:00Z',ai_daily_limit:10,ai_used_today:3,scheduled_plan:null};
        api=async url => {
          if(url==='/bootstrap')return boot;
          if(url==='/billing/subscription')return window.fixtureSubscription;
          if(url.includes('/stats'))return window.fixtureStats;
          if(url.includes('/sources'))return [{id:1,kind:'tg',ref:'very_long_source_name_123456789',title:'Название источника с длинным заголовком',enabled:1}];
          if(url.includes('/feed'))return [{id:123,business_draft:channel.business_mode ? 1 : 0,media:[],source_title:'Новости города',text_out:'В городе откроют новый парк. В нём появятся прогулочные дорожки, место для пикников и площадка для встреч.',created_at:new Date().toISOString()}];
          if(url.includes('/ads?'))return [{id:1,advertiser:'Компания с длинным названием',contacts:['@business_contact','advertising@example.company'],seen_count:12,status:'new',source_title:'Партнёрский канал',raw_text:'Предложение по размещению рекламы.',reasons:['Найдены контакты и предложение услуги']}];
          if(url==='/admin/api-keys')return {keys:[],limit:20,server_key_configured:true};
          if(url==='/admin/overview')return {totals:{posts:120,published:20,ai_today:8},users:[{tg_id:1,first_name:'Пользователь с длинным именем',channels:4,daily_limit:100,max_channels:5,plan:'custom',access_until:'2026-11-03'}],channels:[channel]};
          if(url==='/admin/monitoring')return {system:{cpu:12,ram_percent:60,ram_used:1.2,ram_total:2,disk_percent:70,disk_used:17,disk_total:25,process_mb:180,activity:'Проверка источников'},capacity:{users:30,channels:120,active_channels:100,database_bytes:1000000},queue:{new:2,pending:3,ready:4,publishing:1,attention:0},events:[]};
          return [];
        };
        $('#boot').hidden=true; $('#app').hidden=false;
        $('#supportLink').hidden=false; $('#supportLink').href='https://t.me/test';
        fillOptions($('#langSelect'),[['ru','Русский']]); fillOptions($('#tzSelect'),[['Europe/Moscow','Москва']]); fillOptions($('#digestTime'),[['21:00','21:00']]);
        fillOptions($('#promoPlan'),Object.entries(ACCESS_PLANS).map(([key,value])=>[key,value.name]));
        enterNormalMode();
      });
      for (const view of ['home','feed','sources','settings','billing','ads','admin']) {
        await page.evaluate(async view => {
          if(view==='admin') { $('#openAdmin').click(); await loadAdmin(); await refreshAdminMonitoring(); }
          else { openPage(view); if(PAGE_LOADERS[view])await PAGE_LOADERS[view](); }
        },view);
        await page.waitForFunction(()=>!billingLoading);
        await capture(page,width,view);
      }
      await page.evaluate(()=>$('#closeAdmin').click());
      for (const [language,theme] of [['ru','dark'],['en','light'],['en','dark']]) {
        await preference(page,language,theme);
        for (const view of ['home','feed','settings','billing']) {
          await page.evaluate(async view=>{openPage(view);if(PAGE_LOADERS[view])await PAGE_LOADERS[view]();},view);
          await page.waitForFunction(()=>!billingLoading);
          await capture(page,width,`${language}-${theme}-${view}`);
        }
      }
      await preference(page,'ru','light');
      await page.evaluate(() => { $('#closeAdmin').click(); openPage('settings'); document.querySelectorAll('#page-settings > details').forEach(el=>el.open=true); });
      assert.equal(await page.locator('#businessSettings').isVisible(),false);
      await capture(page,width,'settings-expanded');
      await page.evaluate(async () => { channel.business_mode=1; channel.business_profile='{"name":"Stored company"}'; openPage('home'); renderStats(window.fixtureStats); });
      assert.equal(await page.locator('#publishNow').isDisabled(),true);
      assert.ok(!(await page.locator('#nextStep').textContent()).includes('Досье'));
      await page.evaluate(async()=>{openPage('feed');await loadFeed()});
      assert.equal(await page.locator('#feed [data-act]').count(),0);
      assert.ok((await page.locator('#feed').textContent()).includes('Материал сохранён'));
      await capture(page,width,'preserved-draft');
      await page.evaluate(()=>{boot.user.has_access=false;window.fixtureSubscription.status='inactive';showGate()});
      await page.locator('#gate [data-go="billing"]').click();
      assert.equal(await page.locator('#page-billing').isVisible(),true);
      assert.equal(await page.locator('#page-billing a,#page-billing [data-plan],#page-billing input').count(),0);
      await page.locator('#billingBack').click();
      assert.equal(await page.locator('#gate').isVisible(),true);
      await capture(page,width,'gate');
      await page.evaluate(()=>showOnboarding());
      await capture(page,width,'onboarding');
      if(width<=390)await page.setViewportSize({width,height:568});
      for (const [language,theme] of [['ru','light'],['en','dark']]) {
        await preference(page,language,theme);
        await page.evaluate(()=>setupTour());
        assert.equal(await page.locator('#tourTopics option').count(),8);
        for(let slide=1;slide<=8;slide++){
          await capture(page,width,`${language}-${theme}-tour-${slide}`);
          assert.equal(await page.locator('.slide:visible').count(),1);
          assert.equal(await page.locator('#tourCounter').textContent(),`${String(slide).padStart(2,'0')} / 08`);
          const footer=await page.locator('.tour-foot').boundingBox();
          assert.ok(footer.y>=0 && footer.y+footer.height<=page.viewportSize().height+1,`${width}px ${language} topic ${slide}: footer outside viewport`);
          await page.locator('#tourNext').click();
        }
        assert.equal(await page.locator('#tour').isVisible(),false);
      }
      await page.close();
      const account = await browser.newPage({viewport:{width,height:844},deviceScaleFactor:1});
      await routeAssets(account,true);
      await account.goto('https://visual.test/account');
      await account.evaluate(()=>{
        state.session={authenticated:false,telegram_client_id:'1234',login_nonce:'nonce'};
        state.catalog={plans:FALLBACK_PLANS,checkout_available:false,provider:'yookassa'};
        renderSession();renderPlans();
      });
      assert.equal(await account.locator('[data-plan]').count(),5);
      assert.equal(await account.locator('[data-plan]:enabled').count(),0);
      await capture(account,width,'account-login');
      await preference(account,'en','dark');
      await capture(account,width,'en-dark-account-login');
      await preference(account,'ru','light');
      await account.evaluate(async()=>{
        state.session={authenticated:true,user:{first_name:'Анна'},csrf_token:'csrf'};
        state.subscription={current_plan:'pro',status:'active',period_end:'2026-11-03T00:00:00Z',ai_daily_limit:10,ai_used_today:3};
        api=async (url,options)=>{
          if(url==='/billing/quote')return {quote_id:'quote',plan:JSON.parse(options.body).plan,amount_rub:49.35,change_type:'upgrade',effective_at:'2026-10-03',checkout_available:false};
          if(url==='/billing/orders')return {orders:[{plan:'pro',amount_rub:490,status:'succeeded',created_at:'2026-10-03'}]};
          if(url==='/billing/subscription')return state.subscription;
          return {};
        };
        renderSession();renderPlan();renderPlans();await loadOrders();
      });
      await capture(account,width,'account-plans');
      for (const [language,theme] of [['ru','dark'],['en','light'],['en','dark']]) {
        await preference(account,language,theme);
        await capture(account,width,`${language}-${theme}-account-plans`);
      }
      await preference(account,'ru','light');
      await account.locator('[data-plan="expert"]').click();
      await account.waitForFunction(()=>!!state.quote);
      assert.equal(await account.locator('#accountCheckout').isDisabled(),true);
      await capture(account,width,'account-disabled-checkout');
      await account.evaluate(()=>{
        state.quote={quote_id:'quote-2',plan:'plus',amount_rub:0,change_type:'downgrade',effective_at:'2026-11-03',checkout_available:true};
        notice('#checkoutStatus','');renderPlans();renderQuote();
      });
      assert.equal(await account.locator('#receiptField').isVisible(),false);
      assert.equal(await account.locator('#accountCheckout').isEnabled(),true);
      assert.ok((await account.locator('#quoteSummary').textContent()).includes('вручную оплатить'));
      await capture(account,width,'account-downgrade');
      await preference(account,'en','dark');
      await capture(account,width,'en-dark-account-downgrade');
      await account.close();
    }
    console.log(JSON.stringify({out,screenshots,failures}));
    assert.equal(failures.length,0);
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
