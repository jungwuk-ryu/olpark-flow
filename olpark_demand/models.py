"""Count forecasting candidates with portable on-disk formats."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from .features import CALENDAR_FEATURES, EVENT_FEATURES, WEATHER_FEATURES
from .negbin import NBModel, fit_negbin


CANDIDATES = ("weekday_median", "cat_calendar", "cat_event", "cat_weather", "pooled_nb")
CAT_PARAMS = {
    "iterations": 300, "depth": 5, "learning_rate": 0.06, "loss_function": "Poisson",
    "one_hot_max_size": 32, "has_time": True, "thread_count": 4,
    "random_seed": 20261123, "allow_writing_files": False, "verbose": False,
}


def numerical_features(name):
    if name not in CANDIDATES:
        raise ValueError(f"Unknown candidate {name}")
    if name == "weekday_median":
        return []
    columns = ["hour", *CALENDAR_FEATURES]
    if name in ("cat_event", "cat_weather", "pooled_nb"):
        columns.extend(EVENT_FEATURES)
    if name in ("cat_weather", "pooled_nb"):
        columns.extend(WEATHER_FEATURES)
    return list(dict.fromkeys(columns))


class WeekdayMedian:
    """Use training data at a point, local hour and weekday, with explicit fallbacks."""

    def __init__(self, weekday, hour, pooled):
        self.weekday = weekday
        self.hour = hour
        self.pooled = pooled

    @classmethod
    def fit(cls, frame):
        data = frame.assign(dow=pd.to_datetime(frame["date"]).dt.dayofweek)
        weekday = data.groupby(["site", "hour", "dow"])["y"].median().to_dict()
        hour = data.groupby(["site", "hour"])["y"].median().to_dict()
        return cls(weekday, hour, float(data.y.median()))

    def predict(self, frame):
        dow = pd.to_datetime(frame["date"]).dt.dayofweek
        return np.array([self.weekday.get((s, h, d), self.hour.get((s, h), self.pooled))
                         for s, h, d in zip(frame.site, frame.hour, dow)], dtype=float)

    def save(self, path):
        record = {"weekday": [[*key, val] for key, val in self.weekday.items()],
                  "hour": [[*key, val] for key, val in self.hour.items()], "pooled": self.pooled}
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path):
        record = json.loads(path.read_text(encoding="utf-8"))
        return cls({(s, h, d): v for s, h, d, v in record["weekday"]},
                   {(s, h): v for s, h, v in record["hour"]}, record["pooled"])


def model_path(folder: Path, name: str):
    if name not in CANDIDATES:
        raise ValueError(f"Unknown candidate {name}")
    return folder / (name + (".cbm" if name.startswith("cat_") else ".json"))


def fit(name, frame):
    if name == "weekday_median":
        return WeekdayMedian.fit(frame)
    columns = numerical_features(name)
    if name == "pooled_nb":
        # NB supplies its own hour and weekday background, including point interactions.
        predictors = [c for c in columns if c not in ("hour", "weekday")]
        return fit_negbin(frame, predictors)
    model = CatBoostRegressor(**CAT_PARAMS)
    inputs = frame[["site", *columns]]
    model.fit(inputs, frame["y"], cat_features=["site"])
    return model


def predict(name, model, frame):
    if name.startswith("cat_"):
        result = model.predict(frame[["site", *numerical_features(name)]],
                               prediction_type="Exponent", thread_count=4)
    else:
        result = model.predict(frame)
    result = np.asarray(result, dtype=float)
    if result.shape != (len(frame),) or not np.isfinite(result).all() or np.any(result < 0):
        raise ValueError(f"Invalid count forecast from {name}")
    return result


def save(folder: Path, name, model):
    path = model_path(folder, name)
    if name.startswith("cat_"):
        model.save_model(str(path), format="cbm")
    else:
        model.save(path)


def load(folder: Path, name):
    path = model_path(folder, name)
    if name == "weekday_median":
        return WeekdayMedian.load(path)
    if name == "pooled_nb":
        return NBModel.load(path)
    model = CatBoostRegressor()
    model.load_model(str(path), format="cbm")
    return model
