// 维护专项任务并呈现项目周报。 / Maintain specialist tasks and present weekly reports.
import {line,api,context,count,dialogForm,empty,esc,failure,field,go,href,icon,issueLink,options,pageTitle,qs,toast,weekLabel,weekTools} from "./core.js";

let taskItems=[];
const kindNames={cycle:"节拍整改",safety:"安全检测",report:"周报任务"};
const detailFields={
  cycle:[["target","目标节拍（秒）","number"],["before","整改前实测（秒）","number"],["after","整改后实测（秒）","number"],["retest","复测日期","date"],["conditions","测量条件","textarea"]],
  safety:[["object","检查对象","text"],["report","检测报告名称或编号","text"],["findings","发现项","textarea"],["result","复验结论","textarea"],["retest","复验日期","date"]],
  report:[["drafted","出稿时间","date"],["confirmed","确认时间","date"]]
};
const statuses=["待处理","进行中","待确认","已完成"];
function taskCard(t){
  const infos=(detailFields[t.kind] || []).filter(([k])=>t.details?.[k]!==null && t.details?.[k]!==undefined && t.details?.[k]!=="");
  return '<article class="card"><div class="card-head"><div class="head-area"><span class="tag">'+esc(kindNames[t.kind] || t.kind)+'</span><h2>'+esc(t.title)+'</h2></div><button class="btn small-btn" type="button" data-edit-task="'+esc(t.id)+'">'+icon("edit")+'编辑</button></div><div class="card-body"><div class="flow-line"><strong>'+esc(t.status || "待处理")+'</strong><span class="card-text">'+esc(weekLabel(t.week))+' · '+esc(t.owner || "待分配")+(t.due?' · 截止 '+esc(t.due):"")+'</span></div>'+
    (infos.length?'<dl class="info-grid">'+infos.map(([k,label])=>'<div><dt>'+esc(label)+'</dt><dd>'+esc(t.details[k])+'</dd></div>').join("")+'</dl>':"")+
    ((t.issue_ids || []).length?'<div class="hint-row"><small>关联问题</small>'+t.issue_ids.map(id=>issueLink(id)).join("")+'</div>':"")+'</div></article>';
}
export async function tasks(params,signal){
  const all=params.all==="1", w=params.week || context.config.week;
  const data=await api("tasks"+qs({week:all?"":w}),{signal});
  taskItems=data.items || [];
  const kind=params.kind || "", visible=kind?taskItems.filter(t=>t.kind===kind):taskItems;
  return pageTitle("专项任务",all?"全部周的节拍、安全和周报任务":weekLabel(w)+" · 节拍、安全和周报任务",(all?"":weekTools(w))+'<button type="button" class="btn primary" data-new-task>'+icon("plus")+'新建任务</button>')+
    '<div class="stack"><div class="card filters"><div class="filters-row"><div class="segment">'+[["","全部类型"],...Object.entries(kindNames)].map(([k,label])=>'<button type="button" data-task-kind="'+k+'" aria-pressed="'+(k===kind)+'">'+label+'</button>').join("")+'</div>'+
    '<button class="text-btn" type="button" data-task-all>'+icon("calendar")+(all?"返回当前周":"查看全部周")+'</button></div></div>'+
    (visible.length?'<div class="two-col">'+visible.map(taskCard).join("")+'</div>':'<section class="card"><div class="card-head"><h2>任务清单</h2></div>'+empty("当前范围暂无专项任务。","checklist")+'</section>')+'</div>';
}
function taskSpec(kind,initial={}){
  return (detailFields[kind] || []).map(([k,label,type])=>field({key:"detail_"+k,label,value:initial[k],type,min:type==="number"?0.000001:undefined,step:type==="number"?"any":undefined})).join("");
}
function editTask(t={},week){
  const selected=new Map((t.issue_ids || []).map(id=>[Number(id),"已关联问题"]));
  const body='<div class="form-grid">'+field({key:"title",label:"任务名称",value:t.title,wide:true,required:true})+field({key:"kind",label:"任务类型",type:"select",value:t.kind || "cycle",opts:Object.entries(kindNames),required:true})+
    field({key:"status",label:"任务状态",type:"select",value:t.status || "待处理",opts:[...new Set([...statuses,...(t.status?[t.status]:[])])],required:true})+
    field({key:"week",label:"所属日历周",type:"week",value:t.week || week || context.config.week,required:true})+field({key:"due",label:"截止日期",type:"date",value:t.due})+field({key:"owner",label:"任务负责人",value:t.owner || context.actor,wide:true})+'</div>'+
    '<div class="divider"></div><div class="form-grid" data-task-details>'+taskSpec(t.kind || "cycle",t.details)+'</div><div class="divider"></div><label class="field">关联问题<div class="field-inline"><input type="search" data-task-search placeholder="输入客户号或关键词"><button type="button" class="btn" data-search-btn>搜索</button></div></label><div data-task-results></div><div class="tools" data-linked-items style="margin-top:8px"></div>';
  dialogForm({title:t.id?"编辑专项任务":"新建专项任务",wide:true,body,onMount(form){
    const inputs=form.querySelector("[data-task-details]"), linked=form.querySelector("[data-linked-items]"), results=form.querySelector("[data-task-results]");
    form.elements.kind.onchange=()=>{inputs.innerHTML=taskSpec(form.elements.kind.value,form.elements.kind.value===t.kind?t.details:{});};
    function paint(){
      linked.innerHTML=[...selected.entries()].map(([id,label])=>'<button class="filter-chip" type="button" title="解除本任务与该问题的关联" data-task-unlink="'+id+'">'+esc(label)+icon("x")+'</button>').join("");
    }
    linked.onclick=e=>{const b=e.target.closest("[data-task-unlink]");if(b){selected.delete(Number(b.dataset.taskUnlink));paint();}};
    const issueLabel=x=>"#"+x.number+" · "+x.station;
    for(const id of selected.keys()){
      void api("issues/"+id).then(issue=>{if(form.isConnected && selected.has(id)){selected.set(id,issueLabel(issue));paint();}}).catch(()=>{});
    }
    paint();
    let seq=0;
    async function search(){
      const epoch=++seq, term=form.querySelector("[data-task-search]").value.trim();
      if(!term){results.innerHTML="";return;}
      try{
        const data=await api("issues"+qs({q:term,scope:"all",limit:25}));
        if(epoch!==seq)return;
        results.innerHTML=data.items?.length?'<label class="field">搜索结果<div class="field-inline"><select data-task-candidate>'+options(data.items.map(x=>[x.id,issueLabel(x)]),undefined,"请选择问题")+'</select><button type="button" class="btn" data-task-link>关联</button></div></label>':empty("未找到匹配的问题。","search");
        const add=results.querySelector("[data-task-link]");
        if(add)add.onclick=()=>{const sel=results.querySelector("select");if(sel.value){selected.set(Number(sel.value),sel.selectedOptions[0].textContent);paint();}};
      }catch(e){if(epoch===seq)results.innerHTML=failure(e);}
    }
    form.querySelector("[data-search-btn]").onclick=search;
    form.querySelector("[data-task-search]").onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();void search();}};
  },async onSave(v){
    const kind=v.kind, details=Object.fromEntries((detailFields[kind] || []).map(([k])=>[k,v["detail_"+k] || ""]));
    await api("tasks"+(t.id?"/"+t.id:""),{method:"POST",data:{title:v.title,kind,week:v.week,due:v.due,owner:v.owner,status:v.status,issue_ids:[...selected.keys()],details,revision:t.revision}});
    await context.refresh();toast("专项任务已保存");
  }});
}
export function bindTasks(node,params){
  const wk=params.week || context.config.week;
  node.onclick=e=>{
    const b=e.target.closest("button");if(!b)return;
    if(b.hasAttribute("data-new-task"))editTask({kind:params.kind || "cycle"},wk);
    else if(b.dataset.editTask){const found=taskItems.find(t=>String(t.id)===b.dataset.editTask);if(found)editTask(found,wk);}
    else if(b.hasAttribute("data-task-kind"))go("tasks",{...params,kind:b.dataset.taskKind});
    else if(b.hasAttribute("data-task-all"))go("tasks",{...params,all:params.all==="1"?"":"1"});
  };
  const labels=new Map();
  for(const el of node.querySelectorAll("[data-issue-label]")){
    const id=el.dataset.issueLabel;
    if(!labels.has(id)) labels.set(id, api("issues/"+id).then(x=>"#"+x.number));
    labels.get(id).then(value=>{if(el.isConnected)el.textContent=value;}).catch(()=>{});
  }
}
export async function report(params,signal){
  const wk=params.week || context.config.week;
  const selection={week:wk,as_of:params.as_of};
  const data=await api("report"+qs(selection),{signal});
  const raw=data.text || "";
  let refs=data.issue_refs;
  if(!refs && raw.includes("#")){
    try {
      const ov=await api("overview"+qs(selection),{signal});
      refs=Object.fromEntries([...(ov.events || []).map(e=>[e.number,e.issue_id]),...(ov.plans || []).map(p=>[p.number,p.id])]);
    }catch(e){if(e.name==="AbortError")throw e;}
  }
  const linked=raw.split("\n").map(line=>{
    const m=/^(?:(\d{4}-\d{2}-\d{2}\s+))?#([^\s]+)(?=\s|$)/.exec(line);
    if(!m || !Object.hasOwn(refs || {},m[2]))return esc(line);
    return esc(m[1] || "")+issueLink(refs[m[2]],m[2])+esc(line.slice(m[0].length));
  }).join("\n");
  const lastDay=data.end<context.config.today?data.end:context.config.today;
  const cutoff='<label>统计截至 <input type="date" data-report-cutoff min="'+esc(data.start)+'" max="'+esc(lastDay)+'" value="'+esc(data.as_of)+'"'+(lastDay<data.start?' disabled':'')+'></label>';
  return '<div class="report-view">'+pageTitle("项目周报",weekLabel(wk)+" · "+line.name,weekTools(wk)+cutoff)+
    '<div class="stack"><section class="card"><div class="card-head"><div class="head-area">'+icon("report")+'<h2>周报内容</h2></div><div class="tools"><button type="button" class="btn small-btn" data-report-copy>'+icon("copy")+'复制全文</button><button type="button" class="btn small-btn" data-print>'+icon("print")+'打印</button></div></div><pre class="report-output" tabindex="0">'+linked+'</pre></section></div></div>';
}
export function bindReport(node,params){
  node.querySelector("[data-report-cutoff]").onchange=e=>{
    if(e.target.validity.valid)go("report",{...params,week:params.week || context.config.week,as_of:e.target.value});
  };
  node.querySelector("[data-print]").onclick=()=>window.print();
  node.querySelector("[data-report-copy]").onclick=async()=>{
    try{if(!navigator.clipboard)throw new Error();await navigator.clipboard.writeText(node.querySelector(".report-output").textContent);toast("已复制周报");}
    catch{const selection=window.getSelection(), range=document.createRange();range.selectNodeContents(node.querySelector(".report-output"));selection.removeAllRanges();selection.addRange(range);toast("已选中全文，可按 Ctrl+C 复制");}
  };
}
