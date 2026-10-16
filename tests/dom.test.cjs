const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');
const fs = require('node:fs');
const scripts = JSON.parse(execFileSync(process.env.WZY_TEST_PYTHON || '.venv/bin/python', ['-c', 'import json; from wzy.collector import EXTRACT,NEXT_STATE,NEXT_CLICK; print(json.dumps([EXTRACT,NEXT_STATE,NEXT_CLICK]))'], {encoding:'utf8'}));
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
