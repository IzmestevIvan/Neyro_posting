// Reproduce stalled Telegram SDK and stalled bootstrap without live credentials.
const {chromium}=require('playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||(fs.existsSync(chrome)?chrome:undefined)});
 try {
  for(const stalled of ['sdk','api']) {
   const page=await browser.newPage({viewport:{width:390,height:844},locale:'ru-RU',hasTouch:true});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{
    window.bridgeEvents=[];
    window.TelegramWebviewProxy={postEvent:event=>window.bridgeEvents.push(event)};
    const original=window.setTimeout;
    window.setTimeout=(fn,ms,...args)=>original(fn,[12000,15000].includes(ms)?ms/10:ms,...args);
   });
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.host==='telegram.org') {
     if(stalled==='sdk')return;
     return route.fulfill({contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:"test",ready(){window.bridgeEvents.push("web_app_ready")},expand(){}}};'});
    }
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/miniapp/index.html','utf8')});
    if(url.pathname.startsWith('/static/'))return route.fulfill({contentType:url.pathname.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync('app/miniapp/'+url.pathname.split('/').pop(),'utf8')});
    if(url.pathname==='/api/bootstrap')return;
    return route.fulfill({status:404,body:''});
   });
   await page.goto('https://startup.test/',{waitUntil:'commit'});
   await page.waitForFunction(()=>document.getElementById('boot')?.innerText.includes('Загрузка затянулась') || document.getElementById('retryBoot'),{},{timeout:8000});
   assert((await page.locator('#boot').innerText()).includes(stalled==='sdk'?'Загрузка затянулась':'Сервер не ответил вовремя'));
   assert(await page.evaluate(()=>bridgeEvents.includes('web_app_ready')));
   assert.deepEqual(errors,[]);
   await page.close();
  }
  console.log('Startup failures show recovery UI and release Telegram loading cover: OK');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
