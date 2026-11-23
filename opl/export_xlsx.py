"""生成单张 A4 黑白现场工作单。 / Generate a single monochrome A4 field worksheet."""

# 标准库负责内存文件、Unicode 宽度和并列续行。 / The standard library handles
# in-memory output, Unicode widths and parallel continuation rows.
from functools import lru_cache
from io import BytesIO
from itertools import zip_longest
import os
import re
import unicodedata
from zipfile import ZipFile

# openpyxl 生成原生 .xlsx，含打印范围、页脚和重复表头。
# openpyxl writes native XLSX print settings, page footers and repeating headers.
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.dimensions import RowDimension
from openpyxl.xml import LXML


_LAYOUTS = {
    "bilingual": ("print_fields", ("编号", "类别", "工位号和名称", "问题中文", "客户原文", "近期行动", "跟进信息"),
                  (.055, .08, .14, .185, .19, .17, .18)),
    "field": ("field_print", ("编号及类别", "工位号和名称", "问题描述", "近期行动", "跟进信息"),
              (.09, .14, .35, .25, .17)),
}
# 列宽以 Excel 默认字体的数字宽度计，行高单位为磅。 / Excel column widths use
# default-font digit widths; row heights are in points.
_PAPER = {"portrait": (107.2, 40), "landscape": (149.9, 28)}
_FONT = "Microsoft YaHei"
_BODY_SIZE = 9
# 多个投产单号通常可放在同一行，超长正文再接续。 / Keep typical multi-order entries on one row.
_LINES_PER_ROW = 12
_MAX_ROWS = 1_048_576
_MAX_CHARS = 16_000  # 非 BMP 字符占两个 UTF-16 单元。 / Non-BMP characters use two UTF-16 units.
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


class _AutoFitRow(RowDimension):
    """缓存初始行高，允许 Excel 按本机字体重新展开。 / Cache a height without locking Excel's native AutoFit."""
    def __iter__(self):
        # 保留 openpyxl 的标准属性，只取消固定高度。 / Preserve normal row attributes and unlock height.
        for key, value in super().__iter__():
            yield key, "0" if key == "customHeight" else value


def _text(value):
    value = "" if value is None else str(value)
    if _INVALID_XML.search(value):
        # XML 禁用的控制码以可逆的可见文字保留。 / Escape forbidden XML code points reversibly.
        value = _INVALID_XML.sub(lambda m: "\\u%04X" % ord(m.group()), value.replace("\\", "\\\\"))
    return value


@lru_cache(maxsize=2048)
def _glyph_width(ch):
    """估算 9 磅正文的屏幕像素宽度。 / Estimate glyph width at the 9 pt body size."""
    if ch == "\t":
        return 48
    category = unicodedata.category(ch)
    if unicodedata.combining(ch) or category == "Cf":
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 13
    if ch in "MWmw@%#&":
        return 12
    if ch in " .,:;'!|ijlI":
        return 4.5
    if ch.isupper() or ch.isdigit():
        return 8.8
    return 7.5


def _chunks(value, width, font_size):
    """逐段返回原始子串及预计行数。 / Yield exact source segments and their visual heights."""
    text = _text(value)
    if not text:
        yield "", 1
        return
    # 为字体替换和打印机边距留余量。 / Reserve space for font substitution and cell padding.
    capacity = max(15, (width * 7 - 6) * .9 * _BODY_SIZE / font_size)
    start = 0
    while start < len(text):
        pos = line_start = start
        break_after = None
        used = 0
        lines = 1
        while pos < len(text) and pos - start < _MAX_CHARS:
            ch = text[pos]
            if ch in "\r\n":
                if lines == _LINES_PER_ROW:
                    break
                pos += 2 if text.startswith("\r\n", pos) else 1
                lines += 1
                line_start = pos
                used = 0
                break_after = None
                if lines == _LINES_PER_ROW:
                    break
                continue
            advance = _glyph_width(ch)
            if pos > line_start and used + advance > capacity:
                # 优先整词换行，超宽单词按字符折行。 / Wrap at word breaks when
                # possible and at character boundaries for an unbroken identifier.
                pos = break_after if break_after is not None else pos
                if lines == _LINES_PER_ROW:
                    break
                lines += 1
                line_start = pos
                used = 0
                break_after = None
                continue
            used += advance
            pos += 1
            if ch.isspace() or unicodedata.east_asian_width(ch) in ("W", "F"):
                break_after = pos
        yield text[start:pos], lines
        start = pos


def _styles(book):
    """同一张工作单全程使用纯黑与纯白。 / Use only pure black ink on white paper."""
    black, white = "FF000000", "FFFFFFFF"
    thin = Side(style="thin", color=black)
    dotted = Side(style="dotted", color=black)
    medium = Side(style="medium", color=black)
    for name, size, bold, vertical, border in (
        ("report_title", 13, True, "center", Border()),
        ("report_scope", 9, False, "center", Border()),
        ("column_heading", 9, True, "center", Border(left=thin, right=thin, top=medium, bottom=medium)),
        ("first_line", _BODY_SIZE, False, "top", Border(left=thin, right=thin, top=thin, bottom=thin)),
        ("continued", _BODY_SIZE, False, "top", Border(left=thin, right=thin, top=dotted, bottom=thin)),
    ):
        style = NamedStyle(name=name)
        style.font = Font(name=_FONT, size=size, bold=bold, color=black)
        style.fill = PatternFill("solid", fgColor=white)
        style.border = border
        style.alignment = Alignment(horizontal="center" if name == "column_heading" else "left",
                                    vertical=vertical, wrap_text=True)
        style.number_format = "@"
        book.add_named_style(style)


def _put(sheet, row, column, value, style):
    if row > _MAX_ROWS:
        raise ValueError("已超过单张 Excel 工作表的行数限制，请缩小筛选范围后再导出。")
    cell = sheet.cell(row, column)
    cell.style = style
    cell.value = value
    # 带等号的客户原文、错误码和前导零编号均为文本。 / Preserve formula-like
    # customer wording, literal error codes and leading zeros as plain text.
    cell.data_type = "s"


def _banner(sheet, row, value, widths, style, size):
    for chunk, lines in _chunks(value, sum(widths), size):
        _put(sheet, row, 1, chunk, style)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(widths))
        sheet.row_dimensions[row].height = 5 + lines * size * 1.65
        row += 1
    return row


def _preserve_returns(output):
    """兼容仅安装 openpyxl 时的回车序列化。 / Preserve CR in the pure-Python XML writer."""
    # XML 解析会把裸回车转成换行；lxml 会自行转义，标准写入器需要数值实体。
    # XML parsers normalize raw carriage returns; the lxml writer already emits entities.
    # On Windows the standard writer also expands each literal LF on its way to disk.
    with ZipFile(output) as source:
        part = "xl/worksheets/sheet1.xml"
        xml = source.read(part).replace(os.linesep.encode(), b"\n").replace(b"\r", b"&#13;")
        restored = BytesIO()
        with ZipFile(restored, "w") as target:
            for entry in source.infolist():
                target.writestr(entry, xml if entry.filename == part else source.read(entry))
    return restored


def render_xlsx(title: str, filters: str, issues: list[dict], *,
                orientation: str = "portrait", layout: str = "bilingual") -> BytesIO:
    """按筛选顺序输出现场五列或双语七列工作单，返回流指针位于开头。

    Return a seek-ready five-column field or seven-column bilingual sheet in issue order.
    """
    if orientation not in _PAPER:
        raise ValueError("打印方向须选择纵向（portrait）或横向（landscape）。")
    if layout not in _LAYOUTS:
        raise ValueError("打印布局须选择现场版（field）或双语版（bilingual）。")
    key, headers, shares = _LAYOUTS[layout]
    total_width, minimum_height = _PAPER[orientation]
    widths = tuple(round(total_width * share, 2) for share in shares)
    if layout == "field":
        # 现场版短行分别为 14 mm 和 9.5 mm。 / The field-sheet row pitch is 14 or 9.5 mm.
        minimum_height = (14 if orientation == "portrait" else 9.5) * 72 / 25.4
    book = Workbook()
    _styles(book)
    sheet = book.active
    sheet.title = "现场工作单"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = orientation
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    # Excel 即使读到显式 false 也可能采用打印机默认方向。 / Omit usePrinterDefaults:
    # desktop Excel otherwise substitutes the driver's Letter/portrait for the saved A4 orientation.
    sheet.page_setup.blackAndWhite = True
    sheet.print_options.horizontalCentered = True
    sheet.page_margins = PageMargins(left=.26, right=.26, top=.38, bottom=.38, header=.16, footer=.16)
    sheet.oddFooter.right.text = "第 &P / &N 页"
    sheet.oddFooter.right.font = _FONT + ",Regular"
    sheet.oddFooter.right.size = 8
    sheet.oddFooter.right.color = "000000"
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width

    row = _banner(sheet, 1, title or "EOL 现场工作单", widths, "report_title", 13)
    scope = f"共 {len(issues)} 项" + (f"　筛选条件　{filters}" if filters else "")
    if filters and filters.endswith(f"共 {len(issues)} 条"):
        scope = filters
    row = _banner(sheet, row, scope, widths, "report_scope", 9)
    for col, heading in enumerate(headers, 1):
        _put(sheet, row, col, heading, "column_heading")
    sheet.row_dimensions[row].height = 28
    sheet.print_title_rows = f"{row}:{row}"
    sheet.freeze_panes = f"B{row + 1}"
    row += 1
    if not issues:
        row = _banner(sheet, row, "当前筛选没有匹配的问题。", widths, "report_scope", 9)

    for issue in issues:
        fields = issue[key]
        if len(fields) != len(widths):
            count = "五项" if layout == "field" else "七项"
            raise ValueError(f"每条工作单记录须包含{count}打印字段。")
        columns = [_chunks(v, width, _BODY_SIZE) for v, width in zip(fields, widths)]
        # 现场版首列还含类别；续行只复印编号。 / In the compact layout, repeat the
        # identifier alone when the first cell also contains the issue category.
        first = _text(fields[0])
        number = first.splitlines()[0] if first and layout == "field" else first
        number_chunk = next(_chunks(number, widths[0], _BODY_SIZE))
        repeat_number = number_chunk if number_chunk[0] == number else None
        for offset, parts in enumerate(zip_longest(*columns)):
            # 即使在页首接续，也能认出同一问题。 / Repeat a whole short issue
            # number so continuation rows are identifiable across a printed page break.
            max_lines = 1
            for col, part in enumerate(parts, 1):
                if col == 1 and part is None:
                    part = repeat_number
                value, lines = part or ("", 1)
                _put(sheet, row, col, value, "continued" if offset else "first_line")
                max_lines = max(max_lines, lines)
            content_height = 4 + max_lines * 13.25
            if layout == "field" and max_lines <= 2:
                content_height = 3 + max_lines * 11.8
            height = max(17 if offset else minimum_height, content_height)
            sheet.row_dimensions[row] = _AutoFitRow(sheet, index=row, ht=height)
            row += 1

    sheet.print_area = f"A1:{get_column_letter(len(widths))}{row - 1}"
    output = BytesIO()
    book.save(output)
    book.close()
    if not LXML and ("\r" in str(title) or "\r" in str(filters) or
                     any("\r" in str(value) for issue in issues for value in issue[key])):
        output = _preserve_returns(output)
    output.seek(0)
    return output
