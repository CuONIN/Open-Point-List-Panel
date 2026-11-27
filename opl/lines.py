"""定义线体身份、工作目录和界面选项。 / Declare line identity, storage and interface capabilities."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Line:
    key: str
    name: str
    folder: str
    storage: str
    prefix: str
    phased: bool

    def data(self, root):
        return Path(root) / self.storage

    def config(self):
        return dict(key=self.key, name=self.name, prefix=self.prefix, phased=self.phased)


EOL = Line("eol", "EOL 测试线", "EOL", "data", "", True)
STATOR = Line("pal_stator", "PAL 定子线", "PAL_Stator", "data/lines/pal_stator", "/lines/pal_stator", False)
LINES = {line.key: line for line in (EOL, STATOR)}
