// 呈现每周变更、计划与工位统计。 / Present weekly changes, work plans and station counts.
import {line,api,areaToken,context,count,empty,esc,href,icon,issueLink,pageTitle,qs,stateName,statePill,weekLabel,weekTools} from "./core.js";

const clarificationStates = ["clarify_zf","approve_zf","confirm_internal"];
const milestones = [
  [clarificationStates,"待澄清／确认","#8871a3"],
  [["working"],"进入整改","#4c9a7e"],[["ready"],"完成待上报","#c6904d"],
  [["reported"],"已上报待核查","#5d79ab"],[["rework"],"需返工","#ba7158"],
  [["closed"],"客户已关闭","#608972"],[["zf_only"],"转纯 ZF","#798b98"]
];

function withIssues(params, label, cls="text-btn") {
  return '<a class="'+cls+'" href="'+esc(href("issues",params))+'">'+label+'</a>';
}

// 原生可聚焦滚动区支持滚轮、滚动条和方向键。 / A focusable native region supports wheel, scrollbar and keyboard input.
function weeklyList(items, heading, renderItem, emptyMessage, emptyIcon) {
  if (!items.length) return empty(emptyMessage, emptyIcon);
  return '<div class="overview-scroll" role="region" aria-labelledby="'+heading+'" tabindex="0">'+
    '<ul class="flow-list">'+items.map(renderItem).join("")+'</ul></div>';
}

function planItem(item) {
  const dates = [];
  if (item.working_since) {
    dates.push('<span><b>本周开工</b> <time datetime="'+esc(item.working_since)+'">'+esc(item.working_since)+'</time></span>');
  }
  // 正式计划可能精确到日或周，也可能落在所选周之外。 / A formal plan can be a date or ISO week outside the selected week.
  const planLabel = item.is_week_plan ? "本周正式计划" : "正式计划";
  const planValue = item.plan
    ? '<time datetime="'+esc(item.plan)+'">'+esc(weekLabel(item.plan))+'</time>' : '待安排';
  dates.push('<span><b>'+planLabel+'</b> '+planValue+'</span>');
  const owner = item.is_week_plan ? (item.plan_owner || item.owner) : (item.owner || item.plan_owner);
  return '<li><div class="flow-line">'+issueLink(item.id,item.number)+'<b>'+esc(item.station)+'</b>'+
    (item.state ? statePill(item.state,item.state_label) : '')+'</div>'+
    '<div class="overview-dates">'+dates.join('')+'</div>'+
    '<div class="flow-detail">'+esc([owner || item.tracker,item.zh || item.problem].filter(Boolean).join(" · "))+'</div></li>';
}

export function distributionTable(rows, type="area", linkParams={}) {
  if(!rows?.length)return empty("当前筛选范围内没有问题。");
  const typeLabel=type==="area"?"区域":"工位";
  return '<div class="table-scroll plain"><table class="area-summary"><thead><tr><th>'+typeLabel+'</th><th class="numeric">总数</th><th class="numeric">High</th><th class="numeric">Medium</th><th class="numeric">Low</th><th class="distribution">优先级分布</th></tr></thead><tbody>'+rows.map(r=>{
    const params={...linkParams,[type]:r[type]}, total=Number(r.total || 0);
    delete params.page;
    delete params.issue;
    const cell=(key,value)=>withIssues({...params,...(key?{priority:key}:{})},esc(count(value)),"linklike num");
    const bars=["High","Medium","Low"].map(k=>'<i style="width:'+(total?Math.min(100,Number(r[k] || 0)/total*100):0)+'%"></i>').join("");
    const name=type==="area"?areaToken(r[type]):esc(r[type] || "未映射工位");
    return '<tr><td>'+withIssues(params,name,"linklike")+'</td><td class="numeric">'+cell("",total)+'</td>'+["High","Medium","Low"].map(k=>'<td class="numeric">'+cell(k,r[k])+'</td>').join("")+'<td class="distribution"><span class="bar" aria-hidden="true">'+bars+'</span></td></tr>';
  }).join("")+'</tbody></table></div>';
}

export async function overview(params, signal) {
  const week=params.week || context.config.week;
  const data=await api("overview"+qs({week}),{signal});
  const w=data.week || week, stocks=data.stocks || {}, t=data.totals || {};
  const weekLink={week:w,mode:"latest",scope:"all"};
  const metrics=milestones.map(([states,label,color])=>{
    const total=states.reduce((sum,state)=>sum+(data.counts?.[state] || 0),0);
    const breakdown=states.map(state=>stateName(state)+" "+count(data.counts?.[state])).join(" · ");
    return withIssues({...weekLink,state:states},
      '<span class="metric-caption" title="'+esc(breakdown)+'"><i class="swatch" style="--swatch:'+color+'"></i>'+label+'</span><b class="big-number">'+count(total)+'</b>',"metric");
  }).join("");
  const eventBody=weeklyList(data.events || [], "weekly-events-title", ev=>
    '<li><div class="flow-line">'+issueLink(ev.issue_id,ev.number)+statePill(ev.to_state)+'<time class="stamp" datetime="'+esc(ev.day)+'">'+esc(ev.day)+'</time></div><div class="flow-detail">'+esc([ev.from_state && stateName(ev.from_state)+" → "+stateName(ev.to_state),ev.station,ev.actor,ev.note].filter(Boolean).join(" · "))+'</div></li>',
    "本周暂无状态变更。","clock");
  const planBody=weeklyList(data.plans || [], "weekly-plans-title", planItem,
    "本周暂无正式计划或新开工的整改项。","calendar");
  const asof={mode:"asof",scope:"ours",week:w,as_of:data.as_of};
  let deltaLabel="较上周末",deltaValue="缺上周基线";
  if(t.delta!==null && t.delta!==undefined){const change=Number(t.delta);deltaLabel=change<0?"较上周净减少":change>0?"较上周净增加":"较上周净变化";deltaValue=count(Math.abs(change));}
  const tiles=[
    ["我方未结",count(t.ours),href("issues",asof)],["客户未结",count(t.customer),href("issues",{...asof,scope:"customer"})],
    [deltaLabel,deltaValue],["本周新增",count(t.new)],["重开／转回",count(t.reopened)]
  ];
  const overall='<div class="overall-numbers">'+tiles.map(([label,value,url])=>{
    const inside='<span>'+esc(label)+'</span><b'+(value==="缺上周基线"?' class="no-data"':"")+'>'+esc(value)+'</b>';
    return url?'<a class="overall-num" href="'+esc(url)+'">'+inside+'</a>':'<div class="overall-num">'+inside+'</div>';
  }).join("")+'</div>';
  const pending=[...clarificationStates,"working","ready"].map(k=>withIssues({...asof,state:k},esc(stateName(k))+" "+count(stocks[k]),"inline-count")).join("");
  return pageTitle("项目概览",(data.start || "")+" 至 "+(data.end || "")+" · "+weekLabel(w),weekTools(w))+
    '<div class="stack"><section class="card"><div class="card-head"><div class="head-area">'+icon("dashboard")+'<h2>本周状态更新</h2></div>'+withIssues(weekLink,"查看本周更新")+'</div><div class="slab">'+metrics+'</div></section>'+
    '<div class="dashboard-grid"><section class="card"><div class="card-head"><h2 id="weekly-events-title">状态变更记录</h2><span class="card-text">'+esc(weekLabel(w))+'</span></div><div class="card-body">'+eventBody+'<div class="hint-row"><small>截至所选日</small>'+pending+'</div></div></section>'+
    '<section class="card"><div class="card-head"><h2 id="weekly-plans-title">本周实施计划与整改</h2>'+withIssues({mode:"planned",week:w,scope:"all"},"查看全部")+'</div><div class="card-body">'+planBody+'<div class="hint-row">'+withIssues({mode:"unreported",week:w,scope:"all"},"查看计划中尚未上报的项")+'</div></div></section></div>'+
    '<section class="card"><div class="card-head"><div class="head-area">'+icon("production")+'<h2>'+esc(line.name)+'整体情况</h2></div><span class="card-text">截至 '+esc(data.as_of || data.end)+'</span></div>'+overall+
    (line.phased?'<div class="table-caption"><h3>按区域类型</h3></div>'+distributionTable(data.areas,"area",asof):"")+
    '<details class="collapse"'+(line.phased?"":" open")+'><summary>查看各工位统计</summary>'+distributionTable(data.stations,"station",asof)+'</details></section></div>';
}
