// Isolated client preferences: no real Telegram calls, storage or network access.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync('app/miniapp/preferences.js','utf8');
function node(attrs={}) {
  return {attrs:{...attrs},textContent:'',value:'',getAttribute(key){return this.attrs[key]??null},hasAttribute(key){return key in this.attrs},setAttribute(key,value){this.attrs[key]=value},matches(selector){return selector.split(',').some(part => part.slice(1,-1) in this.attrs)}};
}
function create(options={}) {
  const listeners={}, windowListeners={}, telegramListeners={};
  const storage=new Map(Object.entries(options.stored||{}));
  const title=node({'data-i18n':'Мой тариф','data-i18n-en':'My plan'});
  const input=node({'data-i18n-placeholder':'Название','data-i18n-placeholder-en':'Name','data-i18n-aria-label':'Канал','data-i18n-aria-label-en':'Channel'});
  const userPost=node();userPost.textContent='Мой тариф — это мои слова';
  const languageSelect=node({'data-pref-language':''});const themeSelect=node({'data-pref-theme':''});
  const nodes=[title,input,userPost,languageSelect,themeSelect];
  const media={matches:!!options.dark,addEventListener(event,fn){this.listener=fn}};
  const document={documentElement:{lang:'ru',dataset:{}},readyState:'complete',querySelectorAll(selector){return nodes.filter(n=>n.matches(selector))},addEventListener(event,fn){(listeners[event]??=[]).push(fn)},dispatchEvent(event){for(const fn of listeners[event.type]||[])fn(event)}};
  const navigator={languages:options.languages||['ru-RU'],language:'ru-RU'};
  const window={matchMedia:()=>media,addEventListener(event,fn){windowListeners[event]=fn}};
  if(options.telegram) window.Telegram={WebApp:{...options.telegram,onEvent(event,fn){telegramListeners[event]=fn}}};
  const localStorage={getItem(key){if(options.brokenStorage)throw Error('denied');return storage.get(key)||null},setItem(key,value){if(options.brokenStorage)throw Error('denied');storage.set(key,value)}};
  const context=vm.createContext({window,document,navigator,localStorage,CustomEvent:class{constructor(type,props){this.type=type;this.detail=props.detail}},Set});
  vm.runInContext(source,context);
  return {api:window.NeyroPrefs,window,document,navigator,storage,media,listeners,windowListeners,telegramListeners,title,input,userPost,languageSelect,themeSelect};
}
{
  const app=create({languages:['en-US'],dark:true});
  assert.equal(app.api.language,'en');assert.equal(app.api.theme,'dark');
  assert.equal(app.document.documentElement.lang,'en');assert.equal(app.document.documentElement.dataset.theme,'dark');
  assert.equal(app.title.textContent,'My plan');assert.equal(app.input.attrs.placeholder,'Name');assert.equal(app.input.attrs['aria-label'],'Channel');
  assert.equal(app.userPost.textContent,'Мой тариф — это мои слова');
  assert.equal(app.api.t('Осталось {n}','{n} left',{n:3}),'3 left');
  app.api.setLanguage('ru');assert.equal(app.title.textContent,'Мой тариф');assert.equal(app.storage.get('neyro:language'),'ru');
  assert.equal(app.languageSelect.value,'ru');assert.equal(app.api.t('Текст','Text'),'Текст');
  app.navigator.languages=['en-GB'];app.windowListeners.languagechange();assert.equal(app.api.language,'ru');
  app.api.setLanguage('auto');assert.equal(app.api.language,'en');
  app.api.setTheme('light');app.media.matches=true;app.media.listener();assert.equal(app.api.theme,'light');
  assert.equal(app.themeSelect.value,'light');assert.equal(app.storage.get('neyro:theme'),'light');
  app.api.setTheme('system');assert.equal(app.api.theme,'dark');
  app.media.matches=false;app.media.listener();assert.equal(app.api.theme,'light');
  app.api.setTheme('invalid');assert.equal(app.api.themePreference,'system');
  app.api.setLanguage('invalid');assert.equal(app.api.languagePreference,'auto');
}
{
  const app=create({telegram:{initData:'signed-init-data',colorScheme:'dark',initDataUnsafe:{user:{language_code:'en'}}},languages:['ru'],dark:false});
  assert.equal(app.api.language,'en');assert.equal(app.api.theme,'dark');
  app.window.Telegram.WebApp.colorScheme='light';app.telegramListeners.themeChanged();assert.equal(app.document.documentElement.dataset.theme,'light');
  app.api.setTheme('dark');app.telegramListeners.themeChanged();assert.equal(app.api.theme,'dark');
}
{
  // Loading the SDK in a normal browser does not override its OS theme.
  const app=create({telegram:{colorScheme:'light',platform:'unknown',initDataUnsafe:{}},dark:true,languages:['fr-FR','ru-RU']});
  assert.equal(app.api.theme,'dark');assert.equal(app.api.language,'ru');
  app.navigator.languages=['fr-FR'];app.windowListeners.languagechange();assert.equal(app.api.language,'en');
}
{
  const app=create({stored:{'neyro:language':'en','neyro:theme':'dark'}});
  assert.equal(app.api.language,'en');assert.equal(app.api.theme,'dark');
  app.storage.set('neyro:language','ru');app.storage.set('neyro:theme','light');
  app.windowListeners.storage({key:'neyro:language'});assert.equal(app.api.language,'ru');assert.equal(app.api.theme,'light');
  app.storage.clear();app.windowListeners.storage({key:null});assert.equal(app.api.languagePreference,'auto');assert.equal(app.api.themePreference,'system');
}
{
  const app=create({brokenStorage:true,dark:true});
  app.api.setLanguage('en');app.api.setTheme('light');
  assert.equal(app.api.language,'en');assert.equal(app.api.theme,'light');
  assert.equal(app.title.textContent,'My plan');
  app.document.dispatchEvent({type:'change',target:{value:'dark',matches:selector=>selector==='[data-pref-theme]'}});
  assert.equal(app.api.theme,'dark');
}
{
  const app=create({stored:{'neyro:language':'bad','neyro:theme':'bad'}});
  assert.equal(app.api.languagePreference,'auto');assert.equal(app.api.themePreference,'system');
}
console.log('Preferences: browser/Telegram defaults, persistent overrides, live system changes, safe opt-in translation and blocked storage passed');
