"""验证源工作表识别、行动拆分、工位映射及实表基数。 / Test source-sheet detection, action parsing, station mapping and baseline counts."""

# unittest 断言字段值；openpyxl 生成隔离的工作簿样例。 / unittest checks field values; openpyxl creates isolated workbook fixtures.
from collections import Counter
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from opl.excel import (
    StationMap, bilingual, classify_responsible, priority_band, read_book, split_actions,
)


class WorkbookImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "input.xlsx"

    def test_header_order_hidden_rows_invalid_number_and_helper_columns(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "End of Line "
        headers = ["extra", "No.:", "Author:", "Date:", "Problem:", "Station", "Action:",
                   "Status:", "Responsible:", "Due Date:", "负责人", "投产单号", "整改人数",
                   "整改时间", "1代表不清晰和ZF 2代表有措施需要ZF确认 3内部确认 4待投产 5 无计划", "备注", "Remark",
                   "Step 1", "Step 2", "Status changed by:", "Date:", "", "Dropdown", "priority"]
        sheet.append(["项目"])
        sheet.append([])
        sheet.append(headers)
        sheet.append(["保留原始值", 7, "Customer", datetime(2026, 11, 1), "Problem A", "AGV",
                      "18.11.2026 JF: ready", "offen", "HAOSEN", "19/11/2026", "甲/乙", "P001",
                      "1 PLC", "待定", 1, "请客户澄清", "customer remark", "Yes", "No", "JF", "05/11/2026",
                      None, None, "high"])
        sheet.row_dimensions[4].hidden = True
        sheet.append([None, 8, None, None, None, "AGV", None, "offen", "ZF"])
        sheet.append([None, "#REF!", "Customer", "21/11/2026", "damaged ID", "AGV", None, "offen", "HAOSEN"])
        sheet.append([None, 9])  # preallocated serial only
        sheet.append([None, "Legend:", None, None, "A = task"])
        book.save(self.file)
        kind, exact, rows = read_book(self.file)
        self.assertEqual((kind, exact), ("haosen_macro", "End of Line "))
        self.assertEqual([(r["row"], r["no"]) for r in rows], [(4, "7"), (5, "8"), (6, "#REF!")])
        item = rows[0]
        self.assertEqual((item["date"], item["due"], item["changed_on"]),
                         ("2026-11-01", "2026-11-19", ""))  # ambiguous standalone slash date
        self.assertEqual((item["owner"], item["hint"], item["note"]), ("甲/乙", "1", "请客户澄清"))
        self.assertEqual((item["step1"], item["step2"], item["remark"]), ("Yes", "No", "customer remark"))
        self.assertEqual(item["priority"], "")
        self.assertEqual(item["raw"]["A"], "保留原始值")
        self.assertEqual(item["raw"]["U"], "05/11/2026")
        self.assertNotIn("X", item["raw"])

    def test_missing_header_and_conflicting_worksheets(self):
        book = Workbook()
        sheet = book.active
        sheet.append(["No", "Problem", "Station", "Responsible"])
        book.save(self.file)
        with self.assertRaisesRegex(ValueError, "表头"):
            read_book(self.file)
        sheet.append(["No", "Problem", "Station", "Responsible", "Status"])
        book.create_sheet("Other").append(["No", "Problem", "Station", "Responsible", "Status"])
        book.save(self.file)
        with self.assertRaisesRegex(ValueError, "多张"):
            read_book(self.file)

    def test_does_not_read_another_line_or_lockfile(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "Stator housing"
        sheet.append(["No", "Problem", "Station", "Responsible", "Status"])
        book.save(self.file)
        with self.assertRaisesRegex(ValueError, "EOL"):
            read_book(self.file)
        with self.assertRaisesRegex(ValueError, "临时"):
            read_book(Path(self.temp.name) / "~$book.xlsx")


class TextAndStationTests(unittest.TestCase):
    def test_partition_bilingual_text(self):
        self.assertEqual(bilingual("E-Plan in geman language\n\nE-Plan 电气图纸需使用德语。"),
                         ("E-Plan in geman language", "E-Plan 电气图纸需使用德语。"))
        self.assertEqual(bilingual("Install an extra guard.现场需要增设防护。"),
                         ("Install an extra guard.", "现场需要增设防护。"))
        self.assertEqual(bilingual("需要增加防护Conveyor index requires additional protection"),
                         ("Conveyor index requires additional protection", "需要增加防护"))
        self.assertEqual(bilingual("Email: 答复: HVL Missing Robot Functions / Reference Run"),
                         ("Email: 答复: HVL Missing Robot Functions / Reference Run", ""))

    def test_dated_updates_same_day_stay_independent(self):
        text = ("01.04.2026 JF: Ordered. Target delivery 12.04.2026.\n"
                "Check the invoice.\n01.04.2026 JF: Parts received.\n"
                "01.04.2026 JF：已订购，预计四月收到。\n01.04.2026 JF：已到货。")
        steps = split_actions(text)
        self.assertEqual(len(steps), 2)
        self.assertEqual([s["date"] for s in steps], ["2026-04-01", "2026-04-01"])
        self.assertEqual([s["author"] for s in steps], ["JF", "JF"])
        self.assertEqual([s["zh"] for s in steps], ["已订购，预计四月收到。", "已到货。"])
        self.assertIn("12.04.2026.\nCheck the invoice.", steps[0]["original"])

    def test_action_date_ambiguity_measurements_and_continuations(self):
        steps = split_actions("Agreed onsite\n11/12/2026 JF: tests scheduled\n3/4 inch fitting\n"
                              "11/4 现场补测\n2026年11月25日：已核查。")
        self.assertEqual(len(steps), 4)
        self.assertEqual((steps[0]["date"], steps[0]["date_raw"]), ("", ""))
        self.assertEqual((steps[1]["date"], steps[1]["date_raw"]), ("", "11/12/2026"))
        self.assertIn("3/4 inch fitting", steps[1]["original"])
        self.assertEqual((steps[2]["date"], steps[2]["date_raw"]), ("", "11/4"))
        self.assertEqual(steps[3]["date"], "2026-11-25")
        mdy = split_actions("1/19/2026 comment\n11/12/2026TG: ready")
        self.assertEqual((mdy[1]["date"], mdy[1]["author"]), ("2026-11-12", "TG"))
        self.assertEqual(split_actions("19.07.25 -50 dB")[0]["original"], "-50 dB")
        self.assertEqual(split_actions("18.11.2026 New Date: 01.12.2026")[0]["author"], "")

    def test_station_aliases_ambiguous_numbers_and_priority_rules(self):
        with TemporaryDirectory() as root:
            stations = StationMap(root)
        self.assertEqual(stations.resolve("AVO6600 Oil Filling 2 - Station"),
                         stations.resolve("2482-1062"))
        self.assertEqual(stations.resolve("Leakage test 3")["station"], "Leakage Test 2-1")
        self.assertEqual(stations.resolve("2482 1093")["area"], "S2")
        self.assertEqual(stations.resolve("AVO5410 Conveyor / Transfer system CTA4")["area"], "S1")
        self.assertTrue(stations.resolve("2482-1260")["station_review"])
        self.assertTrue(stations.resolve("AVO6500")["station_review"])
        self.assertEqual(stations.resolve("24821221")["station"], "AGV 二期")
        self.assertEqual(stations.resolve("AGV 1221")["machine"], "24821221")
        self.assertEqual(stations.resolve("1220")["machine"], "24821220")
        self.assertEqual(stations.resolve("AVO5710 AGV")["area"], "AGV")
        self.assertEqual(stations.resolve("Automatic Stations General")["station"], "General")
        self.assertEqual(classify_responsible(" zF / ZF "), "zf")
        self.assertEqual(classify_responsible("HAOSEN ／ ZF"), "joint")
        self.assertEqual(classify_responsible("ZF supplier pending"), "unknown")
        self.assertEqual([priority_band(x) for x in ["HIGH+", "medium", "", "unexpected"]],
                         ["High", "Medium", "Low", "待核"])


class DatedCustomerBaselineTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]
    source = root / "EOL/List/ZF/Acceptance_Report_HVL_Gen6_Haosen-2026-11-18.xlsx"

    @unittest.skipUnless(source.is_file(), "本地 11 月 18 日客户快照未提供")
    def test_november_18_customer_snapshot(self):
        kind, _, rows = read_book(self.source)
        self.assertEqual(kind, "zf")
        unclosed = [r for r in rows if r["status"].casefold() != "erledigt"]
        active = [r for r in unclosed if classify_responsible(r["responsible"]) != "zf"]
        self.assertEqual((len(rows), len(unclosed), len(active)), (1236, 421, 403))
        self.assertEqual(Counter(priority_band(r["priority"]) for r in active),
                         {"High": 89, "Medium": 192, "Low": 122})
        sm = StationMap(self.root)
        stations = [sm.resolve(r["station"]) for r in active]
        self.assertEqual(Counter(s["area"] for s in stations),
                         {"S1": 165, "S2": 85, "AGV": 73, "General": 80})
        self.assertEqual(sum(s["station"] == "Oil Filling 2" for s in stations), 23)


if __name__ == "__main__":
    unittest.main()
