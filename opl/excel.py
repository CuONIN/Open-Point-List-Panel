"""从客户及豪森工作簿识别 EOL 表、双语字段和分段行动。 / Parse EOL sheets, bilingual fields and dated actions."""

# openpyxl 仅读取已保存的单元格值；re 识别表头、日期与行动边界。 / openpyxl reads saved cell values; re recognizes headers, dates and action boundaries.
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
import re
import unicodedata
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel
from openpyxl.utils.exceptions import InvalidFileException

from .lines import EOL, STATOR
from .stator import variant_station
from .stations import StationMap


def _text(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def classify_responsible(raw):
    parties = {
        party.strip()
        for party in re.split(r"[/,;|&+、]|\s+\band\b\s+|\s+\bund\b\s+", _normalized(raw))
        if party.strip()
    }
    if parties == {"zf"}:
        return "zf"
    if parties == {"haosen"}:
        return "haosen"
    if "zf" in parties and "haosen" in parties:
        return "joint"
    return "unknown"


def priority_band(raw):
    key = _normalized(raw).replace(" ", "")
    return {"high": "High", "high+": "High", "medium": "Medium", "low": "Low", "": "Low"}.get(
        key, "待核"
    )


_HEADERS = {
    "no": ("no", "nr", "number", "nr nummer", "编号", "序号"),
    "date": ("date", "datum", "creation date", "提出日期"),
    "station": ("station", "工位", "工作站"),
    "author": ("author", "ersteller", "提出人"),
    "department": ("department", "abteilung", "部门"),
    "category": ("category", "kategorie", "类别"),
    "priority": ("prio", "priority", "priorität", "优先级"),
    "problem": ("problem", "problem description", "beschreibung", "问题描述", "问题"),
    "action": ("action", "actions", "maßnahme", "maßnahmen", "行动", "措施"),
    "responsible": ("responsible", "verantwortlich", "责任方"),
    "due": ("due date", "fälligkeit", "截止日期"),
    "status": ("status", "客户状态"),
    "remark": ("remark", "remarks", "bemerkung", "客户备注"),
    "changed_by": ("status changed by", "status geändert von", "状态变更人"),
    "changed_on": ("status date", "status changed on", "status change date", "状态变更日期"),
    "owner": ("负责人", "我方负责人"),
    "people": ("整改人数", "整改人员"),
    "work_time": ("整改时间",),
    "order_no": ("投产单号",),
    "note": ("备注", "我方备注", "现场备注"),
    "hint": ("分类码", "内部分类码"),
    "step1": ("step 1", "step1"),
    "step2": ("step 2", "step2"),
}
_HEADER_NAMES = {word: field for field, names in _HEADERS.items() for word in names}
_REQUIRED = {"no", "station", "problem", "responsible", "status"}
_DATE_FIELDS = ("date", "changed_on", "due")


def _header_layout(values, line=EOL):
    columns = {}
    last = -1
    boundary = len(values)
    for i, value in enumerate(values):
        label = _normalized(value).strip(" .:：")
        if not label:
            continue
        if (label == "dropdown" or label.startswith("finished by haosen")
                or (label == "machine" and _REQUIRED <= columns.keys() and i > last + 1)):
            boundary = i
            break
        if "不清晰" in label and "待投产" in label:
            key = "hint"
        else:
            key = _HEADER_NAMES.get(label)
        if line == STATOR:
            if label == "station number":
                key = "station"
            elif label == "variant":
                key = "variant"
            elif key == "date" and "responsible" in columns and "status" not in columns:
                key = "due"
        if key == "date" and ("changed_by" in columns or ("date" in columns and "status" in columns)):
            key = "changed_on"
        if key is not None and key not in columns:
            columns[key] = i
            last = i
    if not _REQUIRED <= columns.keys():
        return None
    width = last + 1
    while width < boundary and _text(values[width]):
        width += 1
    return columns, width


def stator_sheet(sheet):
    """核对工作表及其项目栏。 / Verify the worksheet and its project designation."""
    if _normalized(sheet.title) not in {"open points list", "stator housing"}:
        return False
    for row in sheet.iter_rows(max_row=9, max_col=24, values_only=True):
        if any(_normalized(value) in {"pa stator housing", "stator housing"} for value in row):
            return True
    return False


def legacy_stator_book(path):
    """识别原 EOL 目录内单独的定子资料。 / Route dedicated PAL books found in legacy EOL folders."""
    with closing(load_workbook(path, read_only=True, data_only=True, keep_links=False)) as book:
        if any(re.fullmatch(r"list of defects|end of line(?:\s*\(\d+\))?|eol", _normalized(s)) for s in book.sheetnames):
            return False
        return any(stator_sheet(s) for s in book)


def _find_sheet(book, line=EOL):
    named = [
        sheet for sheet in book
        if re.fullmatch(r"list of defects|end of line(?:\s*\(\d+\))?|eol", _normalized(sheet.title))
    ]
    other_lines = re.compile(r"stator|gearbox|final assembly|定子|齿轮箱|总装", re.I)
    candidates = named or [
        s for s in book if s.sheet_state == "visible" and not other_lines.search(s.title)
    ]
    if line == STATOR:
        candidates = [s for s in book if stator_sheet(s)]
    else:
        candidates = [s for s in candidates if not stator_sheet(s)]
    matches = []
    for sheet in candidates:
        for index, row in enumerate(
            sheet.iter_rows(max_row=min(sheet.max_row or 60, 60), max_col=80, values_only=True), 1
        ):
            layout = _header_layout(row, line)
            if layout:
                matches.append((sheet, index, *layout))
                break
    if len(matches) > 1:
        raise ValueError("找到多张问题清单表，无法确定当前线体来源：" + "、".join(x[0].title for x in matches))
    if not matches:
        raise ValueError(f"找不到 {line.name} 问题表头。请检查线体、项目名称及问题表。")
    return matches[0]


def _snapshot_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (type(None), bool, int, float, str)):
        return value
    return str(value)


def _looks_like_record(row):
    # 只有预分配编号的空行和下拉图例不属于问题。 / Number-only placeholders and the dropdown legend are outside the issue table.
    if re.fullmatch(r"\d+", row["no"]):
        return any(row[key] for key in ("station", "problem", "date", "author", "status", "action", "responsible"))
    return bool((row["station"] or row["problem"]) and (row["author"] or row["date"])
                and (row["status"] or row["responsible"] or row["problem"]))


def _iso(year, month, day):
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _date_order(samples):
    orders = set()
    for raw in samples:
        found = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(?:\d{4}|\d{2})", raw)
        if found:
            a, b = map(int, found.groups())
            if 12 < a <= 31 and 1 <= b <= 12:
                orders.add("dmy")
            if 12 < b <= 31 and 1 <= a <= 12:
                orders.add("mdy")
    return next(iter(orders)) if len(orders) == 1 else None


def _parse_date(raw, slash_order=None):
    if re.fullmatch(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", raw):
        y, m, d = (int(x) for x in re.findall(r"\d+", raw))
        return _iso(y, m, d)
    parts = re.fullmatch(r"(\d{1,2})([./-])(\d{1,2})\2(\d{4}|\d{2})", raw)
    if not parts:
        return ""
    a, sep, b, year = parts.groups()
    aa, bb, yyyy = int(a), int(b), int(year)
    if len(year) == 2:
        yyyy += 2000
    if sep == "/":
        if aa > 12:
            return _iso(yyyy, bb, aa)
        if bb > 12 or aa == bb or slash_order == "mdy":
            return _iso(yyyy, aa, bb)
        if slash_order != "dmy":
            return ""
    return _iso(yyyy, bb, aa)


def read_book(path, line=EOL):
    """返回来源类型、原始工作表名和问题行，已确认日期使用 ISO 格式。 / Return (source kind, exact worksheet name, issue rows); dates are ISO when known."""
    path = Path(path)
    if path.name.startswith("~$"):
        raise ValueError("这是 Excel 打开工作簿时生成的临时锁文件。")
    try:
        wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except (InvalidFileException, BadZipFile, KeyError) as exc:
        raise ValueError("无法读取此 Excel 工作簿，请提供有效的 .xlsx 或 .xlsm 文件。") from exc
    with closing(wb):
        sheet, header, columns, width = _find_sheet(wb, line)
        kind = "zf"
        if "hint" in columns:
            kind = "haosen_macro"
        elif {"owner", "people", "work_time", "order_no", "note"} & columns.keys():
            kind = "haosen"
        rows = []
        for row_no, values in enumerate(
            sheet.iter_rows(min_row=header + 1, max_col=width, values_only=True), header + 1
        ):
            item = {key: _text(values[columns[key]]) if key in columns else "" for key in _HEADERS}
            if line == STATOR:
                variant = _text(values[columns["variant"]]) if "variant" in columns else ""
                item["station_cell"] = item["station"]
                item["variant"] = variant
                if variant_station(variant) and item["station"].casefold() in {"", "bmw"}:
                    item["variant"], item["station"] = item["station"], variant
                # 保留无号描述供复核，忽略表尾图例。 / Retain unnumbered descriptions; exclude the footer.
                if _normalized(item["no"]).startswith(("legend", "background information")):
                    break
            if not _looks_like_record(item) and not (line == STATOR and not item["no"] and item["problem"]):
                continue
            item["row"] = row_no
            item["raw"] = {get_column_letter(i): _snapshot_value(v)
                           for i, v in enumerate(values, 1) if v is not None}
            for key in _DATE_FIELDS:
                value = values[columns[key]] if key in columns else None
                if isinstance(value, datetime):
                    item[key] = value.date().isoformat()
                elif isinstance(value, (int, float)) and 20_000 <= value <= 80_000:
                    item[key] = from_excel(value, wb.epoch).date().isoformat()
            if line == STATOR and _parse_date(item["changed_by"]):
                misplaced = item["changed_by"]
                name_or_date = item["changed_on"]
                item["changed_by"] = "" if _parse_date(name_or_date) else name_or_date
                item["changed_on"] = name_or_date if _parse_date(name_or_date) else misplaced
            rows.append(item)
        for key in _DATE_FIELDS:
            order = _date_order(row[key] for row in rows)
            for row in rows:
                row[key] = _parse_date(row[key], order)
        return kind, sheet.title, rows


_HAN = re.compile(r"[\u3400-\u9fff]")
_LATIN_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}")


def bilingual(text):
    """拆分混合原文和中文，并保留译文里的设备术语。 / Separate mixed source/Chinese text. Keep equipment terms in translated lines."""
    source, chinese = [], []
    active = source
    for line in _text(text).splitlines():
        matches = list(_HAN.finditer(line))
        if not line.strip():
            active.append("")
        elif not matches:
            # IP、端子号、零件号可能接续上一行中文。 / A bare IP, terminal number or part code can continue a Chinese sentence.
            if active is chinese and re.fullmatch(r"[\s\d./+_-]+", line):
                chinese.append(line)
            else:
                source.append(line)
                active = source
        else:
            start, end = matches[0].start(), matches[-1].end()
            left, right = line[:start], line[end:]
            if len(matches) <= 2 and left.strip() and len(_LATIN_WORD.findall(right)) >= 4:
                # 邮件主题中的“答复”标记属于原文。 / e.g. a German/English email subject with the reply marker “答复”.
                source.append(line)
                active = source
                continue
            if len(_LATIN_WORD.findall(left)) >= 4 and re.search(r"[a-z]{3}", left) and (
                not any(part.strip() for part in source) or left.rstrip().endswith((".", "!", "?"))
            ):
                source.append(left.rstrip())
                chinese.append(line[start:])
            elif (len(matches) >= 4 and len(_LATIN_WORD.findall(right)) >= 4
                  and re.match(r"[\s。.!?]*[A-Za-zÀ-ÖØ-öø-ÿ]", right)
                  and re.search(r"[a-z]{3}", right)):
                chinese.append(line[:end])
                source.append(right.lstrip(" .。"))
            else:
                chinese.append(line)
            active = chinese
    return "\n".join(source).strip(), "\n".join(chinese).strip()


# 只在行首日期处分段，保留正文中提到的交付日期。 / Dates open an event only at the start of a line. Due dates quoted in the body survive intact.
_DATE_PREFIX = re.compile(
    r"^\s*(?P<date>\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
    r"|\d{1,2}(?P<sep>[./-])\d{1,2}(?P=sep)(?:\d{4}|\d{2})"
    r"|\d{1,2}[./]\d{1,2}(?=\s|[:：]|[\u3400-\u9fff]|$))"
    r"(?![\d./])"
)
_AUTHOR = re.compile(
    r"^(?:(?P<initials>[A-Z]{2,4})(?=\s*(?:[:：.]|$))"
    r"|(?P<name>[A-Z]\.\s*[A-Z][a-zäöüß-]+)(?=\s*[:：])"
    r"|(?P<cn>[\u3400-\u9fff]{2,4})(?=\s*[:：]))"
)
_AUTHOR_IGNORE = {"NOK", "OK", "Update", "Status", "Haosen", "备注", "措施", "说明", "更新"}


def _action_blocks(text):
    blocks = []
    current = None
    for line in _text(text).splitlines():
        matched = _DATE_PREFIX.match(line)
        if matched and len(re.findall(r"\d+", matched["date"])) == 2:
            # 尺寸属于上条行动的正文。 / A dimension such as “3/4 inch fitting” or “11.4 mm” is a continuation.
            rest = line[matched.end():].lstrip()
            if re.match(r"(?i)(?:mm|cm|m|inch|inches|in\.|bar|kg|v|hz|a)\b", rest):
                matched = None
        if matched or current is None:
            current = {"date_raw": matched["date"] if matched else "", "author": "", "body": [], "lines": []}
            blocks.append(current)
        current["lines"].append(line)
        if matched:
            remaining = re.sub(r"^\s*(?:[:：]|[-–—]\s+)?\s*", "", line[matched.end():])
            name = _AUTHOR.match(remaining)
            if name and name.group() not in _AUTHOR_IGNORE:
                current["author"] = name.group()
                remaining = remaining[name.end():].lstrip(" \t:：.-")
            current["body"].append(remaining)
        else:
            current["body"].append(line)
    return blocks


def split_actions(text, *, slash_order=None):
    """拆分行动，日期不明确时保留原始日期并留空标准日期。 / Return distinct action updates; unresolvable dates retain date_raw and a blank date."""
    blocks = _action_blocks(text)
    order = slash_order or _date_order(block["date_raw"] for block in blocks)
    updates = []
    for block in blocks:
        original, zh = bilingual("\n".join(block["body"]))
        updates.append({
            "original": original,
            "zh": zh,
            "date_raw": block["date_raw"],
            "date": _parse_date(block["date_raw"], order),
            "author": block["author"],
            "raw": "\n".join(block["lines"]).strip(),
        })
    # 有的表先列全部原文，然后按日期重复列出译文。 / Translated action blocks often repeat every date after the entire original list.
    # 同日数量一致才配对，避免合并不同事件。 / Require equal multiplicity per date so that two distinct same-day updates stay distinct.
    groups = defaultdict(lambda: ([], []))
    for index, entry in enumerate(updates):
        if entry["zh"] and entry["original"]:
            continue
        key = entry["date"] or entry["date_raw"]
        groups[key][1 if entry["zh"] else 0].append(index)
    merged = set()
    for original_indices, translated_indices in groups.values():
        if len(original_indices) != len(translated_indices):
            continue
        pairs = list(zip(original_indices, translated_indices))
        if any(updates[a]["author"] and updates[b]["author"] and
               updates[a]["author"].casefold() != updates[b]["author"].casefold() for a, b in pairs):
            continue
        for a, b in pairs:
            updates[a]["zh"] = updates[b]["zh"]
            updates[a]["raw"] += "\n\n" + updates[b]["raw"]
            merged.add(b)
    return [entry for i, entry in enumerate(updates) if i not in merged and entry["raw"]]
