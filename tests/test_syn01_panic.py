"""TC-SYN-01: a potassium panic from the analyzer reaches doctor, nurse and pharmacist over SSE
in under 500 ms, the potassium-sparing order is auto-held and a STAT ECG task is raised."""
from __future__ import annotations

import json
import queue
import statistics
import threading
import time

import httpx

from conftest import GATEWAY, hl7_potassium, token

TARGET_MS = 500
ROUNDS = 10
TEAM = ("dr.rao", "nurse.anita", "pharm.kiran")


class AlertListener(threading.Thread):
    def __init__(self, username: str):
        super().__init__(daemon=True)
        self.username = username
        self.events: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.stop = threading.Event()

    def run(self):
        headers = {"Authorization": f"Bearer {token(self.username)}", "Accept": "text/event-stream"}
        with httpx.stream("GET", f"{GATEWAY}/api/clinical/alerts/stream", headers=headers, timeout=httpx.Timeout(10, read=None)) as resp:
            assert resp.status_code == 200, resp.status_code
            event = None
            for line in resp.iter_lines():
                if self.stop.is_set():
                    return
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    if event == "ready":
                        self.ready.set()
                    elif event == "alert":
                        self.events.put((time.perf_counter(), json.loads(line[5:])))

    def wait_for(self, result_ids: set[int], timeout: float = 5.0):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                at, alert = self.events.get(timeout=max(0.01, deadline - time.perf_counter()))
            except queue.Empty:
                break
            if alert.get("kind") == "lab.panic" and alert.get("payload", {}).get("result_id") in result_ids:
                return at, alert
        return None, None


def _reset_spironolactone(doctor):
    meds = doctor.ok("GET", "clinical/prescriptions", params={"patient_id": "P0001"})
    spiro = [m for m in meds if m["rxcui"] == "9997" and m["status"] in ("active", "on-hold")]
    assert spiro, "seeded spironolactone order for P0001 is missing"
    for m in spiro:
        if m["status"] == "on-hold":
            doctor.ok("POST", f"clinical/prescriptions/{m['id']}/release", json={"reason": "TC-SYN-01 reset"})
    return spiro[0]["id"]


def test_potassium_panic_fanout(as_user, record):
    doctor, nurse, lab = as_user("dr.rao"), as_user("nurse.anita"), as_user("lab.suresh")
    listeners = {u: AlertListener(u) for u in TEAM}
    for listener in listeners.values():
        listener.start()
    for listener in listeners.values():
        assert listener.ready.wait(10), f"SSE stream for {listener.username} never became ready"

    latencies: dict[str, list[float]] = {u: [] for u in TEAM}
    server_ms: list[float] = []
    try:
        for _ in range(ROUNDS):
            order_id = _reset_spironolactone(doctor)
            t0 = time.perf_counter()
            ingest = lab.ok("POST", "diag/lims/hl7", json={"message": hl7_potassium("MRN-100001", 6.8)})
            assert ingest["panic_count"] == 1
            result_ids = {r["result_id"] for r in ingest["results"] if r["flag"] == "HH"}

            for u, listener in listeners.items():
                at, alert = listener.wait_for(result_ids)
                assert alert is not None, f"{u} received no panic alert"
                latencies[u].append((at - t0) * 1000)
                assert alert["severity"] in ("critical", "high")
                if u == "dr.rao":
                    assert "Auto-held" in alert["message"] and "spironolactone" in alert["message"]
                    assert "lisinopril" in alert["message"], "ACE inhibitor should be flagged for review"
                    p = alert["payload"]
                    from datetime import datetime
                    server_ms.append((datetime.fromisoformat(p["synapse_at"].replace("Z", "+00:00")) -
                                      datetime.fromisoformat(p["lab_received_at"].replace("Z", "+00:00"))).total_seconds() * 1000)

            held = [m for m in doctor.ok("GET", "clinical/prescriptions", params={"patient_id": "P0001"}) if m["id"] == order_id][0]
            assert held["status"] == "on-hold" and "Clinical Synapse" in (held["hold_reason"] or "")
            tasks = nurse.ok("GET", "clinical/tasks")
            assert any(t["patient_id"] == "P0001" and t["kind"] == "stat_ecg" for t in tasks), "no STAT ECG task for the nurse"
    finally:
        for listener in listeners.values():
            listener.stop.set()
        _reset_spironolactone(doctor)

    every = [v for vals in latencies.values() for v in vals]
    p95 = sorted(every)[max(0, int(len(every) * 0.95) - 1)]
    record("TC-SYN-01", rounds=ROUNDS, target_ms=TARGET_MS,
           per_recipient_p50_ms={u: round(statistics.median(v), 1) for u, v in latencies.items()},
           p50_ms=round(statistics.median(every), 1), p95_ms=round(p95, 1), max_ms=round(max(every), 1),
           synapse_processing_p50_ms=round(statistics.median(server_ms), 1))
    assert p95 < TARGET_MS, f"p95 end-to-end latency {p95:.0f} ms exceeds {TARGET_MS} ms"
