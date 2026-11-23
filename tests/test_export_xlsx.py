"""核对现场黑白工作单的打印设置及内容完整性。 / Verify printed layout and field fidelity."""

# unittest 覆盖边界条件，openpyxl 从生成的文件回读原生设置和原文。
# unittest exercises the edge cases; openpyxl reads the actual exported format back.
import unittest
from unittest.mock import patch

from zipfile import ZipFile
from xml.etree import ElementTree

from openpyxl import load_workbook

from opl.export_xlsx import render_xlsx


HEADERS = ("编号", "类别", "工位号和名称", "问题中文", "客户原文", "近期行动", "跟进信息")
FIELD_HEADERS = ("编号及类别", "工位号和名称", "问题描述", "近期行动", "跟进信息")


def issue(number="0007a"):
    return {"print_fields": [
        number, "S2", "24821053\nLeak Test 2", "检查接头", "Check seal",
        "下周复核", "张工\nCW48",
    ]}


class WorkbookExportTests(unittest.TestCase):
    def sheet(self, rows, *, orientation="portrait", title="EOL 现场工作单", filters="区域 S2", layout="bilingual"):
        output = render_xlsx(title, filters, rows, orientation=orientation, layout=layout)
        self.assertEqual(output.tell(), 0)
        book = load_workbook(output)
        self.addCleanup(book.close)
        self.assertEqual(book.sheetnames, ["现场工作单"])
        return book.active

    def test_excel_autofit_and_driver_defaults(self):
        """核查 Excel 实际读取的 OOXML 标记。 / Check the flags used by desktop Excel when opening the workbook."""
        with ZipFile(render_xlsx("", "", [issue()], orientation="landscape")) as archive:
            root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        row = root.find("{*}sheetData/{*}row[@r='4']")
        self.assertIn("ht", row.attrib)
        self.assertEqual(row.get("customHeight"),"0")
        paper = root.find("{*}pageSetup")
        self.assertNotIn("usePrinterDefaults", paper.attrib)
        self.assertEqual((paper.get("paperSize"),paper.get("orientation")), ("9","landscape"))

    def test_scoped_empty_result_and_default_orientation(self):
        book = load_workbook(render_xlsx("", "", []))
        self.addCleanup(book.close)
        self.assertEqual(book.active.page_setup.orientation, "portrait")
        sheet = self.sheet([])
        self.assertEqual(sheet["A1"].value, "EOL 现场工作单")
        self.assertIn("0 项", sheet["A2"].value)
        self.assertIn("区域 S2", sheet["A2"].value)
        self.assertIn("没有匹配", sheet["A4"].value)

    def test_compact_sheet_header_ratios_scoped_caption_and_short_row_pitch(self):
        # 现场版读取独立的五列投影，两行短内容仍有手写空间。 / The compact sheet reads its
        # own projection and retains the requested pitch for short two-line entries.
        fields = ["0007a\n电气", "24821053\nLeak Test 2", "检查接头", "等待备件", "张工\nCW48"]
        row = dict(issue(), field_print=fields)
        for orientation, pitch in (("portrait", 14), ("landscape", 9.5)):
            with self.subTest(orientation=orientation):
                sheet = self.sheet([row], layout="field", orientation=orientation, filters="我方未结 · 共 1 条")
                self.assertEqual(tuple(c.value for c in sheet[3]), FIELD_HEADERS)
                self.assertEqual(tuple(c.value for c in sheet[4]), tuple(fields))
                self.assertEqual(sheet.max_column, 5)
                self.assertEqual(sheet.max_row, 4)
                self.assertEqual(sheet["A2"].value, "我方未结 · 共 1 条")
                self.assertEqual(sheet.print_title_rows, "$3:$3")
                self.assertTrue(sheet.print_area.endswith("$A$1:$E$4"))
                self.assertTrue(sheet.page_setup.blackAndWhite)
                widths = [sheet.column_dimensions[c].width for c in "ABCDE"]
                for actual, ratio in zip(widths, (.09, .14, .35, .25, .17)):
                    self.assertAlmostEqual(actual / sum(widths), ratio, places=3)
                self.assertAlmostEqual(sheet.row_dimensions[4].height * 25.4 / 72, pitch, places=2)
                self.assertEqual(sheet["C4"].font.sz, 9)

    def test_multiple_production_ids_stay_in_one_followup_cell(self):
        """两个现场长单号可在同一格内逐行打印。 / Keep both full production IDs together in the follow-up column."""
        info = "存量待梳理\n负责人　王良昊/林伟\n计划　待安排\n投产单号\nYT2026062407006\nYT2026081207007"
        row = {"field_print": ["890\n机械","24821071\nHigh Voltage Test 1","核对继电器","更新图纸",info]}
        for orientation in ("portrait", "landscape"):
            sheet = self.sheet([row], layout="field", orientation=orientation)
            self.assertEqual(sheet.max_row, 4)
            self.assertEqual(sheet["E4"].value, info)

    def test_compact_continuations_repeat_id_and_keep_complete_problem_and_action(self):
        source = "=问题 01\r\n" + "大件工装 " * 9000 + "\n问题尾行\n"
        action = "\n".join(f"第 {i} 项整改反馈" for i in range(90))
        following = "状态：待复核\n" + "负责人／计划\n" * 51 + "张工\nCW48"
        row = {"field_print": ["007a\n机械", "24821053\nEOL Test", source, action, following]}
        sheet = self.sheet([row], layout="field")
        self.assertGreater(sheet.max_row, 4)
        self.assertEqual(sheet["A4"].value, "007a\n机械")
        self.assertTrue(all(sheet.cell(i, 1).value == "007a" for i in range(5, sheet.max_row + 1)))
        self.assertEqual("".join(sheet.cell(i, 3).value for i in range(4, sheet.max_row + 1)), source)
        self.assertTrue(all(sheet.cell(i, 3).data_type == "s" for i in range(4, sheet.max_row + 1)))
        self.assertEqual("".join(sheet.cell(i, 4).value or "" for i in range(4, sheet.max_row + 1)), action)
        self.assertEqual("".join(sheet.cell(i, 5).value or "" for i in range(4, sheet.max_row + 1)), following)
        self.assertTrue(all(sheet.cell(i, 2).value is None for i in range(5, sheet.max_row + 1)))

    def test_one_black_and_white_a4_sheet_in_each_orientation(self):
        # 同一批短项目，两种方向均可单页宽打印。 / Short items print one page wide in both layouts.
        rows = [issue(str(i)) for i in range(1, 21)]
        for orientation in ("portrait", "landscape"):
            with self.subTest(orientation=orientation):
                sheet = self.sheet(rows, orientation=orientation)
                self.assertEqual(tuple(c.value for c in sheet[3]), HEADERS)
                self.assertEqual(sheet.max_column, 7)
                self.assertEqual(sheet.max_row, 23)
                self.assertEqual(sheet.freeze_panes, "B4")
                self.assertEqual(sheet.print_title_rows, "$3:$3")
                self.assertTrue(sheet.print_area.endswith("$A$1:$G$23"))
                self.assertTrue(sheet.sheet_properties.pageSetUpPr.fitToPage)
                self.assertEqual(str(sheet.page_setup.paperSize), sheet.PAPERSIZE_A4)
                self.assertEqual(sheet.page_setup.orientation, orientation)
                self.assertEqual(sheet.page_setup.fitToWidth, 1)
                self.assertEqual(sheet.page_setup.fitToHeight, 0)
                self.assertTrue(sheet.page_setup.blackAndWhite)
                for row in sheet.rows:
                    for cell in row:
                        if cell.value is None:
                            continue
                        self.assertEqual(cell.font.color.rgb, "FF000000")
                        self.assertEqual(cell.fill.fgColor.rgb, "FFFFFFFF")
                        self.assertTrue(cell.alignment.wrap_text)
                        for side in (cell.border.left, cell.border.right, cell.border.top, cell.border.bottom):
                            if side and side.style:
                                self.assertEqual(side.color.rgb, "FF000000")
                # 用纸张可打印高度检查短项目密度。 / Check short-item density from
                # the printable paper height, top banners, repeated header and body row pitch.
                page_points = (297 if orientation == "portrait" else 210) / 25.4 * 72
                usable = page_points - 72 * (sheet.page_margins.top + sheet.page_margins.bottom)
                body = sheet.row_dimensions[4].height
                for top in (sum(sheet.row_dimensions[i].height for i in (1, 2, 3)),
                            sheet.row_dimensions[3].height):
                    self.assertIn(int((usable - top) // body), range(15, 21))

    def test_obeys_projection_order_and_preserves_every_literal_value(self):
        row = issue("0007a")
        row["print_fields"][3:7] = ["=1+1", "#REF!", "+49 123", "现场"]
        row["sections"] = [("档案专属信息", [("记录", "不应出现在工作单的详细内容")])]
        row["attachments"] = [{"name": "other.jpg", "path": "unused"}]
        sheet = self.sheet([row, issue("0007b")], title="=Title")
        self.assertEqual(tuple(c.value for c in sheet[4]), tuple(row["print_fields"]))
        self.assertEqual(sheet["A5"].value, "0007b")
        self.assertEqual(sheet["A1"].data_type, "s")
        self.assertEqual(sheet["D4"].data_type, "s")
        self.assertEqual(sheet["E4"].data_type, "s")
        self.assertFalse(any(c.data_type == "f" for r in sheet.rows for c in r))
        self.assertFalse(any(c.hyperlink for r in sheet.rows for c in r))

    def test_complete_wrapped_text_and_continuations_in_both_orientations(self):
        source = ("=原始问题  End of line – Prüfstand, Überprüfung der Rückmeldung.  \r\n"
                  + ("待客户确认 / Anlage 01. " * 204) + "\n" * 510 + " \t"
                  + "SERIAL_391X" * 3450 + "\n" + "\u200d" * 34000 + "🚙" * 150 + " 尾行  \n")
        for orientation in ("portrait", "landscape"):
            with self.subTest(orientation=orientation):
                case = issue("0123b")
                case["print_fields"][4] = source
                case["print_fields"][5] = "\n".join(f"第 {i} 次复核" for i in range(80))
                sheet = self.sheet([case], orientation=orientation)
                self.assertGreater(sheet.max_row, 10)
                for col in range(2, 8):
                    self.assertEqual("".join(sheet.cell(i, col).value or "" for i in range(4, sheet.max_row + 1)),
                                     case["print_fields"][col - 1])
                for row in range(4, sheet.max_row + 1):
                    self.assertEqual(sheet.cell(row, 1).value, "0123b")
                    fragment = sheet.cell(row, 5).value or ""
                    self.assertLessEqual(len(fragment.encode("utf-16-le")) // 2, 32767)
                    self.assertGreaterEqual(sheet.row_dimensions[row].height, 13.25 * (1 + fragment.count("\n")))
                    self.assertLessEqual(sheet.row_dimensions[row].height, 409)

    def test_long_scope_and_title_are_complete_before_repeatable_header(self):
        title, filters = "本周整改与现场状态" * 100, "owner/area/CW48 " * 800
        sheet = self.sheet([issue()], title=title, filters=filters)
        rows = list(sheet.rows)
        self.assertEqual("".join(r[0].value for r in rows if r[0].style == "report_title"), title)
        self.assertEqual("".join(r[0].value for r in rows if r[0].style == "report_scope"),
                         f"共 1 项　筛选条件　{filters}")
        header = next(r[0].row for r in rows if r[0].style == "column_heading")
        self.assertEqual(sheet.print_title_rows, f"${header}:${header}")

    def test_invalid_requests_and_native_excel_row_limit(self):
        # 单张 Excel 达到原生行数上限时须显式报告。 / Explicitly report a single-sheet overflow.
        with self.assertRaisesRegex(ValueError, "打印方向"):
            render_xlsx("", "", [], orientation="sideways")
        with self.assertRaisesRegex(ValueError, "打印布局"):
            render_xlsx("", "", [], layout="unknown")
        with self.assertRaisesRegex(ValueError, "五项"):
            render_xlsx("", "", [{"field_print": ["missing"]}], layout="field")
        with self.assertRaisesRegex(ValueError, "七项"):
            render_xlsx("", "", [{"print_fields": ["missing"]}])
        with patch("opl.export_xlsx._MAX_ROWS", 4), self.assertRaisesRegex(ValueError, "行数限制"):
            render_xlsx("", "", [issue("1"), issue("2")])


if __name__ == "__main__":
    unittest.main()
