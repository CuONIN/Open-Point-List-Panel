"""识别定子线工位、变型列错位与多工位组合。 / Resolve PAL station identifiers, displaced cells and station groups."""

import re
import unicodedata


def variant_station(value):
    """仅在变型值可识别为工位时回退。 / Accept only identifiable station values in the variant column."""
    return bool(re.search(r"\b201[-\s]?\d{3}\b|\blift\b|elevator|kühltunnel|allgemein|^all$|convey|^alr$", value, re.I))


class StatorStations:
    def resolve(self, value):
        raw = unicodedata.normalize("NFKC", value).strip()
        names = []
        # 斜杠后的三位数沿用 201 前缀。 / Expand abbreviated suffixes in multi-station groups.
        for group in re.findall(r"(?<!\d)201[-\s]?\d{3}(?:\s*/\s*(?:201-)?\d{3})*(?!\d)", raw):
            names.extend("201-" + part.strip()[-3:] for part in group.split("/"))
        if re.search(r"\blift\b|elevator", raw, re.I):
            names.insert(0, "Elevator")
        if not names:
            names = ["全线" if raw.casefold() in {"all", "allgemein"} else raw or "待核"]
        names = list(dict.fromkeys(names))
        return dict(station=" / ".join(names), station_names=names, machine="", avo="", area="",
                    station_review=not bool(raw))
