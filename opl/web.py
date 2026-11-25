"""将中文界面、清单查询、现场编辑和本地导出提供给浏览器。 / Serve the Chinese interface, issue queries, edits and local exports."""

# Flask 将请求映射到处理函数；Werkzeug 提供 HTTP 异常及文件上传对象。 / Flask routes requests; Werkzeug supplies HTTP exceptions and file uploads.
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from . import importer, queries, records, workflow
from .excel import classify_responsible
from .store import (ROOT, Conflict, connect, decode, dump, file_hash, initialize, json_hash,
                    need_issue, today, week_of, writing)


def create_app(root=ROOT):
    root=Path(root).resolve()
    app=Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=256*1024*1024,MAX_FORM_MEMORY_SIZE=16*1024*1024)
    app.json.ensure_ascii=False
    initial=connect(root)
    try:
        initialize(initial)
    finally:
        initial.close()

    def db():
        if "db" not in g:
            g.db=connect(root)
            if request.method in ("GET","HEAD"):
                g.db.execute("BEGIN")
        return g.db

    @app.teardown_appcontext
    def close_db(_err):
        if "db" in g:
            g.db.close()

    @app.before_request
    def local_only():
        host=urlsplit("//"+request.host).hostname
        if host not in ("127.0.0.1","localhost","::1"):
            return jsonify(error="仅接受本机访问。"),403
        if request.method not in ("GET","HEAD","OPTIONS"):
            origin=request.headers.get("Origin")
            if (origin and origin!=request.host_url.rstrip("/")) or request.headers.get("Sec-Fetch-Site")=="cross-site":
                return jsonify(error="请从本地工作台提交修改。"),403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="same-origin"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"]="no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(ex):
        return jsonify(error=str(ex)),409 if isinstance(ex,Conflict) else 400

    @app.errorhandler(LookupError)
    def missing(ex):
        return jsonify(error=str(ex)),404

    @app.errorhandler(HTTPException)
    def http_error(ex):
        message="文件过大，单次请求上限为 256 MB。" if ex.code==413 else ex.description
        return jsonify(error=message),ex.code

    def change(fn,*args):
        data=request.get_json(silent=True) or {}
        with writing(db()):
            fn(db(),*args,data)
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(app="sbr-eol-opl",project=json_hash(str(root).casefold()),status="ok")

    @app.get("/api/config")
    def config():
        filters = queries.options(db())
        pure_responsibles = [name for name in filters["responsible"] if classify_responsible(name) == "zf"]
        return jsonify(states=workflow.STATES,filters=filters,pure_responsibles=pure_responsibles,today=today(),week=week_of(today()),
                       translation_enabled=bool(os.environ.get("OPENAI_API_KEY")))

    @app.get("/api/overview")
    def overview():
        return jsonify(queries.overview(db(),request.args))

    @app.get("/api/issues")
    def issues():
        return jsonify(queries.issue_list(db(),request.args))

    @app.get("/api/issues/export.<fmt>")
    def export_issues(fmt):
        if fmt not in ("xlsx","pdf"):
            raise LookupError("请选择 Excel 或 PDF 导出。")
        from .exports import print_data
        title = "ZF Saarbrücken · EOL 现场派工单"
        orientation = request.args.get("orientation", "portrait")
        layout = request.args.get("layout", "field")
        if layout not in ("field", "bilingual"):
            raise ValueError("请选择现场精简或中英对照格式。")
        if orientation not in ("portrait","landscape"):
            raise ValueError("A4 打印方向请选择竖版或横版。")
        conn = db()
        filters, rows = print_data(conn, request.args)
        conn.commit()  # Printing can take longer than reading the consistent SQLite snapshot.
        if fmt == "xlsx":
            from .export_xlsx import render_xlsx
            contents = render_xlsx(title, filters, rows, orientation=orientation, layout=layout)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            from .export_pdf import render_pdf
            try:
                contents = render_pdf(title, filters, rows, project_root=root, orientation=orientation, layout=layout)
            except (OSError, RuntimeError) as exc:
                return jsonify(error=f"PDF 导出未完成。{exc}"), 503
            mime = "application/pdf"
        return send_file(contents, mimetype=mime, as_attachment=True,
                         download_name=f"EOL-现场派工单-{today()}-{len(rows)}条-{layout}-{orientation}.{fmt}")

    @app.get("/api/issues/<int:uid>")
    def detail(uid):
        return jsonify(queries.detail(db(),uid))

    @app.route("/api/issues/<int:uid>/transition",methods=["GET","POST"])
    def transition(uid):
        if request.method=="POST":
            return change(workflow.transition,uid)
        i=need_issue(db(),uid)
        target=request.args.get("to")
        fields=workflow.form_fields(i["state"],target,decode(i["local"])) if target else []
        return jsonify(fields=fields,allowed=workflow.allowed(i["state"]))

    @app.post("/api/issues/<int:uid>/events/<int:eid>")
    def correct_event(uid,eid):
        return change(workflow.correct_event,uid,eid)

    @app.post("/api/issues/<int:uid>/plan")
    def plan(uid):
        return change(workflow.reschedule,uid)

    @app.post("/api/issues/<int:uid>/profile")
    def profile(uid):
        return change(records.profile,uid)

    @app.post("/api/issues/<int:uid>/actions")
    def actions(uid):
        return change(records.actions_edit,uid)

    @app.post("/api/issues/<int:uid>/feedback")
    def feedback(uid):
        with writing(db()):
            records.feedback(db(),root,uid,request.form,request.files.getlist("files"))
        return jsonify(ok=True)

    @app.post("/api/issues/<int:uid>/relations")
    def relation(uid):
        return change(records.relation,uid)

    @app.delete("/api/issues/<int:uid>/relations/<int:rid>")
    def remove_relation(uid,rid):
        with writing(db()):
            records.relation(db(),uid,request.get_json() or {},rid)
        return jsonify(ok=True)

    @app.get("/api/issues/<int:uid>/relation-candidates")
    def related(uid):
        return jsonify(items=queries.candidates(db(),uid))

    @app.get("/api/files/<int:fid>")
    def evidence(fid):
        row=db().execute("SELECT * FROM files WHERE id=?",(fid,)).fetchone()
        if not row:
            raise LookupError("找不到附件。")
        path=(root/row["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise LookupError("附件文件已移动或缺失。")
        inline=row["mime"].startswith(("image/","video/")) or row["mime"]=="application/pdf"
        return send_file(path,download_name=row["name"],mimetype=row["mime"],as_attachment=not inline,conditional=True)

    @app.get("/api/imports/<int:bid>/source")
    def source(bid):
        row=db().execute("SELECT file,path FROM batches WHERE id=?",(bid,)).fetchone()
        if not row:
            raise LookupError("找不到来源版本。")
        path=(root/row["path"]).resolve()
        if not path.is_relative_to(root/"data"/"sources") or not path.is_file():
            raise LookupError("归档文件缺失。")
        return send_file(path,download_name=row["file"],as_attachment=True)

    @app.get("/api/imports")
    def imports():
        page=request.args.get("review_page",1,type=int)
        reviews,total=queries.review_list(db(),page)
        batches=[{**dict(r),"summary":decode(r["summary"])} for r in db().execute(
            "SELECT id,file,kind,version,revision_no,digest,sheet,applied,summary FROM batches ORDER BY version DESC,revision_no DESC,id DESC")]
        pending=db().execute("""SELECT (SELECT COUNT(*) FROM issues WHERE json_extract(customer,'$.problem')<>''
             AND COALESCE(json_extract(local,'$.zh'),'')='' AND COALESCE(json_extract(customer,'$.embedded_zh'),'')='')
             + (SELECT COUNT(*) FROM actions WHERE active=1 AND original<>'' AND zh='')""").fetchone()[0]
        return jsonify(items=batches,reviews=reviews,review_total=total,review_page=page,review_pages=max(1,(total+99)//100),
                       translation_pending=pending)

    @app.post("/api/imports/preview")
    def preview():
        uploads=[u for u in request.files.getlist("files") if u.filename]
        if not uploads:
            raise ValueError("请选择一个或多个 Excel 文件。")
        folder=root/"data"/"staging"
        folder.mkdir(parents=True,exist_ok=True)
        items=[]
        saved=[]
        try:
            for upload in uploads:
                filename=Path(upload.filename.replace("\\","/")).name
                suffix=Path(filename).suffix.lower()
                if suffix not in (".xlsx",".xlsm") or filename.startswith("~$"):
                    raise ValueError("请使用 .xlsx 或 .xlsm 正式工作簿。")
                token=uuid.uuid4().hex
                path=folder/(token+suffix)
                saved.append(path)
                upload.save(path)
                info,decisions,_=importer.inspect_import(db(),root,path,filename,request.form.get("version",""),request.form.get("revision_no",0))
                meta=folder/(token+".json")
                saved.append(meta)
                meta.write_text(dump({"file":filename,"suffix":suffix,"version":info["version"],
                                      "revision_no":info["revision_no"],"digest":info["digest"]}),"utf-8")
                examples=[{"row":d["src"]["row"],"no":d["src"]["no"],"station":d["src"]["station"],
                           "mode":d["mode"],"changes":d.get("changes",[]),"reason":d["reason"]} for d in decisions
                          if d["mode"] in ("new","review") or d.get("changes")]
                items.append({**info,"token":token,"examples":examples[:25]})
        except Exception:
            for path in saved:
                path.unlink(missing_ok=True)
            raise
        return jsonify(items=items)

    @app.post("/api/imports/commit")
    def commit():
        data=request.get_json() or {}
        token=str(data.get("token",""))
        if not re.fullmatch(r"[a-f0-9]{32}",token):
            raise ValueError("无效的预览标识。")
        folder=root/"data"/"staging"
        meta=folder/(token+".json")
        if not meta.is_file():
            raise ValueError("预览已提交或失效，请重新选择工作簿。")
        info=decode(meta.read_text("utf-8"))
        path=folder/(token+info["suffix"])
        if not path.is_file() or file_hash(path)!=info["digest"]:
            raise ValueError("预览文件已改变，请重新选择。")
        result=importer.import_book(db(),root,path,info["file"],data.get("version") or info["version"],
                                    data.get("revision_no",info["revision_no"]))
        path.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        return jsonify(result)

    @app.post("/api/reviews/<int:rid>")
    def review(rid):
        with writing(db()):
            importer.resolve_review(db(),root,rid,request.get_json() or {})
        return jsonify(ok=True)

    @app.get("/api/tasks")
    def get_tasks():
        return jsonify(items=records.tasks(db(),request.args.get("week","")))

    @app.post("/api/tasks")
    @app.post("/api/tasks/<int:tid>")
    def save_task(tid=None):
        with writing(db()):
            tid=records.write_task(db(),request.get_json() or {},tid)
        return jsonify(id=tid,ok=True)

    @app.get("/api/report")
    def weekly_report():
        if request.args.get("as_of"):
            _, start, end, _ = queries.week_context(request.args)
            if not start <= request.args["as_of"] <= end or request.args["as_of"] > today():
                raise ValueError("统计截至日期须在所选周内，且不能晚于今天。")
        summary = queries.overview(db(),request.args)
        refs = {p["number"]:p["id"] for p in summary["plans"]}
        refs.update({e["number"]:e["issue_id"] for e in summary["events"]})
        return jsonify(text=queries.report(db(),request.args,summary),issue_refs=refs,
                       start=summary["start"], end=summary["end"], as_of=summary["as_of"])

    @app.post("/api/translate")
    def translate():
        data=request.get_json(silent=True) or {}
        uid=int(data["issue_id"]) if data.get("issue_id") else None
        return jsonify(records.translate(db(),uid,data.get("limit",3)))

    return app
