// Offline layout fixtures: no production requests, credentials or publications.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const macChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const executablePath = process.env.CHROME_PATH || (fs.existsSync(macChrome) ? macChrome : undefined);
  const browser = await chromium.launch({headless:true, executablePath});
  const out = process.env.VISUAL_OUTPUT || '/private/tmp/neyro-visual';
  fs.mkdirSync(out, {recursive:true});
  const failures = [];
  try {
    for (const width of [320, 390, 768, 1280]) {
      const page = await browser.newPage({viewport:{width, height:844}, deviceScaleFactor:1});
      page.on('pageerror', e => failures.push(e.message));
      await page.route('**/*', route => {
        const name = new URL(route.request().url()).pathname;
        if (name === '/') return route.fulfill({contentType:'text/html', body:fs.readFileSync('app/miniapp/index.html','utf8')});
        if (name === '/static/style.css') return route.fulfill({contentType:'text/css', body:fs.readFileSync('app/miniapp/style.css','utf8')});
        if (name === '/static/app.js') return route.fulfill({contentType:'text/javascript', body:fs.readFileSync('app/miniapp/app.js','utf8').replace(/^start\(\);$/m,'')});
        return route.fulfill({contentType:'application/json', body:'[]'});
      });
      await page.goto('http://visual.test/');
      await page.evaluate(() => {
        const c = {id:1,title:'Тестирование NeuroPost — длинное название канала', username:'test', owner_id:1,
          autopost:1,paused:0,pace:'24',delay_mode:'30-90',quality:'super',lang:'ru',tz:'Europe/Moscow',sources:2,
          window_start:8,window_end:23,digest_enabled:1,digest_time:'21:00',instructions:'',stopwords:'',
          signature_text:'Подписаться',signature_url:'',watermark:1,watermark_position:'bottom-right',logo_configured:true};
        channel=c; boot={channels:[c],user:{is_admin:true,has_access:true,id:1,max_channels:5,daily_limit:100}, bot_username:'testbot'};
        window.fixtureStats={today:3,pending:0,subscribers:1234,remaining:97,sources:2,queued:0,mode:'автопостинг',
          posts_chart:[],subscribers_chart:[],waiting:{},last_rejection:{created_at:new Date().toISOString(),reason:'Недостаточно просмотров для выбранного фильтра'}};
        api=async url => {
          if(url==='/admin/monitoring')return {system:{cpu:12,ram_percent:60,ram_used:1.2,ram_total:2,disk_percent:70,disk_used:17,disk_total:25,process_mb:180,activity:'Проверка источников'},capacity:{users:30,channels:120,active_channels:100,database_bytes:1000000},queue:{new:2,pending:3,ready:4,publishing:1,attention:0},events:[{title:'ИИ временно недоступен',explanation:'Материал сохранён, следующая попытка позже.',detail:'HTTP 503',time:new Date().toISOString()}]};
          if(url==='/admin/api-keys')return {keys:[{id:1,label:'Ключ Gemini №1',enabled:true,last_error:'Ограничение квоты или частоты запросов',cooldown_until:new Date(Date.now()+60000).toISOString()}],limit:20,server_key_configured:true};
          if(url==='/admin/overview')return {totals:{posts:1234,published:222,ai_today:98},users:[{tg_id:123456789,first_name:'Пользователь с очень длинным именем',channels:4,daily_limit:100}],channels:[c]};
          if(url.includes('/stats'))return window.fixtureStats;
          if(url.includes('/sources'))return [{id:1,kind:'tg',ref:'very_long_source_name_123456789',title:'Название источника с длинным заголовком',enabled:1}];
          return [];
        };
        $('#boot').hidden=true; $('#app').hidden=false; $('#openAdmin').hidden=false;
        $('#supportLink').hidden=false; $('#supportLink').href='https://t.me/test';
        fillOptions($('#langSelect'), [['ru','Русский']]); fillOptions($('#tzSelect'),[['Europe/Moscow','Москва']]);
        fillOptions($('#digestTime'),[['21:00','21:00']]); fillOptions($('#promoPlan'),[['start','Старт']]);
        enterNormalMode();
      });
      for (const view of ['home','sources','settings','admin']) {
        await page.evaluate(async view => {
          if(view==='admin') {
            $('#openAdmin').click(); await loadAdmin();
            renderSystem({cpu:12,ram_percent:60,ram_used:1.2,ram_total:2,disk_percent:70,disk_used:17,disk_total:25,process_mb:180,activity:'Проверка источников'});
          } else { openPage(view); if(view==='home')renderStats(window.fixtureStats); }
          if(view==='settings')document.querySelectorAll('#page-settings > details').forEach(d=>d.open=true);
        }, view);
        await page.screenshot({path:path.join(out,`${width}-${view}.png`), fullPage:true});
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
        if(overflow) failures.push(`${width}px ${view}: horizontal overflow`);
        const visibleSupport = await page.locator('#supportLink').isVisible();
        if(visibleSupport !== (view==='settings')) failures.push(`${width}px ${view}: misplaced support link`);
        if(view==='settings') {
          const reachable = await page.locator('#supportLink').evaluate(el => {
            const rect = el.getBoundingClientRect();
            return rect.top >= 0 && rect.bottom <= innerHeight - 92;
          });
          if(!reachable) failures.push(`${width}px settings: support requires scrolling`);
        }
      }
      await page.close();
    }
    console.log(JSON.stringify({out, failures}));
    assert.equal(failures.length,0);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
