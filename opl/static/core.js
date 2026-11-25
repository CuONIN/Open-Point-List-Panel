// 共享请求、路由、表单及界面组件。 / Shared requests, routing, forms and controls.
export const context = {config: {}, actor: "", refresh: async () => {}, refreshConfig: async () => {}};
export const multiKeys = new Set(["area","state","priority","station","machine","avo","department","category","responsible","owner","author","zf"]);
export function valuesOf(value) {
  return [...new Set((Array.isArray(value)?value:[value]).filter(v=>v!==undefined && v!==null && v!=="").map(String))];
}

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}
const drawings = {
  dashboard: '<rect x="3" y="3" width="7" height="8" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="3" y="15" width="7" height="6" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/>',
  rows: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 9v11"/>',
  checklist: '<path d="m3 6 1.7 1.8L8 4.5M11 6h10M3 13l1.7 1.8L8 11.5M11 13h10M11 20h10"/>',
  report: '<path d="M7 3h7l4 4v14H5V3h2Zm7 0v5h4M8 12h7M8 16h7"/>',
  import: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4"/>',
  production: '<path d="M3 20V9l6 3V8l7 4V5h4l1 15Zm4-4h2m3 0h2m3 0h1"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  user: '<circle cx="12" cy="8" r="3.3"/><path d="M5 20v-1a7 7 0 0 1 14 0v1"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18"/>',
  prev: '<path d="m14.5 5-7 7 7 7"/>', next: '<path d="m9.5 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>', up: '<path d="m6 15 6-6 6 6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/>',
  filter: '<path d="M3 6h18M6 12h12m-9 6h6"/>',
  reset: '<path d="M4 11a8 8 0 1 1 2 6M4 4v7h7"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
  note: '<path d="M4 4h16v13H9l-5 4zm4 5h8m-8 4h5"/>',
  link: '<path d="m9 15 6-6m-2-2 2-2a4 4 0 0 1 6 6l-3 3m-7 3-2 2a4 4 0 0 1-6-6l3-3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  file: '<path d="M5 3h9l5 5v13H5zm9 0v5h5"/>',
  save: '<path d="M4 3h13l4 4v14H3V3zm4 0v6h8V3M7 21v-8h10v8"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.1M3 12h.1M3 18h.1"/>',
  print: '<path d="M7 9V3h10v6m-10 8H3V9h18v8h-4M7 14h10v7H7zm11-2h1"/>',
  copy: '<rect x="8" y="7" width="12" height="14" rx="1"/><path d="M16 7V3H4v14h4"/>',
  edit: '<path d="m4 16-1 5 5-1L20 8l-4-4zm10-10 4 4"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1"/><path d="m3 17 6-5 4 3 3-3 5 4"/>',
  arrow: '<path d="M4 12h15m-5-5 5 5-5 5"/>',
  inbox: '<path d="M3 13 6 5h12l3 8v6H3zm0 0h5l2 3h4l2-3h5"/>',
  bolt: '<path d="m13 2-8 12h6l-1 8 9-13h-7z"/>',
  flag: '<path d="M5 21V4m1 0h11l-2 4 2 4H5"/>',
  book: '<path d="M4 4h7l1 2 1-2h7v15h-7l-1 2-1-2H4z"/>'
};
export function icon(name) {
  return '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + (drawings[name] || drawings.file) + '</svg>';
}
export function localStorageRead(key) { try { return localStorage.getItem(key) || ""; } catch { return ""; } }
export function localStorageWrite(key, value) { try { localStorage.setItem(key, value); } catch { /* Browser storage is optional. */ } }
export function setActor(value) {
  context.actor = (value || "").trim();
  localStorageWrite("opl-actor", context.actor);
  document.getElementById("operator-name").textContent = context.actor || "设置操作人";
}
export async function api(endpoint, options = {}) {
  const {method = "GET", data, signal} = options;
  const headers = {Accept:"application/json", "X-Requested-With":"XMLHttpRequest"};
  if (context.config.csrf_token) headers["X-CSRF-Token"] = context.config.csrf_token;
  const init = {method, headers, signal, credentials:"same-origin", cache:"no-store"};
  if (data instanceof FormData) init.body = data;
  else if (data !== undefined) { headers["Content-Type"]="application/json"; init.body=JSON.stringify(data); }
  let response;
  try { response = await fetch("/api/" + endpoint, init); }
  catch (error) { if (error.name === "AbortError") throw error; throw new Error("无法连接本地工作台，请确认应用仍在运行。"); }
  let body;
  try { body = await response.json(); }
  catch(e) { if(e.name==="AbortError")throw e;throw new Error("本地工作台返回了无法读取的数据（HTTP "+response.status+"），请重试。"); }
  if (!response.ok || body?.error) {
    const error = new Error(typeof body?.error === "string" ? body.error : "请求失败（" + response.status + "）");
    error.status = response.status;
    throw error;
  }
  return body ?? {};
}
export function qs(params = {}) {
  const q = new URLSearchParams();
  for (const [key,value] of Object.entries(params)) for (const item of valuesOf(value)) q.append(key,item);
  const result=q.toString();
  return result ? "?" + result : "";
}
export function href(route,params={}) { return "#" + route + qs(params); }
export function go(route,params={}) {
  const next=href(route,params);
  if(location.hash===next) void context.refresh(); else location.hash=next;
}
export function route() {
  const hash=location.hash.slice(1),pos=hash.indexOf("?");
  const name=pos<0?hash:hash.slice(0,pos);
  const query=new URLSearchParams(pos<0?"":hash.slice(pos+1)), params={};
  let migrated=false;
  for(const key of new Set(query.keys())){
    if(key==="phase"){migrated=true;continue;}
    const canon=key==="tracker"?"owner":key;
    if(canon!==key)migrated=true;
    if(multiKeys.has(canon)){
      const values=query.getAll(key).map(v=>{
        if(canon==="station" && /^AGV[\s_-]*(?:[一二12ⅠⅡ]\s*期|phase[\s_-]*[12]|S[12])$/i.test(v.trim())){migrated=true;return "AGV";}
        return v;
      });
      params[canon]=valuesOf([...valuesOf(params[canon]),...values]);
    }
    else params[canon]=query.get(key);
  }
  return {name:name || "overview",params,migrated};
}
export function pageTitle(title,subtitle="",tools="") {
  return '<div class="page-head"><div><h1>'+esc(title)+'</h1>'+(subtitle?'<p>'+esc(subtitle)+'</p>':'')+'</div><div class="tools">'+tools+'</div></div>';
}
export function empty(message, name="inbox") {
  return '<div class="empty"><span class="empty-icon">'+icon(name)+'</span><p>'+esc(message)+'</p></div>';
}
export function loading() { return '<div class="loading"><span></span>正在读取数据…</div>'; }
export function failure(e) { return '<div class="error-banner" role="alert">'+esc(e?.message || e)+'</div>'; }
export function count(n) { return Number(n ?? 0).toLocaleString("zh-CN"); }
const shades = {
  triage:["#51626a","#edf1f2"], clarify_zf:["#885b16","#fbefcf"], approve_zf:["#815a1c","#fbf0d8"],
  confirm_internal:["#725491","#f0e9fa"], waiting:["#865a29","#faeddc"], planned:["#345f95","#e9f1fb"],
  working:["#1f7057","#e1f3e8"], ready:["#965a23","#f9ead9"], reported:["#465793","#e9eefb"],
  rework:["#a74936","#f9e9e3"], negotiate:["#7f4e76","#f4e9f2"], closed:["#426454","#e9f0e9"], zf_only:["#54606b","#eaf0f3"]
};
export function stateName(key, label) { return label || context.config.states?.[key] || key || "待梳理"; }
export function statePill(key,label,issueId) {
  const [ink,bg]=shades[key]||shades.triage;
  const style=' style="--state-ink:'+ink+';--state-bg:'+bg+'"';
  const name=esc(stateName(key,label));
  return issueId && key!=="closed"
    ? '<button class="state-btn" type="button" data-transition="'+esc(issueId)+'"'+style+' title="更改我方状态">'+name+icon("down")+'</button>'
    : '<span class="pill"'+style+'>'+name+'</span>';
}
export function areaToken(area) {
  const cls=["S1","S2","AGV","General"].includes(area) ? area : "";
  return '<span class="area-token area-'+cls+'">'+esc(area || "待核")+'</span>';
}
export function priorityLabel(value) {
  const cls=["High","Medium","Low"].includes(value) ? value : "";
  return '<span class="priority pr-'+cls+'"><i class="indicator"></i>'+esc(value || "待核")+'</span>';
}
export function issueLink(id,number) {
  const r=route();
  const pending=number===null || number===undefined;
  return '<a class="linklike code" href="'+esc(href(r.name,{...r.params,issue:id}))+'" data-issue="'+esc(id)+'"'+(pending?' data-issue-label="'+esc(id)+'"':"")+' title="查看问题详情">'+(pending?"查看问题":"#"+esc(number))+'</a>';
}
export function weekLabel(week) {
  const m=String(week || "").match(/^(\d{4})-W(?:CW)?(\d{1,2})$/i);
  return m ? m[1]+" · CW"+m[2].padStart(2,"0") : String(week || "");
}
export function shiftWeek(week, by) {
  const m=String(week || "").match(/^(\d{4})-W(\d{2})$/);
  if(!m) return context.config.week;
  const fourth=new Date(Date.UTC(+m[1],0,4)); fourth.setUTCDate(fourth.getUTCDate()-(fourth.getUTCDay()+6)%7+7*(+m[2]-1+by)+3);
  const year=fourth.getUTCFullYear(), first=new Date(Date.UTC(year,0,4));
  first.setUTCDate(first.getUTCDate()-(first.getUTCDay()+6)%7+3);
  return year+"-W"+String(1+Math.round((fourth-first)/604800000)).padStart(2,"0");
}
export function weekTools(week) {
  return '<div class="week-tools"><button class="icon-btn" type="button" data-week-step="-1" aria-label="上一周">'+icon("prev")+'</button><input type="week" aria-label="统计周" value="'+esc(week)+'" data-week-picker><button class="icon-btn" type="button" data-week-step="1" aria-label="下一周">'+icon("next")+'</button><button type="button" class="btn" data-week-today>本周</button></div>';
}
export function options(items,value="",placeholder,includeBlank=false) {
  const result=[];
  if(placeholder !== undefined || includeBlank)result.push('<option value="">'+esc(placeholder || "请选择")+'</option>');
  for(const item of items || []) {
    const key=Array.isArray(item)?item[0]:item, label=Array.isArray(item)?item[1]:item;
    result.push('<option value="'+esc(key)+'"'+(String(key)===String(value)?" selected":"")+'>'+esc(label)+'</option>');
  }
  return result.join("");
}
export function field(spec) {
  const {key,label,type="text",required=false,value="",opts,placeholder="",wide=false,help="",min,step}=spec;
  const at=' name="'+esc(key)+'"'+(required?" required":"")+(placeholder?' placeholder="'+esc(placeholder)+'"':"");
  let control;
  if(type==="textarea")control='<textarea'+at+' rows="3">'+esc(value)+'</textarea>';
  else if(type==="select")control='<select'+at+'>'+options(opts || spec.options,value,"请选择")+'</select>';
  else if(type==="plan"){
    const isWeek=String(value).includes("W");
    control='<span class="plan-input"><select data-plan-type aria-label="计划精度"><option value="date">日期</option><option value="week"'+(isWeek?" selected":"")+'>日历周</option></select><input type="'+(isWeek?"week":"date")+'"'+at+' value="'+esc(String(value).replace("-CW","-W"))+'"></span>';
  } else control='<input type="'+esc(type)+'"'+at+' value="'+esc(value)+'"'+(min!==undefined?' min="'+esc(min)+'"':"")+(step!==undefined?' step="'+esc(step)+'"':"")+'>';
  return '<label class="field'+(wide || type==="textarea"?" wide":"")+'"><span>'+esc(label)+(required?' <span aria-hidden="true">*</span>':'')+'</span>'+control+(help?'<small>'+esc(help)+'</small>':"")+'</label>';
}
export function head(title,id="edit-title") {return '<div class="dialog-head"><h2 id="'+id+'">'+esc(title)+'</h2><button class="icon-btn" type="button" data-close-dialog aria-label="关闭">'+icon("x")+'</button></div>';}
export function closeDialog(dialog) { if(dialog?.open)dialog.close(); }
export function showDialog(dialog) { if(!dialog.open)dialog.showModal(); }
export function dialogForm({title,body,submit="保存",onSave,onMount,wide=false}) {
  const dialog=document.getElementById("edit-dialog"), frame=dialog.querySelector(".dialog-frame");
  frame.innerHTML=head(title)+'<form class="dialog-form"><div class="dialog-body">'+body+'<div class="form-message"></div></div><div class="dialog-foot"><button type="button" class="btn" data-close-dialog>取消</button><button type="submit" class="btn primary">'+esc(submit)+'</button></div></form>';
  dialog.style.width=wide?"min(840px,calc(100vw - 24px))":"";
  const form=frame.querySelector("form");
  form.addEventListener("change",event=>{
    if(event.target.matches("[data-plan-type]")){
      const inp=event.target.parentElement.querySelector("input");
      inp.type=event.target.value; inp.value=""; inp.focus();
    }
  });
  form.onsubmit=async event=>{
    event.preventDefault();
    if(!form.reportValidity() || form.dataset.busy)return;
    const message=form.querySelector(".form-message"), btn=form.querySelector('[type=submit]');
    form.dataset.busy="1";btn.disabled=true;message.innerHTML="";
    try {
      const data=new FormData(form);
      const keep=await onSave?.(Object.fromEntries(data),data,form);
      if(keep!==false)closeDialog(dialog);
    } catch (e) {message.innerHTML=failure(e);message.scrollIntoView({block:"nearest"});}
    finally {delete form.dataset.busy;btn.disabled=false;}
  };
  showDialog(dialog);
  onMount?.(form);
  return form;
}
let toastTimer;
export function toast(message) {
  const node=document.getElementById("toast");
  node.textContent=message;node.classList.add("show");
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.classList.remove("show"),3000);
}
export function actorField() {return field({key:"actor",label:"记录人",value:context.actor,required:true,placeholder:"现场操作人姓名"});}
export function dayField() {return field({key:"day",label:"实际发生日期",type:"date",value:context.config.today,required:true});}
export function definitionGrid(pairs) {
  return '<dl class="info-grid">'+pairs.map(([label,value])=>'<div><dt>'+esc(label)+'</dt><dd>'+esc(value ?? "—")+'</dd></div>').join("")+'</dl>';
}
