"""Do the operator's hourly files and Seoul's card-based daily table count the same riders?

The model was fitted on the operators' hourly station files. The autumn counts come from
Seoul's card-tag files, whose station-day totals are published separately in the daily
table ``CardSubwayStatsNew``. Before any autumn count is opened, this compares the two
on dates the model has already seen: every third date from 2026-03-14 (the first date
the card-tag files cover) to the end of each operator file.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/source_levels.py
"""
import gzip
import io
import json
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import urlopen
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
API = "http://openapi.seoul.go.kr:8088/sample/json/CardSubwayStatsNew/1/5/"
NAMES = {"5:2556": ("5호선", "올림픽공원(한국체대)"), "9:4136": ("9호선2~3단계", "올림픽공원(한국체대)"),
         "8:2814": ("8호선", "몽촌토성(평화의문)"), "9:4135": ("9호선2~3단계", "한성백제")}


def daily_table(day: str, site: str) -> dict | None:
    """Boardings and alightings of one station-line on one date from the public daily table."""
    line, station = NAMES[site]
    url = API + day.replace("-", "") + "/" + quote(line + "/" + station, safe="/")
    for attempt in range(4):
        try:
            with urlopen(url, timeout=20) as response:
                body = json.load(response)
            rows = body.get("CardSubwayStatsNew", {}).get("row", [])
            if not rows:
                return None
            row = rows[0]
            if row["USE_YMD"] != day.replace("-", "") or row["SBWY_ROUT_LN_NM"] != line or row["SBWY_STNS_NM"] != station:
                raise ValueError("The daily table answered for another station or date")
            return {"on": int(float(row["GTON_TNOPE"])), "off": int(float(row["GTOFF_TNOPE"])), "registered": row["REG_YMD"]}
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))


def operator_days() -> pd.DataFrame:
    """Full-day boardings and alightings per station-line from the operators' hourly files (every hour column)."""
    raw = ROOT / "experiments/capacity_study/external_rail/raw"
    stations = {s.split(":")[1] for s in NAMES}
    parts = []
    table = pd.read_csv(io.BytesIO(gzip.decompress((raw / "metro1_8_20260630.csv.gz").read_bytes())), encoding="cp949", dtype=str)
    table = table[table["역번호"].isin(stations)]
    hours = [c for c in table.columns if "시간대" in c]
    values = table[hours].apply(lambda c: pd.to_numeric(c.str.replace(",", ""), errors="raise"))
    table = table.assign(persons=values.sum(axis=1), before6=values[[c for c in hours if c.startswith("06시간대이전")]].sum(axis=1),
                         site=table["호선명"].str.replace("호선", "") + ":" + table["역번호"],
                         direction=table["승하차구분"].map({"승차": "on", "하차": "off"}))
    parts.append(table.rename(columns={"수송일자": "date"}).groupby(["date", "site", "direction"], as_index=False)[["persons", "before6"]].sum())
    with zipfile.ZipFile(raw / "metro9_20260731.zip") as archive:
        name = next(n for n in archive.namelist() if n[-12:-8] == "2026")
        table = pd.read_csv(io.BytesIO(archive.read(name)), encoding="cp949", dtype=str)
    table = table[table["역번호"].isin(stations)]
    hours = [c for c in table.columns if "시-" in c]
    values = table[hours].apply(lambda c: pd.to_numeric(c.str.replace(",", ""), errors="coerce"))
    early = [c for c in hours if int(c[:2]) in (2, 3, 4, 5)]
    table = table.assign(persons=values.sum(axis=1), before6=values[early].sum(axis=1), site="9:" + table["역번호"],
                         direction=table["구분"].map({"순승차": "on", "순하차": "off"}))
    table = table[table.direction.notna()].rename(columns={"날짜": "date"})
    # The line 9 file repeats some alighting rows with different values; neither is usable.
    table = table[table.groupby(["date", "site", "direction"]).persons.transform("size").eq(1)]
    parts.append(table[["date", "site", "direction", "persons", "before6"]])
    return pd.concat(parts, ignore_index=True)


def main():
    operator = operator_days().set_index(["date", "site", "direction"])
    rows = []
    for stamp in pd.date_range("2026-03-14", "2026-07-31", freq="3D"):
        day = stamp.strftime("%Y-%m-%d")
        for site in NAMES:
            if (day, site, "on") not in operator.index:
                continue
            public = daily_table(day, site)
            if public is None:
                continue
            for direction in ("on", "off"):
                if (day, site, direction) in operator.index:
                    entry = operator.loc[(day, site, direction)]
                    rows.append({"date": day, "site": site, "direction": direction, "operator_hourly_file": int(entry.persons),
                                 "of_which_before_06": int(entry.before6), "card_daily_table": public[direction]})
            time.sleep(0.15)
    frame = pd.DataFrame(rows)
    frame["ratio"] = frame.operator_hourly_file / frame.card_daily_table
    frame.to_csv(HERE / "source_levels.csv", index=False)
    summary = {}
    for (site, direction), part in frame.groupby(["site", "direction"]):
        summary[f"{site}:{direction}"] = {
            "days": int(len(part)), "operator_total": int(part.operator_hourly_file.sum()), "card_total": int(part.card_daily_table.sum()),
            "ratio_of_totals": round(float(part.operator_hourly_file.sum() / part.card_daily_table.sum()), 4),
            "median_daily_ratio": round(float(part.ratio.median()), 4),
            "daily_ratio_p10_p90": [round(float(v), 4) for v in np.quantile(part.ratio, [0.1, 0.9])],
            "share_before_06": round(float(part.of_which_before_06.sum() / part.operator_hourly_file.sum()), 4)}
    (HERE / "source_levels.json").write_text(json.dumps({
        "question": "operator hourly files (model's training source) over Seoul's card-based daily table, same station-line and date",
        "dates": "every third date from 2026-03-14 to the end of each operator file", "by_site_direction": summary},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
