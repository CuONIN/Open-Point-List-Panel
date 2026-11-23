"""在临时库中运行现场操作浏览器回归。 / Run browser workflow regressions against an isolated database."""

# Waitress serves the real app; subprocess runs the optional Playwright driver.
# Waitress 提供真实应用，subprocess 启动可选的 Playwright 测试驱动。
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread

from waitress import create_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opl.importer import import_book
from opl.store import connect
from opl.web import create_app
from test_workbench import customer_rows, workbook


def run():
    """先建样例，再测试 UI 写入。 / Seed fixture workbooks before testing real UI writes."""
    with TemporaryDirectory(prefix="opl-ui-") as directory:
        root = Path(directory)
        app = create_app(root)
        file = root / "ZF-2025-01-02.xlsx"
        workbook(file, customer_rows())
        with closing(connect(root)) as conn:
            import_book(conn, root, file)
        server = create_server(app, host="127.0.0.1", port=0, threads=3)
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            subprocess.run([os.environ.get("OPL_TEST_NODE","node"), str(ROOT/"tests"/"browser_flow.cjs"),
                            f"http://127.0.0.1:{server.effective_port}/"], check=True, timeout=120)
        finally:
            server.close()
            thread.join(timeout=3)


if __name__ == "__main__":
    run()
