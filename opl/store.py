"""定义 SQLite 数据结构、事务、版本迁移及稳定编号。 / Define SQLite storage, transactions, migrations and stable numbering."""

# sqlite3 负责本地事务；zoneinfo 按柏林日历解释业务日期。 / sqlite3 manages transactions; zoneinfo supplies the Berlin business calendar.
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .lines import EOL

ROOT = Path(__file__).resolve().parents[1]
BERLIN = ZoneInfo("Europe/Berlin")


def today():
    return datetime.now(BERLIN).date().isoformat()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def week_of(day):
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def week_bounds(value):
    try:
        year, week = value.upper().replace("CW", "W").split("-W")
        start = date.fromisocalendar(int(year), int(week), 1)
    except (ValueError, AttributeError):
        raise ValueError("请选择有效的年份和 ISO 周。") from None
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def iso_day(value, label="日期"):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError(f"{label}须为有效日期。") from None


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def decode(value, fallback=None):
    return json.loads(value) if value else fallback


def file_hash(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_hash(value):
    return hashlib.sha256(dump(value).encode("utf-8")).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
 id INTEGER PRIMARY KEY, file TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
 revision_no INTEGER NOT NULL, digest TEXT NOT NULL, path TEXT NOT NULL, sheet TEXT NOT NULL,
 applied INTEGER NOT NULL, summary TEXT NOT NULL DEFAULT '{}', recorded TEXT NOT NULL,
 UNIQUE(kind,digest));
CREATE UNIQUE INDEX IF NOT EXISTS zf_release ON batches(version, revision_no) WHERE kind='zf';
CREATE TABLE IF NOT EXISTS issues (
 id INTEGER PRIMARY KEY, base TEXT NOT NULL, customer TEXT NOT NULL, local TEXT NOT NULL DEFAULT '{}',
 state TEXT NOT NULL, state_day TEXT NOT NULL, reason TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 created_day TEXT NOT NULL, last_batch INTEGER REFERENCES batches(id));
CREATE TABLE IF NOT EXISTS numbers (
 base TEXT NOT NULL, slot INTEGER NOT NULL, issue_id INTEGER NOT NULL REFERENCES issues,
 PRIMARY KEY(base,slot), UNIQUE(base,issue_id));
CREATE TABLE IF NOT EXISTS source_rows (
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES batches, row_no INTEGER NOT NULL,
 issue_id INTEGER REFERENCES issues, fields TEXT NOT NULL, UNIQUE(batch_id,row_no));
CREATE INDEX IF NOT EXISTS source_issue ON source_rows(issue_id,batch_id);
CREATE TABLE IF NOT EXISTS bindings (
 kind TEXT NOT NULL, identity TEXT NOT NULL, issue_id INTEGER NOT NULL REFERENCES issues,
 PRIMARY KEY(kind,identity));
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues, from_state TEXT NOT NULL,
 to_state TEXT NOT NULL, day TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, batch_id INTEGER REFERENCES batches, recorded TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_issue ON events(issue_id,day,id);
CREATE INDEX IF NOT EXISTS event_week ON events(day,to_state);
CREATE TABLE IF NOT EXISTS plans (
 id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues, day TEXT NOT NULL,
 plan TEXT NOT NULL, tracker TEXT NOT NULL, note TEXT NOT NULL, actor TEXT NOT NULL, recorded TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS plan_issue ON plans(issue_id,day,id);
CREATE TABLE IF NOT EXISTS actions (
 id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues, identity TEXT NOT NULL,
 original TEXT NOT NULL, zh TEXT NOT NULL DEFAULT '', en TEXT NOT NULL DEFAULT '',
 day TEXT NOT NULL, date_raw TEXT NOT NULL, author TEXT NOT NULL, position INTEGER NOT NULL,
 active INTEGER NOT NULL, manual INTEGER NOT NULL DEFAULT 0, batch_id INTEGER REFERENCES batches,
 UNIQUE(issue_id,identity));
CREATE TABLE IF NOT EXISTS feedback (
 id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues, day TEXT NOT NULL,
 actor TEXT NOT NULL, note TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', recorded TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fb_issue ON feedback(issue_id,day,id);
CREATE TABLE IF NOT EXISTS files (
 id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL, mime TEXT NOT NULL, digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS file_links (
 issue_id INTEGER NOT NULL REFERENCES issues, file_id INTEGER NOT NULL REFERENCES files,
 feedback_id INTEGER REFERENCES feedback, UNIQUE(issue_id,file_id,feedback_id));
CREATE TABLE IF NOT EXISTS relations (
 id INTEGER PRIMARY KEY, a INTEGER NOT NULL REFERENCES issues, b INTEGER NOT NULL REFERENCES issues,
 kind TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, recorded TEXT NOT NULL,
 CHECK(a<>b), UNIQUE(a,b,kind));
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, issue_id INTEGER REFERENCES issues, day TEXT NOT NULL, actor TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, recorded TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY, batch_id INTEGER REFERENCES batches, source_id INTEGER REFERENCES source_rows,
 kind TEXT NOT NULL, reason TEXT NOT NULL, data TEXT NOT NULL, candidates TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open', resolution TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS review_open ON reviews(status,id);
CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, owner TEXT NOT NULL, due TEXT NOT NULL,
 week TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS task_issues (
 task_id INTEGER NOT NULL REFERENCES tasks, issue_id INTEGER NOT NULL REFERENCES issues,
 PRIMARY KEY(task_id,issue_id));
CREATE TABLE IF NOT EXISTS translations (
 fingerprint TEXT PRIMARY KEY, original TEXT NOT NULL, zh TEXT NOT NULL, en TEXT NOT NULL,
 source TEXT NOT NULL, recorded TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS current_state ON issues(state,state_day);
CREATE INDEX IF NOT EXISTS issue_area ON issues(json_extract(customer,'$.area'));
CREATE INDEX IF NOT EXISTS issue_station ON issues(json_extract(customer,'$.station'));
"""


def connect(root=ROOT, line=EOL):
    root = Path(root)
    line.data(root).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(line.data(root) / "opl.sqlite3", timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > 2:
        raise ValueError("数据库版本高于本程序，请使用匹配的应用版本。")
    conn.execute("PRAGMA journal_mode=WAL")
    if version == 0:
        conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
    if version < 2:
        # 将已建库的工位和负责人同步到现场口径。 / Upgrade derived data without changing Excel snapshots or workflow events.
        from .people import internal_owner
        from .stations import AGV_STATIONS
        with writing(conn):
            if conn.execute("PRAGMA user_version").fetchone()[0] >= 2:
                return
            for record in conn.execute("SELECT id,customer,local FROM issues").fetchall():
                customer, local = decode(record["customer"]), decode(record["local"])
                original = (dump(customer), dump(local))
                machine = customer.get("machine")
                if machine in AGV_STATIONS:
                    customer.update(station=AGV_STATIONS[machine], area="AGV", station_review=False)
                for key in ("owner","tracker"):
                    if key in local:
                        local[key] = internal_owner(local[key])
                if original != (dump(customer),dump(local)):
                    conn.execute("UPDATE issues SET customer=?,local=?,revision=revision+1 WHERE id=?",
                                 (dump(customer),dump(local),record["id"]))
            conn.execute("PRAGMA user_version=2")


@contextmanager
def writing(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def need_issue(conn, issue_id):
    row = conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
    if row is None:
        raise LookupError("没有找到这个问题。")
    return row


class Conflict(ValueError):
    pass


def edited_issue(conn, issue_id, data):
    row = need_issue(conn, issue_id)
    if str(row["revision"]) != str(data.get("revision", "")):
        raise Conflict("此问题有新的修改。请刷新后核对，再保存。")
    return row


def required(data, key, label):
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"请填写{label}。")
    return value


def audit(conn, issue_id, day, actor, kind, payload):
    conn.execute("INSERT INTO audit(issue_id,day,actor,kind,payload,recorded) VALUES(?,?,?,?,?,?)",
                 (issue_id, day, actor, kind, dump(payload), now()))


def suffix(slot):
    result = ""
    slot += 1
    while slot:
        slot, letter = divmod(slot - 1, 26)
        result = chr(97 + letter) + result
    return result


def assign_number(conn, issue_id, base):
    """客户重号按首次分配的槽位生成稳定后缀。 / Assign duplicate-number suffixes by persistent insertion slot."""
    if not conn.execute("SELECT 1 FROM numbers WHERE base=? AND issue_id=?", (base, issue_id)).fetchone():
        slot = conn.execute("SELECT COALESCE(MAX(slot)+1,0) FROM numbers WHERE base=?", (base,)).fetchone()[0]
        conn.execute("INSERT INTO numbers VALUES(?,?,?)", (base, slot, issue_id))


def number_map(conn):
    rows = conn.execute("SELECT base,slot,issue_id,COUNT(*) OVER(PARTITION BY base) AS n FROM numbers")
    return {(r["issue_id"], r["base"]): r["base"] + (suffix(r["slot"]) if r["n"] > 1 else "") for r in rows}
