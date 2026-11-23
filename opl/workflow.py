"""校验现场流转条件并保存有生效日的状态和实施计划。 / Validate meeting-driven transitions and store dated states and work plans."""

# store 将状态、事件和修订号放在同一 SQLite 事务中。 / The store layer keeps state, events and revisions in one SQLite transaction.
from .people import internal_owner
from .store import (audit, decode, dump, edited_issue, iso_day, now, required, today,
                    week_bounds, week_of)

STATES = {
    "triage": "待梳理", "clarify_zf": "待 ZF 澄清问题", "approve_zf": "待 ZF 确认方案",
    "confirm_internal": "待内部确认", "waiting": "等待实施条件", "planned": "已列入计划",
    "working": "整改中", "ready": "完成待上报", "reported": "已上报待核查",
    "rework": "需返工", "negotiate": "无法整改需谈判", "closed": "客户已关闭", "zf_only": "转纯 ZF",
}
CLARIFY = ("clarify_zf", "approve_zf", "confirm_internal")
ARCHIVE = ("closed", "zf_only")
ROUTES = {
    "triage": ("planned", "working", "ready", *CLARIFY, "waiting"),
    "planned": ("working", "ready", *CLARIFY, "waiting"),
    "working": ("ready", *CLARIFY, "waiting"), "ready": ("reported", "working"),
    "reported": ("rework",), "rework": ("planned", "working", *CLARIFY, "waiting"),
    "waiting": ("planned", "working", *CLARIFY), "negotiate": ("triage",),
    "closed": (), "zf_only": (),
}
for group in CLARIFY:
    ROUTES[group] = tuple(s for s in ("triage", "planned", "working", "ready", *CLARIFY, "waiting") if s != group)


def allowed(state):
    choices = list(ROUTES[state])
    if state not in (*ARCHIVE, "negotiate"):
        choices.append("negotiate")
    return choices


def state_label(state, reason):
    if state == "triage":
        return {"baseline": "存量待梳理", "new": "新问题点"}.get(reason, STATES[state])
    return STATES[state]


def field(key, label, typ="text", required=True, options=None):
    item = dict(key=key, label=label, type=typ, required=required)
    if options:
        item["options"] = options
    return item


def issue_owner(local):
    if "owner" in local.get("manual_fields", ()):
        return internal_owner(local.get("owner", ""))
    return internal_owner(local.get("tracker") or local.get("owner", ""))


def assign_owner(local, value):
    local["owner"] = internal_owner(value)
    local.pop("tracker", None)
    manual = set(local.get("manual_fields", []))
    local["manual_fields"] = sorted((manual - {"tracker"}) | {"owner"})


def record_owner(conn, issue_id, local, value, day, actor):
    if issue_owner(local) == value and "tracker" not in local:
        return
    old = {key: local[key] for key in ("owner", "tracker", "manual_fields") if key in local}
    assign_owner(local, value)
    conn.execute("UPDATE issues SET local=? WHERE id=?", (dump(local), issue_id))
    audit(conn, issue_id, day, actor, "owner_change", {"old": old, "owner": value})


def form_fields(current, target, local):
    if target not in allowed(current):
        raise ValueError("该问题当前不能切换到所选状态。")
    fields = [field("owner", "我方负责人")]
    fields[0]["value"] = issue_owner(local)
    if current in CLARIFY or current == "negotiate":
        fields.append(field("outcome", "答复、谈判结论或改走其他路径的原因", "textarea"))
    if current == "waiting" and target in ("planned", "working"):
        fields.append(field("available_day", "已确认的到货、到场或窗口日期", "date"))
    if target in CLARIFY:
        fields.extend([field("contact", "对接人或部门"), field("note", "需澄清的问题或待确认的方案", "textarea")])
    elif target == "waiting":
        fields.extend([field("resource", "等待事项", "select", options=["待投产","物料","停线／停电窗口","供应商","人力"]),
                       field("note", "阻碍和已知条件", "textarea"), field("order", "已下单物料的单号", required=False),
                       field("expected", "已知到货、窗口或到场日期", "date", False),
                       field("followup", "未定时的下次跟进日", "date", False)])
    elif target == "planned":
        fields.extend([field("plan", "计划日期或 ISO 周", "plan"), field("note", "下一步行动", "textarea")])
    elif target == "working":
        fields.extend([field("executor", "执行人"), field("note", "开工行动", "textarea")])
        if current == "ready":
            fields.append(field("outcome", "上报前需补做的原因"))
    elif target == "ready":
        fields.append(field("note", "整改结果", "textarea"))
    elif target == "reported":
        fields.extend([field("contact", "接收人或反馈凭据"), field("note", "上报说明", "textarea", False)])
    elif target == "rework":
        fields.extend([field("outcome", "退回原因"), field("note", "下一步行动", "textarea")])
    elif target == "negotiate":
        fields.extend([field("obstacle", "无法实施的原因"), field("note", "拟谈事项", "textarea")])
    elif target == "triage":
        fields.append(field("note", "重新评估的下一步行动", "textarea"))
    return fields


def valid_plan(value):
    value = value.strip().upper().replace("CW", "W")
    if "-W" in value:
        start, _ = week_bounds(value)
        return week_of(start)
    return iso_day(value, "计划日期")


def business_day(data):
    day = iso_day(required(data, "day", "实际发生日期"))
    if day > today():
        raise ValueError("实际发生日期不能晚于柏林今天。")
    return day


def append_event(conn, issue_id, before, target, day, actor, note, kind, payload, batch=None):
    conn.execute("""INSERT INTO events(issue_id,from_state,to_state,day,actor,note,kind,payload,batch_id,recorded)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                 (issue_id,before,target,day,actor,note,kind,dump(payload),batch,now()))
    conn.execute("UPDATE issues SET state=?,state_day=?,reason=?,revision=revision+1 WHERE id=?",
                 (target,day,kind,issue_id))


def add_plan(conn, issue_id, day, plan, owner, note, actor):
    conn.execute("INSERT INTO plans(issue_id,day,plan,tracker,note,actor,recorded) VALUES(?,?,?,?,?,?,?)",
                 (issue_id,day,plan,owner,note,actor,now()))


def transition(conn, issue_id, data):
    """逐项验证目标状态的必填内容，再追加会议事件。 / Validate the target form before appending a meeting event."""
    issue = edited_issue(conn, issue_id, data)
    local = decode(issue["local"])
    current, target = issue["state"], data.get("to", "")
    fields = form_fields(current, target, local)
    actor, day = required(data,"actor","记录人"), business_day(data)
    if day < issue["state_day"]:
        raise ValueError("状态日期早于上次状态事件。可补录为历史反馈，或先更正相应事件日期。")
    data = {"owner": data.get("tracker", ""), **data}
    payload = {}
    for spec in fields:
        key = spec["key"]
        value = required(data,key,spec["label"]) if spec["required"] else str(data.get(key) or "").strip()
        if key == "owner":
            value = required({"owner":internal_owner(value)}, "owner", "我方负责人")
        if value and spec["type"] == "date":
            value = iso_day(value,spec["label"])
        if value and spec["type"] == "plan":
            value = valid_plan(value)
        if spec.get("options") and value not in spec["options"]:
            raise ValueError("请选择有效的等待事项。")
        payload[key] = value
    if target == "waiting":
        if not (payload["expected"] or payload["followup"]):
            raise ValueError("请填写已知约期或下次跟进日。")
        if payload["resource"] == "物料" and payload["expected"] and not payload["order"]:
            raise ValueError("已确认到货日期的物料还需填写单号。")
    record_owner(conn, issue_id, local, payload["owner"], day, actor)
    if target == "planned":
        add_plan(conn,issue_id,day,payload["plan"],payload["owner"],payload["note"],actor)
    append_event(conn,issue_id,current,target,day,actor,payload.get("note") or payload.get("contact",""),
                 "meeting",payload)


def reschedule(conn, issue_id, data):
    """按业务日留存改期，补录旧计划时保留后来的指派。 / Record schedule changes without replacing a later assignment."""
    issue = edited_issue(conn,issue_id,data)
    if issue["state"] in ARCHIVE:
        raise ValueError("已归档的问题须恢复跟踪后才能调整计划。")
    day, actor = business_day(data), required(data,"actor","记录人")
    if day < issue["created_day"]:
        raise ValueError("计划记录不能早于建账日期。")
    data = {"owner": data.get("tracker", ""), **data}
    owner, note = required(data,"owner","我方负责人"), required(data,"note","计划行动及改期原因")
    plan = valid_plan(required(data,"plan","计划日期或周"))
    owner = required({"owner":internal_owner(owner)}, "owner", "我方负责人")
    add_plan(conn,issue_id,day,plan,owner,note,actor)
    later_owner = conn.execute("""SELECT 1 FROM audit WHERE issue_id=? AND day>? AND kind IN ('profile','owner_change')
                                  UNION ALL SELECT 1 FROM plans WHERE issue_id=? AND day>? LIMIT 1""",
                               (issue_id,day,issue_id,day)).fetchone()
    if not later_owner and day >= issue["state_day"]:
        record_owner(conn,issue_id,decode(issue["local"]),owner,day,actor)
    conn.execute("UPDATE issues SET revision=revision+1 WHERE id=?", (issue_id,))
    audit(conn,issue_id,day,actor,"plan",{"plan":plan,"owner":owner,"note":note})


def correct_event(conn, issue_id, event_id, data):
    """日期更正须保持相邻事件顺序，并同步关联计划。 / Keep adjacent events ordered and update their linked plans."""
    edited_issue(conn,issue_id,data)
    actor, reason = required(data,"actor","记录人"),required(data,"note","更正原因")
    day = business_day(data)
    rows = conn.execute("SELECT * FROM events WHERE issue_id=? ORDER BY id",(issue_id,)).fetchall()
    for pos,row in enumerate(rows):
        if row["id"] != event_id:
            continue
        if row["kind"] != "meeting":
            raise ValueError("客户版本日期请通过版本记录核对。")
        if (pos and rows[pos-1]["day"] > day) or (pos+1 < len(rows) and rows[pos+1]["day"] < day):
            raise ValueError("更正后日期与前后状态顺序冲突。")
        conn.execute("UPDATE events SET day=? WHERE id=?",(day,event_id))
        if row["to_state"] == "planned":
            payload = decode(row["payload"])
            planrow = conn.execute("""SELECT id FROM plans WHERE issue_id=? AND day=? AND plan=?
                                   AND actor=? AND note=? ORDER BY ABS(julianday(recorded)-julianday(?)) LIMIT 1""",
                                   (issue_id,row["day"],payload["plan"],row["actor"],payload["note"],row["recorded"])).fetchone()
            if planrow:
                conn.execute("UPDATE plans SET day=? WHERE id=?",(day,planrow["id"]))
        audit(conn,issue_id,today(),actor,"event_date",dict(event=event_id,old=row["day"],new=day,note=reason))
        conn.execute("UPDATE issues SET revision=revision+1,state_day=(SELECT day FROM events WHERE issue_id=? ORDER BY id DESC LIMIT 1) WHERE id=?",(issue_id,issue_id))
        return
    raise LookupError("找不到要更正的状态事件。")
