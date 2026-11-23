"""以柏林业务日还原问题状态，提供筛选、统计、详情和周报。 / Rebuild dated issue views for filters, statistics, details and weekly reports."""

# datetime 计算 ISO 周；collections 聚合工位优先级，SQLite 提供事件时间线。 / datetime computes ISO weeks; collections groups priority counts over SQLite events.
from collections import Counter, defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
import re

from .people import customer_author, internal_owner
from .stations import station_label
from .store import decode, iso_day, need_issue, number_map, today, week_bounds, week_of
from .workflow import ARCHIVE, STATES, allowed, issue_owner, state_label


def owner_names(value):
    return [s.strip() for s in re.split(r"[/、,，;；\n]+",value or "") if s.strip()]


def event_view(row):
    e=dict(row)
    e["payload"]=decode(e["payload"],{})
    return e


def rows_at(conn, cutoff=None, only=None, *, source=False):
    """选取截止日前的事件与计划，逆向应用后续字段变更。 / Select effective events and plans and undo later field edits."""
    cutoff=cutoff or today()
    numbers=number_map(conn)
    predicate=" AND issue_id=?" if only else ""
    params=(cutoff,only) if only else (cutoff,)
    events={r["issue_id"]:r for r in conn.execute(
        "SELECT * FROM events WHERE id IN (SELECT MAX(id) FROM events WHERE day<=?"+
        predicate+" GROUP BY issue_id)",params)}
    plans={}
    for r in conn.execute("SELECT * FROM plans WHERE day<=?"+predicate+" ORDER BY day,id",params):
        plans[r["issue_id"]]=r
    responses={}
    for r in conn.execute("SELECT * FROM feedback WHERE day<=?"+predicate+" ORDER BY day,id",params):
        responses[r["issue_id"]]=r
    undo=defaultdict(list)
    if cutoff<today():
        for r in conn.execute("SELECT * FROM audit WHERE day>?"+predicate+" ORDER BY day DESC,id DESC",params):
            if r["kind"] in ("customer_diff","profile","supplement","owner_change","outdated_translation","translation"):
                undo[r["issue_id"]].append((r["kind"],decode(r["payload"])))
    action_heads={}
    for a in conn.execute("""SELECT issue_id,zh,original,day,date_raw FROM (
              SELECT issue_id,zh,original,day,date_raw,ROW_NUMBER() OVER
              (PARTITION BY issue_id ORDER BY day DESC,position,id) AS rank
              FROM actions WHERE active=1 AND day<=?""" + predicate + ") WHERE rank=1", params):
        action_heads[a["issue_id"]]=a
    rows=[]
    for i in conn.execute("SELECT * FROM issues"+(" WHERE id=?" if only else ""),(only,) if only else ()):
        uid=i["id"]
        e=events.get(uid)
        if not e:
            continue
        customer,local=decode(i["customer"]),decode(i["local"])
        for kind,payload in undo[uid]:
            if kind=="customer_diff":
                for k,change in payload["changes"].items():
                    customer[k]=change["old"]
            elif kind=="profile":
                local=payload["old"]
            elif kind=="supplement":
                for k in payload["added"]:
                    local.pop(k,None)
            elif kind=="outdated_translation":
                local.update(zh=payload.get("zh",""),en=payload.get("en",""))
            elif kind=="translation" and payload.get("kind")=="problem":
                local.pop("zh",None)
                local.pop("en",None)
            elif kind=="owner_change":
                for k in ("owner","tracker","manual_fields"):
                    local.pop(k,None)
                local.update(payload["old"])
        plan,fb=plans.get(uid),responses.get(uid)
        latest_action=action_heads.get(uid)
        if cutoff < today():
            # 历史周报只纳入截止日以前的行动。 / A later action must not appear in a historical weekly report.
            from .excel import split_actions
            parts=split_actions(customer.get("action",""))
            head=max((x for x in parts if x["date"]<=cutoff),key=lambda x:x["date"],default=None)
            latest_action=dict(head,day=head["date"]) if head else None
        summary=fb["note"] if fb else ""
        summary_day=fb["day"] if fb else ""
        if latest_action and (not summary or latest_action["day"] and latest_action["day"]>summary_day):
            summary=latest_action["zh"] or latest_action["original"]
            summary_day=latest_action["day"] or latest_action["date_raw"]
        payload=decode(e["payload"],{})
        base=customer["no"]
        row={k:customer.get(k,"") for k in (
            "station","machine","avo","area","priority","priority_raw","department","category","responsible",
            "resp_class","problem","due","author","date","station_review","station_raw","status","changed_on")}
        row.update({k:local.get(k,"") for k in ("people","work_time","order_no","hint","note","en")})
        row["author_raw"] = row["author"]
        row["author"] = customer_author(row["author"])
        row["station"] = station_label(row["station"], row["machine"])
        if row["station"] == "AGV":
            row["area"] = "AGV"
        row["owner"] = issue_owner(local)
        if cutoff < today() and payload.get("tracker") and "owner" not in local.get("manual_fields", ()):
            row["owner"] = internal_owner(payload["tracker"])
        if source:
            row["source_action"] = customer.get("action", "")
            row["changed_by"] = customer.get("changed_by", "")
        row.update(id=uid,number=numbers.get((uid,base),base),base=base,
                   state=e["to_state"],state_label=state_label(e["to_state"],e["kind"]),state_day=e["day"],
                   state_kind=e["kind"],zh=local.get("zh") or customer.get("embedded_zh",""),
                   plan=plan["plan"] if plan else "",plan_note=plan["note"] if plan else "",
                   plan_owner=internal_owner(plan["tracker"]) if plan else "",latest=summary,latest_day=summary_day,
                   revision=i["revision"],created_day=i["created_day"], aliases=[])
        rows.append(row)
    by_id={r["id"]:r for r in rows}
    for (uid,base),display in numbers.items():
        if uid in by_id:
            by_id[uid]["aliases"].extend((base,display))
    return rows


def group_by(rows,key):
    groups=defaultdict(Counter)
    fixed = {"area":("S1","S2","AGV","General")}
    for name in fixed.get(key, ()):
        groups[name]
    for row in rows:
        counts=groups[row.get(key) or "待核"]
        counts["total"]+=1
        counts[row["priority"]]+=1
    result=[{key:k,**{p:c[p] for p in ("total","High","Medium","Low","待核")}} for k,c in groups.items()]
    if key not in fixed:
        result.sort(key=lambda r:(-r["total"],r[key]))
    return result


def week_context(params):
    week=params.get("week") or week_of(today())
    start,end=week_bounds(week)
    cutoff=min(end,today())
    if params.get("as_of"):
        cutoff=min(iso_day(params["as_of"]),today())
    return week,start,end,cutoff


def scheduled(row,week):
    plan=row["plan"]
    return bool(plan) and (plan==week if "-W" in plan else week_of(plan)==week)


def unreported(conn,cutoff):
    sent,cycle={},{}
    for r in conn.execute("""SELECT issue_id,to_state,id FROM events WHERE day<=?
                          AND to_state IN ('planned','rework','reported','triage') ORDER BY id""",(cutoff,)):
        (sent if r["to_state"]=="reported" else cycle)[r["issue_id"]]=r["id"]
    return {uid for uid,seq in sent.items() if seq>cycle.get(uid,0)}


MULTI_FIELDS = ("station", "machine", "avo", "area", "author", "department", "category", "priority",
               "responsible", "owner", "zf", "state")
ZF_GROUPS = {"pure": {"zf"}, "joint": {"joint"}, "any": {"zf", "joint"}, "none": {"haosen"}}


def selected(params, key):
    if hasattr(params, "getlist"):
        values = params.getlist(key)
    else:
        values = params.get(key, [])
        if not isinstance(values, (list, tuple, set)):
            values = [values]
    return tuple(dict.fromkeys(str(v).strip() for v in values if v is not None and str(v).strip()))


def add_related_numbers(conn, rows, numbers, cutoff):
    """附上同一统计日的关联编号，包括筛选范围外的问题。 / Include dated links even when their targets are filtered out."""
    if not rows:
        return
    edges = set(map(tuple, conn.execute("SELECT a,b,kind FROM relations")))
    if cutoff < today():
        # 倒放后续变更。每次变更会在两端各记一笔，只需读取一端。 / Undo each later link change once.
        for log in conn.execute("SELECT issue_id,payload FROM audit WHERE kind='relation' AND day>? ORDER BY day DESC,id DESC", (cutoff,)):
            change = decode(log["payload"])
            record = change.get("removed", change)
            if log["issue_id"] != record["a"]:
                continue
            edge = (record["a"], record["b"], record["kind"])
            if "removed" in change:
                edges.add(edge)
            else:
                edges.discard(edge)
    neighbors, children = defaultdict(list), defaultdict(list)
    for a,b,kind in sorted(edges):
        if a not in numbers or b not in numbers:
            continue
        neighbors[a].append(dict(id=b, number=numbers[b], kind=kind, direction="out"))
        neighbors[b].append(dict(id=a, number=numbers[a], kind=kind, direction="in"))
        if kind == "detail":
            children[a].append(b)
    for row in rows:
        uid = row["id"]
        refs = list(neighbors[uid])
        pending = list(children[uid])
        seen = {uid, *pending}
        while pending:
            for other in children[pending.pop()]:
                if other not in seen:
                    seen.add(other)
                    pending.append(other)
                    refs.append(dict(id=other, number=numbers[other], kind="detail", direction="descendant"))
        row["related"] = refs


def filtered_issues(conn, params, *, source=False):
    """同维度取并集，维度间取交集，最后统一排序。 / Union values within each dimension, intersect dimensions, then sort."""
    week, start, end, week_end = week_context(params)
    mode = params.get("mode") or "current"
    cutoff = week_end if mode != "current" or params.get("as_of") else today()
    rows = rows_at(conn, cutoff, source=source)
    numbers = {row["id"]: row["number"] for row in rows}
    states = selected(params, "state")
    scope = params.get("scope") or ("all" if mode == "occurred" or set(states).intersection(ARCHIVE) else "ours")
    if scope == "ours":
        rows = [r for r in rows if r["state"] not in ARCHIVE]
    elif scope == "customer":
        rows = [r for r in rows if r["state"] != "closed"]
    if mode == "occurred":
        sql = "SELECT DISTINCT issue_id FROM events WHERE kind<>'baseline' AND day BETWEEN ? AND ?"
        values = [start, min(end, cutoff)]
        if states:
            sql += " AND to_state IN (" + ",".join("?" for _ in states) + ")"
            values.extend(states)
        if params.get("event_kind"):
            sql += " AND kind=?"
            values.append(params["event_kind"])
        occurred = {r[0] for r in conn.execute(sql, values)}
        rows = [r for r in rows if r["id"] in occurred]
    elif states:
        rows = [r for r in rows if r["state"] in states]
    if mode == "latest":
        rows = [r for r in rows if r["state_kind"] != "baseline" and start <= r["state_day"] <= min(end, cutoff)]
    if mode in ("planned", "unreported"):
        rows = [r for r in rows if scheduled(r, week)]
        if mode == "unreported":
            sent = unreported(conn, cutoff)
            rows = [r for r in rows if r["id"] not in sent and r["state"] not in ARCHIVE]
    for key in MULTI_FIELDS:
        if key == "state":
            continue
        choices = selected(params, key)
        if key == "owner":
            choices += selected(params, "tracker")  # Bookmarks saved by the first release.
        if not choices:
            continue
        values = {v.casefold() for v in choices}
        if key == "station":
            values = {station_label(v).casefold() for v in values}
        if key == "author":
            values = {customer_author(v).casefold() for v in values}
        if key == "owner":
            rows = [r for r in rows if any(v.casefold() in values for v in [r["owner"], *owner_names(r["owner"])])]
        elif key == "zf":
            accepted = set().union(*(ZF_GROUPS.get(v, set()) for v in values))
            rows = [r for r in rows if r["resp_class"] in accepted]
        else:
            rows = [r for r in rows if str(r.get(key) or "").casefold() in values]
    q = (params.get("q") or "").strip().casefold().lstrip("#")
    if q:
        if re.fullmatch(r"\d+[a-z]*", q) and not q.startswith("2482"):
            rows = [r for r in rows if q in r["aliases"]]
        else:
            keys = ("number", "station", "station_raw", "machine", "avo", "zh", "en", "problem",
                    "latest", "note", "owner", "author", "author_raw", "order_no")
            rows = [r for r in rows if q in " ".join(str(r[k] or "") for k in keys).casefold()]
    # 按数字及后缀分配顺序排序（z 后为 aa）。 / Sort by numeric ID and its stable suffix slot.
    order = params.get("sort") or "number_desc"
    if order not in ("number_asc","number_desc"):
        raise ValueError("编号排序须选择升序或降序。")
    def number_key(row):
        suffix = row["number"][len(row["base"]):]
        return (int(row["base"]) if row["base"].isdigit() else -1, len(suffix), suffix, row["id"])
    rows.sort(key=number_key, reverse=order == "number_desc")
    add_related_numbers(conn, rows, numbers, cutoff)
    return rows, cutoff


def issue_list(conn, params):
    rows, cutoff = filtered_issues(conn, params)
    limit=min(max(int(params.get("limit") or 40),1),300)
    total=len(rows)
    pages=max(1,(total+limit-1)//limit)
    page=min(max(int(params.get("page") or 1),1),pages)
    return dict(items=rows[(page-1)*limit:page*limit],total=total,page=page,pages=pages,
                stations=group_by(rows,"station"),areas=group_by(rows,"area"),as_of=cutoff)


def options(conn):
    choices=defaultdict(set)
    keys=("station","machine","avo","area","author","department","category","responsible","owner","priority")
    for row in rows_at(conn):
        for key in keys:
            if key == "owner":
                choices[key].update(owner_names(row[key]))
            elif row[key]:
                choices[key].add(row[key])
    return {key:sorted(choices[key]) for key in keys}


def overview(conn,params):
    """对比上周末存量，并按问题对本周流转去重。 / Compare the previous closing balance and deduplicate weekly transitions by issue."""
    week,start,end,cutoff=week_context(params)
    rows=rows_at(conn,cutoff)
    items={r["id"]:r for r in rows}
    active=[r for r in rows if r["state"] not in ARCHIVE]
    prior=(date.fromisoformat(start)-timedelta(days=1)).isoformat()
    first=conn.execute("SELECT MIN(version) FROM batches WHERE applied=1 AND kind='zf'").fetchone()[0]
    prior_total=None
    if first and first<=prior and start<=today():
        prior_total=conn.execute("""SELECT COUNT(*) FROM events WHERE id IN
         (SELECT MAX(id) FROM events WHERE day<=? GROUP BY issue_id) AND to_state NOT IN ('closed','zf_only')""",(prior,)).fetchone()[0]
    current_events=[]
    per_state=defaultdict(set)
    per_reason=defaultdict(set)
    for e in conn.execute("""SELECT * FROM events WHERE kind<>'baseline' AND day BETWEEN ? AND ?
                          ORDER BY day DESC,id DESC""",(start,min(end,cutoff))):
        item=items.get(e["issue_id"])
        if not item:
            continue
        current_events.append({**event_view(e),"number":item["number"],"station":item["station"],"owner":item["owner"]})
        per_state[e["to_state"]].add(e["issue_id"])
        per_reason[e["kind"]].add(e["issue_id"])
    plans=[r for r in rows if scheduled(r,week)]
    plans.sort(key=lambda r:(r["plan"],r["number"]))
    return dict(week=week,start=start,end=end,as_of=cutoff,
                totals=dict(ours=len(active),customer=sum(r["state"]!="closed" for r in rows),
                            delta=None if prior_total is None else len(active)-prior_total,new=len(per_reason["new"]),
                            reopened=len(per_reason["reopened"]|per_reason["returned"]),
                            returned=len(per_reason["returned"]),closed=len(per_state["closed"]),transferred=len(per_state["zf_only"])),
                areas=group_by(active,"area"),stations=group_by(active,"station"),
                counts={s:len(per_state[s]) for s in STATES},stocks=dict(Counter(r["state"] for r in rows)),
                events=current_events,plans=plans,baseline=first)


def links_for(conn, uid, numbers):
    result=[]
    rows=conn.execute("""WITH RECURSIVE children(other) AS (
         SELECT b FROM relations WHERE a=? AND kind='detail'
         UNION SELECT r.b FROM relations r JOIN children c ON r.a=c.other WHERE r.kind='detail')
       SELECT r.id,r.kind,r.note,r.actor,r.a,r.b,i.id AS other,i.base,i.customer,i.state,i.reason
       FROM relations r JOIN issues i ON i.id=CASE WHEN r.a=? THEN r.b ELSE r.a END WHERE r.a=? OR r.b=?
       UNION ALL SELECT NULL,'detail','','',?,c.other,i.id,i.base,i.customer,i.state,i.reason
       FROM children c JOIN issues i ON i.id=c.other WHERE NOT EXISTS(
       SELECT 1 FROM relations d WHERE d.a=? AND d.b=c.other AND d.kind='detail')""",(uid,uid,uid,uid,uid,uid))
    for r in rows:
        result.append(dict(id=r["id"],kind=r["kind"],note=r["note"],actor=r["actor"],issue_id=r["other"],
                           number=numbers.get((r["other"],r["base"]),r["base"]),station=station_label(decode(r["customer"])["station"]),
                           state_label=state_label(r["state"],r["reason"]),
                           direction="descendant" if r["id"] is None else ("out" if r["a"]==uid else "in")))
    return result


def file_view(r):
    return {"id":r["id"],"name":r["name"],"mime":r["mime"],"url":f"/api/files/{r['id']}"}


def detail(conn,uid):
    issue=need_issue(conn,uid)
    rows=rows_at(conn,only=uid)
    if not rows:
        raise LookupError("问题尚未在当前日期生效。")
    item=rows[0]
    item["allowed"]=allowed(issue["state"])
    item["events"]=[event_view(r) for r in conn.execute("SELECT * FROM events WHERE issue_id=? ORDER BY day DESC,id DESC",(uid,))]
    item["actions"]=[dict(r) for r in conn.execute("""SELECT id,original,zh,en,day AS date,date_raw,author,manual
                    FROM actions WHERE active=1 AND issue_id=? ORDER BY position,id""",(uid,))]
    files_by_fb=defaultdict(list)
    attachments={}
    for f in conn.execute("SELECT f.*,l.feedback_id FROM file_links l JOIN files f ON f.id=l.file_id WHERE issue_id=?",(uid,)):
        v=file_view(f)
        attachments[f["id"]]=v
        files_by_fb[f["feedback_id"]].append(v)
    item["attachments"]=list(attachments.values())
    item["feedback"]=[{**dict(f),"files":files_by_fb[f["id"]]} for f in conn.execute(
        "SELECT * FROM feedback WHERE issue_id=? ORDER BY day DESC,id DESC",(uid,))]
    item["relations"]=links_for(conn,uid,number_map(conn))
    item["snapshots"]=[]
    for s in conn.execute("""SELECT b.id,b.file,b.sheet,b.kind,b.version,b.revision_no,b.applied,s.row_no,s.fields
               FROM source_rows s JOIN batches b ON b.id=s.batch_id WHERE s.issue_id=?
               ORDER BY b.version DESC,b.revision_no DESC,b.id DESC""",(uid,)):
        raw=decode(s["fields"])
        item["snapshots"].append({"id":s["id"],"file":s["file"],"sheet":s["sheet"],"row":s["row_no"],
                                  "version":s["version"],"kind":s["kind"],"applied":bool(s["applied"]),
                                  "revision_no":s["revision_no"],"raw":raw,"url":f"/api/imports/{s['id']}/source"})
    item["plan_history"]=[dict(r) for r in conn.execute(
        "SELECT id,day,plan,tracker AS owner,note,actor,recorded FROM plans WHERE issue_id=? ORDER BY day DESC,id DESC",(uid,))]
    item["audit"]=[{**dict(r),"payload":decode(r["payload"])} for r in conn.execute(
        "SELECT * FROM audit WHERE issue_id=? ORDER BY id DESC",(uid,))]
    return item


def candidates(conn,uid):
    issue=need_issue(conn,uid)
    current=decode(issue["customer"])
    rows=rows_at(conn)
    words=lambda value:set(re.findall(r"[\w-]{3,}",value.casefold()))
    terms=words(current["problem"])
    linked={r["issue_id"] for r in links_for(conn,uid,number_map(conn))}
    scores=[]
    for r in rows:
        if r["id"]==uid or r["id"] in linked:
            continue
        other=words(r["problem"])
        score=len(terms&other)/max(1,len(terms|other))
        if r["base"]==issue["base"]:
            score+=0.5
        if score>=0.18:
            scores.append((score,r))
    return [r for score,r in sorted(scores,key=lambda p:-p[0])[:16]]


def review_list(conn, page=1, limit=100, status="open"):
    page=max(1,int(page)); limit=max(1,min(250,int(limit)))
    total=conn.execute("SELECT COUNT(*) FROM reviews WHERE status=?",(status,)).fetchone()[0]
    sql="""SELECT r.*,s.row_no,s.fields,b.file,b.version FROM reviews r
          LEFT JOIN source_rows s ON r.source_id=s.id LEFT JOIN batches b ON b.id=r.batch_id WHERE r.status=?
          ORDER BY CASE r.kind WHEN 'match' THEN 0 WHEN 'file' THEN 1 ELSE 2 END,r.id LIMIT ? OFFSET ?"""
    numbers=number_map(conn)
    people={}
    def candidate(uid):
        if uid not in people:
            i=need_issue(conn,uid)
            c=decode(i["customer"])
            people[uid]={"id":uid,"number":numbers.get((uid,i["base"]),i["base"]),
                         "station":station_label(c["station"],c.get("machine","")),"problem":c["problem"],"date":c["date"],"author":c["author"]}
        return people[uid]
    reviews=[]
    for r in conn.execute(sql,(status,limit,(page-1)*limit)):
        src=decode(r["fields"],{})
        data=decode(r["data"],{})
        matches=[candidate(i) for i in decode(r["candidates"],[])]
        reviews.append(dict(id=r["id"],kind=r["kind"],reason=r["reason"],source_no=src.get("no",data.get("no","")),
                            station=src.get("station",""),problem=src.get("problem",data.get("file","")),
                            candidates=matches,data=data,file=r["file"] or data.get("file",""),row=r["row_no"],
                            version=r["version"],date=src.get("date",""),author=src.get("author",""),
                            source_kind=(conn.execute("SELECT kind FROM batches WHERE id=?",(r["batch_id"],)).fetchone()[0] if r["batch_id"] else "")))
    return reviews,total


def report(conn,params,summary=None):
    from .records import tasks
    o=summary if summary is not None else overview(conn,params)
    t=o["totals"]
    delta=t["delta"]
    net="缺上周基线" if delta is None else f"较上周末{'净增加' if delta>0 else '净减少'} {abs(delta)} 条"
    lines=[f"ZF Saarbrücken · EOL 测试线周报  {o['week'].replace('-W','-CW')}",
           f"{o['start']} 至 {o['end']}，统计截至 {o['as_of']}","",
           f"我方未结 {t['ours']} 条，客户未结 {t['customer']} 条，{net}。",
           f"本周新增 {t['new']} 条，重开或责任转回 {t['reopened']} 条，客户关闭 {t['closed']} 条，转纯 ZF {t['transferred']} 条。","",
           "区域汇总   未结 / High / Medium / Low"]
    for a in o["areas"]:
        lines.append(f"{a['area']}   {a['total']} / {a['High']} / {a['Medium']} / {a['Low']}")
    lines.extend(["","本周状态更新"])
    indexed={r["id"]:r for r in rows_at(conn,o["as_of"])}
    for e in o["events"]:
        r=indexed[e["issue_id"]]
        lines.extend([f"{e['day']}  #{e['number']}  {e['station']}  {STATES.get(e['from_state'],'新增')} → {STATES[e['to_state']]}",
                      r["zh"] or "中文待译",r["problem"],f"{e['actor']} · {e['note']}"])
    if not o["events"]: lines.append("本周尚无状态变更。")
    lines.extend(["","本周计划"])
    for r in o["plans"]:
        lines.extend([f"#{r['number']}  {r['station']}  {r['plan']}  {r['plan_owner']}  {r['plan_note']}  · {r['state_label']}",
                      r["zh"] or "中文待译",r["problem"]])
    if not o["plans"]: lines.append("本周尚无已登记计划。")
    lines.extend(["","专项任务"])
    items=tasks(conn,o["week"])
    for task in items:
        lines.append(f"{task['title']}  {task['owner']}  {task['due']}  {task['status']}")
    if not items: lines.append("本周尚无专项任务。")
    return "\n".join(lines)
