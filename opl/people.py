"""规范化现场人员与客户提出者的显示姓名。 / Normalize names for assignments and customer filters."""

# re handles Excel's mixed Chinese and European delimiters. / re 处理 Excel 中的中西文分隔符。
import re


CUSTOMER_NAMES = {s.split()[-1].casefold(): s for s in (
    "Arta Iseni", "Mathias Eisenhut", "Elias Paltz", "Paul Klein", "Stefan Vinzent")}


def customer_author(raw):
    """客户原值留在快照里，仅转换显示顺序。 / Preserve raw evidence; display given name first."""
    name = re.sub(r"\s*\(ZF[^()]*\)\s*$", "", str(raw or ""), flags=re.I).strip()
    if name.count(",") == 1:
        family, given = name.split(",", 1)
        name = " ".join(x.strip() for x in (given, family) if x.strip())
    return "/".join(CUSTOMER_NAMES.get(part.strip().casefold(), part.strip()) for part in name.split("/"))


def internal_owner(value):
    """保留共同负责的同事，移除客户 Tobias。 / Remove Tobias while retaining co-assignees."""
    value = str(value or "").strip()
    if not re.search(r"\btobias\b", value, re.I):
        return value
    value = re.sub(r"\bGleser\s*,\s*Tobias\b", "Tobias Gleser", value, flags=re.I)
    names = re.split(r"[/／、,，;；\n|&＋+]+", value)
    return "/".join(name.strip() for name in names if name.strip()
                    and customer_author(name).casefold() not in ("tobias", "tobias gleser"))
