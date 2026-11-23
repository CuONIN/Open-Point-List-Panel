"""生成现场或双语黑白清单。 / Print a compact field or bilingual worklist as a searchable PDF.

使用标准库 html 转义数据，tempfile、pathlib 管理临时文件，subprocess 驱动本机浏览器。
The standard library escapes cell text, creates temporary files and runs a local browser.
"""
from html import escape
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


_BILINGUAL_COLUMNS = (
    ("编号", 6.5), ("类别", 7.5), ("工位号和名称", 14), ("问题中文", 18.5),
    ("客户原文", 18.5), ("近期行动", 17), ("跟进信息", 18),
)
_FIELD_COLUMNS = (
    ("编号及类别", 9), ("工位号和名称", 16), ("问题描述", 34),
    ("近期行动", 24), ("跟进信息", 17),
)

# 不同用途采用相同来源的两种投影。 / Each layout selects the corresponding prepared fields.
_LAYOUTS = {"bilingual": ("print_fields", _BILINGUAL_COLUMNS), "field": ("field_print", _FIELD_COLUMNS)}
_PAPER = {"portrait": ("8.5pt", "14.4mm", "14mm"), "landscape": ("8.8pt", "9.6mm", "9.5mm")}

_PRINT_CSS = """
@page {
    size: A4 PAPER_ORIENTATION;
    margin: PAGE_MARGINS;
    @bottom-right {
        content: counter(page) " / " counter(pages);
        font: 8pt Arial, sans-serif;
        color: #000;
    }
}
* { box-sizing: border-box; }
body {
    margin: 0;
    color: #000;
    background: #fff;
    font: FIELD_SIZE/1.26 "Microsoft YaHei", "Noto Sans CJK SC", DengXian, Arial, sans-serif;
}
body.field { font-size: 9pt; line-height: 1.17; }
header { margin: 0 0 2mm; }
h1 { margin: 0 0 1mm; font-size: 13pt; line-height: 1.2; }
p { margin: 0; font-size: 8pt; }
h1, p, td { white-space: pre-wrap; overflow-wrap: anywhere; }
table { width: 100%; table-layout: fixed; border-spacing: 0; }
thead { display: table-header-group; break-inside: avoid; }
th, td {
    border: 0;
    border-right: .55pt solid #000;
    border-bottom: .55pt solid #000;
    padding: .8mm 1mm;
    text-align: left;
    vertical-align: top;
}
th:first-child, td:first-child { border-left: .55pt solid #000; }
th { border-top: .75pt solid #000; border-bottom-width: .8pt; vertical-align: middle; text-wrap: balance; }
tbody tr { height: SHORT_ROW_HEIGHT; break-inside: avoid; }
.field tbody tr { height: COMPACT_ROW_HEIGHT; }
.field th, .field td { padding: .45mm .8mm; }
.field th { font-size: 8.5pt; }
/* 跟进信息的短行共用一格。 / Keep status, owner, dates and references legible in one cell. */
td:last-child:not([colspan]) { font-size: 8.3pt; line-height: 1.14; padding-block: .4mm; }
.bilingual td:first-child:not([colspan]) { padding-inline: .5mm; }
.field td:last-child:not([colspan]) { font-size: 9pt; line-height: 1.1; }
/* 横向纸张高度较低，缩小行间距和留白。 / Preserve nine-point type on the shorter landscape sheet. */
body.field.landscape { line-height: 1.11; }
.field.landscape td { padding-block: .3mm; }
.field.landscape td:last-child:not([colspan]) { line-height: 1.08; padding-block: .3mm; }
tbody tr.long { break-inside: auto; }
td.empty { text-align: center; vertical-align: middle; }
"""


def _document(title: str, filters: str, issues: list[dict], orientation: str, layout: str = "bilingual") -> str:
    """创建自包含打印表格。 / Build one self-contained table with repeating headers."""
    if orientation not in _PAPER:
        raise ValueError("纸张方向应为 portrait（纵向）或 landscape（横向）。")
    if layout not in _LAYOUTS:
        raise ValueError("打印版式应为 field（现场版）或 bilingual（双语版）。")
    field_key, columns = _LAYOUTS[layout]
    font_size, row_height, compact_height = _PAPER[orientation]
    margins = "7mm 8mm 8mm" if (layout, orientation) == ("field", "landscape") else "9mm 9mm 11mm"
    css = (_PRINT_CSS.replace("PAPER_ORIENTATION", orientation).replace("PAGE_MARGINS", margins)
           .replace("FIELD_SIZE", font_size).replace("SHORT_ROW_HEIGHT", row_height)
           .replace("COMPACT_ROW_HEIGHT", compact_height))
    safe_title = escape(title)
    caption = filters or f"共 {len(issues)} 条"
    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; '
        'base-uri &#39;none&#39;; form-action &#39;none&#39;">',
        f'<title>{safe_title}</title><style>{css}</style></head><body class="{layout} {orientation}">',
        f'<header><h1>{safe_title}</h1><p>{escape(caption)}</p></header>',
        '<table><colgroup>',
    ]
    parts.extend(f'<col style="width:{width}%">' for _, width in columns)
    parts.append('</colgroup><thead><tr>')
    parts.extend(f'<th scope="col">{label}</th>' for label, _ in columns)
    parts.append('</tr></thead><tbody>')
    for issue in issues:
        # 只读取所选版式的字段。 / Read only the fields used for this layout.
        fields = issue[field_key]
        if (not isinstance(fields, (list, tuple)) or len(fields) != len(columns)
                or not all(isinstance(value, str) for value in fields)):
            raise ValueError(f"打印行须包含 {len(columns)} 个按顺序排列的文本字段。")
        # 超长行可以从本页余白接续。 / Let tall rows flow into the remaining page space.
        long_row = any(len(value) > width * 8 or value.count("\n") >= 8
                       for value, (_, width) in zip(fields, columns))
        parts.append('<tr class="long">' if long_row else '<tr>')
        parts.extend(f'<td>{escape(value)}</td>' for value in fields)
        parts.append('</tr>')
    if not issues:
        parts.append(f'<tr><td class="empty" colspan="{len(columns)}">当前筛选条件下没有问题。</td></tr>')
    parts.append('</tbody></table></body></html>')
    return ''.join(parts)


def _browsers() -> list[Path]:
    """查找本机支持静默打印的浏览器。 / Find local browsers with headless PDF support."""
    if os.name == 'nt':
        subpaths = ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe')
        paths = (Path(os.environ[root]) / subpath for subpath in subpaths
                 for root in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA') if os.environ.get(root))
        return list(dict.fromkeys(path for path in paths if path.is_file()))
    names = ('microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser')
    return [Path(path) for name in names if (path := shutil.which(name))]


def render_pdf(
    title: str, filters: str, issues: list[dict], *,
    project_root: Path | None = None, orientation: str = "portrait", layout: str = "bilingual",
) -> BytesIO:
    """输出指针置于开头的 PDF。 / Return the selected worklist as a PDF at byte zero.

    project_root 与同类导出的接口保持一致。 / The shared export interface accepts project_root.
    现场版读取五列，双语版读取七列。 / The field and bilingual layouts read five and seven prepared fields.
    """
    document = _document(title, filters, issues, orientation, layout)
    browsers = _browsers()
    if not browsers:
        raise RuntimeError("生成 PDF 需要本机的 Microsoft Edge 或 Google Chrome。请安装其中一个浏览器后重试。")
    timeout = min(900, 90 + len(document) // 15_000)
    with TemporaryDirectory(prefix="opl-print-", ignore_cleanup_errors=True) as temp:
        directory = Path(temp)
        source = directory / "report.html"
        source.write_text(document, encoding="utf-8")
        for index, browser in enumerate(browsers):
            output = directory / f"report-{index}.pdf"
            # 每次使用独立本地配置目录。 / Isolate the headless process from the user's session.
            args = [
                str(browser), "--headless", "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", "--disable-sync", "--disable-component-update",
                "--disable-extensions", "--host-resolver-rules=MAP * ~NOTFOUND",
                f"--user-data-dir={directory / f'profile-{index}'}", "--no-pdf-header-footer",
                f"--print-to-pdf={output}", source.as_uri(),
            ]
            try:
                result = subprocess.run(
                    args, cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0 and output.is_file():
                    data = output.read_bytes()
                    if data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF"):
                        return BytesIO(data)
            except (OSError, subprocess.TimeoutExpired):
                continue
    raise RuntimeError("本机浏览器未完成 PDF 导出。请确认 Edge 或 Chrome 可运行、临时目录可写，然后重试。")
