'use strict';
const $ = id => document.getElementById(id);
const statusNames = {complete:'列表完整', partial:'部分结果', unknown:'完整性未知', failed:'采集失败', paused:'需人工处理', running:'采集中', never:'尚未采集'};
let state = {products:[], stores:[], comparisons:[]};
let collecting = false;
const terminalNames = {next_disabled:'下一页已禁用（末页证据，不等于总数完整）', controls_absent:'未发现分页控件（非末页证明）', not_reached:'尚未取得末页证据', not_observed:'未观察分页'};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(text, error=false) { $('notice').textContent=text; $('notice').className=error?'error':''; }
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json','X-WZY-Request':'1'}, ...(body?{body:JSON.stringify(body)}:{})});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '输入不合法，请核对填写内容');
  return data;
}
function action(label, handler, secondary=false) {
  const button = el('button', label, secondary?'secondary':'');
  button.type='button';
  button.addEventListener('click', () => Promise.resolve().then(handler).catch(e=>notice(e.message,true)));
  return button;
}
function time(value) { return value ? new Date(value).toLocaleString('zh-CN') : '暂无'; }
async function refresh() {
  state = await api('/api/state');
  renderSources(); renderProducts(); renderMatches(); renderListingPolicy();
  await refreshOperations();
  const runs = await api('/api/runs');
  $('runs').replaceChildren(...runs.map(r=>el('p', `#${r.id}　${time(r.started)}　${statusNames[r.status]}　实际 ${r.actual_count??'—'} / 声明 ${r.declared_count??'未知'}　${r.errors.join('、')}`)));
  if (!runs.length) $('runs').append(el('p','还没有采集记录。请在上方选择来源开始只读采集。'));
}
function renderSources() {
  $('sources').replaceChildren(...state.stores.map(s=>{
    const row=el('div',undefined,'source'), name=el('div'), evidence=el('div');
    name.append(el('strong',s.name),el('small',s.url));
    evidence.append(el('span',`${statusNames[s.latest_status]} · 实际 ${s.actual_count??'—'} / 声明 ${s.declared_count??'未知'}`,s.stale?'warning':''),el('small',`最后记录：${time(s.latest_at)}；完整快照：${s.snapshot_run?'#'+s.snapshot_run:'暂无'}`));
    evidence.append(el('small',terminalNames[s.pagination_terminal] || terminalNames.not_observed));
    if(s.errors.length) evidence.append(el('small',s.errors.join('、')));
    const button=action('只读采集',async()=>{
      collecting=true; renderSources(); notice('正在采集；请勿在同一账号执行其他自动化任务。');
      try { const result=await api('/api/collect','POST',{store_id:s.id}); notice(`${statusNames[result.status]}。${result.errors.length?'请检查浏览器登录或页面结构；不会自动重试。':'列表与 SKU 覆盖需分别核验。'}`,result.status!=='complete'); }
      finally { collecting=false; await refresh(); }
    });
    button.disabled=collecting; row.append(name,evidence,button); return row;
  }));
}
function renderProducts() {
  const search=$('search').value.trim().toLowerCase(), filter=$('filter').value;
  const matched=new Set(state.comparisons.filter(m=>m.own&&m.competitor&&!m.needs_review).flatMap(m=>[m.own_key,m.competitor_key]));
  const products=state.products.filter(p=>(filter==='all'||p.kind===filter||(filter==='pending'&&!matched.has(p.key)))&&`${p.title} ${p.category}`.toLowerCase().includes(search));
  $('summary').textContent=`已匹配 ${state.counts.matched} 对；待匹配 ${state.counts.pending} 项（自有侧 ${state.counts.own_only}，竞品侧 ${state.counts.competitor_only}）。未匹配不代表缺货或已移除。`;
  $('empty').hidden=products.length!==0;
  $('products').replaceChildren(...products.map(p=>{
    const tr=el('tr'), title=el('td',p.title), source=el('td',state.stores.find(s=>s.id===p.store_id)?.name||'');
    source.append(el('small',p.category||'自有库存')); title.append(el('small',p.description||''));
    if(p.consignment) title.append(el('small','代销'));
    const evidence=el('td',p.provisional?'暂存观察，非完整快照':p.stale?'保留完整快照，当前采集异常':'完整快照');
    evidence.append(el('small',time(p.observed_at)),el('small','SKU 覆盖未知'));
    const history=el('td'); history.append(action('历史',()=>showHistory(p),true));
    tr.append(source,title,el('td',p.price===null?'价格未知':`¥ ${p.price}`,'money'),el('td',`${p.stock||'库存未知'} / ${p.status||'状态未采集'}`),evidence,history); return tr;
  }));
}
function renderMatches() {
  for(const [id,kind] of [['own-product','own'],['competitor-product','competitor']]) {
    const select=$(id), old=select.value;
    const placeholder=el('option','请选择商品'); placeholder.value='';
    select.replaceChildren(placeholder,...state.products.filter(p=>p.kind===kind&&p.matchable).map(p=>{
      const option=el('option',`${p.title} [${p.category||'自有'}] ¥${p.price??'未知'} (${p.key.slice(-8)})`); option.value=p.key; return option;
    }));
    select.value=old;
  }
  $('matches').replaceChildren(...state.comparisons.map(m=>{
    const tr=el('tr'), controls=el('td');
    controls.append(action('移除匹配',async()=>{if(!confirm('仅移除本地匹配，不修改商品。继续？'))return;await api('/api/matches','DELETE',{competitor_key:m.competitor_key});await refresh();notice('已移除本地匹配。');},true));
    tr.append(el('td',m.own?.title||'当前快照无此自有商品'),el('td',m.competitor?.title||'当前快照无此竞品'),el('td',m.difference===null?'无法比较':`¥ ${m.difference}`,'money'),el('td',m.needs_review?'身份缺失或不明确，需重新核对':m.stale?'数据陈旧或一侧缺失':'人工已确认'),controls);return tr;
  }));
}
async function showHistory(p) {
  const rows=await api('/api/history?key='+encodeURIComponent(p.key));
  $('history-title').textContent=p.title+' · 价格历史';
  $('history-content').replaceChildren(...rows.map(r=>el('p',`${time(r.observed_at)}　¥ ${r.price??'未知'}　${statusNames[r.run_status]}　${r.stock}`)));
  $('history-dialog').showModal();
}
let listingBusy=false, verifiedItem='', pendingListing=null;
const listingStates={up:'销售中',down:'已下架'};
const operationStates={pending:'待执行',dispatched:'已发送，待读回',succeeded:'读回成功',noop:'无需修改',rejected:'已拒绝',uncertain:'结果不确定，需人工核验（禁止盲目重试）'};
function renderListingPolicy() {
  const allowed=state.listing_allowed_items;
  $('listing-policy').textContent=(state.listing_writes_enabled?'真实上下架已启用。':'真实上下架未启用。')+(allowed==null?'允许目标：自有库存中的明确 ID（无额外白名单）。':`允许目标 ID：${allowed.length?allowed.join('、'):'无（空白名单）'}。`);
  $('listing-submit').disabled=!state.listing_writes_enabled||listingBusy;
  $('listing-read').disabled=listingBusy;
}
async function refreshOperations() {
  const operations=await api('/api/listings/operations');
  $('listing-operations').replaceChildren(...operations.map(o=>el('p',`${o.id} · ${o.item_id} · ${listingStates[o.expected]} → ${listingStates[o.desired]} · ${operationStates[o.status]||o.status} · 读回 ${listingStates[o.after_state]||'未知'} · ${time(o.finished_at||o.created_at)} ${o.error||''}`)));
}
$('listing-item').addEventListener('input',()=>{verifiedItem='';$('listing-expected').value='';$('listing-confirmed').checked=false;});
$('listing-desired').addEventListener('change',()=>{$('listing-confirmed').checked=false;});
$('listing-read').addEventListener('click',async()=>{
  if(listingBusy)return;
  const item=$('listing-item').value.trim();
  if(!/^[A-Za-z0-9_-]{1,128}$/.test(item)){notice('请输入明确的自有商品 ID。',true);return;}
  listingBusy=true;renderListingPolicy();
  try {
    const evidence=await api('/api/listings/items/'+encodeURIComponent(item));
    if($('listing-item').value.trim()!==item)return;
    verifiedItem=item;$('listing-expected').value=evidence.state;$('listing-confirmed').checked=false;
    $('listing-evidence').textContent=`自有商品 ${item}：${listingStates[evidence.state]||'未知'} · ${time(evidence.observed_at)}。独立状态证据，不代表列表或 SKU 完整。`;
  } catch(e){verifiedItem='';$('listing-expected').value='';notice(e.message,true);}
  finally{listingBusy=false;renderListingPolicy();}
});
$('listing-form').addEventListener('submit',async e=>{
  e.preventDefault();
  if(listingBusy||!state.listing_writes_enabled)return;
  const item=$('listing-item').value.trim(), desired=$('listing-desired').value, expected=$('listing-expected').value;
  if(item!==verifiedItem||!expected||!$('listing-confirmed').checked){notice('请先核验当前状态并明确勾选确认。',true);return;}
  if(state.listing_allowed_items!=null&&!state.listing_allowed_items.includes(item)){notice('商品不在允许写入列表。',true);return;}
  const request={item_id:item,desired,expected,confirmed:true};
  const signature=JSON.stringify(request);
  if(pendingListing&&pendingListing.signature!==signature){notice('上一次请求结果尚未确定，请先在审计中核验；不要换目标或换键重试。',true);return;}
  if(!pendingListing)pendingListing={signature,body:{...request,idempotency_key:crypto.randomUUID()}};
  listingBusy=true;renderListingPolicy();
  try {
    const result=await api('/api/listings/state','POST',pendingListing.body);
    $('listing-evidence').textContent=`操作 ${result.id}：${operationStates[result.status]||result.status}；读回 ${listingStates[result.after_state]||'未知'}。${result.error||''}`;
    pendingListing=null;verifiedItem='';$('listing-expected').value='';$('listing-confirmed').checked=false;
    await refreshOperations();
  } catch(e){notice('请求未确认：'+e.message+'。保留相同幂等键；再次提交只查询/复用同一操作，请检查审计。',true);}
  finally{listingBusy=false;renderListingPolicy();}
});
$('shop-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/shops','POST',{url:$('shop-url').value.trim()});$('shop-url').value='';await refresh();notice('店铺已保存，尚未采集。');}catch(e){notice(e.message,true);}});
$('match-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/matches','POST',{own_key:$('own-product').value,competitor_key:$('competitor-product').value,confirmed:$('confirmed').checked});$('confirmed').checked=false;await refresh();notice('人工匹配已保存。');}catch(e){notice(e.message,true);}});
$('refresh').addEventListener('click',()=>refresh().then(()=>notice('已刷新本地数据；未访问浏览器。')).catch(e=>notice(e.message,true)));
$('filter').addEventListener('change',renderProducts);$('search').addEventListener('input',renderProducts);
$('close-history').addEventListener('click',()=>$('history-dialog').close());
refresh().then(()=>notice('本地工作台已就绪。只读采集需要你主动启动。')).catch(e=>notice(e.message,true));
