"""把客户工位、AVO 和设备号映射到项目工位及分期。 / Map customer station labels, AVOs and machine IDs to project stations and phases."""

# dataclasses 表示设备；openpyxl 从 HVL 清单补充名称和别名。 / dataclasses represent machines; openpyxl reads the HVL station dictionary.
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
import re
import unicodedata

from openpyxl import load_workbook


def _plain(raw):
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    return " ".join(unicodedata.normalize("NFKC", str(raw or "")).split()).strip()


def _key(name):
    words = re.sub(r"\b(?:mws|station)\b", "", _plain(name).casefold())
    return "".join(ch for ch in words if ch.isalnum())


def _machine(raw):
    match = re.fullmatch(r"2482[\s.-]?(\d{4})", _plain(raw))
    return "2482" + match[1] if match else ""


@dataclass(frozen=True)
class Station:
    name: str
    machine: str = ""
    avo: str = ""
    area: str = "S1"


# 设备字典对检漏和吹扫模块采用连续编号。 / The English machine list numbers the leakage and flushing modules consecutively.
# 客户表用线号和模块号表达同一台设备。 / Customer issue sheets use a line/module pair for the same equipment.
def _project_stations():
    specs = []

    def add(name, suffix, avo, area, *aliases):
        machine = f"2482{suffix}" if suffix else ""
        specs.append((Station(name, machine, str(avo or ""), area), aliases))

    for i in range(1, 5):
        phase, module = (i - 1) // 2 + 1, (i - 1) % 2 + 1
        area = "S2" if i >= 3 else "S1"
        add(f"Leakage Test {phase}-{module}", 1050 + i, 6500, area, f"Leakage Test {i}")
        add(f"Testbench {i}", 1090 + i, 7500, area, f"Test Bench {i}")

    for i in range(1, 7):
        phase, module = (i - 1) // 3 + 1, (i - 1) % 3 + 1
        add(f"Flash and Blowout {i}", 1100 + i, 7800, f"S{phase}",
            f"Blow Out Cooling Water and Flashing {phase}-{module}",
            f"Blowout and Flashing {phase}-{module}")
        area = "S2" if i in (5, 6) else "S1"
        add(f"Conveyor System CTA{i}", 1220 + 10 * i, 5010 + 100 * i, area,
            f"Conveyor / Transfer system CTA{i}", f"Conveyor CTA{i}")
        add(f"Electrical Cabinets CTA{i}", 1220 + 10 * i, "", area,
            f"Electrical cabinets CAT{i}", f"Electrical Cabinet CTA{i}", f"Cabinet CTA{i}")

    for i in (1, 2):
        second = f"S{i}"
        add(f"Oil Filling {i}", 1060 + i, 6600, second)
        add(f"High Voltage Test {i}", 1070 + i, 6700, second, f"HV Test {i}")
        add(f"Unrigging after Test {i}", 1110 + i, 8100, second,
            f"Disassembly after testing {i}", f"Unrigging {i}")
        add(f"Assembly Y-Adapter {i}", 1040 + i, 6550, "S1", f"Preparation for testing {i}")
        add(f"Disassembly Y-Adapter {i}", 1080 + i, 6850, "S1", f"Y adapter disassembly {i}")
        add(f"NVH Cover Assembly {i}", 1140 + i * 10, 8600 if i == 1 else 8650, "S1",
            f"Assembly NVH-Cover {i}")
        add(f"OK Product Unloading {i}", 1170 + i, 8800, "S1", f"OK Palettizing {i}")

    add("AGV 一期", 1220, 5710, "AGV", "AGV", "AGV 1", "AGV Phase 1", "Automated Guided Vehicles (AGV)")
    add("AGV 二期", 1221, "", "AGV", "AGV 2", "AGV Phase 2")
    add("Oil Tank", 1200, "", "S1", "Intermediate Oil Tank 3500L")
    add("Cooling Liquid Tank", 1210, "", "S1")
    add("WPC Changeover", 1010, 6100, "S1", "Exchange Interchangeable Support WPC")
    add("Back Up Transfer Station", 1020, 6250, "S1", "Manual OK Loading")
    add("Automatic Code Scanning Station", 1030, 5120, "S1", "Data Check Station After Loading")
    add("Greasing Station", 1120, 8000, "S1")
    add("Customer Barcode Station", 1130, 8300, "S1", "Labels and shipping plugs assembly")
    add("Camera Check Station", 1140, 8400, "S1", "Camera Check before Shipping")
    add("NOK Product Unloading", 1180, 8900, "S1", "NOK Unload")
    add("Load Clamping Matrice", "", 8970, "S1")
    add("Unload Clamping Matrice", "", 8970, "S1")
    return specs


_MACHINE_IN_LABEL = re.compile(r"(?<!\d)2482[\s.-]?\d{4}(?!\d)")
_AVO_IN_LABEL = re.compile(r"(?i)\bAVO[\s.:_-]*(\d{4}(?:\s*[-–]\s*\d{4})?)(?!\d)")


AGV_STATIONS = {"24821220": "AGV 一期", "24821221": "AGV 二期"}


def station_label(value, machine=""):
    """展示和统计时合并 AGV，来源匹配仍使用机器号。 / Group AGVs for display while preserving machine identity."""
    if machine in AGV_STATIONS or str(value).strip().casefold() in ("agv 一期", "agv 二期"):
        return "AGV"
    return value


class StationMap:
    """使用 AVO、名称和设备号确定工位，并区分共用 AVO 的机器。 / Resolve AVO/name/equipment labels without merging distinct machines sharing an AVO."""

    def __init__(self, root):
        self.by_name = {}
        self.stations = {}
        for station, aliases in _project_stations():
            self.stations[station.name] = station
            for alias in (station.name, *aliases):
                self.by_name[_key(alias)] = station.name

        root = Path(root)
        station_dir = (root / "EOL" / "Station") if (root / "EOL").is_dir() else root / "Station"
        for filename in sorted(station_dir.glob("Maschinennummer*.xls*")):
            self._read_hvl(filename)

        self.by_machine = defaultdict(list)
        self.by_avo = defaultdict(list)
        for station in self.stations.values():
            if station.machine:
                self.by_machine[station.machine].append(station)
            if station.avo:
                self.by_avo[station.avo].append(station)

    def _read_hvl(self, filename):
        with closing(load_workbook(filename, read_only=True, data_only=True, keep_links=False)) as book:
            sheet = next((s for s in book if "hvl" in s.title.casefold()), book.active)
            # 在含 HVL 与 SVL 字典的表格里定位 HVL 列组。 / Locate the HVL column block; the same sheet can also contain the SVL dictionary.
            width = min(sheet.max_column or 24, 50)
            block = None
            header = None
            for n, row in enumerate(sheet.iter_rows(max_col=width, max_row=25, values_only=True), 1):
                for i, v in enumerate(row):
                    if v and re.search(r"\bHVL\b", str(v), re.I):
                        block = i
                if block is None or block + 1 >= len(row):
                    continue
                labels = [_plain(v).casefold() for v in row]
                if labels[block] == "avo" and labels[block + 1] in ("maschine", "machine"):
                    machine_col = next((j for j in range(block + 2, width)
                                        if labels[j] in ("maschinennummer", "machine number")), None)
                    if machine_col is not None:
                        header = n, block, machine_col
                        break
            if header is None:
                return
            n, block, machine_col = header
            for row in sheet.iter_rows(min_row=n + 1, max_col=machine_col + 1, values_only=True):
                name = _plain(row[block + 1])
                machine = _machine(row[machine_col])
                avo = _plain(row[block])
                if not name or (not machine and not re.fullmatch(r"\d{4}", avo)):
                    continue
                canonical = self.by_name.get(_key(name), name)
                existing = self.stations.get(canonical)
                if existing:
                    # 设备表可以给范围，客户模板可能给单个工位的 AVO。 / An equipment range in the reference (e.g. CTA5) coexists with the
                    # station-specific AVO in the customer template.
                    avo = avo if re.fullmatch(r"\d{4}", avo) else existing.avo or avo
                    self.stations[canonical] = replace(
                        existing, machine=machine or existing.machine, avo=avo
                    )
                else:
                    self.stations[canonical] = Station(canonical, machine, avo)
                self.by_name[_key(name)] = canonical

    @staticmethod
    def _view(station, review=False):
        return {"station": station.name, "machine": station.machine, "avo": station.avo,
                "area": station.area, "station_review": review}

    def resolve(self, raw_station):
        raw = _plain(raw_station)
        avo_match = _AVO_IN_LABEL.search(raw)
        avo = re.sub(r"\s+", "", avo_match[1]) if avo_match else ""
        machine_match = _MACHINE_IN_LABEL.search(raw)
        machine = _machine(machine_match.group()) if machine_match else ""
        short = _AVO_IN_LABEL.sub("", _MACHINE_IN_LABEL.sub("", raw)).strip(" \t()[]-/")
        namekey = _key(short)
        by_name = self.stations.get(self.by_name.get(namekey))
        by_machine = self.by_machine.get(machine, [])
        # 已确认的 AGV 设备号优先于通用 AGV 名称。 / An explicit AGV machine ID takes precedence.
        if machine in AGV_STATIONS:
            return self._view(replace(self.stations[AGV_STATIONS[machine]], avo=avo or self.stations[AGV_STATIONS[machine]].avo))
        if by_name:
            machine_conflict = bool(machine and by_name.machine != machine)
            avo_conflict = bool(avo and by_name.avo and by_name.avo != avo and "-" not in by_name.avo)
            if machine_conflict or avo_conflict:
                return self._view(Station(raw, machine, avo, by_name.area), True)
            return self._view(replace(by_name, avo=avo or by_name.avo))
        if machine and len(by_machine) == 1:
            found = by_machine[0]
            conflict = bool(avo and found.avo and avo != found.avo and "-" not in found.avo)
            if conflict:
                return self._view(Station(raw, machine, avo, found.area), True)
            return self._view(replace(found, avo=avo or found.avo), bool(namekey))
        if len(by_machine) > 1:
            area = by_machine[0].area
            common = next((re.search(r"CTA\d+", s.name) for s in by_machine
                           if re.search(r"CTA\d+", s.name)), None)
            label = f"{common.group()} 辊道／电柜" if common else machine
            return self._view(Station(label, machine, avo, area), True)
        if not machine and not avo and re.search(r"\bagv\b|automated guided vehicle", raw, re.I):
            return self._view(self.stations["AGV 一期"])
        if re.search(r"\bgeneral\b", raw, re.I):
            return self._view(Station("General", machine, avo, "General"))
        if avo and not namekey and not machine and len(self.by_avo.get(avo, [])) == 1:
            return self._view(self.by_avo[avo][0])
        return self._view(Station(short or raw or "待确认工位", machine, avo), True)
