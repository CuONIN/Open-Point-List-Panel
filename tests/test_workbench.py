"""验证问题库、导入、筛选、现场流转与备份的端到端行为。 / Exercise import, querying, workflow and backup end to end."""

# Flask test_client 在临时数据库中执行真实请求；unittest 检查结果。 / Flask's test client runs real requests against a temporary database; unittest checks results.
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook
from werkzeug.datastructures import MultiDict

from opl import importer, maintenance
from opl.exports import print_data
from opl.store import connect, decode, dump
from opl.web import create_app


HEADERS = ("No.", "Date:", "Station", "Author", "Department", "Category", "Prio",
           "Problem", "Action", "Responsible", "Due date", "Status")


def customer_rows():
    return [
        [1405,"2025-01-01","Greasing Station","JF","Electrical","PLC","High+",
         "Verify T1 signal\n核对 T1 信号","02.01.2025 JF: Reset T1.\n02.01.2025 JF: Recheck.","HAOSEN","2025-01-31","offen"],
        [1405,"2025-01-01","Oil Filling 2","AL","Mechanical","Pump","Medium",
         "Verify T2 valve\n核对 T2 阀","02.01.2025 AL: Order T2.","HAOSEN / ZF","","in Arbeit"],
        [16,"2025-01-01","AGV","TB","Logistics","","","AGV marker\nAGV 标记","","ZF","","offen"],
        [18,"2025-01-01","General","JF","Mechanical","","","Drawings\n图纸","","HAOSEN","","erledigt"],
    ]


def workbook(path, rows, title="List of defects", headers=HEADERS):
    wb = Workbook()
    sheet = wb.active
    sheet.title = title
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    wb.save(path)
    wb.close()


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = create_app(self.root).test_client()
        self.conn = connect(self.root)
        self.addCleanup(self.conn.close)
        self.rows = customer_rows()
        self.import_version("2025-01-02", self.rows)
        self.a, self.b = [r[0] for r in self.conn.execute("SELECT id FROM issues WHERE base='1405' ORDER BY id")]

    def import_version(self, day, rows):
        p = self.root / f"ZF-{day}.xlsx"
        workbook(p, rows)
        return importer.import_book(self.conn, self.root, p)

    def get(self, url):
        response = self.client.get("/api/" + url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.json

    def change(self, uid, action, day="2025-01-06", status=200, **values):
        rev = self.get(f"issues/{uid}")["revision"]
        data = {"actor":"现场甲", "day":day, "revision":rev, **values}
        response = self.client.post(f"/api/issues/{uid}/{action}", json=data)
        self.assertEqual(response.status_code, status, response.data)
        return response.json

    def move(self, to, day="2025-01-06", **fields):
        self.change(self.a, "transition", day, to=to, owner="李工", **fields)

    def test_overview_latest_state_after_customer_archive_import(self):
        """概览按截止日归类，保留此前流转。 / Classify by latest state while preserving prior transitions."""
        self.move("working", executor="电工", note="开工")
        self.change(self.b, "transition", to="ready", owner="李工", note="完成待报")
        self.rows[0][-1] = "erledigt"
        self.rows[1][9] = "ZF"
        self.import_version("2025-01-08", self.rows)
        summary = self.get("overview?week=2025-W02")
        for state, ids in (("working", []), ("ready", []), ("closed", [self.a]), ("zf_only", [self.b])):
            with self.subTest(state=state):
                query = {"week":"2025-W02", "mode":"latest", "scope":"all", "state":state}
                rows = self.client.get("/api/issues", query_string=query).json["items"]
                self.assertEqual([r["id"] for r in rows], ids)
                self.assertTrue(all(r["state"] == state for r in rows))
                self.assertEqual(summary["counts"][state], len(ids))
                self.assertEqual([r["id"] for r in print_data(self.conn, query)[1]], ids)
        before = self.get("overview?week=2025-W02&as_of=2025-01-07")["counts"]
        self.assertEqual((before["working"], before["ready"], before["closed"], before["zf_only"]), (1,1,0,0))
        self.assertEqual({e["to_state"] for e in summary["events"]}, {"working","ready","closed","zf_only"})

    def test_scopes_multiple_tags_and_whole_filtered_export(self):
        self.assertEqual(self.get("issues")["total"], 2)
        self.assertEqual(self.get("issues?scope=customer")["total"], 3)
        self.assertEqual(self.get("issues?scope=all")["total"], 4)
        self.assertEqual(self.get("issues?zf=joint&zf=pure&scope=all")["total"], 2)
        self.assertEqual(self.get("issues?area=S1&area=S2&priority=Medium")["items"][0]["id"], self.b)
        query = MultiDict([("q","1405"),("area","S1"),("area","S2"),("priority","High"),("priority","Medium"),
                           ("limit","1"),("page","2")])
        result = self.client.get("/api/issues", query_string=query).json
        self.assertEqual((len(result["items"]),result["total"],result["page"]), (1,2,2))
        self.assertEqual(sum(r["total"] for r in result["stations"]), 2)
        caption, rows = print_data(self.conn,query)
        self.assertEqual({r["number"] for r in rows},{"1405a","1405b"})
        self.assertIn("S1 或 S2", caption)
        empty = self.get("issues?priority=High&area=AGV")
        self.assertEqual((empty["total"],empty["pages"]), (0,1))

    def test_meeting_cycle_week_filters_and_optimistic_writes(self):
        initial = self.get(f"issues/{self.a}")
        fields = self.get(f"issues/{self.a}/transition?to=planned")["fields"]
        self.assertEqual([f["key"] for f in fields], ["owner","plan","note"])
        self.move("planned", day="2025-01-03", plan="2025-W02", note="校准", status=200)
        for link in ("transition","plan","profile"):
            self.change(self.a,link,status=409,revision=initial["revision"])
        self.change(self.a,"transition",to="reported",owner="李工",status=400)
        self.move("working",executor="电气组",note="开工")
        self.move("ready",day="2025-01-07",note="实测完成，波形附后")
        self.move("reported",day="2025-01-08",contact="会议纪要 A-3")
        self.move("rework",day="2025-01-09",outcome="需再测",note="复测")
        self.assertEqual(self.get("issues?mode=occurred&week=2025-W02&state=reported&state=rework")["total"],1)
        self.assertEqual(self.get("issues?mode=asof&as_of=2025-01-08&state=reported")["total"],1)
        self.assertEqual(self.get("issues?mode=latest&week=2025-W02&state=rework")["total"],1)
        self.assertEqual(self.get("issues?mode=unreported&week=2025-W02")["total"],1)
        self.move("working",day="2025-01-10",executor="电气组",note="重测")
        self.move("ready",day="2025-01-10",note="更新报告")
        self.move("reported",day="2025-01-10",contact="客户会议")
        self.assertEqual(self.get("issues?mode=unreported&week=2025-W02")["total"],0)
        self.assertEqual(self.get(f"issues/{self.a}")["owner"],"李工")
        self.assertEqual(self.get("issues?owner=其他同事&owner=李工")["total"],1)
        self.assertNotIn("tracker",self.get("config")["filters"])
        self.assertIn("实测完成",self.get("report?week=2025-W02")["text"])

    def test_number_lists_blank_choices_and_export_match_the_grid(self):
        """核查同号后缀、空选、空值与导出。 / Exercise exact lists, blank selections and printed results."""
        def numbers(params):
            return [r["number"] for r in self.client.get("/api/issues", query_string=params).json["items"]]
        query = {"scope":"all", "q":"#1405， 16,1405A,16,", "sort":"number_asc"}
        self.assertEqual(numbers(query), ["16","1405a","1405b"])
        self.assertEqual([r["number"] for r in print_data(self.conn,query)[1]], numbers(query))
        self.assertEqual(numbers({"scope":"all","q":"1405B, 18"}), ["1405b","18"])
        self.assertEqual(numbers({"scope":"all","q":"140, 8"}), [])
        self.assertEqual(numbers({"area":"S2","q":"1405,18"}), ["1405b"])
        self.assertEqual(numbers({"scope":"all","category":"__blank__"}), ["18","16"])
        self.assertEqual(len(numbers({"owner":"__blank__"})), 2)
        for key in ("state","priority","owner","zf"):
            self.assertEqual(numbers({key:"__none__"}), [])
        config = self.get("config")
        self.assertEqual(config["pure_responsibles"], ["ZF"])
        self.assertIn("__blank__", config["filters"]["owner"])
        self.assertIn("closed", config["states"])

    def test_weekly_plan_union_uses_latest_state_at_the_week_cutoff(self):
        """计划与当周进入整改取并集，检查跨周和同日多次流转。 / Combine plans with the latest working state at each cutoff."""
        self.move("planned", day="2025-01-03", plan="2025-W02", note="本周排期")
        self.move("working", day="2025-01-06", executor="现场", note="按计划开工")
        self.change(self.b,"plan",day="2025-01-06",plan="2025-W04",owner="甲",note="原排期")
        self.change(self.b,"transition",day="2025-01-07",to="working",owner="乙",executor="现场",note="提前开工")
        weekly = "week=2025-W02"
        plan = self.get("overview?"+weekly)["plans"]
        self.assertEqual({r["id"]:(r["is_week_plan"],r["working_since"]) for r in plan},
                         {self.a:(True,"2025-01-06"),self.b:(False,"2025-01-07")})
        self.assertEqual(len(plan),2)
        self.assertEqual(self.get("issues?mode=planned&scope=all&"+weekly)["total"],2)
        report = self.get("report?"+weekly)["text"]
        self.assertIn("进入整改 2025-01-07",report)
        self.assertNotIn("2025-W04",report)
        self.assertEqual(self.get("overview?week=2025-W03")["plans"],[])
        self.change(self.b,"transition",day="2025-01-07",to="ready",owner="乙",note="当天已完成")
        self.assertEqual([r["id"] for r in self.get("overview?"+weekly)["plans"]],[self.a])
        self.assertEqual(self.get("issues?mode=unreported&"+weekly)["total"],1)
        self.move("ready", day="2025-01-13",note="下周完成")
        self.assertEqual(self.get("overview?"+weekly)["plans"][0]["working_since"],"2025-01-06")

    def test_close_with_customer_evidence_then_import_lagging_and_reopening_versions(self):
        """现场凭据、关闭日和重开的客户依据均保留。 / Preserve field evidence until the customer explicitly reopens."""
        fields = self.get(f"issues/{self.a}/transition?to=closed")["fields"]
        self.assertEqual([field["key"] for field in fields], ["evidence"])
        self.change(self.a,"transition",to="closed",status=400)
        self.change(self.a,"transition",to="closed",day="2025-01-01",evidence="会议纪要",status=400)
        self.change(self.a,"transition",to="closed",day="2025-01-08",evidence="ZF 验收纪要 §3")
        detail = self.get(f"issues/{self.a}")
        first = detail["events"][0]
        self.assertEqual((detail["state"],detail["status"],first["day"],first["actor"],first["kind"]),
                         ("closed","offen","2025-01-08","现场甲","manual_closed"))
        self.assertEqual(first["payload"]["evidence"],"ZF 验收纪要 §3")
        self.assertEqual(self.get("issues?state=closed&q=1405")["total"],1)
        self.assertEqual(self.get("issues?q=1405")["total"],1)
        self.rows[0][8] = "Updated general note"
        for day in ("2025-01-07","2025-01-09"):
            self.rows[0][8] += " +"
            imported = self.import_version(day,self.rows)
            self.assertEqual(imported["summary"]["reopened"],0)
        self.rows[0][-1] = "erledigt"
        self.assertEqual(self.import_version("2025-01-10",self.rows)["summary"]["closed"],0)
        self.assertEqual(len(self.get(f"issues/{self.a}")["events"]),2)
        self.rows[0][-1] = "offen"
        self.rows[0][8] = "Customer reopened: please repeat the test."
        self.assertEqual(self.import_version("2025-01-11",self.rows)["summary"]["reopened"],1)
        self.assertEqual(self.get(f"issues/{self.a}")["state"],"triage")
        self.assertEqual(self.get("overview?week=2025-W02")["totals"]["closed"],1)
        pure = self.get("issues?state=zf_only")["items"][0]
        self.assertEqual(self.get(f"issues/{pure['id']}/transition")["allowed"],["closed"])

    def test_customer_versions_links_keep_ids_and_independent_closures(self):
        self.change(self.a,"relations",kind="detail",other=self.b,note="独立验证")
        self.change(self.b,"relations",kind="detail",other=self.a,status=400)
        self.rows[0][-1] = "erledigt"
        updated = self.import_version("2025-01-12", list(reversed(self.rows)))
        self.assertEqual(updated["summary"]["closed"],1)
        self.assertEqual(self.get(f"issues/{self.a}")["number"],"1405a")
        self.assertEqual(self.get(f"issues/{self.b}")["state"],"triage")
        self.assertEqual(self.get(f"issues/{self.b}")["relations"][0]["issue_id"],self.a)
        self.assertTrue(importer.import_book(self.conn,self.root,self.root/"ZF-2025-01-12.xlsx")["duplicate"])
        self.rows[0][-1] = "in Arbeit"
        self.rows[0][9] = "ZF"
        self.import_version("2025-01-15",self.rows)
        self.assertEqual(self.get(f"issues/{self.a}")["state"],"zf_only")
        self.rows[0][9] = "HAOSEN / ZF"
        self.rows[0][8] += "\n16.01.2025 JF: Verify coupling END-T3."
        self.import_version("2025-01-16",self.rows)
        older = self.import_version("2025-01-07",self.rows[:1])
        self.assertTrue(older["historical"])
        detail = self.get(f"issues/{self.a}")
        self.assertEqual(detail["state"],"triage")
        self.assertEqual(len(detail["actions"]),3)
        self.assertEqual(self.get("issues?mode=asof&as_of=2025-01-14&state=closed&scope=all")["total"],2)
        _,old = print_data(self.conn,{"mode":"asof","as_of":"2025-01-08","q":"1405a"})
        self.assertNotIn("END-T3",str(old[0]["print_fields"]))
        _,new = print_data(self.conn,{"q":"1405a"})
        self.assertIn("END-T3",str(new[0]["print_fields"]))

    def test_plan_adjustments_legacy_owner_feedback_tasks_and_backup(self):
        raw = decode(self.conn.execute("SELECT local FROM issues WHERE id=?",(self.a,)).fetchone()[0])
        raw.update(owner="原表负责人", tracker="现场负责人")
        self.conn.execute("UPDATE issues SET local=? WHERE id=?",(dump(raw),self.a))
        self.assertEqual(self.get(f"issues/{self.a}")["owner"],"现场负责人")
        self.change(self.a,"plan",tracker="新负责人",plan="2025-W03",note="下一周停线")
        self.change(self.a,"profile",owner="甲/乙",people="2 PLC",work_time="5 h",order_no="PO-123")
        self.assertEqual(self.get(f"issues/{self.a}")["plan_history"][0]["owner"],"新负责人")
        self.assertEqual(self.get("issues?owner=乙")["total"],1)
        rev = self.get(f"issues/{self.a}")["revision"]
        reply = self.client.post(f"/api/issues/{self.a}/feedback", data={
            "actor":"质检", "revision":str(rev), "day":"2025-01-10", "note":"验收记录 END-42",
            "files":(BytesIO(b"evidence complete"),"Report.txt")})
        self.assertEqual(reply.status_code,200,reply.data)
        self.assertEqual(self.get(f"issues/{self.a}")["state_day"],"2025-01-02")
        res = self.client.post("/api/tasks",json={"kind":"cycle","title":"节拍复测","week":"2025-W02",
              "owner":"质检","issue_ids":[self.a],"details":{"target":28,"after":27.5,"conditions":"暖机"}})
        self.assertEqual(res.status_code,200,res.data)
        _,rows = print_data(self.conn,{"q":"1405a"})
        for value in ("甲/乙","验收记录 END-42","2025-W03"):
            self.assertIn(value,str(rows[0]["print_fields"]))
        archive = maintenance.backup(self.conn,self.root)
        copy = maintenance.restore(archive,self.root/"restored")
        with closing(connect(copy)) as restored:
            path = restored.execute("SELECT path FROM files LIMIT 1").fetchone()[0]
            self.assertEqual((copy/path).read_bytes(),b"evidence complete")
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_phase_report_cutoff_number_suffixes_and_overlapping_activity(self):
        """按历史截点计算周活动并集，保留 1340 的不同后缀。 / Count each eligible issue once at the requested snapshot."""
        self.rows[0][0] = self.rows[1][0] = 1340
        extra = [1341,"2025-01-03","Oil Filling 2","ZZ","Spare","","","Extra fixture","","HAOSEN","","offen"]
        self.import_version("2025-01-03", [*self.rows, extra])
        self.change(self.b,"transition",day="2025-01-05",to="working",owner="乙",executor="乙",note="上周开工")
        self.move("planned",plan="2025-W02",note="排进本周")
        self.move("ready",day="2025-01-07",note="整改完成")
        self.move("reported",day="2025-01-08",contact="客户会议")
        self.rows[0][-1] = "erledigt"
        self.import_version("2025-01-09", [*self.rows, extra])
        path = "report?week=2025-W02&as_of="
        def section(day):
            text = self.get(path + day)["text"]
            return text.split("分期统计（客户编号 ≤ 1340，含同号后缀）")[1].split("本周状态更新")[0]
        wed = section("2025-01-08")
        thu = section("2025-01-09")
        self.assertIn("一期（S1 + General）\n  问题总数 3 / 我方未结 1", wed)
        self.assertIn("二期（S2）\n  问题总数 1 / 我方未结 1", wed)
        self.assertEqual(wed.count("本周计划、整改、关闭或上报（去重） 1"),2)
        self.assertIn("本周客户关闭 0 / 截至日已上报待核查 1", wed)
        self.assertIn("一期（S1 + General）\n  问题总数 3 / 我方未结 0", thu)
        self.assertIn("本周客户关闭 1 / 截至日已上报待核查 0", thu)
        self.assertEqual(thu.count("本周计划、整改、关闭或上报（去重） 1"),2)
        self.assertEqual(self.client.get("/api/"+path+"2025-01-13").status_code,400)

    def test_customer_author_sort_order_and_unified_agv(self):
        """AGV 汇总同名工位，保留两个机器号。 / Group AGV by station while preserving both equipment IDs."""
        self.rows[0][2:4] = ["24821220","Gleser, Tobias (ZF SBR)"]
        self.rows[1][2:4] = ["24821221","Feber, Andreas (ZF SBR)"]
        self.rows[0][0], self.rows[1][0] = 9, 100
        self.rows[0][7], self.rows[1][7] = "AGV first phase scanner", "AGV second phase relay"
        self.import_version("2025-01-03", self.rows)
        base = "issues?author=Tobias+Gleser&author=Andreas+Feber&area=AGV"
        def numbers(query):
            return [r["number"] for r in self.get(query)["items"]]
        self.assertEqual(numbers(base+"&sort=number_asc"), ["9","100"])
        self.assertEqual(numbers(base+"&sort=number_desc"), ["100","9"])
        self.assertEqual(numbers(base+"&phase=S2&sort=number_asc"), ["9","100"])
        self.assertEqual(numbers(base+"&station=AGV+二期&sort=number_asc"), ["9","100"])
        self.assertEqual(numbers(base+"&machine=24821221"), ["100"])
        result = self.get(base)
        self.assertEqual([(r["station"], r["total"]) for r in result["stations"]], [("AGV",2)])
        self.assertTrue(all(r["station"] == "AGV" and "phase" not in r for r in result["items"]))
        self.assertEqual(numbers(base.replace("area=AGV","area=S1")), ["9"])
        self.assertEqual(numbers(base.replace("area=AGV","area=S2")), ["100"])
        self.assertNotIn("AGV", self.get("config")["filters"]["area"])
        self.assertNotIn("phases", self.get("overview"))
        self.assertNotIn("phase", self.get("config")["filters"])
        self.assertEqual(self.get("issues?author=Gleser%2C+Tobias+%28ZF+SBR%29")["total"], 1)
        self.assertEqual(self.get("issues?sort=number_desc&q=1405")["items"][0]["number"], "1405b")
        self.assertEqual(self.get("issues?sort=number_asc&q=1405")["items"][0]["number"], "1405a")
        self.assertEqual(self.client.get("/api/issues/export.xlsx?orientation=invalid").status_code,400)
        self.assertEqual(self.client.get("/api/issues/export.pdf?layout=invalid").status_code,400)

    def test_upgrade_preserves_evidence_and_excludes_customer_from_internal_assignments(self):
        """升级可以重复执行，工位快照与现场时间线不变。 / Repeated upgrades preserve source evidence and the timeline."""
        from opl.store import initialize
        from opl.people import internal_owner
        raw = decode(self.conn.execute("SELECT customer FROM issues WHERE id=?",(self.a,)).fetchone()[0])
        raw.update(machine="24821221",station="24821221",area="S1")
        self.conn.execute("UPDATE issues SET customer=?,local=? WHERE id=?",
                          (dump(raw),dump({"owner":"Tobias/张兆敏"}),self.a))
        self.conn.execute("PRAGMA user_version=1")
        evidence = self.conn.execute("SELECT fields FROM source_rows WHERE issue_id=?",(self.a,)).fetchone()[0]
        events = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        initialize(self.conn)
        once = self.get(f"issues/{self.a}")
        initialize(self.conn)
        twice = self.get(f"issues/{self.a}")
        self.assertEqual((twice["owner"],twice["station"],twice["machine"],twice["revision"]),
                         ("张兆敏","AGV","24821221",once["revision"]))
        self.assertEqual(internal_owner("Tobias Gleser (ZF SBR) / 甲"),"甲")
        self.assertEqual(self.conn.execute("SELECT fields FROM source_rows WHERE issue_id=?",(self.a,)).fetchone()[0],evidence)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],events)
        self.change(self.a,"transition",to="ready",owner="Tobias",note="现场",status=400)
        self.assertNotIn("Tobias",self.get("config")["filters"]["owner"])

    def test_separate_print_cells_and_linked_orders(self):
        """纸面、清单均使用可追溯的关联编号。 / Check printed cells and grid links, including filtered-out targets."""
        closed = self.conn.execute("SELECT id FROM issues WHERE base='18'").fetchone()[0]
        self.change(self.a,"relations",kind="detail",other=self.b,note="明细")
        self.change(self.b,"relations",kind="detail",other=closed,note="下级")
        self.change(self.a,"relations",kind="duplicate",other=closed,note="同一现象")
        self.change(self.a,"profile",owner="测试组",order_no="YT-001\nYT-002")
        self.move("planned",plan="2025-W02",note="现场校准")

        item = self.get("issues?q=1405a")["items"][0]
        self.assertEqual({(r["number"],r["kind"],r["direction"]) for r in item["related"]},
                         {("1405b","detail","out"),("18","duplicate","out"),("18","detail","descendant")})
        self.assertEqual(next(r for r in item["related"] if r["number"]=="1405b")["id"],self.b)
        _, rows = print_data(self.conn,{"q":"1405a"})
        for key in ("field_print","print_fields"):
            cells=rows[0][key]
            self.assertIn("核对 T1 信号",cells[2] if key=="field_print" else cells[3])
            self.assertNotIn("现场校准",cells[2] if key=="field_print" else cells[3])
            self.assertIn("现场校准",cells[-2])
            for value in ("已列入计划","李工","2025-W02","YT-001\nYT-002","#1405b","#18"):
                self.assertIn(value,cells[-1])
        old = self.get("issues?mode=asof&as_of=2025-01-03&q=1405a")["items"][0]
        self.assertEqual(old["related"],[])
        _, before = print_data(self.conn,{"mode":"asof","as_of":"2025-01-03","q":"1405a"})
        self.assertNotIn("投产单",before[0]["field_print"][-1])
        self.assertNotIn("#1405b",before[0]["field_print"][-1])

    def test_preview_commit_haosen_and_invalid_files(self):
        path = self.root / "内部补充.xlsx"
        supplement = self.rows[0].copy()
        supplement[0] = 900
        workbook(path, [supplement + ["甲/乙","电气 2 人","现场见图"]], "End of Line ", HEADERS+("负责人","整改人数","备注"))
        with path.open("rb") as f:
            res = self.client.post("/api/imports/preview",data={"files":(f,path.name)})
        self.assertEqual(res.status_code,200,res.data)
        preview = res.json["items"][0]
        self.assertEqual((preview["kind"],preview["summary"]["matched"]),("haosen",1))
        res = self.client.post("/api/imports/commit", json={"token":preview["token"]})
        self.assertEqual(res.status_code,200,res.data)
        self.assertEqual(self.get(f"issues/{self.a}")["people"],"电气 2 人")
        response = self.client.post("/api/imports/preview", data={"files":(BytesIO(b"invalid"),"bad.xlsx")})
        self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(self.get("issues")["total"],2)


if __name__ == "__main__":
    unittest.main()
