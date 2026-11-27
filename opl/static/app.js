// 协调页面路由与详情弹窗。 / Coordinate page routes and detail overlays.
import {api,line,closeDialog,context,dialogForm,failure,field,go,href,icon,loading,localStorageRead,route,setActor,shiftWeek,toast} from "./core.js";
import {overview} from "./overview.js";
import {issues,bindIssues,positionOpenFilter} from "./issues.js";
import {openIssue,transition,bindDrawer} from "./detail.js";
import {imports,bindImports} from "./imports.js";
import {tasks,bindTasks,report,bindReport} from "./tasks.js";

const pages={
  overview:{label:"项目概览",render:overview},issues:{label:"问题清单",render:issues,bind:bindIssues},
  tasks:{label:"专项任务",render:tasks,bind:bindTasks},report:{label:"项目周报",render:report,bind:bindReport},
  imports:{label:"导入与核对",render:imports,bind:bindImports}
};
const main=document.getElementById("main"),drawer=document.getElementById("detail-dialog"),edit=document.getElementById("edit-dialog");
let controller,renderSeq=0,pageKey="",previousRoute="",configured=false,pendingConfig,detailId="";
const scrollPositions=new Map();
if("scrollRestoration" in history)history.scrollRestoration="manual";
const listStorageKey = key => key === "eol" ? "opl-last-issues" : "opl-last-issues-"+key;
const storageKey = listStorageKey(line.key);
document.getElementById("line-switch").addEventListener("change", e=>{
  const page = address().page;
  let hash = "#" + page;
  if(page === "issues"){
    const targetKey = e.target.selectedOptions[0].dataset.line;
    try{
      const saved = sessionStorage.getItem(listStorageKey(targetKey));
      if(saved && /^#issues(?:\?|$)/.test(saved))hash = saved;
    }catch{ /* 无本地存储时打开默认清单。 / Fall back to the default list. */ }
  }
  location.assign(e.target.value + hash);
});
const listNav=document.querySelector('[data-nav="issues"]');
try{
  const remembered=sessionStorage.getItem(storageKey);
  if(remembered && /^#issues(?:\?|$)/.test(remembered))listNav.setAttribute("href",remembered);
}catch{ /* The link still works without browser storage. */ }
context.refreshConfig=()=>{
  if(!pendingConfig)pendingConfig=api("config").then(data=>{context.config=data;configured=true;}).finally(()=>{pendingConfig=null;});
  return pendingConfig;
};
function menu(open){
  document.body.classList.toggle("nav-open",open);
  document.querySelector(".mobile-menu").setAttribute("aria-expanded",String(open));
}
function address(){
  const {name,params,migrated}=route();
  const id=params.issue;
  delete params.issue;
  const page=pages[name]?name:"overview";
  return {page,params,base:href(page,params),id:/^[1-9]\d*$/.test(id || "")?String(id):"",migrated};
}
function openAddress(id) {
  id=String(id);if(!/^[1-9]\d*$/.test(id))return;
  const a=address();
  if(id!==a.id){
    const chain=history.state?.oplOverlay;
    const depth=a.id && chain?.base===a.base?chain.depth+1:1;
    history.pushState({oplOverlay:{base:a.base,depth}},"",href(a.page,{...a.params,issue:id}));
  }
  syncAddress();
}
function closeAddress(){
  const a=address(), chain=history.state?.oplOverlay;
  if(!a.id){closeDialog(drawer);detailId="";return;}
  if(chain?.base===a.base && chain.depth>0 && chain.depth<history.length){
    if(drawer.dataset.closing)return;
    drawer.dataset.closing="1";
    history.go(-chain.depth);
  }else{
    history.replaceState(null,"",a.base);syncAddress();
  }
}
async function render(){
  const req=++renderSeq;
  controller?.abort();controller=new AbortController();
  const a=address(),page=pages[a.page],changed=previousRoute!==a.page;
  const rendered=main.dataset.route;
  if(rendered){
    const table=main.querySelector(".desktop-issues");
    scrollPositions.set(rendered,{y:window.scrollY,top:table?.scrollTop || 0,left:table?.scrollLeft || 0});
  }
  const restore=scrollPositions.get(a.base);
  pageKey=a.base;previousRoute=a.page;
  document.getElementById("crumb").textContent=page.label;
  document.title=page.label+" · "+line.name;
  for(const link of document.querySelectorAll("[data-nav]")){
    if(link.dataset.nav===a.page)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");
  }
  if(a.page==="issues"){
    listNav.setAttribute("href",a.base);
    try{sessionStorage.setItem(storageKey,a.base);}catch{ /* Navigation keeps its current link. */ }
  }
  main.setAttribute("aria-busy","true");
  if(changed){delete main.dataset.route;main.innerHTML=loading();window.scrollTo(0,0);}
  try{
    if(!configured)await context.refreshConfig();
    if(req!==renderSeq)return;
    const html=await page.render(a.params,controller.signal);
    if(req!==renderSeq)return;
    main.onclick=null;main.innerHTML=html;main.dataset.route=a.base;page.bind?.(main,a.params);
    const next=main.querySelector(".desktop-issues");
    if(next && restore){next.scrollTop=restore.top;next.scrollLeft=restore.left;}
    window.scrollTo(0,restore?.y || 0);
  }catch(e){
    if(req!==renderSeq || e.name==="AbortError")return;
    main.onclick=null;main.innerHTML='<div class="stack">'+failure(e)+'<div><button type="button" class="btn" data-app="retry">'+icon("reset")+'重新读取</button></div></div>';
  }finally{if(req===renderSeq)main.removeAttribute("aria-busy");}
}
context.refresh=render;

function syncAddress(){
  const a=address();
  if(a.migrated)history.replaceState(history.state,"",a.id?href(a.page,{...a.params,issue:a.id}):a.base);
  if(a.id && history.state?.oplOverlay?.base!==a.base){
    const target=location.hash;
    history.replaceState(null,"",a.base);
    history.pushState({oplOverlay:{base:a.base,depth:1}},"",target);
  }
  delete drawer.dataset.closing;
  if(a.base!==pageKey){closeDialog(edit);menu(false);void render();}
  if(a.id!==detailId || (a.id && !drawer.open)){
    closeDialog(edit);detailId=a.id;
    if(a.id)void openIssue(a.id);else closeDialog(drawer);
  }
}
document.querySelectorAll("[data-icon]").forEach(node=>{node.innerHTML=icon(node.dataset.icon);});
setActor(localStorageRead("opl-actor"));
bindDrawer();
document.addEventListener("click",event=>{
  const el=event.target.closest("button,a");
  if(!el)return;
  if(el.matches(".skip")){event.preventDefault();main.focus();return;}
  if(el.hasAttribute("data-close-dialog")){
    const dialog=el.closest("dialog");
    if(!dialog?.querySelector("form[data-busy]")){if(dialog===drawer)closeAddress();else closeDialog(dialog);}
    return;
  }
  if(el.hasAttribute("data-nav")){menu(false);return;}
  if(el.dataset.app==="menu"){menu(!document.body.classList.contains("nav-open"));return;}
  if(el.dataset.app==="retry"){void render();return;}
  if(el.dataset.app==="actor"){
    dialogForm({title:"设置现场操作人",body:'<div class="form-grid">'+field({key:"actor",label:"操作人姓名",value:context.actor,required:true,wide:true})+'</div>',onSave(v){setActor(v.actor);toast("操作人已更新");}});return;
  }
  if(el.hasAttribute("data-week-today") || el.hasAttribute("data-week-step")){
    const r=address(), current=r.params.week || context.config.week;
    if(r.page==="report")delete r.params.as_of;
    go(r.page,{...r.params,week:el.hasAttribute("data-week-today")?context.config.week:shiftWeek(current,Number(el.dataset.weekStep))});return;
  }
  if(el.dataset.issue){
    if(el.tagName==="A" && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey))return;
    event.preventDefault();openAddress(el.dataset.issue);return;
  }
  if(el.dataset.transition){void transition(el.dataset.transition).catch(e=>{if(e.name!=="AbortError")toast(e.message);});}
});
document.addEventListener("change",e=>{
  if(e.target.matches("[data-week-picker]") && e.target.value){
    const a=address();
    if(a.page==="report")delete a.params.as_of;
    go(a.page,{...a.params,week:e.target.value});
  }
});
for(const dialog of [drawer,edit]){
  dialog.addEventListener("cancel",e=>{
    if(dialog.querySelector("form[data-busy]")){e.preventDefault();return;}
    if(dialog===drawer){e.preventDefault();closeAddress();}
  });
  dialog.addEventListener("click",e=>{
    if(e.target!==dialog || dialog.querySelector("form[data-busy]"))return;
    const r=dialog.getBoundingClientRect();
    if(e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom){
      if(dialog===drawer)closeAddress();else closeDialog(dialog);
    }
  });
}
window.addEventListener("hashchange",syncAddress);
window.addEventListener("popstate",syncAddress);
window.addEventListener("resize",positionOpenFilter);

const initial=new URL(location.href),legacy=initial.searchParams.get("issue");
if(legacy && /^[1-9]\d*$/.test(legacy)){
  const a=address();initial.searchParams.delete("issue");
  initial.hash=href(a.page,{...a.params,issue:a.id || legacy});
  history.replaceState(null,"",initial);
}
syncAddress();
