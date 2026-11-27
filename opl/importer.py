"""预览并事务性合并客户版本和豪森补充资料。 / Preview and transactionally merge customer releases and Haosen supplements."""

# difflib 比较已定位候选的原文；hashlib 指纹和原始行快照用于追溯。 / difflib compares candidate text; hashes and source rows preserve provenance.
from __future__ import annotations

import re
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from unicodedata import normalize

from .excel import StationMap, bilingual, classify_responsible, priority_band, read_book, split_actions
from .lines import EOL, STATOR
from .stator import StatorStations
from .people import CUSTOMER_NAMES, customer_author, internal_owner
from .store import (assign_number, audit, decode, dump, file_hash, iso_day, json_hash, need_issue,
                    now, number_map, today, writing)
from .workflow import ARCHIVE, append_event, assign_owner, issue_owner


def norm(value):
    return " ".join(normalize("NFKC", str(value or "")).casefold().split())


def signature(row):
    return (norm(row.get("station_raw",row["station"])), norm(row["date"]), norm(row["author"]),
            norm(bilingual(row["problem"])[0]).strip('"“” '))


def binding_key(row):
    return json_hash([row["no"], *signature(row)])


def observed_version(path):
    match = re.search(r"(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)",Path(path).name)
    if match:
        return iso_day("-".join(match.groups()),"文件版本日期")
    european = re.search(r"(?<!\d)(\d{2})[-_.](\d{2})[-_.](20\d{2})(?!\d)", Path(path).name)
    if european:
        return iso_day("-".join(reversed(european.groups())), "文件版本日期")
    return ""


def source_file(root,path,line=EOL):
    digest = file_hash(path)
    dest = line.data(root)/"sources"/(digest + path.suffix.lower())
    dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():
        temp = dest.with_name(uuid.uuid4().hex + ".tmp")
        try:
            shutil.copyfile(path,temp)
            if file_hash(temp)!=digest:
                raise ValueError("工作簿正在被更新，请待保存完成后重新导入。")
            temp.replace(dest)
        finally:
            temp.unlink(missing_ok=True)
    return digest, dest.relative_to(root).as_posix()


def station_fields(src, stations):
    mapped = stations.resolve(src["station"])
    row = {k:v for k,v in src.items() if k != "raw"}
    row.update(mapped)
    row["station_raw"] = src["station"]
    row["priority_raw"] = src["priority"]
    row["priority"] = priority_band(src["priority"])
    row["resp_class"] = classify_responsible(src["responsible"])
    original,zh = bilingual(src["problem"])
    row.update(problem=original or src["problem"], embedded_zh=zh)
    return row


class MatchIndex:
    def __init__(self, conn, source, incoming):
        self.by_sig, self.by_meta = defaultdict(set),defaultdict(set)
        self.by_num, self.by_text = defaultdict(set),defaultdict(set)
        self.customers = {}
        self.bindings = dict(conn.execute("SELECT identity,issue_id FROM bindings WHERE kind=?",(source,)))
        self.batch_sigs = Counter(signature(r) for r in incoming)
        self.batch_keys = Counter(binding_key(r) for r in incoming)
        for i in conn.execute("SELECT id,customer FROM issues"):
            row = decode(i["customer"])
            key = signature(row)
            uid = i["id"]
            self.customers[uid] = row
            self.by_sig[key].add(uid)
            self.by_meta[key[:3]].add(uid)
            if key[3]:
                self.by_text[key[3]].add(uid)
        for base,uid in conn.execute("SELECT base,issue_id FROM numbers"):
            self.by_num[base].add(uid)

    def match(self, row, within_number=False):
        sig = signature(row)
        key = binding_key(row)
        if key in self.bindings and self.batch_keys[key] == 1:
            return self.bindings[key], [], "已确认来源绑定"
        number = self.by_num[row["no"]]
        exact = self.by_sig[sig]
        if within_number:
            exact = exact & number
            if len(exact) == 1 and self.batch_keys[key] == 1 and all(sig):
                return next(iter(exact)), [], "客户编号及完整来源特征相符"
        if all(sig) and len(exact)==1 and self.batch_sigs[sig]==1:
            return next(iter(exact)), [], "工位、日期、作者和原文唯一"
        text = self.by_text[sig[3]] if sig[3] else set()
        if within_number:
            text = text & number
        meta = self.by_meta[sig[:3]]
        corroborated = []
        for uid in text:
            other = signature(self.customers[uid])
            matches = [bool(a and a==b) for a,b in zip(sig[:3],other[:3])]
            if sum(matches)>=2 and (matches[0] or uid in number):
                corroborated.append(uid)
        if len(corroborated)==1 and self.batch_sigs[sig]==1:
            return corroborated[0], [], "原文与辅助字段唯一"
        anchors = number & meta
        if len(anchors)==1 and all(sig[:3]) and self.batch_sigs[sig]==1 and sig[3]:
            uid = next(iter(anchors))
            previous = signature(self.customers[uid])[3]
            if previous and SequenceMatcher(None,previous,sig[3],autojunk=False).ratio() >= 0.72:
                return uid, [], "编号、工位、日期、作者及修订文本相符"
        candidates = sorted(number | text | meta | exact,
                            key=lambda i:(i not in exact,i not in anchors,i not in text,i not in number,i))
        return None, candidates[:30], "请核对客户编号、工位和原文"


def target_state(row, current=None):
    if norm(row["status"])=="erledigt":
        return "closed"
    if norm(row["status"]) not in ("offen","in arbeit") or row["resp_class"]=="unknown":
        return current or "triage"
    if row["resp_class"]=="zf":
        return "zf_only"
    return "triage" if current in (*ARCHIVE,None) else current


def customer_transition(row, old, issue, version):
    """客户明确重开时才撤销现场确认。 / Require new reopening evidence before superseding a field-confirmed closure."""
    target = target_state(row, issue["state"])
    if issue["reason"] != "manual_closed" or target == "closed":
        return target
    confirmed = issue["state_day"]
    if version < confirmed:
        return "closed"
    # 客户仍沿用旧开放值时保留现场凭据；承认之后的状态变更或明确更新日。
    # A carried-forward open row does not undo confirmation; a subsequent customer status change can.
    status, previous = norm(row["status"]), norm(old["status"])
    changed = row.get("changed_on", "")
    if status not in ("offen", "in arbeit"):
        return "closed"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", changed or "") and changed <= version and changed != old.get("changed_on"):
        return target if changed > confirmed else "closed"
    explicit_change = status != previous and (previous == "erledigt" or version > confirmed)
    return target if explicit_change else "closed"


def statistics():
    return dict(rows=0,new=0,matched=0,changed=0,closed=0,transferred=0,reopened=0,rework=0,missing=0,
                review=0,unchanged=0,translation=0)


def customer_decision(row, old, issue, version, previous_version):
    """比较客户原始快照，为状态变更保留行动依据。 / Compare customer snapshots and retain the action supporting rework."""
    target = customer_transition(row, old, issue, version)
    if (target != issue["state"] or target not in ("working", "ready", "reported")
            or norm(row["status"]) not in ("offen", "in arbeit") or row["resp_class"] not in ("haosen", "joint")
            or row["action"] == old["action"]):
        return target, None

    previous = split_actions(old["action"])
    known_text = {norm(entry["original"] or entry["zh"]) for entry in previous}
    previous_day = max((entry["date"] for entry in previous
                        if entry["date"] and (entry["original"] or entry["zh"])), default="")
    cutoff = previous_day or previous_version
    # 日期属于段首，正文里提到的计划日期不会变成更新日。 / Only a dated action heading establishes an update.
    candidates = (entry for entry in split_actions(row["action"])
                  if entry["date"] > cutoff and (entry["original"] or entry["zh"])
                  and norm(entry["original"] or entry["zh"]) not in known_text)
    latest = max(candidates, key=lambda entry: entry["date"], default=None)
    if latest is None:
        return target, None
    return "rework", {"action_date": latest["date"], "action_text": latest["original"] or latest["zh"],
                      "comparison_date": cutoff, "basis": "上版最新行动日期" if previous_day else "上版清单日期"}


def inspect_import(conn, root, path, filename, version="", revision_no=0, line=EOL):
    """预演匹配和字段差异，遇到歧义时生成待核决策。 / Preview matches and differences, marking ambiguous decisions for review."""
    kind,sheet,rows = read_book(path,line)
    if not rows:
        raise ValueError("工作表中没有可识别的问题行。")
    version = version or observed_version(filename)
    if not version and kind=="zf":
        raise ValueError("客户文件名未包含版本日期，请填写客户版本日期。")
    version = iso_day(version or today(),"版本日期")
    revision_no = int(revision_no or 0)
    if revision_no < 0:
        raise ValueError("同日修订号应为非负整数。")
    digest = file_hash(path)
    previous = conn.execute("SELECT * FROM batches WHERE kind=? AND digest=?",(kind,digest)).fetchone()
    info = dict(file=filename,kind=kind,sheet=sheet,version=version,revision_no=revision_no,digest=digest)
    if previous:
        return {**info,"duplicate":True,"version":previous["version"],"summary":{**statistics(),"rows":len(rows)}},[],[]
    last = conn.execute("SELECT version,revision_no FROM batches WHERE kind='zf' AND applied=1 ORDER BY version DESC,revision_no DESC LIMIT 1").fetchone()
    info["previous_version"] = last["version"] if last else ""
    historical = bool(kind=="zf" and last and (version,revision_no)<tuple(last))
    if kind=="zf" and conn.execute("SELECT 1 FROM batches WHERE kind='zf' AND version=? AND revision_no=?",(version,revision_no)).fetchone():
        raise ValueError("同日修订号已经存在，请为不同内容选择新的修订号。")
    if kind!="zf" and not conn.execute("SELECT 1 FROM issues LIMIT 1").fetchone():
        raise ValueError("请先导入客户清单建立问题总账，再补充豪森资料。")
    stations = StatorStations() if line == STATOR else StationMap(root)
    index = MatchIndex(conn,kind,rows)
    summary = statistics()
    summary["rows"] = len(rows)
    baseline = kind=="zf" and not last
    decisions = []
    for row in rows:
        uid,candidates,why = (None,[],"初始客户基线") if baseline else index.match(row, within_number=(line == STATOR and kind != "zf"))
        if not row["no"].isdigit() and not (uid and index.bindings.get(binding_key(row)) == uid):
            uid, why = None, "缺少有效客户编号，请核对来源行"
        mode = "match" if uid else "review"
        if not uid and kind=="zf" and not historical and row["no"].isdigit() and (baseline or not candidates):
            mode = "new"
        if mode == "review" and line == STATOR and kind != "zf" and row["no"].isdigit():
            # 个别豪森行的描述串到了相邻问题，独立核实其 Action。 / Some descriptions shifted; anchor only the action to the numbered customer row.
            anchors = index.by_num[row["no"]] & index.by_meta[signature(row)[:3]]
            if len(anchors) == 1:
                target = next(iter(anchors))
                prior_action = index.customers[target]["action"].strip()
                if prior_action and prior_action in row["action"]:
                    mode, uid, candidates = "feedback_only", target, [target]
                    why = "编号、工位及客户行动相符，问题描述不一致；已单独核对行动，其余字段待确认"
        if historical:
            mode = "archive"
        decisions.append(dict(mode=mode,id=uid,candidates=candidates,reason=why,src=row))
    # 多行命中同一问题时送人工复核。 / An ambiguous many-to-one match must never silently attach the second row to the first.
    hits = Counter(d["id"] for d in decisions if d["id"])
    found = set()
    for item in decisions:
        uid,src = item["id"],item["src"]
        if uid and hits[uid]>1:
            item.update(mode="archive" if historical else "review",id=None,candidates=[uid],reason="同版本多行指向同一问题")
        mode = item["mode"]
        if mode=="match":
            found.add(uid)
            summary["matched"]+=1
        if mode in ("review","feedback_only"):
            summary["review"]+=1
        if mode=="new":
            summary["new"]+=1
        if kind=="zf" and mode in ("match","new"):
            row = station_fields(src,stations)
            if uid and not row["no"].isdigit():
                row["no"] = index.customers[uid]["no"]
            item["customer"] = row
            if not row.get("embedded_zh") and (not uid or norm(row["problem"])!=norm(index.customers[uid]["problem"])):
                summary["translation"]+=1
            if uid:
                old = index.customers[uid]
                fields = [k for k in row if k!="row" and row[k]!=old.get(k)]
                item["changes"] = fields
                summary["changed" if fields else "unchanged"]+=1
                issue = need_issue(conn, uid)
                before = issue["state"]
                after, evidence = customer_decision(row, old, issue, version, info["previous_version"])
                item["decision"] = (after, evidence)
                if after != before:
                    if after=="closed": summary["closed"]+=1
                    elif after=="zf_only": summary["transferred"]+=1
                    elif after=="rework":
                        summary["rework"]+=1
                        item["reason"] += f"；客户行动更新于 {evidence['action_date']}，转需返工"
                    elif before in ARCHIVE: summary["reopened"]+=1
    missing = []
    if kind=="zf" and not baseline and not historical:
        uncertain = {uid for d in decisions if d["mode"]=="review" for uid in d["candidates"]}
        missing = sorted(set(index.customers)-found-uncertain)
        summary["missing"] = len(missing)
    info.update(summary=summary,baseline=baseline,historical=historical,duplicate=False)
    return info,decisions,missing


def add_review(conn,batch,sid,kind,reason,data,candidates):
    conn.execute("INSERT INTO reviews(batch_id,source_id,kind,reason,data,candidates) VALUES(?,?,?,?,?,?)",
                 (batch,sid,kind,reason,dump(data),dump(candidates)))


def action_key(text,occurrence,date_raw="",author=""):
    return json_hash([norm(text),norm(date_raw),norm(author),occurrence])


def sync_actions(conn,uid,src,batch,kind,changed=True):
    entries = split_actions(src["action"])
    if kind=="zf" and changed:
        conn.execute("UPDATE actions SET active=0 WHERE issue_id=?",(uid,))
    counts = Counter()
    for pos,item in enumerate(entries):
        original = item["original"]
        if not original and not item["zh"]:
            continue
        key = (norm(original or item["zh"]),norm(item["date_raw"] or item["date"]),norm(item["author"]))
        identity = action_key(key[0],counts[key],key[1],key[2])
        counts[key]+=1
        prev = conn.execute("SELECT * FROM actions WHERE issue_id=? AND identity=?",(uid,identity)).fetchone()
        if not prev and kind!="zf" and original and item["zh"]:
            candidates = conn.execute("""SELECT * FROM actions WHERE issue_id=? AND active=1
                 AND original=? AND (date_raw=? OR (day<>'' AND day=?))""",
                 (uid,original,item["date_raw"],item["date"])).fetchall()
            if len(candidates)==1:
                prev=candidates[0]
        if prev:
            if kind=="zf" and changed:
                conn.execute("UPDATE actions SET active=1,position=?,batch_id=? WHERE id=?",(pos,batch,prev["id"]))
            if not prev["zh"] and item["zh"]:
                conn.execute("UPDATE actions SET zh=? WHERE id=?",(item["zh"],prev["id"]))
        elif kind=="zf":
            conn.execute("""INSERT INTO actions(issue_id,identity,original,zh,day,date_raw,author,position,active,batch_id)
                          VALUES(?,?,?,?,?,?,?,?,1,?)""",(uid,identity,original,item["zh"],item["date"],
                          item["date_raw"],item["author"],pos,batch))


def apply_customer(conn, root, batch, src, info, uid=None, customer=None, line=EOL, decision=None):
    """复用内部 ID，仅在生效的客户事实要求时触发自动流转。 / Retain the issue ID and transition only when effective customer facts require it."""
    row = customer or station_fields(src, StatorStations() if line == STATOR else StationMap(root))
    base = row["no"]
    if not base.isdigit():
        raise ValueError("客户新增行需要有效的客户编号。")
    row["no"] = base
    day = info["version"]
    if uid is None:
        state = target_state(row)
        reason = "baseline" if info.get("baseline") else "new"
        uid = conn.execute("""INSERT INTO issues(base,customer,state,state_day,reason,created_day,last_batch)
                            VALUES(?,?,?,?,?,?,?)""",(base,dump(row),state,day,reason,day,batch)).lastrowid
        assign_number(conn,uid,base)
        append_event(conn,uid,"",state,day,"客户版本","初始客户基线" if reason=="baseline" else "新出现的客户问题",
                     reason,{"observed":True},batch)
        sync_actions(conn,uid,src,batch,"zf")
        return uid
    existing = need_issue(conn,uid)
    old = decode(existing["customer"])
    assign_number(conn,uid,base)
    changes = {k:{"old":old.get(k),"new":v} for k,v in row.items() if k!="row" and old.get(k)!=v}
    if changes:
        local = decode(existing["local"])
        if "problem" in changes and norm(old["problem"])!=norm(row["problem"]):
            audit(conn,uid,day,"客户版本","outdated_translation",
                  {"original":old["problem"],"zh":local.get("zh") or old.get("embedded_zh",""),"en":local.get("en","")})
            for k in ("zh","en"):
                local.pop(k,None)
            local["manual_fields"] = [k for k in local.get("manual_fields",[]) if k not in ("zh","en")]
        if "action" in changes:
            manual=conn.execute("SELECT 1 FROM actions WHERE issue_id=? AND active=1 AND manual=1",(uid,)).fetchone()
            if manual:
                add_review(conn,batch,None,"actions","客户行动已修订，当前有人工确认的分段",
                           {"action":src["action"]},[uid])
            else:
                sync_actions(conn,uid,src,batch,"zf")
        audit(conn,uid,day,"客户版本","customer_diff",{"batch":batch,"changes":changes})
        conn.execute("UPDATE issues SET base=?,customer=?,local=?,last_batch=?,revision=revision+1 WHERE id=?",
                     (base,dump(row),dump(local),batch,uid))
    before = existing["state"]
    after, evidence = decision or customer_decision(row, old, existing, day, info["previous_version"])
    if evidence:
        append_event(conn,uid,before,after,max(existing["state_day"],day),"客户版本",
                     f"客户新增 {evidence['action_date']} 的行动，自动转入需返工。","customer_rework",
                     {"version":day, **evidence},batch)
        return uid
    if after!=before:
        kind = {"closed":"customer_closed","zf_only":"transferred"}.get(after,"reopened" if before=="closed" else "returned")
        source_day = row.get("changed_on","")
        reliable = (after=="closed" and old.get("status")!=row["status"] and
                    re.fullmatch(r"\d{4}-\d\d-\d\d",source_day or "") and
                    old.get("changed_on")!=source_day and existing["state_day"]<=source_day<=day)
        prior_version = conn.execute("SELECT version FROM batches WHERE id=?",(existing["last_batch"],)).fetchone()
        reliable = reliable and source_day >= prior_version["version"]
        effective = max(existing["state_day"], source_day if reliable else day)
        append_event(conn,uid,before,after,effective,"客户版本",
                     {"customer_closed":"客户表已关闭","transferred":"责任转为纯 ZF","reopened":"客户重新开放",
                      "returned":"责任转回我方"}[kind],kind,
                     {"version":day,"observed":not bool(reliable),"source_date":source_day},batch)
    return uid


def suggest(conn, batch, sid, uid, kind, reason, data):
    """相同的待核建议只提示一次。 / Keep one review for the same issue and evidence."""
    existing = conn.execute("SELECT 1 FROM reviews WHERE kind=? AND candidates=? AND data=?",
                            (kind, dump([uid]), dump(data))).fetchone()
    if not existing:
        add_review(conn,batch,sid,kind,reason,data,[uid])


def save_stator_feedback(conn, uid, day, text, sid):
    """用业务日期与正文去重，来源行可追溯。 / Deduplicate by date and note while retaining source provenance."""
    existing = conn.execute("SELECT note FROM feedback WHERE issue_id=? AND day=? AND source LIKE 'haosen_action:%'",
                            (uid,day))
    if any(norm(row[0]) == norm(text) for row in existing):
        return
    conn.execute("INSERT INTO feedback(issue_id,day,actor,note,source,recorded) VALUES(?,?,?,?,?,?)",
                 (uid,day,"豪森清单",text,f"haosen_action:{sid}",now()))
    conn.execute("UPDATE issues SET revision=revision+1 WHERE id=?", (uid,))


def stator_actions(conn, uid, src, customer, batch, sid):
    """逐段区分共同行动、译文与豪森新增反馈。 / Compare source blocks before assigning feedback or translations."""
    local = src["action"].strip()
    if not local or norm(local) == norm(customer["action"]):
        return
    # 较旧的豪森版可能引用已从最新客户表移除的行动。 / Older supplements may quote retired customer actions.
    historical = conn.execute("""SELECT s.fields FROM source_rows s JOIN batches b ON b.id=s.batch_id
                                 WHERE s.issue_id=? AND b.kind='zf'""", (uid,))
    sources = {decode(row[0]).get("action","").strip() for row in historical}
    sources.add(customer["action"].strip())
    # 按整行消去连续的客户原文，客户行动也可能没有日期。 / Remove complete quoted lines, including undated customer text.
    remaining = local.splitlines()
    for source in sorted(filter(None,sources),key=len,reverse=True):
        quoted = [norm(s) for s in source.splitlines()]
        for pos in range(len(remaining)-len(quoted),-1,-1):
            if [norm(s) for s in remaining[pos:pos+len(quoted)]] == quoted:
                del remaining[pos:pos+len(quoted)]
    records = conn.execute("SELECT id,original,day,zh FROM actions WHERE issue_id=?", (uid,)).fetchall()
    for entry in split_actions("\n".join(remaining)):
        original, zh, day = entry["original"], entry["zh"], entry["date"]
        matches = [a for a in records if original and norm(a["original"]) == norm(original) and a["day"] == day]
        if len(matches) == 1:
            if zh and not matches[0]["zh"]:
                conn.execute("UPDATE actions SET zh=? WHERE id=?", (zh,matches[0]["id"]))
            continue
        note = "\n".join(filter(None,(original,zh))).strip(" ：:")
        if not note:
            continue
        # 同日纯中文可能是译文；缺日期或仅外文的差异也留给现场。 / Same-day Chinese may be a translation; review ambiguous additions.
        if day and day <= today() and zh and not original and not any(a["day"] == day for a in records):
            save_stator_feedback(conn,uid,day,note,sid)
        else:
            suggest(conn,batch,sid,uid,"field_action","请核对豪森 Action 的来源及日期",
                    {"text":entry["raw"], "day":day, "note":note})


def enrich(conn, uid, src, batch, sid, kind, day, line=EOL):
    """补充译文和准备信息，保留人工值并标记冲突。 / Fill translations and preparation fields while flagging conflicts with existing values."""
    issue = need_issue(conn,uid)
    local = decode(issue["local"])
    customer = decode(issue["customer"])
    original,zh = bilingual(src["problem"])
    proposed = {k:src.get(k,"") for k in ("owner","people","work_time","order_no","hint","note")}
    proposed["owner"] = internal_owner(proposed["owner"])
    if line == STATOR:
        names = re.split(r"[/、,，;；]+", proposed["owner"])
        contacts = [n for n in names if customer_author(n) in CUSTOMER_NAMES.values()]
        if contacts:
            proposed["owner"] = "/".join(n.strip() for n in names if n and n not in contacts)
            suggest(conn,batch,sid,uid,"contact","负责人栏中含客户联系人",{"text":" / ".join(contacts)})
        if src["due"] and src["due"] != customer["due"]:
            proposed["plan_candidate"] = src["due"]
        stator_actions(conn,uid,src,customer,batch,sid)
    if zh and norm(original)==norm(customer["problem"]):
        proposed["zh"] = zh
    changes = {}
    conflicts = {}
    for key,value in proposed.items():
        current = issue_owner(local) if key == "owner" else local.get(key,"")
        if not value or value==current:
            continue
        if current or key in local.get("manual_fields",[]):
            conflicts[key] = {"current":current,"incoming":value}
        else:
            local[key]=value
            changes[key]=value
    if conflicts:
        add_review(conn,batch,sid,"fields","豪森补充字段与已存内容不同",{"fields":conflicts},[uid])
    if src.get("note"):
        if not conn.execute("SELECT 1 FROM feedback WHERE issue_id=? AND note=? AND source LIKE 'haosen%'",
                            (uid,src["note"])).fetchone():
            conn.execute("INSERT INTO feedback(issue_id,day,actor,note,source,recorded) VALUES(?,?,?,?,?,?)",
                         (uid,day,"豪森清单",src["note"],kind,now()))
    if line != STATOR:
        sync_actions(conn,uid,src,batch,kind)
    if changes:
        conn.execute("UPDATE issues SET local=?,revision=revision+1 WHERE id=?",(dump(local),uid))
        audit(conn,uid,day,"豪森清单","supplement",{"batch":batch,"added":changes})


def import_book(conn,root,path,filename=None,version="",revision_no=0,line=EOL):
    """归档原件并在一个写事务内提交整批行。 / Archive the original and commit a whole batch in one transaction."""
    filename = filename or path.name
    initial_digest = file_hash(path)
    with writing(conn):
        info,decisions,missing = inspect_import(conn,root,path,filename,version,revision_no,line)
        if info["digest"] != initial_digest:
            raise ValueError("工作簿在读取过程中发生了变化，请保存完毕后重新导入。")
        if info["duplicate"]:
            return info
        digest,relative = source_file(root,path,line)
        if digest != initial_digest or file_hash(root/relative) != digest:
            raise ValueError("工作簿在归档过程中发生了变化，请保存完毕后重新导入。")
        kind,day = info["kind"],info["version"]
        batch = conn.execute("""INSERT INTO batches(file,kind,version,revision_no,digest,path,sheet,applied,summary,recorded)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""",(filename,kind,day,info["revision_no"],digest,relative,
                              info["sheet"],int(not info["historical"]),dump(info["summary"]),now())).lastrowid
        for item in decisions:
            mode,uid,src = item["mode"],item["id"],item["src"]
            sid = conn.execute("INSERT INTO source_rows(batch_id,row_no,issue_id,fields) VALUES(?,?,?,?)",
                               (batch,src["row"],uid,dump(src))).lastrowid
            if kind=="zf" and mode in ("match","new"):
                uid = apply_customer(conn,root,batch,src,info,uid,item.get("customer"),line,item.get("decision"))
                if norm(src["status"]) not in ("offen","in arbeit","erledigt") or classify_responsible(src["responsible"])=="unknown":
                    add_review(conn,batch,sid,"customer_value","客户状态或责任方待核对",{},[uid])
            elif kind!="zf" and mode=="match":
                enrich(conn,uid,src,batch,sid,kind,day,line)
            elif mode=="feedback_only":
                stator_actions(conn,uid,src,decode(need_issue(conn,uid)["customer"]),batch,sid)
                add_review(conn,batch,sid,"match",item["reason"],{"action_issue":uid},[uid])
            elif mode=="review":
                add_review(conn,batch,sid,"match",item["reason"],{},item["candidates"])
            if uid:
                conn.execute("UPDATE source_rows SET issue_id=? WHERE id=?",(uid,sid))
        for uid in missing:
            add_review(conn,batch,None,"missing","新版未见此行，保留上次有效状态",{},[uid])
        info["id"] = batch
        return info


def resolve_review(conn,root,rid,data,line=EOL):
    r = conn.execute("""SELECT r.*,b.kind AS source_kind,b.version,b.applied,b.revision_no,s.fields AS src
                      FROM reviews r LEFT JOIN batches b ON b.id=r.batch_id
                      LEFT JOIN source_rows s ON s.id=r.source_id WHERE r.id=?""",(rid,)).fetchone()
    if not r or r["status"]!="open":
        raise ValueError("该待核项已处理或不存在。")
    actor = str(data.get("actor") or "").strip()
    if not actor:
        raise ValueError("请填写记录人。")
    decision = data.get("decision","")
    ids = decode(r["candidates"],[])
    uid = int(data.get("issue_id") or (ids[0] if ids else 0)) or None
    if uid:
        need_issue(conn,uid)
    extra = decode(r["data"],{})
    if decision=="dismiss":
        pass
    elif r["kind"]=="actions" and decision in ("bind","accept") and uid in ids:
        issue=need_issue(conn,uid)
        if decode(issue["customer"]).get("action")!=extra["action"]:
            raise ValueError("客户已再次更新行动，请核对最新版本。")
        previous=[dict(a) for a in conn.execute("SELECT * FROM actions WHERE issue_id=? AND active=1",(uid,))]
        sync_actions(conn,uid,{"action":extra["action"]},r["batch_id"],"zf")
        conn.execute("UPDATE issues SET revision=revision+1 WHERE id=?",(uid,))
        audit(conn,uid,today(),actor,"accepted_customer_actions",{"previous":previous,"batch":r["batch_id"]})
    elif r["kind"]=="field_action" and decision=="translation" and uid in ids:
        aid = int(data.get("action_id") or 0)
        action = conn.execute("SELECT id,zh FROM actions WHERE id=? AND issue_id=? AND active=1",
                              (aid,uid)).fetchone()
        if not action:
            raise ValueError("请选择当前问题的一条有效客户行动。")
        zh = str(data.get("translation") or "").strip()
        if not zh or (action["zh"] and action["zh"] != zh):
            raise ValueError("请填写译文。已有不同译文时请在问题详情核对。")
        conn.execute("UPDATE actions SET zh=?,manual=1 WHERE id=?", (zh,aid))
        conn.execute("UPDATE issues SET revision=revision+1 WHERE id=?", (uid,))
        audit(conn,uid,today(),actor,"accepted_action_translation",{"action_id":aid,"source_row":r["source_id"],"zh":zh})
    elif r["kind"]=="field_action" and decision=="accept" and uid in ids:
        event_day = iso_day(data.get("day") or extra.get("day"), "现场反馈日期")
        if event_day > today():
            raise ValueError("反馈日期不能晚于今天。")
        save_stator_feedback(conn,uid,event_day,extra.get("note",extra["text"]),r["source_id"])
    elif r["kind"]=="fields" and decision in ("bind","accept"):
        if uid not in ids:
            raise ValueError("字段冲突须在原问题上处理。")
        issue = need_issue(conn,uid)
        local = decode(issue["local"])
        old = dict(local)
        for k,change in extra["fields"].items():
            if k not in data.get("fields", list(extra["fields"])):
                continue
            current = issue_owner(local) if k == "owner" else local.get(k,"")
            if current!=change["current"]:
                raise ValueError("该字段已再次修改，请在问题详情核对最新值。")
            if k == "owner":
                assign_owner(local, change["incoming"])
            else:
                local[k]=change["incoming"]
        if old != local:
            conn.execute("UPDATE issues SET local=?,revision=revision+1 WHERE id=?",(dump(local),uid))
            audit(conn,uid,today(),actor,"profile",{"old":old,"new":local})
    elif r["kind"]=="match" and decision in ("bind","new"):
        src = decode(r["src"])
        identity = binding_key(src)
        kind = r["source_kind"]
        if decision=="new" and kind!="zf":
            raise ValueError("新增正式问题需要客户来源。")
        if extra.get("action_issue") and uid != extra["action_issue"]:
            raise ValueError("已核实行动的归属，请在同一问题上确认其余字段。")
        if decision=="bind" and not uid:
            raise ValueError("请选择客户问题。")
        if not src["no"].isdigit() and kind == "zf":
            if decision == "new":
                src["no"] = str(data.get("number", "")).strip()
                if not src["no"].isdigit():
                    raise ValueError("请输入核实后的客户编号。")
            elif uid:
                src["no"] = need_issue(conn,uid)["base"]
        if decision=="new":
            uid = None
        if uid and conn.execute("SELECT 1 FROM source_rows WHERE batch_id=? AND issue_id=? AND id<>?",
                                (r["batch_id"],uid,r["source_id"])).fetchone():
            raise ValueError("本版本另一行已绑定该问题，请核对。")
        info = {"version":r["version"],"baseline":False}
        if kind=="zf":
            latest = conn.execute("SELECT id FROM batches WHERE kind='zf' AND applied=1 ORDER BY version DESC,revision_no DESC LIMIT 1").fetchone()
            if latest["id"]==r["batch_id"]:
                previous = conn.execute("SELECT version FROM batches WHERE kind='zf' AND applied=1 AND id<? "
                                        "ORDER BY version DESC,revision_no DESC LIMIT 1",(r["batch_id"],)).fetchone()
                info["previous_version"] = previous["version"] if previous else ""
                uid = apply_customer(conn,root,r["batch_id"],src,info,uid,line=line)
            elif not uid:
                raise ValueError("历史客户行请绑定已有问题，新增请在生效版处理。")
        else:
            enrich(conn,uid,src,r["batch_id"],r["source_id"],kind,r["version"],line)
        conn.execute("UPDATE source_rows SET issue_id=? WHERE id=?",(uid,r["source_id"]))
        conn.execute("INSERT INTO bindings VALUES(?,?,?) ON CONFLICT(kind,identity) DO UPDATE SET issue_id=excluded.issue_id",
                     (kind,identity,uid))
    elif r["kind"]=="file" and decision=="bind" and uid:
        from .records import link_file
        link_file(conn,uid,extra["file_id"])
    else:
        raise ValueError("请选择可用的处理方式。")
    resolution = dict(decision=decision,issue_id=uid,actor=actor,day=today(),note=str(data.get("note","")))
    conn.execute("UPDATE reviews SET status='resolved',resolution=? WHERE id=?",(dump(resolution),rid))
    audit(conn,uid,today(),actor,"review",{"id":rid,**resolution})
