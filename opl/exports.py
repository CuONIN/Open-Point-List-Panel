"""生成供钳工与电工使用的打印行。 / Prepare the same filtered field worklist for PDF and Excel."""

# SQLite window functions select a single latest action per issue. / SQLite 窗口函数为每个问题选取最近行动。
from .excel import split_actions
from .queries import MULTI_FIELDS, filtered_issues, selected
from .stations import station_label
from .store import today
from .workflow import ARCHIVE, STATES

LABELS = {
    "station":"工位", "machine":"机器号", "avo":"AVO", "area":"区域", "author":"客户提出者",
    "department":"客户部门", "category":"客户类别", "priority":"优先级", "responsible":"客户责任方",
    "owner":"我方负责人", "zf":"ZF 参与", "state":"我方状态", "q":"关键词", "week":"统计周",
    "as_of":"截止日", "event_kind":"事件类型",
}

# 现场短栏用中文，双语版保留客户填写的分类。 / Translate known shop-floor categories only in the compact sheet.
FIELD_CATEGORIES = {
    "AME - Process / Mechanical":"机械", "AME - Electrical / Controls":"电气／控制", "Maintenance":"设备维护",
    "General":"通用", "PLC-Software":"PLC 软件", "Electrical-Installation":"电气安装",
    "Documentation":"文档", "Robot":"机器人", "Spare Parts":"备件", "Conveyor":"输送设备",
}

SCOPES = {"ours": "我方未结", "customer": "客户未结", "all": "全部客户记录"}
MODES = {"current": "当前状态", "occurred": "期间进入过", "latest": "最近一次变化",
         "asof": "截至所选日", "planned": "本周实施计划", "unreported": "本周计划尚未上报"}
ZF = {"pure": "纯 ZF", "joint": "豪森与 ZF 共同责任", "any": "所有 ZF 参与", "none": "无 ZF 参与"}
EVENTS = {"baseline": "建账基线", "new": "新增", "meeting": "现场确认", "reopened": "客户重开",
          "returned": "责任转回", "customer_closed": "客户关闭", "transferred": "转纯 ZF"}


def filter_caption(params, cutoff, total):
    states = selected(params, "state")
    mode = params.get("mode") or "current"
    default_scope = "all" if mode == "occurred" or set(states).intersection(ARCHIVE) else "ours"
    parts = [SCOPES.get(params.get("scope") or default_scope, "全部"), MODES.get(mode, mode)]
    for key in (*MULTI_FIELDS, "q", "week", "as_of", "event_kind"):
        values = selected(params, key)
        if key == "owner":
            values = tuple(dict.fromkeys((*values, *selected(params, "tracker"))))
        if key == "station":
            values = tuple(dict.fromkeys(station_label(v) for v in values))
        if values:
            names = {"state": STATES, "zf": ZF, "event_kind": EVENTS}.get(key, {})
            parts.append(f"{LABELS[key]}　" + " 或 ".join(names.get(v, v) for v in values))
    order = "编号升序" if params.get("sort") == "number_asc" else "编号降序"
    return " · ".join([*parts, order, f"截至 {cutoff}", f"共 {total} 条"])


def latest_customer_actions(conn, rows, cutoff):
    """每条问题取最近一条可用客户行动。 / Select the latest dated customer action, falling back to sheet order."""
    if cutoff < today():
        result = {}
        for row in rows:
            actions = split_actions(row["source_action"])
            eligible = (a for a in actions if (a["date"] or "") <= cutoff)
            latest = max(eligible, key=lambda a:a["date"] or "", default=None)
            if latest:
                result[row["id"]] = latest
        return result

    selected_ids = {row["id"] for row in rows}
    return {a["issue_id"]:a for a in conn.execute("""
        SELECT * FROM (SELECT issue_id,day AS date,date_raw,original,zh,en,author,
        ROW_NUMBER() OVER (PARTITION BY issue_id ORDER BY day DESC,position,id) AS pos
        FROM actions WHERE active=1 AND day<=?) WHERE pos=1""", (cutoff,)) if a["issue_id"] in selected_ids}


def print_data(conn, params):
    """筛选与排序复用清单查询，先于分页。 / Reuse the grid selection and order before pagination."""
    rows, cutoff = filtered_issues(conn, params, source=True)
    actions = latest_customer_actions(conn, rows, cutoff)
    # 同日反馈按录入先后排序。 / For the same business day, the last entry wins.
    feedback = {r["issue_id"]:r for r in conn.execute("""
        SELECT * FROM (SELECT issue_id,day,note,source,ROW_NUMBER() OVER
        (PARTITION BY issue_id ORDER BY day DESC,id DESC) AS pos FROM feedback WHERE day<=?) WHERE pos=1""",(cutoff,))}
    meetings = {r["issue_id"]:r for r in conn.execute("""
        SELECT * FROM (SELECT issue_id,day,note,'meeting' AS source,ROW_NUMBER() OVER
        (PARTITION BY issue_id ORDER BY day DESC,id DESC) AS pos FROM events
        WHERE kind='meeting' AND day<=? AND note<>'') WHERE pos=1""",(cutoff,))}
    for row in rows:
        technical = []
        recent_field_note = ""
        action = actions.get(row["id"])
        if action:
            original = action["original"]
            parts = list(dict.fromkeys(x for x in (action["zh"], original) if x))
            if parts:
                stamp = action["date"] or action["date_raw"] or "日期待核"
                technical.append(stamp + "　" + "\n".join(parts))
                recent_field_note = stamp + "　" + (action["zh"] or original)
        notes = [entry for entry in (feedback.get(row["id"]),meetings.get(row["id"])) if entry and entry["note"]]
        if notes:
            recent = max(notes, key=lambda e:e["day"])
            if not action or recent["day"] >= (action["date"] or ""):
                origin = "豪森表备注" if recent["source"].startswith("haosen") else "现场"
                recent_field_note = recent["day"] + " " + origin + "　" + recent["note"]
                technical.append(recent_field_note)
        original = row["problem"]
        if row["en"] and row["en"] != original:
            original = row["en"] + "\n" + original
        followup = [row["state_label"], "负责人　" + (row["owner"] or "待分配"), "计划　" + (row["plan"] or "待安排")]
        by_kind = {}
        for ref in row["related"]:
            kind = ref["kind"]
            label = {"similar":"相似", "duplicate":"重复"}.get(kind)
            if kind == "detail":
                label = "主问题" if ref["direction"] == "in" else "明细"
            by_kind.setdefault(label, []).append("#" + ref["number"])
        for label, numbers in by_kind.items():
            followup.append(label + "　" + "、".join(numbers))
        order_no = row["order_no"].strip()
        if order_no:
            # 长单号从新行开始，便于现场抄录。 / Give production IDs the full width of the field.
            followup.append("投产进度　待投产" if order_no == "待投产" else "投产单号\n" + order_no)
        management = "\n".join(followup)
        row["print_fields"] = [
            row["number"], " › ".join(x for x in (row["department"],row["category"]) if x) or "—",
            "\n".join(x for x in (row["machine"],row["station"]) if x) or "待核", row["zh"] or "待翻译",
            original or "—", "\n".join(technical) or "—", management,
        ]
        # 紧凑版将纸面宽度留给技术正文，缺译文时回退客户原文。 / Reserve paper width for instructions; fall back to source text.
        source_category = row["category"] or row["department"]
        category = FIELD_CATEGORIES.get(source_category, source_category)
        description = row["zh"] or row["problem"] or "来源未填写"
        row["field_print"] = [
            "\n".join(x for x in (row["number"],category) if x), row["print_fields"][2],
            description, recent_field_note or "—", management,
        ]
    return filter_caption(params,cutoff,len(rows)), rows
