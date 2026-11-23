"""验证现场黑白清单的版式与数据。 / Regression tests for the printed field worklist.

unittest 和 HTMLParser 验证列内容，subprocess 在装有 Poppler 时检查 PDF 中的实际文字。
unittest and HTMLParser exercise the HTML contract; optional Poppler checks the printed text.
"""
from html.parser import HTMLParser
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from opl import export_pdf


HEADERS = ("编号", "类别", "工位号和名称", "问题中文", "客户原文", "近期行动", "跟进信息")
FIELD_HEADERS = ("编号及类别", "工位号和名称", "问题描述", "近期行动", "跟进信息")


class TableCells(HTMLParser):
    """记录浏览器能读到的标签和单元格。 / Collect HTML tags and decoded cell values."""

    def __init__(self):
        super().__init__()
        self.tags = []
        self.headers = []
        self.cells = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag in {"th", "td"}:
            self.active = []

    def handle_data(self, data):
        if self.active is not None:
            self.active.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.active is not None:
            target = self.headers if tag == "th" else self.cells
            target.append("".join(self.active))
            self.active = None


def sample(number):
    """构造短现场问题。 / Build one representative short row."""
    return {"print_fields": [
        number, "机械", "24821050\nLeak Test", "调整定位销间隙。", "Jig prüfen.",
        "复测待确认。", "待核查\n李工\nCW48",
    ]}


def field_sample(number):
    """构造五列现场投影。 / Build the five values consumed by the technician's report."""
    return {"field_print": [number + "\n机械", "24821050\nLeak Test",
                            "调整定位销间隙。", "复测待确认。", "待核查\n李工\nCW48"]}


def compact(text):
    """去除 PDF 排版断行。 / Ignore whitespace introduced by typesetting."""
    return "".join(text.split())


def printed_pages(pdf):
    """由 Poppler 读取每页文本。 / Read the actual PDF pages with Poppler."""
    result = subprocess.run(
        [shutil.which("pdftotext"), "-raw", "-enc", "UTF-8", "-", "-"],
        input=pdf, capture_output=True, timeout=30, check=True,
    )
    return result.stdout.decode("utf-8").rstrip("\x0c").split("\x0c")


class ExportPdfTests(unittest.TestCase):
    def test_exact_columns_literal_text_and_only_prepared_fields(self):
        issue = sample("1405a")
        issue["print_fields"][3] = "<script>Ä,中文 & detail</script>\n\n第二行"
        issue.update(number="SECRET-ID", sections=[("SECRET-HISTORY", [])],
                     images=[{"name": "SECRET-FILE", "path": "https://example.invalid/photo.png"}])
        document = export_pdf._document("班组 <清单>", "S2 & 计划", [issue], "portrait")
        parser = TableCells()
        parser.feed(document)
        self.assertEqual(tuple(parser.headers), HEADERS)
        self.assertEqual(parser.cells, issue["print_fields"])
        self.assertNotIn("script", parser.tags)
        self.assertNotIn("img", parser.tags)
        self.assertNotIn("SECRET", document)
        self.assertIn("班组 &lt;清单&gt;", document)
        self.assertIn("S2 &amp; 计划", document)
        css = document.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertEqual(set(re.findall(r"#[a-fA-F0-9]+", css)), {"#000", "#fff"})

    def test_validated_inputs_and_missing_browser(self):
        for fields in ([], [""] * 8, "1234567", [""] * 6 + [None]):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "7 个"):
                export_pdf._document("测试", "", [{"print_fields": fields}], "landscape")
        with self.assertRaisesRegex(ValueError, "5 个"):
            export_pdf._document("测试", "", [{"field_print": [""] * 4}], "portrait", "field")
        with self.assertRaisesRegex(ValueError, "打印版式"):
            export_pdf.render_pdf("测试", "", [], layout="unknown")
        with self.assertRaisesRegex(ValueError, "纸张方向"):
            export_pdf.render_pdf("测试", "", [], orientation="A3")
        with patch.object(export_pdf, "_browsers", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "Edge"):
                export_pdf.render_pdf("测试", "", [])

    def test_field_layout_separates_description_action_and_follow_up(self):
        row = field_sample("1405a")
        references = "待核查\n负责人　李工\n计划　CW48\n关联　1301b、1302\n投产单　WO-2482"
        row["field_print"][-1] = references
        row["print_fields"] = ["HIDDEN-BILINGUAL-VALUE"] * 7
        document = export_pdf._document("现场", "", [row], "portrait", "field")
        parser = TableCells()
        parser.feed(document)
        self.assertEqual(tuple(parser.headers), FIELD_HEADERS)
        self.assertEqual(parser.cells, row["field_print"])
        self.assertEqual(parser.cells[2:4], ["调整定位销间隙。", "复测待确认。"])
        self.assertEqual(parser.cells[4], references)
        self.assertNotIn("HIDDEN-BILINGUAL-VALUE", document)
        self.assertIn('colspan="5"', export_pdf._document("现场", "", [], "portrait", "field"))

    @unittest.skipUnless(export_pdf._browsers(), "需要本机 Edge、Chrome 或 Chromium")
    def test_empty_worklist_is_a_one_page_pdf_at_byte_zero(self):
        output = export_pdf.render_pdf("现场清单", "", [])
        self.assertEqual(output.tell(), 0)
        data = output.read()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", data)), 1)
        if shutil.which("pdftotext"):
            self.assertIn("当前筛选条件下没有问题。", compact(printed_pages(data)[0]))

    @unittest.skipUnless(export_pdf._browsers() and shutil.which("pdftotext"), "需要浏览器及 Poppler")
    def test_both_paper_directions_repeat_headers_and_fit_fifteen_to_twenty_short_rows(self):
        # 实测首张、所有整页和尾页。 / Verify the first, interior and partial last sheet.
        expected = [f"{i}a" for i in range(1001, 1063)]
        for layout, headers, record in (("bilingual", HEADERS, sample), ("field", FIELD_HEADERS, field_sample)):
            rows = [record(number) for number in expected]
            for orientation in ("portrait", "landscape"):
                with self.subTest(layout=layout, orientation=orientation):
                    data = export_pdf.render_pdf("现场清单", "测试线　共 62 条", rows,
                                                 orientation=orientation, layout=layout).getvalue()
                    width, height = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", data).groups()
                    self.assertEqual(float(width) < float(height), orientation == "portrait")
                    pages = printed_pages(data)
                    numbers = []
                    for index, page in enumerate(pages):
                        text = compact(page)
                        self.assertIn("".join(headers), text)
                        ids = re.findall(r"1\d{3}a", text)
                        # 客户序号及重复后缀应保持同一行。 / Keep each normal issue number and suffix together.
                        self.assertTrue(all(number in page for number in ids))
                        numbers.extend(ids)
                        if index < len(pages) - 1:
                            self.assertTrue(15 <= len(ids) <= 20, (index + 1, len(ids)))
                    self.assertEqual(numbers, expected)

    @unittest.skipUnless(export_pdf._browsers() and shutil.which("pdftotext"), "需要浏览器及 Poppler")
    def test_oversized_cell_prints_every_character_across_pages(self):
        # 单个超长无空格标识和多语言分行共用一格。 / Mix an unbroken ID and multilingual lines in one cell.
        value = ("检查开头\n" + "".join(f"ZK{i:04}Q" for i in range(850)) + "\n"
                 + "\n".join(f"第{i:03}项　Überprüfung onsite." for i in range(160)) + "\n核验末尾")
        for layout, key, headers, column in (("bilingual", "print_fields", HEADERS, 4),
                                             ("field", "field_print", FIELD_HEADERS, 2)):
            with self.subTest(layout=layout):
                row = {key: [""] * len(headers)}
                row[key][column] = value
                previous = {key: ["X001"] + [""] * (len(headers) - 1)}
                data = export_pdf.render_pdf("跨页测试", "测试条件", [previous, row], layout=layout).read()
                pages = printed_pages(data)
                self.assertGreater(len(pages), 2)
                self.assertIn("X001", pages[0])
                self.assertIn("检查开头", compact(pages[0]))
                body = []
                for page in pages:
                    # 清除页码和重复表头，逐字对比。 / Remove only page furniture before comparing all text.
                    text = re.sub(r"(?m)^\s*\d+\s*/\s*\d+\s*$", "", page)
                    text = compact(text).replace("跨页测试测试条件", "").replace("X001", "")
                    self.assertIn("".join(headers), text)
                    body.append(text.replace("".join(headers), ""))
                self.assertEqual("".join(body), compact(value))


if __name__ == "__main__":
    unittest.main()
