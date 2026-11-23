"""创建可校验的完整工作区备份并恢复到新目录。 / Back up and verify the workspace before restoring into a fresh directory."""

# sqlite3.backup 创建一致快照；zipfile 归档数据库、附件和来源文件。 / sqlite3.backup takes a consistent snapshot; zipfile archives the database and evidence.
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from .store import dump,file_hash


def backup(conn,root,output=None):
    target=Path(output) if output else root/"backups"/("EOL-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".zip")
    target=target.resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        raise ValueError("备份文件已存在，请选择新的文件名。")
    with tempfile.TemporaryDirectory(dir=target.parent,prefix=".opl-backup-") as temp:
        temp=Path(temp)
        copied=temp/"opl.sqlite3"
        snapshot=sqlite3.connect(copied)
        try:
            conn.backup(snapshot)
            referenced={r[0] for r in snapshot.execute("SELECT path FROM files UNION SELECT path FROM batches")}
        finally:
            snapshot.close()
        files={"data/opl.sqlite3":copied}
        for name in referenced:
            p=(root/name).resolve()
            if not p.is_relative_to(root) or not p.is_file():
                raise ValueError(f"备份缺少引用的文件 {name}")
            files[name]=p
        for p in (root/"EOL"/"Station").glob("*"):
            if p.is_file() and not p.name.startswith("~$"):
                files[p.relative_to(root).as_posix()]=p
        archive=temp/"backup.zip"
        with ZipFile(archive,"w",compression=ZIP_DEFLATED,compresslevel=3) as z:
            hashes={}
            for name,p in sorted(files.items()):
                z.write(p,name)
                hashes[name]=file_hash(p)
            z.writestr("manifest.json",dump({"format":1,"sha256":hashes}))
        archive.rename(target)
    return target


def restore(archive,destination):
    """在新目录解包并验证每项哈希和数据库完整性。 / Extract into a fresh directory, verifying hashes and database integrity."""
    destination=Path(destination).resolve()
    if destination.exists():
        raise ValueError("请输入尚不存在的新目录，以便保留现有工作区。")
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent,prefix=".opl-restore-") as temp:
        copy=Path(temp)/"restored"
        copy.mkdir()
        with ZipFile(archive) as z:
            manifest=json.loads(z.read("manifest.json"))
            if manifest.get("format")!=1 or "data/opl.sqlite3" not in manifest["sha256"]:
                raise ValueError("无法识别备份格式。")
            for name,digest in manifest["sha256"].items():
                parts=PurePosixPath(name).parts
                path=(copy/name).resolve()
                if not path.is_relative_to(copy) or "\\" in name or ":" in name or ".." in parts:
                    raise ValueError("备份中包含无效路径。")
                path.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as src,path.open("xb") as dst:
                    shutil.copyfileobj(src,dst)
                if file_hash(path)!=digest:
                    raise ValueError(f"备份校验失败 {name}")
        db=sqlite3.connect(copy/"data"/"opl.sqlite3")
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("数据库完整性校验未通过。")
        finally:
            db.close()
        copy.rename(destination)
    return destination
