"""합성 교환 예제 검산. Python 3.10+ 및 jsonschema 4.x.

    python -m pip install 'jsonschema>=4.18,<5'
    python validate_example.py [교환파일.json]
    python validate_example.py --self-test

보행망 커밋의 두 경로로 만든 작은 수신자 측 검사용 목록을 사용한다. 실서비스는
같은 검사를 전체 경로 레지스트리, 시간별 통제 스냅샷, 불변 실행 저장소에 연결한다.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError


HERE = Path(__file__).resolve().parent
NETWORK = "48e50c3614e2b8786062278ede9c2b822ea6f4b7"
# src/data/network.json, routes.json, parking.json의 example.json에 필요한 부분.
ORIGINS = {"n-kspo-exit-south": "kspo-dome", "n-kspo-exit-east": "kspo-dome"}
SINKS = {"n-dest-a": "rail", "n-dest-b": "rail"}
ROUTES = {
    ("n-kspo-exit-south", "n-dest-a"): {"e-01", "e-02", "e-03", "e-04", "e-station-a"},
    ("n-kspo-exit-east", "n-dest-b"): {
        "e-14", "e-01", "e-05", "e-06", "e-07", "e-08", "e-09", "e-10", "e-11", "e-station-b"
    },
}
GATES = {"gate-east-2"}
TIME = datetime.fromisoformat
FIVE = timedelta(minutes=5)
# 합성 상태. 엔진은 출발 뒤 실제 이동 중에 만나는 통제도 별도로 처리한다.
CLOSURES = {
    "example-normal-routes-v1": [],
    "example-edge-closed-v1": [("e-02", TIME("2026-11-28T23:55:00+09:00"), TIME("2026-11-29T00:00:00+09:00"))],
}


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def read_json(path):
    def unique_fields(pairs):
        values = {}
        for key, value in pairs:
            require(key not in values, f"중복 JSON 키 {key}")
            values[key] = value
        return values
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_fields)


def validate(packet, schema):
    schema.validate(packet)
    require(packet["usage"] == "synthetic_example", "예제 검산기는 합성 자료 전용")
    versions = packet["versions"]
    require(versions["network_revision"] == NETWORK, "그래프 버전 불일치")
    require(versions["mapping_version"] == "example-routing-v1", "매핑 버전 불일치")
    require(versions["availability_version"] in CLOSURES, "통제 버전 미등록")
    closures = CLOSURES[versions["availability_version"]]
    require(packet["run_id"] != packet["supersedes_run_id"], "자기 자신을 대체하는 실행")
    start, end = (TIME(packet["horizon"][key]) for key in ("start", "end"))
    as_of = TIME(packet["as_of"])
    require(as_of <= TIME(packet["issued_at"]), "발행 시각이 기준시각보다 빠름")
    require(as_of <= start < end, "예측 기준시각과 분석창 관계 오류")
    require(all(t.second == 0 and t.minute % 5 == 0 for t in (start, end)), "분석창 5분 경계 오류")
    evidence_ids = {item["id"] for item in packet["evidence"]}
    require(len(evidence_ids) == len(packet["evidence"]), "근거 ID 중복")
    for item in packet["evidence"]:
        require(TIME(item["available_at"]) <= as_of, "예측 이후 공개된 근거")

    def check_refs(obj):
        require(set(obj["evidence_ids"]) <= evidence_ids, "미등록 근거 ID")

    def bin_start(raw):
        t = TIME(raw)
        require(start <= t and t + FIVE <= end, "분석창 밖의 출발 구간")
        require(t.second == 0 and t.minute % 5 == 0, "5분 경계 밖의 출발 시각")
        return t

    def path_is_closed(origin, sink, departure):
        ids = ROUTES[origin, sink] | {origin, sink}
        return any(node in ids and begin < departure + FIVE and departure < stop
                   for node, begin, stop in closures)

    check_refs(packet["scenario"])
    event_ids = set()
    totals = Counter()
    for ev in packet["events"]:
        event_id, venue = ev["event_id"], ev["venue_id"]
        require(event_id not in event_ids, "공연 회차 ID 중복")
        event_ids.add(event_id)
        check_refs(ev)
        if ev["expected_end_at"] is not None:
            TIME(ev["expected_end_at"])
        ledger = ev["ledger"]
        if ev["status"] == "cancelled_before_admission":
            require(ledger["total_exit_persons"] == 0, "입장 전 취소 공연에 남은 수요")
        keys = set()
        for row in ev["od"]:
            check_refs(row)
            t = bin_start(row["departure_start"])
            origin, sink, mode = row["origin_node_id"], row["destination_node_id"], row["first_mode"]
            require(ORIGINS.get(origin) == venue, "출발 노드와 공연장 불일치")
            require(SINKS.get(sink) == mode and (origin, sink) in ROUTES, "미등록 목적지·수단·경로")
            require(not path_is_closed(origin, sink, t), "출발 구간에 폐쇄된 경로")
            k = (origin, sink, mode, t)
            require(k not in keys, "OD 키 중복")
            keys.add(k)
        residual_keys = set()
        for row in ev["unrouted"]:
            check_refs(row)
            t = bin_start(row["departure_start"]) if row["departure_start"] is not None else None
            origin, sink, mode = row["origin_node_id"], row["intended_destination_id"], row["first_mode"]
            reason = row["reason"]
            k = (origin, sink, mode, t, reason)
            require(k not in residual_keys, "미배정 키 중복")
            residual_keys.add(k)
            if reason == "unknown_first_mode":
                require(mode == "unknown", "미확인 수단 표기 오류")
            elif reason == "time_unresolved":
                require(t is None, "미확인 시간 표기 오류")
            elif reason == "unmapped_origin":
                require(ORIGINS.get(origin) != venue, "등록한 출발 노드를 미매핑 처리")
            elif reason == "unmapped_destination":
                require(SINKS.get(sink) != mode, "등록한 목적지를 미매핑 처리")
            elif reason == "unreachable":
                require(ORIGINS.get(origin) == venue and SINKS.get(sink) == mode
                        and (origin, sink) not in ROUTES, "도달 불가 근거 오류")
            elif reason == "route_closed":
                require(t is not None and ORIGINS.get(origin) == venue and SINKS.get(sink) == mode
                        and (origin, sink) in ROUTES and path_is_closed(origin, sink, t), "통제 근거 오류")
        require(sum(r["persons"] for r in ev["od"]) == ledger["routed_within_horizon_persons"], "배정 합계 불일치")
        require(sum(r["persons"] for r in ev["unrouted"]) == ledger["unassigned_within_horizon_persons"], "미배정 합계 불일치")
        parts = sum(count for name, count in ledger.items() if name != "total_exit_persons")
        require(ledger["total_exit_persons"] == parts, f"{event_id} 인원 보존 실패")
        totals.update(ledger)
    require(dict(totals) == packet["totals"], "공연 합계와 전체 장부 불일치")
    intervals = defaultdict(list)
    for row in packet["vehicle_gate_forecasts"]:
        check_refs(row)
        gate = row["gate_id"]
        require(gate in GATES, "미등록 차량 출차문")
        t, u = TIME(row["interval_start"]), TIME(row["interval_end"])
        require(start <= t < u <= end, "차량 관측구간 범위 오류")
        require((u - t).total_seconds() == 60 * row["source_resolution_minutes"], "차량 관측 단위 불일치")
        if row["vehicles"] > 0:
            require(not any(node == gate and begin <= t and u <= stop for node, begin, stop in closures),
                    "전 구간 폐쇄된 차량 출차문")
        intervals[gate].append((t, u))
        pi = row["prediction_interval"]
        if pi is not None:
            require(pi["lower_vehicles"] <= row["vehicles"] <= pi["upper_vehicles"], "차량 분위수 순서 오류")
    for spans in intervals.values():
        spans.sort()
        require(all(first[1] <= second[0] for first, second in zip(spans, spans[1:])), "차량 구간 중첩")


def self_test(schema):
    fixture = read_json(HERE / "example.json")
    validate(fixture, schema)
    late_cancellation = deepcopy(fixture)
    late_cancellation["events"][0]["status"] = "cancelled_after_admission"
    validate(late_cancellation, schema)
    first = ("events", 0)
    od = first + ("od", 0)
    gate = ("vehicle_gate_forecasts", 0)
    bad_values = [
        (("versions", "network_revision"), "0" * 40),
        (("versions", "availability_version"), "example-edge-closed-v1"),
        (("as_of",), "2026-11-28T12:00:00Z"),
        (("evidence", 0, "available_at"), "2026-11-28T23:00:00+09:00"),
        (("supersedes_run_id",), fixture["run_id"]),
        (od + ("persons",), -1),
        (od + ("destination_node_id",), "unknown-node"),
        (od + ("first_mode",), "bus"),
        (od + ("origin_node_id",), "wrong-origin"),
        (od + ("evidence_ids",), ["unknown"]),
        (od + ("departure_start",), fixture["horizon"]["end"]),
        (od + ("departure_start",), "2026-11-28T23:56:00+09:00"),
        (first + ("od",), fixture["events"][0]["od"] * 2),
        (first + ("status",), "cancelled_before_admission"),
        (("events", 1, "event_id"), fixture["events"][0]["event_id"]),
        (("totals", "total_exit_persons"), 145),
        (first + ("ledger", "total_exit_persons"), 121),
        (gate + ("unit",), "persons"),
        (gate + ("source_resolution_minutes",), 5),
        (gate + ("gate_id",), "n-dest-a"),
        (("vehicle_gate_forecasts",), fixture["vehicle_gate_forecasts"] * 2),
        (("scenario", "kind"), "joint_predictive_draw"),
    ]
    for path, bad in bad_values:
        item = deepcopy(fixture)
        parent = item
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = bad
        try:
            validate(item, schema)
        except (ValueError, ValidationError):
            continue
        raise AssertionError(f"오류 데이터가 통과했습니다. 변경 경로 {path}")
    print(f"정상 2건과 오류 {len(bad_values)}건을 검산했습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="올림픽공원 퇴장 수요 합성 교환 예제 검산")
    parser.add_argument("packet", type=Path, nargs="?", default=HERE / "example.json")
    parser.add_argument("--self-test", action="store_true", help="내장된 정상·오류 사례도 검사합니다.")
    args = parser.parse_args()
    spec = read_json(HERE / "demand-v1.schema.json")
    Draft202012Validator.check_schema(spec)
    validator = Draft202012Validator(spec)
    validate(read_json(args.packet), validator)
    print("합성 예제의 구조, 시각, 경로, 단위와 인원 장부를 확인했습니다.")
    if args.self_test:
        self_test(validator)
