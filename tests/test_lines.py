"""验证线体隔离、定子表映射与幂等同步。 / Verify isolated workspaces, PAL mappings and repeatable imports."""

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from opl import importer, maintenance
from opl.lines import STATOR
from opl.store import connect
from opl.web import create_app
from test_workbench import workbook, customer_rows


def stator_book(path, rows, local=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "Stator housing" if local else "Open points list"
    sheet["I3"] = "PA stator housing"
    headers = ["No.:","Date:","Author:","Station number","Variant","Priority","Problem:","Action:",
               "Responsible:","Date","Status:","Status changed by:","Date:"]
    if local:
        headers += ["整改人数","负责人","整改时间","投产单号"]
    for column, header in enumerate(headers,2):
        sheet.cell(10,column,header)
    for row in rows:
        sheet.append([None,*row])
    book.save(path)
    book.close()


class MultiLineTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.client = create_app(self.root).test_client()
        self.db = connect(self.root,STATOR)
        self.addCleanup(self.db.close)
        self.base = "/lines/pal_stator/api/"
        self.rows = [[367,"2025-01-02","Iseni","","201-050/051","High","First fixture","Customer request",
                      "Haosen","2025-01-25","offen","Iseni","2025-01-02"],
                     [367,"2025-01-02","Klein","Elevator","BMW","Low","Lift door","","ZF/Haosen","","in Arbeit","",""]]
        self.customer = self.root/"PAL-22-09-2025.xlsx"
        stator_book(self.customer,self.rows)
        importer.import_book(self.db,self.root,self.customer,line=STATOR)
        self.local = self.root/"local.xlsx"

    def get(self, endpoint):
        result = self.client.get(self.base+endpoint)
        self.assertEqual(result.status_code,200,result.data)
        return result.json

    def test_isolation_and_report(self):
        eol = self.root/"EOL-2025-01-02.xlsx"
        data = customer_rows()
        data[0][0] = 367
        workbook(eol,data)
        with closing(connect(self.root)) as other:
            importer.import_book(other,self.root,eol)
        self.assertEqual(self.get("issues/1")["problem"],"First fixture")
        self.assertNotEqual(self.client.get("/api/issues/1").json["problem"],"First fixture")
        for station in ["201-050","201-051","Elevator"]:
            self.assertEqual(self.get("issues?scope=all&station="+station)["total"],1)
        self.assertEqual(self.get("issues?station=201-050&station=201-051")["total"],1)
        self.assertNotIn("area",self.get("config")["filters"])
        self.assertEqual(self.get("issues?author=Arta+Iseni")["total"],1)
        self.assertEqual(self.get("overview")["totals"]["ours"],2)
        report = self.get("report")["text"]
        self.assertIn("工位汇总",report)
        self.assertNotIn("分期统计",report)
        res=self.client.post(self.base+"tasks",json=dict(title="定子任务",kind="cycle",week="2025-W40",
                             owner="甲",issue_ids=[1],details={"target":12}))
        self.assertEqual(res.status_code,200,res.data)
        self.assertEqual(self.client.get("/api/tasks").json["items"],[])
        self.assertEqual(len(self.get("tasks")["items"]),1)
        with eol.open("rb") as f:
            res=self.client.post(self.base+"imports/preview",data={"files":(f,eol.name)})
        self.assertEqual(res.status_code,400)
        with self.customer.open("rb") as f:
            res=self.client.post("/api/imports/preview",data={"files":(f,self.customer.name)})
        self.assertEqual(res.status_code,400)
        self.assertIn("pal_stator",self.get("issues/1")["snapshots"][0]["url"])

    def test_shifted_description_only_imports_anchored_action(self):
        rows=[r.copy() for r in self.rows]
        rows[0][6]="Lift door"
        rows[0][7]="05.11.2025 ：待确认\nCustomer request"
        rows[0]+=[2,"甲","2 h","PO-4"]
        stator_book(self.local,rows,True)
        importer.import_book(self.db,self.root,self.local,line=STATOR)
        first=self.get("issues/1")
        self.assertEqual((first["owner"],first["problem"]),("","First fixture"))
        self.assertEqual([f["note"] for f in first["feedback"]],["待确认"])
        self.assertEqual(self.get("issues/2")["feedback"],[])
        self.assertTrue(any(r["data"].get("action_issue")==1 for r in self.get("imports")["reviews"]))

    def test_only_stator_customer_action_can_trigger_rework(self):
        """豪森反馈留在现场时间线，客户行动才触发返工。 / Only the customer release can trigger rework."""
        issue = self.get("issues/1")
        res = self.client.post(self.base+"issues/1/transition",json={
            "to":"ready","day":"2025-11-04","owner":"甲","note":"等待上报","actor":"乙","revision":issue["revision"]})
        self.assertEqual(res.status_code,200,res.data)
        local = [r.copy() for r in self.rows]
        local[0][7] = "05.11.2025 ：现场检查\nCustomer request"
        stator_book(self.local,local,True)
        importer.import_book(self.db,self.root,self.local,line=STATOR)
        self.assertEqual(self.get("issues/1")["state"],"ready")
        self.rows[0][7] += "\n06.11.2025: Adjust the fixture"
        stator_book(self.customer,self.rows)
        info = importer.import_book(self.db,self.root,self.customer,version="2025-11-07",line=STATOR)
        self.assertEqual(info["summary"]["rework"],1)
        self.assertEqual(self.get("issues/1")["events"][0]["kind"],"customer_rework")
        self.assertEqual(self.get("issues/2")["state"],"triage")
        self.assertEqual(self.get("overview?week=2025-W45")["counts"]["rework"],1)

    def test_unnumbered_customer_binding_survives_next_version(self):
        blank=[*self.rows[0]]
        blank[0],blank[6]="","Unnumbered complaint"
        stator_book(self.customer,[*self.rows,blank])
        importer.import_book(self.db,self.root,self.customer,version="2025-11-10",line=STATOR)
        review=next(r for r in self.get("imports")["reviews"] if r["problem"]=="Unnumbered complaint")
        response=self.client.post(self.base+"reviews/"+str(review["id"]),
                                  json={"actor":"甲","decision":"new","number":"400"})
        self.assertEqual(response.status_code,200,response.data)
        blank[8]="ZF"
        stator_book(self.customer,[blank,*self.rows])
        importer.import_book(self.db,self.root,self.customer,version="2025-11-12",line=STATOR)
        found=self.get("issues?q=400&scope=all")["items"]
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["state"],"zf_only")

    def test_repeat_and_reorder(self):
        rows=[r.copy() for r in self.rows]
        rows[0][7]="05.11.2025 ：已完成，待确认\nCustomer request"
        rows[0][9:11]=["2025-12-01","in Arbeit"]
        rows[0] += [2,"Iseni","2 h","PO-4"]
        stator_book(self.local,rows,True)
        importer.import_book(self.db,self.root,self.local,line=STATOR)
        rows[1] += [1,"乙","",""]
        stator_book(self.local,rows[::-1],True)
        importer.import_book(self.db,self.root,self.local,line=STATOR)
        stator_book(self.customer,self.rows[::-1])
        importer.import_book(self.db,self.root,self.customer,version="2025-11-10",line=STATOR)
        issue = self.get("issues/1")
        self.assertEqual((issue["number"],issue["status"],issue["state"]),("367a","offen","triage"))
        self.assertEqual((issue["owner"],issue["due"],issue["plan_candidate"]),("","2025-01-25","2025-12-01"))
        self.assertEqual([(n["day"],n["note"]) for n in issue["feedback"]],[("2025-11-05","已完成，待确认")])
        self.assertEqual([a["original"] for a in issue["actions"]],["Customer request"])
        self.assertEqual(self.get("issues/2")["number"],"367b")
        archive = maintenance.backup(self.db,self.root,line=STATOR)
        restored = maintenance.restore(archive,self.root/"restored")
        with closing(connect(restored,STATOR)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0],1)
        self.assertFalse((restored/"data/opl.sqlite3").exists())
