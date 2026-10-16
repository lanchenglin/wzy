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
function clearBusinessView() {
  document.querySelector('main').replaceChildren($('notice'));
  const dialog = $('history-dialog');
  if (dialog) { dialog.removeAttribute('open'); dialog.replaceChildren(); }
  state = {products:[], stores:[], comparisons:[]};
}
function notice(text, error=false) { $('notice').textContent=text; $('notice').className=error?'error':''; }
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json','X-WZY-Request':'1'}, ...(body?{body:JSON.stringify(body)}:{})});
  if (response.status === 401) {
    clearBusinessView();
    notice('会话已过期，请重新登录。', true);
    window.location.replace('/login');
    throw new Error('请重新登录');
  }
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
  $('logout').hidden = !state.auth_enabled;
  renderSources(); renderProducts(); renderMatches(); renderListingPolicy(); renderGroups(); renderSuggestions();
  await refreshOperations();
  await refreshSchedules();
  await refreshInbox();
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
    evidence.append(el('small',time(p.observed_at)),el('small',`${p.freshness==='fresh'?'时间新鲜':'时间陈旧或未知'}；SKU 覆盖未知`));
    if(p.latest_observation && p.latest_observation.run_id!==p.run_id) evidence.append(el('small',`较新暂存观察 ¥${p.latest_observation.price??'未知'}；#${p.latest_observation.run_id}；${time(p.latest_observation.collected_at)}；${statusNames[p.latest_observation.quality]}`));
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
const detailNames={family:'产品系列',model:'型号',plan:'套餐',quota:'额度',duration:'期限',delivery:'交付方式',warranty:'质保'};
function productEvidence(p) {
  if(!p)return '商品缺失，需核对';
  return `${p.stock||'库存未知'} / ${p.status||'状态未知'}；${p.freshness==='fresh'?'时间新鲜':'时间陈旧或未知'}；${statusNames[p.quality]||'完整性未知'}；#${p.run_id??'—'} ${time(p.collected_at||p.observed_at)}；SKU 覆盖未知`;
}
function renderGroups() {
  const rows=[];
  for(const group of state.groups||[]) {
    for(const c of group.competitors.length?group.competitors:[null]) {
      const tr=el('tr'), own=group.own, comp=c?.product;
      const left=el('td',own?`${own.title} / ¥${own.price??'未知'}`:'无自有匹配');
      left.append(el('small',productEvidence(own)));
      const right=el('td',comp?`${state.stores.find(s=>s.id===comp.store_id)?.name||'店铺 #'+comp.store_id} / ${comp.title} / ¥${comp.price??'未知'}`:'无竞品或身份缺失');
      right.append(el('small',c?.confirmed?'人工确认组':'未匹配'));
      const proof=el('td',productEvidence(comp));
      if(comp?.latest_observation&&comp.latest_observation.run_id!==comp.run_id) proof.append(el('small',`较新暂存 ¥${comp.latest_observation.price??'未知'} #${comp.latest_observation.run_id} ${time(comp.latest_observation.collected_at)} ${statusNames[comp.latest_observation.quality]}`));
      tr.append(left,right,el('td',`¥${c?.difference??'未知'} / ${c?.percentage==null?'未知':c.percentage+'%'}${c?.usable?'':'（参考证据，不可靠比较）'}`),proof,el('td',group.lowest_available?`${state.stores.find(s=>s.id===group.lowest_available.store_id)?.name||'店铺 #'+group.lowest_available.store_id} / ${group.lowest_available.title} ¥${group.lowest_available.price}`:'无符合条件的证据'));
      rows.push(tr);
    }
  }
  $('comparison-groups').replaceChildren(...rows);
}
function renderSuggestions() {
  $('suggestions').replaceChildren(...(state.suggestions||[]).map(s=>{
    const own=state.products.find(p=>p.key===s.own_key), comp=state.products.find(p=>p.key===s.competitor_key);
    const row=el('details'), summary=el('summary',`${own?.title||s.own_key} / ${comp?.title||s.competitor_key}：${{low:'低置信度',medium:'中等置信度',conflict:'存在冲突'}[s.confidence]}`);
    row.append(summary,el('p',`相同明确字段：${s.reasons.map(k=>detailNames[k]).join('、')||'无'}；冲突：${s.conflicts.map(k=>detailNames[k]).join('、')||'未发现（不代表等价）'}；未知：${s.unknown.map(k=>detailNames[k]).join('、')||'无'}`));
    for(const [key,label] of Object.entries(detailNames)) row.append(el('p',`${label}：自有 ${s.own_fields[key]??'未知'} / 竞品 ${s.competitor_fields[key]??'未知'}`));
    row.append(action('带入人工核对表单',()=>{$('own-product').value=s.own_key;$('competitor-product').value=s.competitor_key;$('confirmed').checked=false;$('confirmed').focus();notice('仅带入候选，尚未保存。请核对冲突与未知字段后逐次确认。');},true));
    return row;
  }));
}
let scheduleState={paused:false,sources:[]};
async function refreshSchedules() {
  scheduleState=await api('/api/scheduler');
  $('scheduler-pause').textContent=scheduleState.paused?'恢复全局定时任务':'暂停全部定时任务';
  $('scheduler-status').textContent=`${scheduleState.paused?'全局已暂停':'全局未暂停（仅启用的来源会运行）'}；${scheduleState.busy?'账号任务执行中':'当前无应用账号任务'}`;
  $('schedules').replaceChildren(...(scheduleState.sources||[]).map(s=>{
    const form=el('form');form.id='schedule-'+s.store_id;
    const enabled=el('input');enabled.type='checkbox';enabled.checked=s.enabled;
    const enabledLabel=el('label',`${state.stores.find(x=>x.id===s.store_id)?.name||'来源 #'+s.store_id}：启用定时只读采集 `);enabledLabel.append(enabled);
    const interval=el('input');interval.type='number';interval.min='15';interval.max='1440';interval.step='1';interval.required=true;interval.value=s.interval_minutes;
    const intervalLabel=el('label','间隔（分钟） ');intervalLabel.append(interval);
    const resume=el('input');resume.type='checkbox';
    const resumeLabel=el('label','已人工处理登录/挑战，恢复此来源 ');resumeLabel.append(resume);resumeLabel.hidden=!s.blocked;
    const submit=el('button','保存定时设置');submit.type='submit';
    form.append(enabledLabel,intervalLabel,resumeLabel,submit,el('p',`下次 ${time(s.next_run)}；最后尝试 ${time(s.last_attempt)}；最后完整成功 ${time(s.last_success)}；结果 ${statusNames[s.last_outcome]||'尚无'}；浏览器历史证据 ${{not_observed:'未调用',observed:'已观察页面',login_or_challenge:'登录/挑战',unavailable_or_failed:'不可用或采集失败'}[s.browser_evidence]||'未知'}${s.blocked?'；来源已阻断，需人工恢复':''}`,'hint'));
    form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;try{await api('/api/scheduler/sources/'+s.store_id,'PUT',{enabled:enabled.checked,interval_minutes:Number(interval.value),resume:resume.checked});await refreshSchedules();notice('定时设置已保存；不会立即补跑。');}catch(error){notice(error.message,true);}finally{submit.disabled=false;}});
    return form;
  }));
}
$('scheduler-pause').addEventListener('click',async()=>{try{await api('/api/scheduler','PUT',{paused:!scheduleState.paused});await refreshSchedules();}catch(e){notice(e.message,true);}});
let inboxCursor=null, inboxNext=null;
async function refreshInbox(cursor=null) {
  inboxCursor=cursor;
  const inbox=await api('/api/notifications'+(cursor?'?before_run='+cursor:''));
  inboxNext=inbox.next_before_run;
  $('inbox-older').disabled=!inboxNext;
  $('inbox-summary').textContent=`未读 ${inbox.unread??0} 条；${cursor?'较早':'最新'}一页最多 100 条运行聚合通知。无外部推送。`;
  $('inbox').replaceChildren(...(inbox.events||[]).map(event=>{
    const row=el('article');
    row.append(el('h3',`#${event.run_id} ${statusNames[event.quality]||'未知'} ${event.kind==='diagnostic'?'采集状态变化':'已观察商品变化'}${event.read?'（已读）':'（未读）'}${event.acknowledged?'（已确认）':''}`),el('p',`${time(event.created_at)}；${event.quality==='complete'?'列表完整仍不代表 SKU 完整':'部分/未知/失败证据，不是可交易信号'}`,'hint'));
    for(const c of event.changes)row.append(el('p',`${c.title}：${{price_up:'价格上涨',price_down:'价格下降',stock:'明确库存状态变化'}[c.type]||c.type} ${c.before} → ${c.after}`));
    const update=async(read,acknowledged)=>{await api('/api/notifications/'+encodeURIComponent(event.id),'PATCH',{read,acknowledged});await refreshInbox(inboxCursor);};
    row.append(action(event.read?'标为未读':'标为已读',()=>update(!event.read,false),true),action('确认通知',()=>update(true,true),true));
    return row;
  }));
}
$('inbox-older').addEventListener('click',()=>refreshInbox(inboxNext).catch(e=>notice(e.message,true)));
$('inbox-latest').addEventListener('click',()=>refreshInbox().catch(e=>notice(e.message,true)));
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
refresh().then(()=>notice('本地工作台已就绪。定时采集仅运行显式启用的来源；刷新本地数据查看最新通知。')).catch(e=>notice(e.message,true));

$('logout').addEventListener('click', async () => {
  try {
    await api('/api/auth/logout', 'POST');
    clearBusinessView();
    notice('已退出登录。');
    window.location.replace('/login');
  } catch (e) { notice(e.message, true); }
});
