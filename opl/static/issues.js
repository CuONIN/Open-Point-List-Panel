// 渲染双语问题表、筛选标签、排序与导出。 / Render the bilingual grid, filter tags, sorting and downloads.
import {api,areaToken,context,count,empty,esc,go,icon,issueLink,multiKeys,options,pageTitle,priorityLabel,qs,statePill,valuesOf} from "./core.js";
import {distributionTable} from "./overview.js";

const advanced=[
  ["station","工位"],["machine","八位机器号"],["avo","AVO"],["department","客户部门"],
  ["category","客户类别"],["author","客户提出者"],["responsible","客户责任方"],["owner","我方负责人"],["zf","ZF 责任维度"]
];
const modes=[["current","当前状态"],["occurred","期间进入过"],["latest","最近一次变化"],["asof","截至所选日"],
  ["planned","本周实施计划"],["unreported","本周计划尚未上报"]];
const scopes=[["ours","我方未结"],["customer","客户未结"],["all","全部客户记录"]];
const labels=Object.fromEntries([...advanced,["q","关键词"],["area","区域"],["state","我方状态"],["priority","优先级"],["week","统计周"],["as_of","截止日"],["mode","周筛选口径"],["event_kind","事件类型"]]);
let advancedOpen=false,exportOrientation="portrait",exportLayout="field";
function numberSort(params){return params.sort==="number_asc"?"number_asc":"number_desc";}
function sortButton(params,cls){
  const asc=numberSort(params)==="number_asc";
  return '<button type="button" class="'+cls+'" data-toggle-sort aria-label="当前客户号'+(asc?"升序，切换为降序":"降序，切换为升序")+'" title="切换客户号排序">客户号 <span aria-hidden="true">'+(asc?"↑":"↓")+'</span></button>';
}
function nameTokens(text){
  return String(text).normalize("NFD").replace(/\p{M}/gu,"").toLocaleLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}
const BLANK="__blank__", NONE="__none__";
const stateArchive=new Set(["closed","zf_only"]);
function pureValues(){
  return new Set(context.config.pure_responsibles ?? (context.config.filters?.responsible || []).filter(v=>/^zf$/i.test(v)));
}
function explicitValues(key,value){
  const values=valuesOf(value);
  if(values.includes(NONE))return [];
  return valuesOf(values.flatMap(v=>key==="zf" && v==="any"?["pure","joint"]:v));
}
function scopeOf(p){
  if(scopes.some(([k])=>k===p.scope))return p.scope;
  const pure=pureValues();
  return p.mode==="occurred" || explicitValues("state",p.state).some(s=>stateArchive.has(s)) ||
    explicitValues("responsible",p.responsible).some(v=>pure.has(v)) || explicitValues("zf",p.zf).includes("pure")?"all":"ours";
}
function choices(key,value){
  const cfg=context.config, actual=cfg.filters?.[key] || [];
  let entries=actual;
  if(key==="state")entries=Object.entries(cfg.states || {});
  if(key==="area")entries=["S1","S2","General",...actual];
  if(key==="priority")entries=["High","Medium","Low",...actual];
  if(key==="zf")entries=[["pure","纯 ZF"],["joint","豪森／ZF 共同责任"],["none","无 ZF 参与"],...actual.filter(v=>v===BLANK)];
  const result=new Map();
  const blankLabel=key==="zf"?"待核／未分类":"（未填写）";
  for(const entry of entries){
    const [raw,name]=Array.isArray(entry)?entry:[entry,entry],v=String(raw || BLANK);
    if(v!==NONE)result.set(v,v===BLANK?blankLabel:String(name));
  }
  for(const v of explicitValues(key,value))if(!result.has(v))result.set(v,v===BLANK?blankLabel:v);
  return result;
}
function preset(key,all,scope){
  if(key==="state" && scope!=="all")return all.filter(v=>v!=="closed" && (scope==="customer" || v!=="zf_only"));
  if(key==="responsible" && scope==="ours"){const pure=pureValues();return all.filter(v=>!pure.has(v));}
  return [...all];
}
function selection(key,p){
  const options=choices(key,p[key]), all=[...options.keys()];
  const selected=valuesOf(p[key]).length?explicitValues(key,p[key]):preset(key,all,scopeOf(p));
  return {options,all,selected};
}
function equalSet(a,b){return a.length===b.length && a.every(v=>b.includes(v));}
function summaryText(selected,options){
  if(!options.size)return "无可选值";
  if(!selected.length)return "未选择";
  if(selected.length===options.size)return "全部";
  return selected.length===1?options.get(selected[0]):"已选 "+selected.length+" / "+options.size;
}
function multiField(key,label,p){
  const {options,selected}=selection(key,p), pure=pureValues();
  const rows=[...options].map(([value,name])=>{
    const note=key==="state" && stateArchive.has(value)?"归档":key==="responsible" && pure.has(value)?"纯 ZF":"";
    return '<label class="multi-choice" data-option-label="'+esc(name.toLocaleLowerCase())+'"><input type="checkbox" name="'+key+'" value="'+esc(value)+'"'+(selected.includes(value)?" checked":"")+'><span>'+esc(name)+'</span>'+(note?'<small>'+esc(note)+'</small>':"")+'</label>';
  }).join("");
  return '<div class="field multi-field"><span id="label-'+key+'">'+label+'</span><details class="multi-picker" data-picker="'+key+'"><summary aria-labelledby="label-'+key+' value-'+key+'"><span id="value-'+key+'" data-picker-value>'+esc(summaryText(selected,options))+'</span>'+icon("down")+'</summary>'+
    '<div class="multi-menu"><label class="picker-search">'+icon("search")+'<input type="search" data-option-search placeholder="搜索'+esc(label)+(key==="author"?"姓名":"选项")+'" aria-label="搜索'+esc(label)+'选项"></label>'+
    '<div class="picker-actions"><button type="button" class="text-btn" data-pick="all">全选</button><button type="button" class="text-btn" data-clear-picker data-pick="none">取消全选</button><button type="button" class="text-btn" data-pick="only" hidden>仅选匹配</button></div>'+
    '<div class="multi-choices" role="group" aria-labelledby="label-'+key+'">'+rows+'<small class="muted" data-no-options'+(options.size?" hidden":"")+'>'+(options.size?"没有匹配选项":"当前无可选值")+'</small></div>'+
    '<div class="picker-foot"><small data-picker-count aria-live="polite"></small><button type="submit" class="btn small-btn primary">应用</button></div></div></details></div>';
}
function filterChip(key,value,label,action="remove"){
  return '<button type="button" class="filter-chip" data-remove-filter="'+esc(key)+'" data-filter-value="'+esc(value)+'" data-chip-action="'+action+'" aria-label="'+esc((action==="restore"?"恢复包含 ":action==="reset"?"清除筛选 ":"移除 ")+label)+'">'+esc(label)+icon("x")+'</button>';
}
function chipsFor(key,raw,p){
  if(!labels[key])return "";
  if(!multiKeys.has(key)){
    const label=key==="mode"?(modes.find(([v])=>v===raw)?.[1] || raw):raw;
    return key==="mode" && raw==="current"?"":filterChip(key,raw,labels[key]+" · "+label);
  }
  const {all,selected,options}=selection(key,p);
  if(!selected.length)return filterChip(key,NONE,labels[key]+" · 未选择","reset");
  const excluded=all.filter(v=>!selected.includes(v));
  if(!excluded.length)return "";
  const inverse=excluded.length<selected.length;
  return (inverse?excluded:selected).map(v=>filterChip(key,v,labels[key]+" · "+(inverse?"排除 ":"")+options.get(v),inverse?"restore":"remove")).join("");
}
function filters(p){
  const hasAdvanced=advancedOpen || advanced.some(([k])=>valuesOf(p[k]).length) || (!!p.mode && p.mode!=="current") || Boolean(p.as_of);
  const scope=scopeOf(p);
  const chips=Object.entries(p).map(([k,v])=>chipsFor(k,v,p)).join("");
  return '<form class="card filters" id="issue-filters"><div class="filters-row"><div class="segment" aria-label="问题范围">'+scopes.map(([k,label])=>'<button type="button" data-scope="'+k+'" aria-pressed="'+(k===scope)+'">'+label+'</button>').join("")+'</div>'+
    '<label class="search-box">'+icon("search")+'<input type="search" name="q" value="'+esc(p.q)+'" aria-label="搜索问题" placeholder="客户号（多个用逗号分隔）、姓名或关键词"></label><button class="btn primary" type="submit">应用筛选</button></div>'+
    '<div class="filters-row">'+multiField("area","区域",p)+multiField("state","我方状态",p)+multiField("priority","优先级",p)+
    '<button type="button" class="btn" data-toggle-filters aria-expanded="'+hasAdvanced+'">'+icon("filter")+'更多筛选</button><button type="button" class="text-btn" data-reset-filters>'+icon("reset")+'恢复默认筛选</button><small class="muted">同类选项取任一，跨类同时匹配</small></div>'+
    '<div class="filters-grid" id="advanced-filters"'+(hasAdvanced?"":" hidden")+'>'+advanced.map(([k,l])=>multiField(k,l,p)).join("")+
    '<label class="field">周筛选口径<select name="mode">'+options(modes,p.mode || "current")+'</select></label><label class="field">统计周<input type="week" name="week" value="'+esc(p.week || context.config.week)+'"></label><label class="field">截止日<input type="date" name="as_of" value="'+esc(p.as_of)+'"></label></div>'+
    (chips?'<div class="filter-chips">'+chips+'</div>':"")+'</form>';
}
function latestText(it){
  if(typeof it.latest==="string")return it.latest;
  return it.latest?.note || it.latest?.zh || it.latest?.original || "";
}
function englishText(it){
  return it.en && it.en.trim()!==(it.problem || "").trim();
}
function fieldWork(it){
  const links=new Map((it.related || []).filter(r=>r.id && r.number!=null).map(r=>[r.id,issueLink(r.id,r.number)]));
  const related=[...links.values()];
  const allLinks='<span class="related-links">'+related.join(" ")+'</span>';
  const list=related.length>4?'<details><summary>共 '+related.length+' 条</summary>'+allLinks+'</details>':allLinks;
  const status=it.state==="closed"
    ? '<button type="button" class="archived-state" data-issue="'+esc(it.id)+'" title="查看归档问题详情">'+statePill(it.state,it.state_label)+'</button>'
    : statePill(it.state,it.state_label,it.id);
  return '<div class="field-work"><div class="work-status">'+status+'<span>状态日 <time>'+esc(it.state_day || "—")+'</time></span></div>'+
    '<dl><dt>我方负责人</dt><dd>'+esc(it.owner || it.tracker || "待分配")+'</dd><dt>我方计划</dt><dd>'+esc(it.plan || "待安排")+'</dd>'+
    (related.length?'<dt>关联问题</dt><dd>'+list+'</dd>':"")+
    (it.order_no?'<dt>'+(String(it.order_no).trim()==="待投产"?"投产进度":"投产单号")+'</dt><dd>'+esc(it.order_no)+'</dd>':"")+'</dl></div>';
}
function row(it){
  return '<tr><td class="pin1">'+issueLink(it.id,it.number)+'<span class="cell-sub">'+areaToken(it.area)+'</span></td>'+
    '<td class="pin2"><b>'+esc(it.station || "工位待核")+'</b><span class="cell-sub code">'+esc(it.machine || "—")+'</span><span class="cell-sub">AVO '+esc(it.avo || "—")+'</span></td>'+
    '<td><div class="line-clamp '+(!it.zh?"muted":"main-cell")+'">'+esc(it.zh || "待翻译")+'</div></td><td><div class="line-clamp"'+(englishText(it)?' lang="en"':'')+'>'+esc(it.en || it.problem || "—")+'</div>'+(englishText(it)?'<span class="cell-sub">英文译文 · 原文见详情</span>':"")+'</td>'+
    '<td>'+priorityLabel(it.priority)+'</td><td><div class="line-clamp">'+esc(it.author || "—")+'</div></td><td><div class="line-clamp">'+esc(latestText(it) || "暂无反馈")+'</div><span class="cell-sub">'+esc(it.latest_day || "")+'</span></td>'+
    '<td><div>'+esc(it.department || "—")+'</div><span class="cell-sub">'+esc(it.category || "—")+'</span></td>'+
    '<td>'+esc(it.responsible || "责任方待核")+'<span class="cell-sub">截止 '+esc(it.due || "—")+'</span></td>'+
    '<td class="work-cell">'+fieldWork(it)+'</td></tr>';
}
function mobile(it){
  return '<article class="issue-mobile-card"><div class="flow-line">'+issueLink(it.id,it.number)+areaToken(it.area)+priorityLabel(it.priority)+'</div><p><b>'+esc(it.station || "工位待核")+'</b> <span class="code">'+esc(it.machine)+'</span></p>'+
    '<span class="eyelabel">中文</span><p class="line-clamp">'+esc(it.zh || "待翻译")+'</p><span class="eyelabel">'+(englishText(it)?"英文译文":"客户原文")+'</span><p class="line-clamp">'+esc(it.en || it.problem || "—")+'</p>'+
    '<p class="cell-sub">客户提出者 · '+esc(it.author || "—")+'</p><p class="cell-sub">客户责任方 · '+esc(it.responsible || "待核")+(it.due?"　截止 "+esc(it.due):"")+'</p>'+fieldWork(it)+'</article>';
}
export async function issues(params,signal){
  const res=await api("issues"+qs({...params,scope:scopeOf(params)}),{signal}), items=res.items || [];
  const page=Number(res.page || 1),pages=Math.max(1,Number(res.pages || 1));
  const table=items.length?'<div class="table-scroll desktop-issues" tabindex="0" aria-label="双语问题表，可横向滚动"><table class="issue-table"><colgroup>'+[81,174,225,230,90,140,184,158,150,300].map(w=>'<col style="width:'+w+'px">').join("")+'</colgroup><thead><tr><th class="pin1" aria-sort="'+(numberSort(params)==="number_asc"?"ascending":"descending")+'">'+sortButton(params,"sort-head")+'</th><th class="pin2">工位／设备</th><th>问题中文</th><th>英文／客户原文</th><th>优先级</th><th>客户提出者</th><th>最近反馈／行动</th><th>客户部门／类别</th><th>客户责任方／截止</th><th>现场处理</th></tr></thead><tbody>'+items.map(row).join("")+'</tbody></table></div><div class="issue-list-mobile">'+items.map(mobile).join("")+'</div>':empty("当前筛选没有匹配的问题。可调整条件或恢复默认筛选。","search");
  const dispatch='<div class="tools export-tools">'+sortButton(params,"btn small-btn mobile-sort")+
    '<label class="paper-choice">派工单内容<select data-layout>'+options([["field","现场精简（中文优先）"],["bilingual","中英对照"]],exportLayout)+'</select></label>'+
    '<label class="paper-choice">纸张<select data-orientation>'+options([["portrait","A4 竖版"],["landscape","A4 横版"]],exportOrientation)+'</select></label>'+
    '<button type="button" class="btn small-btn" data-export="xlsx">'+icon("import")+'导出 Excel</button><button type="button" class="btn small-btn" data-export="pdf">'+icon("import")+'导出 PDF</button></div>';
  return pageTitle("问题清单","EOL 测试线 · 双语问题跟踪",'<a class="btn" href="#imports">'+icon("import")+'导入清单</a>')+'<div class="stack">'+filters(params)+'<section class="card"><div class="card-head"><h2>问题明细 <span class="tag num">'+count(res.total)+'</span></h2>'+dispatch+'</div><div class="export-status" role="status" aria-live="polite"></div>'+table+
    '<div class="pager"><span>第 '+count(page)+' / '+count(pages)+' 页</span><div class="tools"><button type="button" class="btn small-btn" data-page="'+(page-1)+'"'+(page<=1?" disabled":"")+'>'+icon("prev")+'上一页</button><button type="button" class="btn small-btn" data-page="'+(page+1)+'"'+(page>=pages?" disabled":"")+'>下一页'+icon("next")+'</button></div></div></section>'+
    '<section class="card"><details class="collapse"><summary>查看本次筛选的工位统计</summary>'+distributionTable(res.stations || [],"station",params)+'</details></section></div>';
}
function queryForExport(params,orientation,layout) {
  const values={...params,scope:scopeOf(params),sort:numberSort(params),orientation,layout};delete values.page;delete values.limit;delete values.issue;return qs(values);
}
async function download(format,params,node){
  if(node.dataset.exporting || !["xlsx","pdf"].includes(format))return;
  const msg=node.querySelector(".export-status"), buttons=[...node.querySelectorAll("[data-export]")];
  const label=format==="xlsx"?"Excel":"PDF";
  node.dataset.exporting=format;buttons.forEach(b=>b.disabled=true);
  const orientation=node.querySelector("[data-orientation]").value, layout=node.querySelector("[data-layout]").value;
  msg.className="export-status status-note";msg.textContent="正在按当前筛选生成 "+label+" 派工单…";
  try{
    const response=await fetch("/api/issues/export."+format+queryForExport(params,orientation,layout),{headers:{Accept:format==="pdf"?"application/pdf":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},credentials:"same-origin",cache:"no-store"});
    const type=(response.headers.get("Content-Type") || "").toLowerCase();
    if(!response.ok || type.includes("application/json") || type.includes("text/html")){
      const json=type.includes("json")?await response.json().catch(()=>({})):{};
      throw new Error(json.error || "导出未完成（HTTP "+response.status+"），请稍后重试。");
    }
    const blob=await response.blob();if(!blob.size)throw new Error("导出文件为空，请重试。");
    const disposition=response.headers.get("Content-Disposition") || "";
    const encoded=disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i), ascii=disposition.match(/(?:^|;)\s*filename\s*=\s*(?:"([^"]+)"|([^;]+))/i);
    let name="EOL-派工单-"+context.config.today+"."+format;
    try{if(encoded)name=decodeURIComponent(encoded[1].trim());else if(ascii)name=(ascii[1] || ascii[2]).trim();}catch{ /* Keep the fallback filename. */ }
    const link=document.createElement("a"), url=URL.createObjectURL(blob);
    link.href=url;link.download=name.replace(/[\\/:*?"<>|]/g,"_");document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
    msg.className="export-status status-ok";msg.textContent=label+" 派工单已生成，已交给浏览器下载。";
  }catch(e){msg.className="export-status status-error";msg.textContent=e.name==="TypeError"?"无法取得导出文件，请确认本地服务仍在运行后重试。":e.message;}
  finally{delete node.dataset.exporting;buttons.forEach(b=>b.disabled=false);}
}
function picked(picker){
  return [...picker.querySelectorAll('input[type="checkbox"]:checked')].map(x=>x.value);
}
export function positionOpenFilter(){
  const picker=document.querySelector("#issue-filters [data-picker][open]");
  if(!picker)return;
  const {left,top,bottom}=picker.getBoundingClientRect(),menu=picker.querySelector(".multi-menu");
  const below=window.innerHeight-bottom,above=top;
  picker.style.setProperty("--menu-shift",Math.max(12-left,Math.min(0,window.innerWidth-12-left-menu.offsetWidth))+"px");
  picker.classList.toggle("menu-above",below<menu.offsetHeight+10 && above>below);
}
// 调整范围时，只解除和用户新勾选项冲突的默认排除项。 / Relax paired defaults when an archived choice is added deliberately.
function buildSelection(source, models, checked, mode, switchScope){
  let scope=switchScope || scopeOf(source);
  const state=models.state, resp=models.responsible, pure=pureValues();
  const states=checked.state, owners=checked.responsible;
  const changed=key=>!equalSet(checked[key],models[key].selected);
  if(switchScope || (!source.scope && mode==="occurred" && source.mode!=="occurred")){
    if(!switchScope)scope="all";
    for(const key of ["state","responsible"]){
      if(switchScope || (!source[key] && !changed(key)))checked[key]=preset(key,models[key].all,scope);
    }
  }else{
    const addedState=v=>states.includes(v) && !state.selected.includes(v);
    const addedPure=owners.some(v=>pure.has(v) && !resp.selected.includes(v));
    const requestedZF=changed("zf") && checked.zf.includes("pure") && !equalSet(checked.zf,models.zf.all);
    const transfer=addedState("zf_only"), closed=addedState("closed");
    if((transfer || requestedZF) && !changed("responsible") && equalSet(owners,preset("responsible",resp.all,"ours")))checked.responsible=[...resp.all];
    const active=state.all.filter(v=>!stateArchive.has(v));
    if((addedPure || requestedZF) && !changed("state") && !states.includes("zf_only") && active.every(v=>states.includes(v))){
      checked.state=state.all.filter(v=>v==="zf_only" || states.includes(v));
    }
    if(transfer || closed || addedPure || requestedZF)scope="all";
  }
  const result={scope};
  for(const key of multiKeys){
    const all=models[key].all, selected=checked[key];
    if(!all.length && !source[key])continue;
    if(equalSet(selected,preset(key,all,scope)) && (selected.length || switchScope || !source[key]))continue;
    result[key]=selected.length?selected:[NONE];
  }
  return result;
}
export function bindIssues(node,params){
  const form=node.querySelector("#issue-filters");
  const source={...params};delete source.issue;
  const models=Object.fromEntries([...multiKeys].map(k=>[k,selection(k,source)]));
  node.querySelector("[data-orientation]").onchange=e=>{exportOrientation=e.target.value==="landscape"?"landscape":"portrait";};
  node.querySelector("[data-layout]").onchange=e=>{exportLayout=e.target.value==="bilingual"?"bilingual":"field";};
  function refreshPicker(picker){
    const selected=picked(picker), opts=models[picker.dataset.picker].options;
    picker.querySelector("[data-picker-value]").textContent=summaryText(selected,opts);
    picker.querySelector("[data-picker-count]").textContent="已选 "+selected.length+" / "+opts.size+" 项";
    picker.querySelector("summary").classList.toggle("picked",selected.length!==opts.size);
    const visible=[...picker.querySelectorAll(".multi-choice:not([hidden]) input")];
    const searching=!!picker.querySelector("[data-option-search]").value.trim();
    const allBtn=picker.querySelector('[data-pick="all"]'),noneBtn=picker.querySelector('[data-pick="none"]'),onlyBtn=picker.querySelector('[data-pick="only"]');
    allBtn.textContent=searching?"全选匹配":"全选";noneBtn.textContent=searching?"取消匹配":"取消全选";onlyBtn.hidden=!searching;
    allBtn.disabled=!visible.length;noneBtn.disabled=!visible.length;onlyBtn.disabled=!visible.length;
  }
  for(const picker of form.querySelectorAll("[data-picker]")){
    refreshPicker(picker);
    picker.addEventListener("toggle",()=>{
      if(!picker.open)return;
      for(const other of form.querySelectorAll("[data-picker][open]"))if(other!==picker)other.open=false;
      positionOpenFilter();
    });
  }
  function applyFilters(switchScope){
    const fd=new FormData(form),checked=Object.fromEntries([...multiKeys].map(k=>[k,valuesOf(fd.getAll(k))]));
    const next=buildSelection(source,models,checked,fd.get("mode"),switchScope);
    for(const k of ["q","mode","week","as_of"]){
      const v=String(fd.get(k) || "").trim();
      if(v && !(k==="mode" && v==="current"))next[k]=v;
    }
    if(!next.mode)delete next.week;
    if(next.mode==="occurred" && source.event_kind) next.event_kind=source.event_kind;
    if(source.limit)next.limit=source.limit;
    if(source.sort)next.sort=numberSort(source);
    go("issues",next);
  }
  form.addEventListener("submit",e=>{e.preventDefault();applyFilters();});
  form.addEventListener("keydown",e=>{
    const picker=e.target.closest("[data-picker]");
    if(picker && e.key==="Escape"){e.preventDefault();picker.open=false;picker.querySelector("summary").focus();}
    if(e.target.matches("[data-option-search]") && e.key==="Enter")e.preventDefault();
  });
  form.addEventListener("change",e=>{const picker=e.target.closest("[data-picker]");if(picker && e.target.type==="checkbox")refreshPicker(picker);});
  form.addEventListener("input",e=>{
    if(!e.target.matches("[data-option-search]"))return;
    const picker=e.target.closest("[data-picker]"), val=e.target.value.trim().toLocaleLowerCase();
    const tokens=picker.dataset.picker==="author"?nameTokens(val):null;
    let match=0;
    for(const opt of picker.querySelectorAll("[data-option-label]")){
      const label=opt.dataset.optionLabel;
      opt.hidden=tokens?!tokens.every(t=>nameTokens(label).some(part=>part.includes(t))):!label.includes(val);
      if(!opt.hidden)match++;
    }
    picker.querySelector("[data-no-options]").hidden=match>0;
    refreshPicker(picker);
  });
  node.onclick=e=>{
    const active=e.target.closest("[data-picker]");
    for(const picker of form.querySelectorAll("[data-picker][open]"))if(picker!==active)picker.open=false;
    const el=e.target.closest("button");if(!el)return;
    if(el.dataset.pick){
      const picker=el.closest("[data-picker]");
      for(const option of picker.querySelectorAll(".multi-choice")){
        if(!option.hidden || el.dataset.pick==="only")option.querySelector("input").checked=!option.hidden && el.dataset.pick!=="none";
      }
      refreshPicker(picker);
    }else if(el.hasAttribute("data-toggle-filters")){
      const area=form.querySelector("#advanced-filters");area.hidden=!area.hidden;advancedOpen=!area.hidden;el.setAttribute("aria-expanded",String(!area.hidden));
    }else if(el.hasAttribute("data-reset-filters")){advancedOpen=false;go("issues");}
    else if(el.dataset.scope)applyFilters(el.dataset.scope);
    else if(el.dataset.removeFilter){
      const k=el.dataset.removeFilter,next={...source};delete next[k];delete next.page;
      if(multiKeys.has(k) && el.dataset.chipAction!=="reset"){
        const value=el.dataset.filterValue, checked=Object.fromEntries([...multiKeys].map(key=>[key,[...models[key].selected]]));
        checked[k]=el.dataset.chipAction==="restore"?valuesOf([...checked[k],value]):checked[k].filter(v=>v!==value);
        for(const key of multiKeys)delete next[key];
        Object.assign(next,buildSelection(source,models,checked,source.mode));
      }
      if(k==="mode"){delete next.week;delete next.event_kind;}
      go("issues",next);
    }else if(el.dataset.page)go("issues",{...source,page:el.dataset.page});
    else if(el.hasAttribute("data-toggle-sort")){
      const next={...source,sort:numberSort(source)==="number_asc"?"number_desc":"number_asc"};delete next.page;go("issues",next);
    }else if(el.dataset.export)void download(el.dataset.export,source,el.closest("section"));
  };
}
