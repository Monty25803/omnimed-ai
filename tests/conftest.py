"""Shared fixtures for the OmniMed integration suite.

The suite runs inside the compose network (`docker compose -f infra/docker-compose.yml
--profile test run --rm tests`) and talks to the stack only through the public gateway,
except where a test case explicitly measures a backend component directly.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8000").rstrip("/")
KEYCLOAK = os.getenv("KEYCLOAK_URL", "http://localhost:8080").rstrip("/")
ORTHANC = os.getenv("ORTHANC_URL", "http://localhost:8042").rstrip("/")
PASSWORD = "omnimed"
RESULTS = Path(__file__).parent / "results"

_tokens: dict[str, tuple[str, float]] = {}
_metrics: dict[str, dict] = {}


def token(username: str) -> str:
    cached = _tokens.get(username)
    if cached and cached[1] - time.time() > 30:
        return cached[0]
    resp = httpx.post(f"{KEYCLOAK}/realms/omnimed/protocol/openid-connect/token",
                      data={"client_id": "omnimed-web", "grant_type": "password", "username": username, "password": PASSWORD, "scope": "openid"},
                      timeout=20)
    resp.raise_for_status()
    body = resp.json()
    _tokens[username] = (body["access_token"], time.time() + body["expires_in"])
    return body["access_token"]


class Api:
    """Thin client for one persona, addressing services as `clinical/...`, `diag/...`, `life/...`, `ai/...`."""

    def __init__(self, username: str | None):
        self.username = username
        self.http = httpx.Client(base_url=f"{GATEWAY}/api", timeout=60)

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {token(self.username)}"} if self.username else {}
        return {**h, **(extra or {})}

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        kw["headers"] = self._headers(kw.get("headers"))
        return self.http.request(method, path, **kw)

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    def delete(self, path: str, **kw) -> httpx.Response:
        return self.request("DELETE", path, **kw)

    def ok(self, method: str, path: str, expect: int | tuple[int, ...] = (200, 201), **kw):
        resp = self.request(method, path, **kw)
        codes = (expect,) if isinstance(expect, int) else expect
        assert resp.status_code in codes, f"{self.username} {method} {path} -> {resp.status_code}: {resp.text[:400]}"
        return resp.json() if "json" in resp.headers.get("content-type", "") else resp


@pytest.fixture(scope="session")
def as_user():
    clients: dict[str | None, Api] = {}

    def get(username: str | None) -> Api:
        if username not in clients:
            clients[username] = Api(username)
        return clients[username]

    yield get
    for c in clients.values():
        c.http.close()


@pytest.fixture(scope="session")
def record():
    """Stores measured numbers per test case; written to tests/results/summary.json."""

    def put(case: str, **values):
        _metrics.setdefault(case, {}).update(values)

    return put


def pytest_sessionfinish(session, exitstatus):
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "summary.json").write_text(json.dumps({"exit_status": int(exitstatus), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                       "metrics": _metrics}, indent=2, default=str))


def hl7_potassium(mrn: str, k: float, extra_obx: bool = True) -> str:
    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    msg_id = f"HL7-TEST-{time.time_ns()}"
    lines = [f"MSH|^~\\&|OMNI-ANALYZER|OMNILAB|OMNIMED|F1|{ts}||ORU^R01|{msg_id}|P|2.5.1", f"PID|1||{mrn}^^^OMNIMED^MR",
             "OBR|1|||80048^Basic metabolic panel^L", f"OBX|1|NM|2823-3^Potassium^LN||{k}|mmol/L|||||F|||{ts}"]
    if extra_obx:
        lines.append(f"OBX|2|NM|2951-2^Sodium^LN||136|mmol/L|||||F|||{ts}")
    return "\n".join(lines)
