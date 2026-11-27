// 预览导入、提交版本及处理待核项。 / Preview and commit imports and resolve review items.
import {actorField,api,apiUrl,context,count,dialogForm,empty,esc,failure,field,go,icon,issueLink,loading,options,pageTitle,qs,setActor,toast} from "./core.js";

let list=[],staged=[],currentPage=1;
const kinds={zf:"ZF 客户版",haosen:"豪森双语版",haosen_new:"豪森补充版",haosen_macro:"豪森补充版",local:"豪森资料",hs:"豪森资料",hs_plan:"豪森补充版"};
const reviewNames={field_action:"现场反馈待核",contact:"客户联系人待核",match:"客户行匹配",fields:"我方字段冲突",actions:"客户行动分段",file:"附件归属",missing:"客户版缺行",metadata:"客户字段待核",invalid:"客户字段待核",customer:"客户字段待核",customer_value:"客户字段待核"};
const summaryNames={rows:"问题行",new:"新增",matched:"匹配",changed:"有更新",closed:"客户关闭",transferred:"转纯 ZF",reopened:"重开／转回",missing:"新版缺行",review:"待核",unchanged:"未变化",translation:"待译"};
const fieldNames={plan_candidate:"豪森建议日期",zh:"中文译文",en:"英文译文",owner:"我方负责人",people:"整改人数／工种",work_time:"原表整改时间",order_no:"投产单号",hint:"豪森分类提示",note:"我方备注",problem:"问题原文",station:"工位",status:"客户状态",responsible:"客户责任方",author:"客户提出者"};
function reviewFields(r){
  const {tracker,...fields}=r.data?.fields || {};
  if(tracker && !fields.owner)fields.owner=tracker;
  return Object.entries(fields);
}
function kindName(k){return kinds[k] || (/haosen|hs|bilingual|supplement/i.test(k || "")?"豪森补充资料":k || "来源待核");}
function stats(summary={}){
  return Object.entries(summaryNames).filter(([k])=>summary[k]!==undefined && (summary[k] || ["rows","matched","review"].includes(k))).map(([k,label])=>'<span class="tag">'+label+' '+count(summary[k])+'</span>').join("");
}
function reviewBlock(r){
  const diff=reviewFields(r);
  const diffs=diff.length?'<div class="table-scroll plain"><table class="mini-table"><thead><tr><th>字段</th><th>当前值</th><th>导入值</th></tr></thead><tbody>'+diff.map(([k,d])=>'<tr><td>'+esc(fieldNames[k] || k)+'</td><td class="pre">'+esc(d.current ?? d.old ?? "")+'</td><td class="pre">'+esc(d.incoming ?? d.new ?? "")+'</td></tr>').join("")+'</tbody></table></div>':"";
  const evidence=[r.station,r.date,r.author].filter(Boolean).map(esc).join(" · ");
  return '<details class="collapse import-review"><summary><span class="tag">'+esc(reviewNames[r.kind] || "资料待核")+'</span> <b>'+esc(r.source_no?"#"+r.source_no:r.problem || r.file)+'</b> '+esc(r.reason)+'</summary><div class="card-body"><div class="cell-sub">'+esc(r.file || "")+(r.row?' · 行 '+esc(r.row):"")+' · '+esc(r.version || "")+'</div><p class="pre">'+esc(r.problem || "")+'</p><p class="cell-sub">'+evidence+'</p>'+diffs+(r.data?.text?'<p class="pre">'+esc(r.data.text)+'</p>':"")+
    (r.kind==="match" && r.source_action?'<details><summary>查看来源行动</summary><p class="pre">'+esc(r.source_action)+'</p></details>':"")+
    ((r.candidates || []).length?'<div class="flow-list">'+r.candidates.map(c=>'<div class="item-row"><div>'+issueLink(c.id,c.number)+' · '+esc(c.station)+'<p class="cell-sub pre">'+esc(c.problem)+'</p></div></div>').join("")+'</div>':"")+
    '<div class="tools" style="margin-top:10px"><button type="button" class="btn" data-review="'+esc(r.id)+'">'+icon("check")+'处理此项</button></div></div></details>';
}
function previewBlock(){
  if(!staged.length)return "";
  return '<section class="card"><div class="card-head"><h2>导入预览</h2><span class="card-text">'+count(staged.length)+' 个文件</span></div><div class="padded-list">'+staged.map((s,i)=>{
    const entries=s.examples || [], flags=[s.duplicate?"已导入相同文件":"",s.historical?"历史对照版本":"",s.baseline?"首次建立客户基线":""].filter(Boolean);
    return '<div class="item-row"><div><b>'+esc(s.file)+'</b><p class="cell-sub">'+esc(kindName(s.kind))+' · '+esc(s.version)+' · 修订 '+esc(s.revision_no ?? 0)+(flags.length?" · "+esc(flags.join(" · ")):"")+'</p><div class="tools" style="margin-top:8px">'+stats(s.summary)+'</div>'+
    (entries.length?'<details><summary class="text-btn">差异样例（'+count(entries.length)+'）</summary>'+entries.map(e=>'<p class="cell-sub">行 '+esc(e.row)+' · #'+esc(e.no)+' · '+esc(e.station)+' · '+esc(e.reason || ({new:"新增",review:"待复核",match:"匹配"}[e.mode] || ""))+'</p>').join("")+'</details>':"")+'</div><div class="tools"><button type="button" class="btn primary small-btn" data-commit="'+i+'">'+(s.duplicate?"确认重导":"确认导入")+'</button><button type="button" class="text-btn" data-drop-preview="'+i+'">取消</button></div></div>';
  }).join("")+'</div></section>';
}
export async function imports(params,signal){
  const page=Math.max(1,Number(params.review_page) || 1);
  const out=await api("imports"+qs({review_page:page}),{signal});
  list=out.reviews || [];currentPage=out.review_page || page;
  const pages=out.review_pages || 1;
  const batches=out.items || [];
  const preview=previewBlock();
  return pageTitle("导入与核对","客户版本、豪森补充资料和待核记录")+'<div class="stack"><section class="card"><div class="card-head"><div class="head-area">'+icon("import")+'<h2>读取 Excel 清单</h2></div></div><form class="card-body" id="import-form"><div class="form-grid"><label class="field wide">问题清单<input type="file" name="files" multiple accept=".xlsx,.xlsm" required></label>'+
    field({key:"version",label:"业务版本日期",type:"date",help:"客户文件名含完整日期时可自动读取。"})+field({key:"revision_no",label:"同日修订号",type:"number",value:0,min:0,step:1})+
    '<div class="wide tools"><button type="submit" class="btn primary">'+icon("search")+'读取并预览</button></div><div class="wide" data-import-message></div></div></form></section>'+preview+
    '<section class="card"><div class="card-head"><h2>待核记录 <span class="tag num">'+count(out.review_total ?? list.length)+'</span></h2></div>'+ (list.length?list.map(reviewBlock).join(""):empty("当前没有待核记录。","check"))+
    (pages>1?'<div class="pager"><span>第 '+currentPage+' / '+pages+' 页</span><div class="tools"><button class="btn small-btn" type="button" data-review-page="'+(currentPage-1)+'"'+(currentPage===1?" disabled":"")+'>上一页</button><button class="btn small-btn" type="button" data-review-page="'+(currentPage+1)+'"'+(currentPage>=pages?" disabled":"")+'>下一页</button></div></div>':"")+'</section>'+
    '<section class="card"><div class="card-head"><h2>版本记录</h2></div>'+ (batches.length?'<div class="padded-list">'+batches.map(b=>'<div class="item-row"><div><b>'+esc(b.file)+'</b><p class="cell-sub">'+esc(b.version)+' · 修订 '+esc(b.revision_no ?? 0)+' · '+esc(kindName(b.kind))+(b.applied===0?' · 历史对照':'')+'</p><div class="tools" style="margin-top:6px">'+stats(b.summary)+'</div></div><a class="btn small-btn" href="'+esc(apiUrl("imports/"+encodeURIComponent(b.id)+"/source"))+'" download>原文件</a></div>').join("")+'</div>':empty("尚无已导入版本。","file"))+'</section>'+
    '<section class="card"><div class="card-head"><h2>翻译队列</h2><span class="tag">'+count(out.translation_pending)+' 段待译</span></div><div class="card-body">'+(context.config.translation_enabled?'<button class="btn" data-translate type="button">'+icon("book")+'翻译下一批</button>':'<p class="card-text">离线模式。可在问题详情补录译文，或在本机设置 OPENAI_API_KEY 后重启。</p>')+'</div></section></div>';
}
async function refresh(){
  await context.refreshConfig();
  await context.refresh();
}
async function reviewDialog(r){
  const actions=r.kind==="field_action" && r.candidates.length?(await api("issues/"+r.candidates[0].id)).actions:[];
  const isFields=r.kind==="fields",canBind=["match","file"].includes(r.kind);
  const choices=[];
  if(r.kind==="field_action")choices.push(["accept","确认为现场反馈"]);
  if(actions.length)choices.push(["translation","补充为某条客户行动的中文译文"]);
  if(isFields)choices.push(["accept","采用勾选字段的导入值"]);
  if(r.kind==="actions")choices.push(["accept","采用新版客户行动分段"]);
  if(canBind)choices.push(["bind","绑定到指定客户问题"]);
  if(r.kind==="match" && r.source_kind==="zf")choices.push(["new","作为独立客户问题新增"]);
  choices.push(["dismiss",isFields?"保留原值并结清待核":"保留现有数据并结清待核"]);
  const body='<p class="pre muted" style="margin-bottom:12px">'+esc(r.reason)+'</p><div class="form-grid">'+actorField()+field({key:"decision",label:"处理方式",type:"select",opts:choices,required:true})+
    (r.kind==="field_action"?field({key:"day",label:"现场反馈日期",type:"date",value:r.data?.day || ""}):"")+
    (r.kind==="match" && !r.source_no && r.source_kind==="zf"?field({key:"number",label:"作为新问题时填写核实后的客户号"}):"")+
    (actions.length?'<div class="wide" data-translation hidden>'+field({key:"action_id",label:"对应的客户行动",type:"select",opts:actions.map(a=>[a.id,[a.date,a.original].filter(Boolean).join(" · ")])})+field({key:"translation",label:"中文译文",type:"textarea",value:r.data.note || ""})+'</div>':"")+
    (isFields?'<fieldset class="wide review-choices" data-accept-fields hidden><legend>选择采用导入值的字段</legend>'+reviewFields(r).map(([k,delta])=>'<label class="review-choice"><input type="checkbox" name="fields" value="'+esc(k)+'"><span><b>'+esc(fieldNames[k] || k)+'</b><br>'+esc(delta.current || "空")+' → '+esc(delta.incoming || "空")+'</span></label>').join("")+'</fieldset>':"")+
    (r.kind==="actions" && r.data?.action?'<details class="wide collapse"><summary>查看最新客户行动</summary><p class="pre">'+esc(r.data.action)+'</p></details>':"")+
    (canBind?'<div class="wide" data-bind-target hidden><div class="form-grid"><label class="field wide">查找客户问题<div class="field-inline"><input type="search" data-find-q placeholder="客户号或关键词"><button type="button" class="btn" data-find>搜索</button></div></label><div class="wide" data-bind-options></div></div></div>':"")+
    field({key:"note",label:"核对说明",type:"textarea"})+'</div>';
  dialogForm({title:"处理待核记录",body,submit:"保存核对结果",onMount(f){
    if(actions.length)f.elements.decision.addEventListener("change",()=>{f.querySelector("[data-translation]").hidden=f.elements.decision.value!=="translation";});
    if(isFields)f.elements.decision.addEventListener("change",()=>{f.querySelector("[data-accept-fields]").hidden=f.elements.decision.value!=="accept";});
    if(!canBind)return;
    const target=f.querySelector("[data-bind-target]"),wrap=f.querySelector("[data-bind-options]");
    const render=items=>{wrap.innerHTML=field({key:"issue_id",label:"目标客户问题",type:"select",opts:items.map(x=>[x.id,"#"+x.number+" · "+x.station+" · "+(x.problem || "").slice(0,40)])});};
    render(r.candidates || []);
    f.elements.decision.onchange=()=>{target.hidden=f.elements.decision.value!=="bind";};
    async function search(){
      const term=f.querySelector("[data-find-q]").value.trim();if(!term){render(r.candidates || []);return;}
      try{const result=await api("issues"+qs({q:term,scope:"all",limit:35}));render(result.items || []);}catch(e){wrap.innerHTML=failure(e);}
    }
    f.querySelector("[data-find]").onclick=search;
    f.querySelector("[data-find-q]").onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();void search();}};
  },async onSave(v,data){
    if(v.decision==="bind" && !v.issue_id)throw new Error("请先选择目标客户问题。");
    if(isFields && v.decision==="accept"){
      v.fields=data.getAll("fields");
      if(!v.fields.length)throw new Error("请选择要采用导入值的字段。");
    }else delete v.fields;
    await api("reviews/"+r.id,{method:"POST",data:{...v,issue_id:v.issue_id?Number(v.issue_id):null}});
    setActor(v.actor);await refresh();toast("核对结果已保存");
  }});
}
export function bindImports(node){
  const form=node.querySelector("#import-form");
  form.onsubmit=async e=>{
    e.preventDefault();if(!form.reportValidity() || form.dataset.busy)return;
    const btn=form.querySelector("[type=submit]"),msg=form.querySelector("[data-import-message]");
    form.dataset.busy="1";btn.disabled=true;msg.innerHTML=loading();
    try{
      const data=await api("imports/preview",{method:"POST",data:new FormData(form)});
      staged=[...staged,...(data.items || [])];await context.refresh();toast("导入预览已生成");
    }catch(error){msg.innerHTML=failure(error);}
    finally{delete form.dataset.busy;btn.disabled=false;}
  };
  node.onclick=e=>{
    const b=e.target.closest("button");if(!b)return;
    if(b.dataset.reviewPage){go("imports",{review_page:b.dataset.reviewPage});return;}
    if(b.dataset.review){const r=list.find(x=>String(x.id)===b.dataset.review);if(r)void reviewDialog(r).catch(err=>toast(err.message));return;}
    if(b.dataset.dropPreview!==undefined){staged.splice(Number(b.dataset.dropPreview),1);void context.refresh();return;}
    if(b.dataset.commit!==undefined){
      const item=staged[Number(b.dataset.commit)];
      if(!item)return;
      dialogForm({title:"确认导入版本",submit:"提交此版本",body:'<p class="pre">'+esc(item.file)+'</p><p class="cell-sub">'+esc(item.version)+' · 修订 '+esc(item.revision_no ?? 0)+'</p><div class="tools" style="margin-top:12px">'+stats(item.summary)+'</div>',
        async onSave(){await api("imports/commit",{method:"POST",data:{token:item.token,version:item.version,revision_no:item.revision_no ?? 0}});staged=staged.filter(v=>v.token!==item.token);await refresh();toast("版本已处理");}});
    } else if(b.hasAttribute("data-translate")){
      dialogForm({title:"翻译下一批内容",submit:"开始翻译",body:'<div class="form-grid">'+field({key:"limit",label:"最多处理片段数（1–30）",type:"number",min:1,step:1,value:3,required:true})+'</div><p class="cell-sub">将待译的描述或行动文字提交至本机已配置的 OpenAI API。</p>',
        async onSave(v){if(Number(v.limit)>30)throw new Error("每批最多可处理 30 个片段。");const r=await api("translate",{method:"POST",data:{limit:Number(v.limit)}});await refresh();toast("已翻译 "+count(r.translated)+" 段"+(r.warning?" · "+r.warning:""));}});
    }
  };
}
