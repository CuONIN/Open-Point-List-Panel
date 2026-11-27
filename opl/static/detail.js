// 展示问题详情并保存现场操作。 / Display issue details and submit field updates.
import {actorField,api,line,areaToken,context,dayField,definitionGrid,dialogForm,empty,esc,failure,field,head,icon,issueLink,loading,priorityLabel,qs,setActor,showDialog,stateName,statePill,toast} from "./core.js";

let current=null, tab="overview", loadSeq=0, detailRequest, transitionRequest;
const tabs=[["overview","问题详情"],["actions","客户行动"],["feedback","现场反馈"],["history","状态历史"],["relations","关联问题"],["versions","客户版本"]];
const names={detail:"主问题／明细",duplicate:"重复问题",similar:"相似问题"};
const fieldNames={station:"工位",machine:"机器号",avo:"AVO",problem:"问题原文",zh:"中文译文",department:"部门",category:"类别",priority:"优先级",responsible:"客户责任方",status:"客户状态",customer_status:"客户状态",due:"客户截止日",action:"客户行动",owner:"我方负责人",people:"整改人数／工种",work_time:"原表整改时间",order_no:"投产单号",remark:"客户备注",plan:"我方计划",no:"客户号",number:"客户号",actor:"记录人",note:"备注",recorded_at:"录入时间"};
function lineBtn(action,label,glyph="",cls="btn small-btn"){return '<button type="button" class="'+cls+'" data-detail="'+action+'">'+(glyph?icon(glyph):"")+esc(label)+'</button>';}
function section(title,body,tools=""){return '<section class="detail-block"><div class="card-head"><h3>'+esc(title)+'</h3><div class="tools">'+tools+'</div></div>'+body+'</section>';}
function bilingual(original,zh,en){
  const extra=en && en.trim()!==(original || "").trim();
  return '<div class="bilingual"><div><span class="eyelabel">中文</span><p class="pre'+(!zh?" muted":"")+'">'+esc(zh || "待翻译")+'</p></div><div><span class="eyelabel">'+(extra?"英文译文":"客户原文")+'</span><p class="pre">'+esc(extra?en:original || "—")+'</p></div></div>'+
    (extra?'<details class="collapse" style="margin-top:9px"><summary>查看客户原文</summary><p class="pre">'+esc(original)+'</p></details>':"");
}
function attachments(files){
  return '<div class="files">'+(files || []).map(f=>{
    let url;try{url=new URL(f.url,location.origin);}catch{return "";}
    if(url.origin!==location.origin || !/^https?:$/.test(url.protocol))return "";
    const isImage=/^image\/(jpeg|png|gif|webp|bmp)$/i.test(f.mime || "") || /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name || "");
    return '<a class="file" href="'+esc(url.pathname+url.search)+'" target="_blank" rel="noopener">'+(isImage?'<img loading="lazy" src="'+esc(url.pathname+url.search)+'" alt="">':icon("file"))+esc(f.name || "附件")+'</a>';
  }).join("")+'</div>';
}
function brief(x){if(typeof x==="string")return x;if(x===null || x===undefined)return "";return JSON.stringify(x,null,2);}
function normalizeOwner(data={}){
  if(!Object.hasOwn(data,"tracker"))return data;
  const {tracker,...fields}=data;
  return {...fields,owner:fields.owner || tracker};
}
function auditText(value){
  return JSON.stringify(value,(_key,v)=>{
    if(!v || typeof v!=="object" || Array.isArray(v))return v;
    return Object.fromEntries(Object.entries(normalizeOwner(v)).map(([k,val])=>[fieldNames[k] || k,val]));
  },2);
}
function summaryView(d){
  const details=definitionGrid([["工位",d.station],...(line.phased?[["八位机器号／AVO",[d.machine,d.avo].filter(Boolean).join(" / ") || "—"],["客户部门 › 类别",[d.department,d.category].filter(Boolean).join(" › ") || "—"]]:[["客户工位原文",d.station_cell || "（工位列为空）"],["车型",d.variant || "—"]]),["客户提出者",d.author || "—"],["客户责任方",d.responsible || "待核"],["我方负责人",d.owner || d.tracker || "待分配"],["客户截止日",d.due || "—"],["我方计划",d.plan || "待安排"],...(d.plan_candidate?[["豪森建议日期",d.plan_candidate]]:[])]);
  const prep=definitionGrid([["整改人数／工种",d.people || "—"],["原表整改时间",d.work_time || "—"],["投产单号",d.order_no || "—"]]);
  const hintsByCode={"1":"问题需与 ZF 澄清","2":"已有措施待 ZF 确认","3":"内部确认","4":"待投产","5":"无计划"};
  const hint=hintsByCode[String(d.hint)]?String(d.hint)+" · "+hintsByCode[String(d.hint)]:d.hint;
  const hints=(hint || d.note)?section("豪森原表提示",definitionGrid([["分类提示",hint || "—"],["我方备注",d.note || "—"]])):"";
  return section("问题描述",bilingual(d.problem,d.zh,d.en),lineBtn("profile","编辑我方资料","edit"))+
    section("责任与日期",details,["closed","zf_only"].includes(d.state)?"":lineBtn("plan",d.plan?"调整计划":"登记计划","calendar"))+
    hints+section("准备信息",prep)+section("关联附件",d.attachments?.length?attachments(d.attachments):empty("尚无关联附件。","image"),lineBtn("feedback","添加反馈","plus"));
}
function actionView(d) {
  const entries=d.actions || [];
  const buttons=lineBtn("actionEdit","编辑行动分段","edit")+(context.config.translation_enabled?lineBtn("translate","翻译待译内容","book"):"");
  return section("客户行动时间线",entries.length?entries.map(a=>'<article class="time-item"><div class="time-head"><b>'+esc(a.date || a.date_raw || "日期待核")+'</b>'+((a.date_raw && a.date_raw!==a.date)?'<span class="code">'+esc(a.date_raw)+'</span>':"")+'<span class="muted">'+esc(a.author || "")+'</span></div>'+bilingual(a.original,a.zh,a.en)+'</article>').join(""):empty("当前客户版本没有行动记录。","note"),buttons);
}
function feedbackView(d){
  const entries=d.feedback || [];
  return section("现场反馈",entries.length?entries.map(a=>'<article class="time-item"><div class="time-head"><b>'+esc(a.day || "日期待核")+'</b><span>'+esc(a.actor || "导入")+'</span></div><p class="pre">'+esc(a.note || "")+'</p>'+attachments(a.files)+'</article>').join(""):empty("尚未登记现场反馈。","note"),lineBtn("feedback","记录反馈","plus","btn primary small-btn"))+
    section("全部关联附件",d.attachments?.length?attachments(d.attachments):empty("尚无关联附件。","image"));
}
function historyView(d){
  const events=d.events || [];
  const keys={...fieldNames,resource:"等待事项",expected:"确认约期",followup:"下次跟进日",order:"物料单号",obstacle:"实施阻碍",outcome:"处理结论",available_day:"确认条件日期",executor:"执行人",contact:"对接人或凭据",basis:"日期依据",version:"来源版本",action_date:"客户行动日期",action_text:"客户行动原文",comparison_date:"上版对比日期"};
  const history=section("我方状态历史",events.length?events.map(ev=>{
    const data=Object.entries(normalizeOwner(ev.payload || {})).filter(([k,v])=>v && k!=="note" && !(ev.kind==="manual_closed" && k==="evidence"));
    return '<article class="time-item"><div class="time-head"><b>'+esc(ev.day || "日期待核")+'</b>'+ (ev.from_state?statePill(ev.from_state)+icon("arrow"):"")+statePill(ev.to_state || ev.state)+'<span>'+esc(ev.actor || "导入")+'</span></div><p class="pre">'+esc(ev.note || "")+'</p>'+
      (data.length?'<div class="cell-sub">'+data.map(([k,v])=>esc(keys[k] || k)+" · "+esc(brief(v))).join("　")+'</div>':"")+
      (ev.recorded?'<small class="muted">录入 '+esc(ev.recorded)+'</small>':"")+
      (ev.id && ["meeting","manual_closed"].includes(ev.kind)?'<button class="text-btn" type="button" data-correct-event="'+esc(ev.id)+'">更正日期</button>':"")+'</article>';
  }).join(""):empty("尚无状态记录。","clock"));
  const plans=d.plan_history || [];
  const planLog=plans.length?section("历次实施计划",plans.map(p=>'<article class="time-item"><div class="time-head"><b>'+esc(p.plan)+'</b><span>我方负责人 · '+esc(p.owner || p.tracker || "待分配")+'</span></div><p class="pre">'+esc(p.note)+'</p><small class="muted">'+esc([p.day,p.actor && "记录人 "+p.actor].filter(Boolean).join(" · "))+'</small></article>').join("")):"";
  const types={profile:"我方资料修改",plan:"计划调整",customer_diff:"客户字段变更",supplement:"豪森资料补充",relation:"问题关系变更",action_edit:"行动分段编辑",event_date:"历史日期更正",translation:"翻译",accepted_customer_actions:"接受客户行动"};
  const log=(d.audit || []).map(x=>'<details class="collapse"><summary>'+esc(x.day)+' · '+esc(types[x.kind] || x.kind)+' · '+esc(x.actor)+'</summary><pre class="summary-details">'+esc(auditText(x.payload))+'</pre></details>').join("");
  return history+planLog+(log?section("字段和资料变更",log):"");
}
function relationView(d){
  const groups=d.relations || [];
  const body=groups.length?["detail","duplicate","similar"].map(kind=>{
    const rows=groups.filter(r=>r.kind===kind);if(!rows.length)return "";
    return '<h3>'+esc(names[kind])+'</h3>'+rows.map(r=>'<div class="item-row"><div><div class="flow-line">'+issueLink(r.issue_id,r.number)+'<span>'+esc(r.station)+'</span><span class="tag">'+esc(r.state_label || "")+'</span></div><small class="muted">'+esc([kind==="detail"?({in:"主问题",out:"直接明细",descendant:"下级明细"}[r.direction] || ""):"",r.note,r.actor].filter(Boolean).join(" · "))+'</small></div>'+(r.id?'<button type="button" class="text-btn" data-unlink="'+esc(r.id)+'">解除</button>':"")+'</div>').join("");
  }).join(""):empty("尚未建立明细、相似或重复关系。","link");
  return section("关联问题",body,lineBtn("relation","建立关系","plus","btn primary small-btn"));
}
function versionView(d){
  const snaps=d.snapshots || [];
  return section("来源与版本",snaps.length?snaps.map(s=>'<details class="collapse"><summary>'+esc(s.version || "版本未标注")+' · '+esc(s.file || "来源文件")+'</summary><div class="card-body"><div class="flow-line"><span class="cell-sub">'+esc(s.sheet || "")+' · 行 '+esc(s.row ?? "—")+'</span>'+(s.id?'<a class="text-btn" href="'+esc(s.url)+'" download>下载原文件</a>':"")+'</div><dl class="info-grid">'+Object.entries(normalizeOwner(s.raw || {})).map(([k,v])=>'<div><dt>'+esc(fieldNames[k] || k)+'</dt><dd class="pre">'+esc(brief(v) || "—")+'</dd></div>').join("")+'</dl></div></details>').join(""):empty("尚无客户版本信息。","file"));
}
function fillDrawer(){
  const d=current;if(!d)return;
  const dialog=document.getElementById("detail-dialog"), frame=dialog.querySelector(".dialog-frame");
  const content={overview:summaryView,actions:actionView,feedback:feedbackView,history:historyView,relations:relationView,versions:versionView}[tab](d);
  frame.innerHTML=head("问题 #"+d.number,"detail-title")+'<div class="dialog-body"><div class="flow-line" style="margin-bottom:13px">'+areaToken(d.area)+'<b>'+esc(d.station || "工位待核")+'</b>'+priorityLabel(d.priority)+statePill(d.state,d.state_label,d.id)+'</div><nav class="detail-tabs" aria-label="问题详情栏目">'+tabs.map(([k,n])=>'<button type="button" class="tab" data-detail-tab="'+k+'" aria-pressed="'+(k===tab)+'">'+n+'</button>').join("")+'</nav><div class="stack">'+content+'</div></div>';
}
export async function openIssue(id,keepTab=false){
  const seq=++loadSeq, dialog=document.getElementById("detail-dialog"), frame=dialog.querySelector(".dialog-frame");
  if(!keepTab || String(current?.id)!==String(id))tab="overview";
  detailRequest?.abort();detailRequest=new AbortController();
  current=null;frame.innerHTML=head("问题详情","detail-title")+'<div class="dialog-body">'+loading()+'</div>';showDialog(dialog);
  try{
    const [d]=await Promise.all([api("issues/"+encodeURIComponent(id),{signal:detailRequest.signal}),
      context.config.states?null:context.refreshConfig()]);
    if(seq!==loadSeq || !dialog.open)return;
    if(!d.id)throw new Error("问题资料暂不可用，请重新读取。");
    current=d;fillDrawer();
  }catch(e){
    if(seq!==loadSeq || e.name==="AbortError" || !dialog.open)return;
    frame.innerHTML=head("问题详情","detail-title")+'<div class="dialog-body stack">'+failure(e)+'<div><button type="button" class="btn" data-retry-detail="'+esc(id)+'">'+icon("reset")+'重新读取详情</button></div></div>';
  }
}
async function updated(id,message,actor) {
  if(actor)setActor(actor);
  if(document.getElementById("detail-dialog").open && String(current?.id)===String(id)) await openIssue(id,true);
  try { await context.refreshConfig(); } catch { /* The edit is already saved. */ }
  await context.refresh();
  toast(message);
}
function errIn(node,e){node.innerHTML=failure(e);}
const requestFields=(d,body)=>({...body,revision:d.revision});
export async function transition(id){
  transitionRequest?.abort();
  const request=new AbortController(),start=location.href;
  transitionRequest=request;
  const d=await api("issues/"+encodeURIComponent(id),{signal:request.signal}), allowed=d.allowed || [];
  if(request.signal.aborted || start!==location.href)return;
  if(!allowed.length){toast("该状态由客户生效版本判定。");return;}
  const idPath="issues/"+id+"/transition"; let changeId=0;
  let form;
  form=dialogForm({title:"更改状态 · #"+d.number,submit:"确认状态变更",
    body:'<div class="flow-line" style="margin-bottom:14px"><span class="muted">当前状态</span>'+statePill(d.state,d.state_label)+'</div><div class="form-grid">'+field({key:"to",label:"转入状态",type:"select",required:true,opts:allowed.map(x=>[x,stateName(x)]),wide:true})+actorField()+dayField()+'</div><div class="divider"></div><div class="form-grid" data-transition-fields></div>',
    onMount(f){
      f.elements.to.addEventListener("change",async()=>{
        const seq=++changeId, selected=f.elements.to.value, target=f.querySelector("[data-transition-fields]"), btn=f.querySelector('[type=submit]');
        const dayLabel=f.elements.day.closest("label").querySelector("span");
        dayLabel.firstChild.textContent=selected==="closed"?"客户确认关闭日期":"实际发生日期";
        target.innerHTML=selected?loading():"";btn.disabled=true;
        if(!selected)return;
        try{
          const data=await api(idPath+qs({to:selected}));
          if(seq!==changeId)return;
          const specs=(data.fields || []).filter(spec=>!["actor","day","to","revision"].includes(spec.key));
          const hasOwner=specs.some(spec=>spec.key==="owner");
          target.innerHTML=specs.filter(spec=>spec.key!=="tracker" || !hasOwner).map(spec=>{
            if(["owner","tracker"].includes(spec.key))return field({...spec,key:"owner",label:"我方负责人",value:spec.value || d.owner || d.tracker || ""});
            return field(spec);
          }).join("");
          btn.disabled=false;
        }catch(e){if(seq===changeId)errIn(target,e);}
      });
    },async onSave(body){
      await api(idPath,{method:"POST",data:requestFields(d,body)});
      await updated(id,"状态已更新",body.actor);
    }
  });
  form.querySelector('[type=submit]').disabled=true;
}
function editProfile(d){
  const fields=[["owner","我方负责人"],["people","整改人数／工种"],["work_time","原表整改时间"],["order_no","投产单号"],["zh","中文问题描述"],["en","英文译文（德文来源适用）"]];
  dialogForm({title:"编辑我方资料 · #"+d.number,body:'<div class="form-grid">'+actorField()+fields.map(([key,label])=>field({key,label,value:(key==="owner"?d.owner || d.tracker:d[key]) || "",type:["zh","en"].includes(key)?"textarea":"text"})).join("")+'</div>',
    async onSave(v){await api("issues/"+d.id+"/profile",{method:"POST",data:requestFields(d,v)});await updated(d.id,"我方资料已保存",v.actor);}});
}
function editPlan(d){
  dialogForm({title:"安排实施计划 · #"+d.number,body:'<div class="form-grid">'+actorField()+dayField()+field({key:"plan",type:"plan",label:"计划日期或日历周",value:d.plan || d.plan_candidate,required:true})+field({key:"owner",label:"我方负责人",value:d.owner || d.tracker,required:true})+field({key:"note",label:"计划行动／调整原因",type:"textarea",required:true})+'</div>',
    async onSave(v){await api("issues/"+d.id+"/plan",{method:"POST",data:requestFields(d,v)});await updated(d.id,"计划已保存",v.actor);}});
}
function addFeedback(d){
  dialogForm({title:"记录现场反馈 · #"+d.number,body:'<div class="form-grid">'+actorField()+dayField()+field({key:"note",type:"textarea",label:"反馈内容",placeholder:"记录会议结论、执行进度或现场观察"})+'<label class="field wide">关联图片、视频或报告<input type="file" name="files" multiple accept=".jpg,.jpeg,.png,.webp,.gif,.mp4,.webm,.mov,.pdf,.txt,.xlsx,.docx"></label></div>',
    async onSave(v,data){data.set("revision",String(d.revision));if(!data.get("files")?.name)data.delete("files");await api("issues/"+d.id+"/feedback",{method:"POST",data});await updated(d.id,"现场反馈已保存",v.actor);}});
}
function correctDate(d,eventId){
  const ev=(d.events || []).find(x=>String(x.id)===String(eventId));if(!ev)return;
  dialogForm({title:"更正状态事件日期",body:'<div class="flow-line" style="margin-bottom:12px">'+statePill(ev.to_state || ev.state)+'<span class="muted">原日期 '+esc(ev.day)+'</span></div><div class="form-grid">'+actorField()+field({key:"day",label:"更正后的实际日期",type:"date",value:ev.day,required:true})+field({key:"note",label:"更正原因",type:"textarea",required:true})+'</div>',
    async onSave(v){await api("issues/"+d.id+"/events/"+eventId,{method:"POST",data:requestFields(d,v)});await updated(d.id,"事件日期已更正",v.actor);}});
}
function addRelation(d){
  const body='<div class="form-grid">'+actorField()+field({key:"kind",label:"关系类型",type:"select",required:true,opts:[["detail","把对方设为本问题的明细"],["duplicate","重复问题"],["similar","相似问题"]]})+
    '<label class="field wide">搜索其他问题<div class="field-inline"><input type="search" data-related-q placeholder="客户号、工位或关键词"><button type="button" class="btn" data-related-search>搜索</button></div></label><div class="wide" data-related-list></div>'+
    field({key:"note",label:"建立依据",type:"textarea",required:true})+'</div>';
  dialogForm({title:"建立问题关系 · #"+d.number,body,onMount(form){
    let seq=0;const box=form.querySelector("[data-related-list]");
    async function populate(search){
      const epoch=++seq;box.innerHTML=loading();
      try{
        const data=await api(search?"issues"+qs({scope:"all",q:form.querySelector("[data-related-q]").value.trim(),limit:35}):"issues/"+d.id+"/relation-candidates");
        if(epoch!==seq)return;
        const values=(data.items || []).filter(r=>r.id!==d.id);
        box.innerHTML=field({key:"other",type:"select",label:search?"搜索结果":"关联候选",required:true,opts:values.map(r=>[r.id,"#"+r.number+" · "+r.station+" · "+(r.zh || r.problem || "").slice(0,51)])})+(!values.length?'<p class="cell-sub">可输入客户号或工位名称重新搜索。</p>':"");
      }catch(e){if(epoch===seq)errIn(box,e);}
    }
    form.querySelector("[data-related-search]").onclick=()=>populate(true);
    form.querySelector("[data-related-q]").onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();void populate(true);}};
    void populate(false);
  },async onSave(v){if(!v.other)throw new Error("请选择关联问题。");await api("issues/"+d.id+"/relations",{method:"POST",data:requestFields(d,{...v,other:Number(v.other)})});await updated(d.id,"问题关系已保存",v.actor);}});
}
function removeRelation(d,id){
  dialogForm({title:"解除问题关系",submit:"解除关系",body:'<div class="form-grid">'+actorField()+'</div>',async onSave(v){await api("issues/"+d.id+"/relations/"+id,{method:"DELETE",data:requestFields(d,v)});await updated(d.id,"问题关系已解除",v.actor);}});
}
function actionRow(a={}){
  return '<article class="detail-block" data-edit-action><div class="card-head"><h3>行动记录</h3><button type="button" class="text-btn" data-remove-action>移除此段</button></div><div class="form-grid">'+
    field({key:"date",label:"确认日期",type:"date",value:a.date})+field({key:"date_raw",label:"日期原文",value:a.date_raw})+
    field({key:"author",label:"记录人／原文署名",value:a.author,wide:true})+field({key:"original",label:"客户原文",type:"textarea",value:a.original,required:true})+field({key:"zh",label:"中文",type:"textarea",value:a.zh})+field({key:"en",label:"英文译文（德文来源适用）",type:"textarea",value:a.en})+'</div></article>';
}
function editActions(d){
  dialogForm({title:"编辑行动分段 · #"+d.number,wide:true,body:'<div class="form-grid">'+actorField()+'</div><div class="divider"></div><div class="stack" data-action-editor>'+(d.actions || []).map(actionRow).join("")+'</div><button type="button" class="btn" style="margin-top:12px" data-add-action>'+icon("plus")+'增加一段</button>',
    onMount(f){
      f.querySelector("[data-add-action]").onclick=()=>f.querySelector("[data-action-editor]").insertAdjacentHTML("beforeend",actionRow());
      f.addEventListener("click",e=>{if(e.target.closest("[data-remove-action]"))e.target.closest("[data-edit-action]").remove();});
    },async onSave(v,_data,form){
      const rows=[...form.querySelectorAll("[data-edit-action]")].map(node=>Object.fromEntries(["original","zh","en","date","date_raw","author"].map(k=>[k,node.querySelector('[name="'+k+'"]').value.trim()])));
      await api("issues/"+d.id+"/actions",{method:"POST",data:requestFields(d,{actor:v.actor,actions:rows})});await updated(d.id,"行动分段已保存",v.actor);
    }});
}
async function translate(d){
  dialogForm({title:"翻译待译内容 · #"+d.number,submit:"开始翻译",body:'<p>本次将此问题待译的描述或行动片段提交到已配置的翻译 API。</p>',
    async onSave(){const r=await api("translate",{method:"POST",data:{issue_id:d.id,limit:10}});await updated(d.id,"已翻译 "+Number(r.translated || 0)+" 段"+(r.warning?" · "+r.warning:""));}});
}
export function bindDrawer(){
  const dialog=document.getElementById("detail-dialog");
  dialog.addEventListener("click",e=>{
    const el=e.target.closest("button");if(!el)return;
    if(el.dataset.retryDetail){void openIssue(el.dataset.retryDetail);return;}
    if(!current)return;
    const d=current;
    if(el.dataset.detailTab){tab=el.dataset.detailTab;fillDrawer();return;}
    if(el.dataset.correctEvent){correctDate(d,el.dataset.correctEvent);return;}
    if(el.dataset.unlink){removeRelation(d,el.dataset.unlink);return;}
    const action={profile:editProfile,plan:editPlan,feedback:addFeedback,relation:addRelation,actionEdit:editActions,translate}[el.dataset.detail];
    if(action)void action(d);
  });
  dialog.addEventListener("close",()=>{if(!dialog.open){detailRequest?.abort();++loadSeq;current=null;tab="overview";}});
}
