"""One evening's station arrival curve for a bundle, fitted to five-minute boardings.

The fit now lives in the package (``olpark_demand/station_curve.py``) so that the update
cycle can refit the curve whenever new evenings are collected. This script keeps the
earlier command working.

    PYTHONPATH=. python experiments/response_model/station_curve_5min.py artifacts/egress-response/BUNDLE
"""
import json
from pathlib import Path
import sys

from olpark_demand import station_curve

if __name__ == "__main__":
    print(json.dumps(station_curve.fit(Path(sys.argv[1]).resolve()), ensure_ascii=False, indent=1))
