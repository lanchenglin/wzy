const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {JSDOM, VirtualConsole} = require('jsdom');
const fs = require('node:fs');
const scripts = JSON.parse(execFileSync(process.env.WZY_TEST_PYTHON || '.venv/bin/python', ['-c', 'import json; from wzy.collector import EXTRACT,NEXT_STATE,NEXT_CLICK; print(json.dumps([EXTRACT,NEXT_STATE,NEXT_CLICK]))'], {encoding:'utf8'}));
const listingScripts = JSON.parse(execFileSync(process.env.WZY_TEST_PYTHON || '.venv/bin/python', ['-c', 'import json; from wzy.listings import PROBE,CLICK; print(json.dumps([PROBE,CLICK]))'], {encoding:'utf8'}));
function dom(html) {
  const d = new JSDOM(html, {url:'https://wzyp.cn/merchant/goods/list',runScripts:'outside-only'});
  d.window.HTMLElement.prototype.getClientRects = function () {
    for(let p=this;p;p=p.parentElement) if(p.hidden || p.style.display==='none') return [];
    return [{width:100,height:20}];
  };
  return d;
}
test('real extraction preserves nested counts but excludes product count spoof and sales', () => {
  const d=dom('<section class="group-box"><div class="goods-item-info-title">数字卡密 1</div></section><div>数字卡密 <span>21</span></div><div>共 <strong>2</strong> 条</div><div class="arco-table"><table><tbody><tr>'+['','<a class="name">商品</a>','价格','状态','秘密销量','库存','日期','操作'].map(s=>'<td>'+s+'</td>').join('')+'</tr></tbody></table></div><div hidden><div class="arco-table">回收站秘密</div></div>');
  const out=d.window.eval('('+scripts[0]+')()');
  const parsed=new JSDOM(out).window.document;
  assert.ok([...parsed.querySelectorAll('p')].some(p=>p.textContent==='数字卡密 21'));
  assert.ok([...parsed.querySelectorAll('p')].some(p=>p.textContent==='共 2 条'));
  assert.equal([...parsed.querySelectorAll('p')].some(p=>p.textContent==='数字卡密 1'),false);
  assert.equal(parsed.querySelectorAll('.arco-table').length,1);
  assert.equal(out.includes('秘密销量'),false);
  assert.equal(out.includes('回收站秘密'),false);
});
test('next control is scoped and disabled controls never clicked', () => {
  const d=dom('<a id="publish">上架</a><div class="arco-pagination"><button class="arco-pagination-item-next">下一页</button></div>');
  let clicks=0;
  d.window.document.querySelector('button').addEventListener('click',()=>clicks++);
  assert.equal(d.window.eval('('+scripts[1]+')()'),'enabled');
  assert.equal(d.window.eval('('+scripts[2]+')()'),true);
  assert.equal(clicks,1);
  d.window.document.querySelector('button').setAttribute('aria-disabled','true');
  assert.equal(d.window.eval('('+scripts[2]+')()'),'disabled');
  assert.equal(clicks,1);
});
test('extraction returns allowlisted text only, excludes hidden content and attributes',()=>{
  const d=dom('<div aria-busy="true"></div><section class="group-box" data-secret="SECRET"><h2 class="goods-group-title">PPO</h2><div class="goods-group-content"><div class="goods-group-item" onclick="SECRET"><span class="goods-item-info-title">商品<span hidden>HIDDEN_SECRET</span></span><div class="goods-item-info-desc">说明<script>SECRET</script><input value="SECRET"></div><div class="goods-item-price-main"><span class="primary">10.00</span></div><div class="stock">3</div><a href="https://evil.invalid/?token=SECRET">购买</a></div></div></section>');
  const out=d.window.eval('('+scripts[0]+')()');
  assert.equal(out.includes('SECRET'),false);
  assert.equal(out.includes('onclick'),false);
  assert.ok(out.includes('data-wzy-loading="true"'));
  assert.ok(out.includes('商品'));
});
test('explicit empty indicator survives extraction',()=>{
  const d=dom('<div class="arco-table"><table><tbody></tbody></table><div class="arco-empty">暂无数据</div></div><div>共 <span>0</span> 条</div>');
  const out=d.window.eval('('+scripts[0]+')()');
  assert.ok(out.includes('arco-empty'));
  assert.ok(out.includes('共 0 条'));
});

test('listing click is scoped to unique visible own row and exact status-cell action',()=>{
  const row=(id)=>'<tr><td></td><td><a class="name" href="https://wzyp.cn/item/'+id+'">上架</a></td><td></td><td><span class="item-span">已下架</span><a class="toggle">上架</a><a>批量上架</a></td><td><a>上架</a></td></tr>';
  const d=dom('<a>上架</a><div hidden class="arco-table"><table>'+row('synthetic')+'</table></div><div class="arco-table"><table><tbody>'+row('other')+row('synthetic')+'</tbody></table></div>');
  let clicks=[];
  d.window.document.querySelectorAll('a').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();clicks.push(a);}));
  const click=args=>d.window.eval('('+listingScripts[1]+')('+JSON.stringify(args)+')');
  assert.equal(click({item_id:'synthetic',expected:'up',desired:'down'}),false);
  assert.equal(click({item_id:'synthetic',expected:'down',desired:'up'}),true);
  assert.equal(clicks.length,1);
  assert.equal(clicks[0].className,'toggle');
  d.window.document.querySelector('.arco-table:not([hidden]) tbody').insertAdjacentHTML('beforeend',row('synthetic'));
  assert.equal(click({item_id:'synthetic',expected:'down',desired:'up'}),false);
  assert.equal(clicks.length,1);
});

test('listing UI shows enable/allowlist, requires confirmation and preserves retry key',async()=>{
  const d=new JSDOM(fs.readFileSync('src/wzy/static/index.html','utf8'),{url:'http://127.0.0.1:8100',runScripts:'outside-only'});
  const state={products:[],stores:[],comparisons:[],counts:{},listing_writes_enabled:true,listing_allowed_items:['synthetic']};
  let posts=[];
  d.window.fetch=async(path,opts)=>{
    if(opts.method==='POST') {posts.push(JSON.parse(opts.body));throw new Error('network');}
    return {ok:true,json:async()=>path==='/api/state'?state:path.startsWith('/api/listings/items/')?{state:'down',observed_at:'2026-10-16'}:[]};
  };
  d.window.eval(fs.readFileSync('src/wzy/static/app.js','utf8'));
  await new Promise(r=>setImmediate(r));
  const doc=d.window.document;
  assert.ok(doc.getElementById('listing-policy').textContent.includes('synthetic'));
  doc.getElementById('listing-item').value='synthetic';
  doc.getElementById('listing-read').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(doc.getElementById('listing-expected').value,'down');
  doc.getElementById('listing-form').dispatchEvent(new d.window.Event('submit',{cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(posts.length,0);
  doc.getElementById('listing-confirmed').checked=true;
  for(let i=0;i<2;i++) {
    doc.getElementById('listing-form').dispatchEvent(new d.window.Event('submit',{cancelable:true}));
    await new Promise(r=>setImmediate(r));
  }
  assert.equal(posts.length,2);
  assert.equal(posts[0].idempotency_key,posts[1].idempotency_key);
  assert.equal(posts[0].expected,'down');
  state.listing_writes_enabled=false;
  doc.getElementById('refresh').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(doc.getElementById('listing-submit').disabled,true);
  d.window.close();
});

test('inbox older page is accessible and read action retains cursor',async()=>{
  const d=new JSDOM(fs.readFileSync('src/wzy/static/index.html','utf8'),{url:'http://127.0.0.1:8100',runScripts:'outside-only'});
  const calls=[];
  d.window.fetch=async(path,opts)=>{
    calls.push(path);
    return {ok:true,json:async()=>path==='/api/state'?{products:[],stores:[],comparisons:[],counts:{}}:path.startsWith('/api/notifications')?{unread:2,next_before_run:path.includes('?')?null:100,events:[{id:'run:1:changes',run_id:path.includes('?')?1:101,quality:'complete',kind:'changes',changes:[]}]}:[]};
  };
  d.window.eval(fs.readFileSync('src/wzy/static/app.js','utf8'));
  await new Promise(r=>setImmediate(r));
  const doc=d.window.document;
  doc.getElementById('inbox-older').click();
  await new Promise(r=>setImmediate(r));
  assert.ok(calls.includes('/api/notifications?before_run=100'));
  assert.ok(doc.getElementById('inbox').textContent.includes('#1 '));
  doc.querySelector('#inbox button').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(calls.at(-1),'/api/notifications?before_run=100');
  d.window.close();
});

test('suggestions prepare manual confirmation, schedules and inbox stay local', async()=>{
  const d=new JSDOM(fs.readFileSync('src/wzy/static/index.html','utf8'),{url:'http://127.0.0.1:8100',runScripts:'outside-only'});
  const own={key:'own:a',title:'PPO',kind:'own',store_id:1,price:'10.00',matchable:true};
  const comp={key:'shop:a',title:'<img src=x>',kind:'competitor',store_id:2,price:'8.00',matchable:true};
  const state={products:[own,comp],stores:[],comparisons:[],counts:{},groups:[{own,competitors:[{product:comp,confirmed:false}],lowest_available:null}],suggestions:[{own_key:own.key,competitor_key:comp.key,confidence:'low',reasons:['family'],conflicts:[],unknown:['duration'],own_fields:{family:'ppo'},competitor_fields:{family:'ppo'}}]};
  const calls=[];
  d.window.fetch=async(path,opts)=>{
    if(opts.method!=='GET') calls.push([path,JSON.parse(opts.body)]);
    return {ok:true,json:async()=>path==='/api/state'?state:path==='/api/scheduler'?{paused:false,sources:[{store_id:2,enabled:false,interval_minutes:60,browser_evidence:'not_observed'}]}:path==='/api/notifications'?{unread:1,events:[{id:'run:1:changes',run_id:1,quality:'partial',kind:'changes',changes:[{title:'<img src=x>',type:'price_down',before:'10',after:'8'}]}]}:[]};
  };
  d.window.eval(fs.readFileSync('src/wzy/static/app.js','utf8'));
  await new Promise(r=>setImmediate(r));
  const doc=d.window.document;
  assert.ok(doc.getElementById('suggestions').textContent.includes('期限'));
  assert.equal(doc.querySelector('#comparison-groups img'),null);
  doc.querySelector('#suggestions button').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(doc.getElementById('own-product').value,'own:a');
  assert.equal(doc.getElementById('confirmed').checked,false);
  assert.equal(calls.length,0);
  doc.getElementById('scheduler-pause').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(calls[0],['/api/scheduler',{paused:true}]);
  doc.querySelector('#schedule-2 input[type=checkbox]').checked=true;
  doc.getElementById('schedule-2').dispatchEvent(new d.window.Event('submit',{cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(calls[1],['/api/scheduler/sources/2',{enabled:true,interval_minutes:60,resume:false}]);
  assert.ok(doc.getElementById('inbox').textContent.includes('部分'));
  assert.equal(doc.querySelector('#inbox img'),null);
  doc.querySelector('#inbox button').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(calls[2][0],'/api/notifications/run%3A1%3Achanges');
  assert.equal(calls[2][1].read,true);
  d.window.close();
});

test('workbench renders untrusted names as text and posts confirmed local matching', async()=>{
  const html=fs.readFileSync('src/wzy/static/index.html','utf8');
  const d=new JSDOM(html,{url:'http://127.0.0.1:8100',runScripts:'outside-only'});
  const products=[{key:'own:a',title:'自有',kind:'own',store_id:1,price:'10.20',matchable:true},{key:'shop:a',title:'<img src=x onerror=alert(1)>',kind:'competitor',store_id:2,price:'10.10',matchable:true}];
  const state={products,stores:[{id:1,name:'自有',url:'fixed',latest_status:'never',errors:[]},{id:2,name:'竞品',url:'fixed',latest_status:'never',errors:[]}],comparisons:[],counts:{matched:0,pending:2,own_only:1,competitor_only:1}};
  const posts=[];
  d.window.fetch=async(path,opts)=>{
    if(opts.method==='POST') posts.push([path,JSON.parse(opts.body),opts.headers]);
    return {ok:true,json:async()=>path==='/api/state'?state:[]};
  };
  d.window.eval(fs.readFileSync('src/wzy/static/app.js','utf8'));
  await new Promise(resolve=>setImmediate(resolve));
  state.stores[0].pagination_terminal='next_disabled';
  d.window.document.getElementById('refresh').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(d.window.document.getElementById('sources').textContent.includes('下一页已禁用'));
  assert.equal(d.window.document.querySelectorAll('#products tr').length,2);
  assert.equal(d.window.document.querySelector('#products img'),null);
  assert.ok(d.window.document.querySelector('#products').textContent.includes('<img'));
  d.window.document.getElementById('own-product').value='own:a';
  d.window.document.getElementById('competitor-product').value='shop:a';
  d.window.document.getElementById('confirmed').checked=true;
  d.window.document.getElementById('match-form').dispatchEvent(new d.window.Event('submit',{cancelable:true}));
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(posts[0].slice(0,2),['/api/matches',{own_key:'own:a',competitor_key:'shop:a',confirmed:true}]);
  assert.equal(posts[0][2]['X-WZY-Request'],'1');
  d.window.close();
});


test('login sends same-origin JSON, clears password and renders generic errors safely',async()=>{
  const d=new JSDOM(fs.readFileSync('src/wzy/static/login.html','utf8'),{url:'https://wzy.example.invalid/login',runScripts:'outside-only'});
  let request;
  d.window.fetch=async(path,opts)=>{request={path,opts};return {ok:false,status:401,json:async()=>({detail:'<img src=x onerror=alert(1)>'})};};
  d.window.eval(fs.readFileSync('src/wzy/static/login.js','utf8'));
  const doc=d.window.document;
  doc.getElementById('username').value='synthetic';
  doc.getElementById('password').value='synthetic-only-test';
  doc.getElementById('login-form').dispatchEvent(new d.window.Event('submit',{cancelable:true}));
  await new Promise(r=>setTimeout(r,10));
  assert.equal(request.path,'/api/auth/login');
  assert.equal(request.opts.headers['X-WZY-Request'],'1');
  assert.equal(request.opts.credentials,'same-origin');
  assert.equal(JSON.parse(request.opts.body).password,'synthetic-only-test');
  assert.equal(doc.getElementById('password').value,'');
  assert.equal(doc.querySelectorAll('img').length,0);
  assert.match(doc.getElementById('login-notice').textContent,/登录失败/);
  assert.equal(d.window.localStorage.length,0);
  assert.equal(doc.getElementById('login-submit').disabled,false);
});


test('expired workbench clears business data without losing status error handling',async()=>{
  const virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError', error=>{if(!error.message.startsWith('Not implemented: navigation'))throw error;});
  const d=new JSDOM(fs.readFileSync('src/wzy/static/index.html','utf8'),{url:'https://wzy.example.invalid/',runScripts:'outside-only',virtualConsole});
  const dialog=d.window.document.getElementById('history-dialog');
  dialog.setAttribute('open','');
  d.window.document.getElementById('history-content').textContent='SYNTHETIC PRIVATE HISTORY';
  d.window.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'请先登录'})});
  d.window.eval(fs.readFileSync('src/wzy/static/app.js','utf8'));
  await new Promise(r=>setTimeout(r,20));
  assert.equal(d.window.document.getElementById('products'),null);
  assert.ok(d.window.document.getElementById('notice'));
  assert.equal(d.window.document.body.textContent.includes('SYNTHETIC PRIVATE HISTORY'),false);
  assert.equal(d.window.document.querySelector('dialog[open]'),null);
});


test('successful logout posts CSRF header and clears business history',async()=>{
  const virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError', error=>{if(!error.message.startsWith('Not implemented: navigation'))throw error;});
  const d=new JSDOM(fs.readFileSync('src/wzy/static/index.html','utf8'),{url:'https://wzy.example.invalid/',runScripts:'outside-only',virtualConsole});
  let sent;
  d.window.fetch=(path,opts)=>{
    if(path==='/api/auth/logout'){sent=opts;return Promise.resolve({ok:true,status:200,json:async()=>({ok:true})});}
    return new Promise(()=>{});
  };
  d.window.eval(fs.readFileSync('src/wzy/static/app.js','utf8'));
  d.window.document.getElementById('history-content').textContent='PRIVATE LOGOUT HISTORY';
  d.window.document.getElementById('history-dialog').setAttribute('open','');
  d.window.document.getElementById('logout').click();
  await new Promise(r=>setTimeout(r,10));
  assert.equal(sent.method,'POST');
  assert.equal(sent.headers['X-WZY-Request'],'1');
  assert.equal(d.window.document.body.textContent.includes('PRIVATE LOGOUT HISTORY'),false);
  assert.equal(d.window.document.getElementById('products'),null);
  assert.match(d.window.document.getElementById('notice').textContent,/已退出/);
});
