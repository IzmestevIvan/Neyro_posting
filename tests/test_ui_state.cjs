// State regressions without a browser or production API.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {value:'', disabled:false, dataset:{},
    addEventListener(){}, setAttribute(){}, classList:{toggle(){}}, querySelectorAll(){return []},
    appendChild(){}, remove(){}});
  return elements.get(selector);
}
let requests = [];
const context = vm.createContext({URL, URLSearchParams, location:{search:''}, window:{},
  document:{querySelector:element,querySelectorAll:()=>[],createElement:()=>element('toast'),body:element('body')},
  setTimeout:()=>0, clearTimeout(){}, console, assert,
  fetch: async (url, options) => {
    requests.push({url, body:JSON.parse(options.body)});
    await new Promise(resolve=>setTimeout(resolve, 5));
    return {ok:true,json:async()=>({id:1,...Object.assign({},...requests.map(r=>r.body))})};
  },
});
vm.runInContext(readFileSync('app/miniapp/app.js','utf8').replace(/^start\(\);$/m,''), context);
(async()=>{
  await vm.runInContext(`(async()=>{
    channel={id:1}; boot={channels:[channel]};
    const old=readTicket('feed'); channel={id:2}; assert.equal(old(),false);
    channel={id:1}; const first=readTicket('feed'); const second=readTicket('feed');
    assert.equal(first(),false); assert.equal(second(),true);
    const p1=save({instructions:'first'},true);
    const p2=save({instructions:'second'},true);
    assert.equal($('#channelSelect').disabled,true);
    await Promise.all([p1,p2]);
    assert.equal(channel.instructions,'second');
    assert.equal($('#channelSelect').disabled,false);
    assert.equal(savingCount,0);
    assert.equal(safeLink('javascript:alert(1)'), '');
    assert.equal(safeLink('https://example.com/a'), 'https://example.com/a');
  })()`,context);
  assert.equal(requests.length,2);
  assert.equal(requests[0].body.instructions,'first');
  assert.equal(requests[1].body.instructions,'second');
  console.log('UI state: stale reads, ordered saves, channel lock and safe links passed');
})().catch(error=>{console.error(error); process.exitCode=1});
