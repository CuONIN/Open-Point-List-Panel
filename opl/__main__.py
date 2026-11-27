"""启动本机服务、同步工作簿及备份恢复的命令行入口。 / CLI for the local server, workbook sync and backups."""

# argparse 解析子命令；Waitress 在本机回环地址提供 Flask 应用。 / argparse parses commands; Waitress serves Flask on loopback.
import argparse
import json
import os
import sqlite3
import sys
import time
import webbrowser
from contextlib import closing,contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler

from . import importer
from .excel import legacy_stator_book
from .lines import EOL, STATOR, LINES
from .maintenance import backup,restore
from .records import index_feedback
from .store import ROOT, connect, decode, dump, file_hash, initialize, json_hash, writing


def source_paths(root, line=EOL):
    """收集线体来源，兼容原客户及豪森文件的位置。 / Discover line-specific sources, including legacy shared folders."""
    def books(folder):
        return [p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in (".xlsx",".xlsm")
                and not p.name.startswith("~$")]

    clients, local = [], []
    for role, files in (("ZF", clients), ("Haosen", local)):
        for path in books(root/"EOL"/"List"/role):
            if legacy_stator_book(path) == (line == STATOR):
                files.append(path)
        if line != EOL:
            files.extend(books(root/line.folder/"List"/role))
    if line == STATOR:
        local.extend(books(root/line.folder))
    return (sorted(clients,key=lambda p:(importer.observed_version(p.name),p.name))
            + sorted(local,key=lambda p:(p.suffix.lower()!=".xlsm",p.name)))


def sync(conn,root,paths=None,version="",revision_no=0,line=EOL):
    results=[]
    for p in source_paths(root,line) if paths is None else paths:
        p=Path(p).resolve()
        if p.name.startswith("~$"):
            continue
        if not p.is_file():
            raise ValueError(f"找不到工作簿 {p.name}")
        if conn.execute("SELECT 1 FROM batches WHERE digest=?",(file_hash(p),)).fetchone():
            results.append({"file":p.name,"duplicate":True})
            continue
        results.append(importer.import_book(conn,root,p,version=version,revision_no=revision_no,line=line))
    index_feedback(conn,root,line)
    return results


def seed(conn,root,line=EOL):
    """首次启动建立客户基线，之后保留现场编辑。 / Establish the customer baseline once and preserve field edits on later launches."""
    if conn.execute("SELECT 1 FROM metadata WHERE key='first_scan'").fetchone():
        return
    paths = source_paths(root,line)
    if not any(p.parent.name == "ZF" for p in paths):
        return
    sync(conn,root,paths,line=line)
    with writing(conn):
        conn.execute("INSERT OR REPLACE INTO metadata VALUES('first_scan','complete')")


def existing_instance(root):
    try:
        info=decode((root/"data"/"instance.json").read_text("utf-8"))
        port=int(info["port"])
        if not 0<port<65536:
            return None
        url=f"http://127.0.0.1:{port}"
        with build_opener(ProxyHandler({})).open(url+"/api/health",timeout=1) as answer:
            status=json.loads(answer.read(4096))
        if status["app"]=="sbr-eol-opl" and status["project"]==json_hash(str(root).casefold()):
            return url
    except (OSError,ValueError,TypeError,KeyError,URLError):
        pass
    return None


@contextmanager
def startup_lock(root):
    folder=root/"data"
    folder.mkdir(parents=True,exist_ok=True)
    f=(folder/"instance.lock").open("a+b")
    if not f.tell():
        f.write(b"0"); f.flush()
    try:
        if os.name=="nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        f.close()
        yield False
        return
    try:
        yield True
    finally:
        f.close()


def serve(root,args):
    from waitress import create_server
    from .web import create_app
    def open_instance(url):
        print(f"现场问题工作台  {url}",flush=True)
        if not args.no_browser:
            webbrowser.open(url)
    with startup_lock(root) as locked:
        if not locked:
            for _ in range(90):
                existing=existing_instance(root)
                if existing:
                    open_instance(existing)
                    return
                time.sleep(0.5)
            raise ValueError("另一个实例正在初始化，请稍后再次启动。")
        for line in LINES.values():
            with closing(connect(root,line)) as conn:
                initialize(conn)
                if not args.no_seed:
                    seed(conn,root,line)
        app=create_app(root)
        try:
            server=create_server(app,host="127.0.0.1",port=args.port,threads=6,max_request_body_size=256*1024*1024)
        except OSError:
            server=create_server(app,host="127.0.0.1",port=0,threads=6,max_request_body_size=256*1024*1024)
        port=int(server.effective_port)
        marker=root/"data"/"instance.json"
        tmp=marker.with_suffix(".tmp")
        tmp.write_text(dump({"port":port,"pid":os.getpid()}),"utf-8")
        tmp.replace(marker)
        open_instance(f"http://127.0.0.1:{port}")
        try:
            server.run()
        finally:
            server.close()
            marker.unlink(missing_ok=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description="ZF Saarbrücken EOL 本地问题工作台")
    parser.add_argument("--root",type=Path,default=ROOT,help="项目资料根目录")
    subs=parser.add_subparsers(dest="command")
    server=subs.add_parser("serve",help="在本机启动工作台")
    server.add_argument("--port",type=int,default=8765)
    server.add_argument("--no-browser",action="store_true")
    server.add_argument("--no-seed",action="store_true")
    sub=subs.add_parser("sync",help="读取指定文件，或扫描各线体的新版本")
    sub.add_argument("--line", choices=LINES, help="指定线体；扫描目录时默认全部，指定文件时默认 EOL")
    sub.add_argument("files",nargs="*",type=Path)
    sub.add_argument("--version",default="",help="客户版本日期 YYYY-MM-DD")
    sub.add_argument("--revision",type=int,default=0,help="同日版本修订顺序")
    sub=subs.add_parser("backup",help="备份数据库、附件和来源文件")
    sub.add_argument("--line", choices=LINES, default=EOL.key)
    sub.add_argument("--output",type=Path)
    sub=subs.add_parser("restore",help="校验并恢复到一个新目录")
    sub.add_argument("archive",type=Path)
    sub.add_argument("destination",type=Path)
    args=parser.parse_args(argv)
    if args.command is None:
        return main(["--root",str(args.root),"serve"])
    root=args.root.resolve()
    if args.command=="serve":
        return serve(root,args)
    if args.command=="restore":
        print(restore(args.archive,args.destination))
        return
    selected = [LINES[args.line]] if args.line else ([EOL] if args.files else LINES.values())
    for line in selected:
        with closing(connect(root,line)) as conn:
            initialize(conn)
            if args.command=="sync":
                for item in sync(conn,root,args.files or None,args.version,args.revision,line):
                    print(line.name,item["file"],"已导入相同文件" if item.get("duplicate") else dump(item["summary"]),flush=True)
            elif args.command=="backup":
                print(backup(conn,root,args.output,line))


if __name__=="__main__":
    try:
        main()
    except (ValueError,OSError,sqlite3.Error) as ex:
        print(f"操作未完成：{ex}",file=sys.stderr)
        sys.exit(1)
