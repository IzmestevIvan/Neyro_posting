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
    for (const width of [320, 390, 768, 900, 1280]) {
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
      const capture = async name => {
        if(name !== 'error') await page.evaluate(()=>document.querySelectorAll('.toast').forEach(el=>el.remove()));
        await page.screenshot({path:path.join(out,`${width}-${name}.png`), fullPage:!name.startsWith('tour-')});
        const problems = await page.evaluate(() => {
          const result = [];
          if (document.documentElement.scrollWidth > innerWidth) result.push('page overflow');
          for (const el of document.querySelectorAll('button,input,select,textarea,.post,.ad,.adminrow')) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height || !el.checkVisibility()) continue;
            if (r.left < -1 || r.right > innerWidth + 1) result.push(`offscreen ${el.id || el.className || el.tagName}`);
            if (el.tagName === 'BUTTON' && r.height < 43 && !el.closest('.dots')) result.push(`small button ${el.id || el.textContent}`);
          }
          return result;
        });
        failures.push(...problems.map(p => `${width}px ${name}: ${p}`));
      };
      await page.evaluate(() => {
        const c = {id:1,title:'Тестирование NeuroPost — длинное название канала', username:'test', owner_id:1,
          autopost:1,paused:0,pace:'24',delay_mode:'30-90',quality:'super',lang:'ru',tz:'Europe/Moscow',sources:2,
          window_start:8,window_end:23,digest_enabled:1,digest_time:'21:00',instructions:'',stopwords:'',
          signature_text:'Подписаться',signature_url:'',watermark:1,watermark_position:'bottom-right',logo_configured:true};
        channel=c; boot={channels:[c],user:{is_admin:true,has_access:true,id:1,max_channels:5,daily_limit:100}, bot_username:'testbot'};
        window.fixtureStats={today:3,pending:0,subscribers:1234,remaining:97,sources:2,queued:0,mode:'автопостинг',
          blockers:['Канал на паузе — снимите паузу в настройках. Нет включённых источников. Добавьте канал или RSS во вкладке «Источники».'],
          posts_chart:[],subscribers_chart:[],waiting:{},last_rejection:{created_at:new Date().toISOString(),reason:'Недостаточно просмотров для выбранного фильтра'}};
        api=async url => {
          if(url==='/admin/monitoring')return {api_load:{started_at:new Date().toISOString(),active:4,waiting:8,concurrency:4,windows:{'15':{requests:150,errors:40,quota:30,server:5,transport:3,interrupted:2,fallbacks:32,average_ms:2500},'60':{requests:600,errors:160,fallbacks:120}},series:Array.from({length:15},(_,i)=>({requests:i+1,errors:i%4}))},system:{cpu:12,ram_percent:60,ram_used:1.2,ram_total:2,disk_percent:70,disk_used:17,disk_total:25,process_mb:180,activity:'Проверка источников'},capacity:{users:30,channels:120,active_channels:100,database_bytes:1000000},queue:{new:2,pending:3,ready:4,publishing:1,attention:0},events:[{title:'ИИ временно недоступен',explanation:'Материал сохранён, следующая попытка позже.',detail:'HTTP 503',time:new Date().toISOString()}]};
          if(url==='/admin/api-keys')return {keys:[{id:1,label:'Ключ Gemini №1',enabled:true,last_error:'Ограничение квоты или частоты запросов',cooldown_until:new Date(Date.now()+60000).toISOString()}],limit:20,server_key_configured:true};
          if(url==='/admin/overview')return {totals:{posts:1234,published:222,ai_today:98},users:[{tg_id:123456789,first_name:'Пользователь с очень длинным именем',channels:4,daily_limit:100,max_channels:5,plan:'custom',access_until:'2026-10-16T12:00:00Z'}],channels:[c]};
          if(url.includes('/stats'))return window.fixtureStats;
          if(url.includes('/sources'))return [{id:1,kind:'tg',ref:'very_long_source_name_123456789',title:'Название источника с длинным заголовком',enabled:1}];
          if(url.includes('/feed') && channel.business_mode)return [{id:123,business_draft:1,media:[],source_title:'Редактор компании',text_out:'Перед оснащением переговорной определите число участников, сценарии встреч и требования к звуку. Это поможет составить понятное техническое задание.',reason:'Требуется согласование. Проверьте терминологию.',created_at:new Date().toISOString()}];
          return [];
        };
        $('#boot').hidden=true; $('#app').hidden=false; $('#openAdmin').hidden=false;
        $('#supportLink').hidden=false; $('#supportLink').href='https://t.me/test';
        fillOptions($('#langSelect'), [['ru','Русский']]); fillOptions($('#tzSelect'),[['Europe/Moscow','Москва']]);
        fillOptions($('#digestTime'),[['21:00','21:00']]); fillOptions($('#promoPlan'),[['start','Старт']]);
        enterNormalMode();
      });
      for (const view of ['home','sources','settings','admin','business-home','business-settings','business-feed']) {
        await page.evaluate(async view => {
          if(view.startsWith('business-')) {
            $('#closeAdmin').click();
            channel.business_mode=1; channel.autopost=0; channel.digest_enabled=0;
            channel.business_profile=JSON.stringify({name:'DOBRA Group',services:'Мультимедиа, телекоммуникации, инженерные сети',audience:'Заказчики и генеральные подрядчики',tone:'Профессионально и понятно'});
            fillSettings();
            view=view.replace('business-','');
          }
          if(view==='admin') {
            $('#openAdmin').click(); await loadAdmin();
            renderSystem({cpu:12,ram_percent:60,ram_used:1.2,ram_total:2,disk_percent:70,disk_used:17,disk_total:25,process_mb:180,activity:'Проверка источников'});
          } else { openPage(view); if(view==='home')renderStats(window.fixtureStats); if(view==='feed')await loadFeed(); }
          if(view==='settings')document.querySelectorAll('#page-settings > details').forEach(d=>d.open=true);
        }, view);
        await capture(view);
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
        if(overflow) failures.push(`${width}px ${view}: horizontal overflow`);
        const visibleSupport = await page.locator('#supportLink').isVisible();
        if(visibleSupport !== view.endsWith('settings')) failures.push(`${width}px ${view}: misplaced support link`);
        if(view.endsWith('settings')) {
          const reachable = await page.locator('#supportLink').evaluate(el => {
            const rect = el.getBoundingClientRect();
            return rect.top >= 0 && rect.bottom <= innerHeight - 92;
          });
          if(!reachable) failures.push(`${width}px settings: support requires scrolling`);
        }
        if(view==='business-settings') {
          assert.equal(await page.locator('[data-field="autopost"]').isDisabled(),true);
          assert.equal(await page.locator('[data-business="name"]').inputValue(),'DOBRA Group');
          assert.ok((await page.locator('[data-business="name"]').boundingBox()).height >= 44);
          await page.locator('#businessSettings').screenshot({path:path.join(out,`${width}-dossier.png`)});
        }
        if(view==='business-feed') {
          await page.locator('[data-act="edit"]').click();
          assert.equal(await page.locator('[data-act="approve"]').isDisabled(),true);
          await page.locator('[data-act="cancel-edit"]').click();
          assert.equal(await page.locator('[data-act="approve"]').isDisabled(),false);
          await page.evaluate(async () => {
            switchFeed('history');
            openBusiness();
          });
          await page.locator('#generateBusiness').click();
          await page.waitForFunction(() => !publishingNow && page === 'feed');
          assert.equal(await page.locator('#feed').isVisible(),true);
          assert.equal(await page.locator('#history').isVisible(),false);
          await page.evaluate(() => {
            openBusiness();
            const original = api;
            api = async (url, options) => {
              if(url.endsWith('/business/draft')) throw Error('Не удалось прочитать указанную страницу. Очистите ссылку.');
              return original(url, options);
            };
          });
          await page.locator('#generateBusiness').click();
          await page.waitForFunction(() => !publishingNow);
          assert.ok((await page.locator('#businessStatus').textContent()).includes('Очистите ссылку'));
          assert.equal(await page.locator('#generateBusiness').isEnabled(),true);
        }
      }
      await page.evaluate(() => {
        window.savedChannel = channel;
        const original = api;
        api = async (url, options) => {
          if (url.includes('/ads?')) return [{id:1,advertiser:'Компания с длинным названием — предложение о сотрудничестве',contacts:['@business_contact','advertising@example.company'],seen_count:12,status:'new',source_title:'Партнёрский канал',raw_text:'Предложение по размещению рекламы. Подробности и условия сотрудничества.',reasons:['Найдены контакты и предложение услуги']}];
          if (url.endsWith('/history')) return ['published','uncertain','partial','failed'].map((status,i)=>({id:i,status,source_title:'Источник с длинным названием',preview:'Текст публикации для проверки истории.',reason: status === 'published' ? null : 'Проверьте результат доставки в канале перед повторной отправкой.'}));
          return original(url,options);
        };
      });
      for (const state of ['ads','ads-archive','history','source-panels','business-edit','business-photo','empty-feed','empty-ads','admin-expanded','error','gate','onboarding']) {
        await page.evaluate(async state => {
          if (state === 'ads' || state === 'ads-archive') { openPage('ads'); await switchAds(state === 'ads' ? 'active' : 'archive'); document.querySelectorAll('.ad-reasons').forEach(el=>el.open=true); }
          if (state === 'history') { openPage('feed'); switchFeed('history'); await loadHistory(); }
          if (state === 'source-panels') {
            openPage('sources'); $('#sourceBatchPanel').hidden=false; $('#copySourcesPanel').open=true;
            $('#copySourceList').innerHTML='<label class="copy-source"><input type="checkbox" checked><span>Источник с длинным названием<small>https://example.org/very/long/feed/address</small></span></label><label class="copy-source"><input type="checkbox"><span>Ещё один источник</span></label>';
            $('#sourceBatchStatus').textContent='Не удалось прочитать один из источников. Проверьте адрес и повторите.';
            $('#sourceBatchCancel').hidden=false;
          }
          if (state === 'business-edit') { openPage('feed'); switchFeed('pending'); await loadFeed(); $('#feed [data-act="edit"]').click(); }
          if (state === 'business-photo') {
            const original=api;
            const canvas=document.createElement('canvas'); canvas.width=1200; canvas.height=800;
            const ctx=canvas.getContext('2d'); ctx.fillStyle='#527b66'; ctx.fillRect(0,0,1200,800);
            ctx.fillStyle='#dce9df'; ctx.fillRect(200,160,800,480);
            const data=canvas.toDataURL('image/jpeg').split(',')[1];
            api=async (url,options)=>url.endsWith('/feed') ? [{id:124,business_draft:1,media_revision:'fixture',media:[{type:'photo',data,source:'https://example.org/projects/one'}],text_out:'Материал компании с фотографией проекта. Проверьте текст и изображение перед согласованием.',source_title:'Редактор компании'}] : original(url,options);
            await loadFeed(); api=original;
          }
          if(state==='empty-feed' || state==='empty-ads') {
            const original=api; api=async url=>[];
            if(state==='empty-feed') {openPage('feed'); await loadFeed();}
            else {openPage('ads'); await loadAds();}
            api=original;
          }
          if (state === 'admin-expanded') {
            $('#openAdmin').click(); await loadAdmin(); await refreshAdminMonitoring();
            $('#channelAudit').innerHTML='<div class="card"><b>Компания с длинным названием</b><p class="hint">Ожидает согласования владельца</p></div>';
            document.querySelectorAll('#page-admin details').forEach(el=>el.open=true);
          }
          if (state === 'error') { $('#closeAdmin').click(); openBusiness(); toast('Предыдущее сообщение'); toast('Не удалось сохранить изменения. Проверьте подключение и повторите попытку.',true); }
          if (state === 'gate') showGate();
          if (state === 'onboarding') showOnboarding();
        },state);
        await capture(state);
        if(state==='error') assert.equal(await page.locator('.toast').count(),1);
        if(state==='business-edit') {
          const colors=await page.locator('[data-act="approve"]').evaluate(el=>({disabled:el.disabled,bg:getComputedStyle(el).backgroundColor}));
          assert.equal(colors.disabled,true);
          assert.equal(colors.bg,'rgb(44, 60, 54)');
        }
      }
      await page.evaluate(() => { document.querySelectorAll('.toast').forEach(e=>e.remove()); channel=window.savedChannel; enterNormalMode(); window.scrollTo(0,0); setupTour(); });
      if(width <= 390) await page.setViewportSize({width,height:568});
      for(let slide=1;slide<=6;slide++) {
        await capture(`tour-${slide}`);
        assert.equal(await page.locator('.slide:visible').count(),1);
        const footer = await page.locator('.tour-foot').boundingBox();
        assert.ok(footer.y + footer.height <= page.viewportSize().height + 1);
        await page.locator('#tourNext').click();
      }
      await page.close();
    }
    console.log(JSON.stringify({out, failures}));
    assert.equal(failures.length,0);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
