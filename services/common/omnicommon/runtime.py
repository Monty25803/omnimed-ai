import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
import nats
from fastapi import FastAPI, HTTPException, Request
from nats.js import JetStreamContext

log = logging.getLogger("omnimed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# Per-request client logs flood the hot DICOM frame path and leak study/instance UIDs into logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgres://omnimed:omnimed@localhost:5432/omnimed")
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    clinical_url: str = os.getenv("CLINICAL_URL", "http://localhost:8081")
    internal_token: str = os.getenv("INTERNAL_TOKEN", "dev-internal-token")
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "omnimed")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "omnimed-secret")
    minio_secure: bool = os.getenv("MINIO_SECURE", "false") == "true"
    reports_bucket: str = os.getenv("REPORTS_BUCKET", "reports")
    pki_bucket: str = os.getenv("PKI_BUCKET", "pki")
    volume_bucket: str = os.getenv("VOLUME_BUCKET", "volumes")


settings = Settings()


@dataclass
class Principal:
    username: str
    role: str
    facility_id: str | None
    staff: dict[str, Any] = field(default_factory=dict)
    token: str = ""

    @property
    def display_name(self) -> str:
        return self.staff.get("display_name") or self.username


class Runtime:
    def __init__(self) -> None:
        self.db: asyncpg.Pool | None = None
        self.nc: nats.NATS | None = None
        self.js: JetStreamContext | None = None
        self.http = httpx.AsyncClient(timeout=10.0)
        self.service = "unknown"

    async def start(self, service: str) -> None:
        self.service = service
        for attempt in range(60):
            try:
                self.db = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10, init=_init_conn)
                break
            except Exception as exc:  # noqa: BLE001
                log.info("waiting for postgres (%s): %s", attempt, exc)
                await asyncio.sleep(2)
        else:
            raise RuntimeError("postgres unavailable")
        for attempt in range(60):
            try:
                self.nc = await nats.connect(settings.nats_url, name=service, max_reconnect_attempts=-1)
                self.js = self.nc.jetstream()
                break
            except Exception as exc:  # noqa: BLE001
                log.info("waiting for nats (%s): %s", attempt, exc)
                await asyncio.sleep(2)
        else:
            raise RuntimeError("nats unavailable")

    async def stop(self) -> None:
        if self.nc:
            await self.nc.drain()
        if self.db:
            await self.db.close()
        await self.http.aclose()

    async def publish(self, subject: str, payload: dict[str, Any]) -> None:
        assert self.js is not None
        await self.js.publish(subject, json.dumps(payload, default=_json_default).encode())

    async def audit(self, p: Principal | None, action: str, resource: str, resource_id: str = "", outcome: str = "allowed", **detail: Any) -> None:
        event = {
            "actor": p.username if p else "anonymous",
            "role": p.role if p else "",
            "action": action,
            "resource": resource,
            "resource_id": str(resource_id),
            "facility": (p.facility_id or "") if p else "",
            "outcome": outcome,
            "detail": {"service": self.service, **detail},
        }
        try:
            await self.publish("audit.record", event)
        except Exception as exc:  # noqa: BLE001
            log.error("AUDIT PUBLISH FAILED %s: %s", event, exc)

    async def authorize(self, request: Request, patient_id: str | None = None, action: str = "read", roles: list[str] | None = None) -> Principal:
        """Delegates token verification and ABAC to the clinical policy engine."""
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        body = {"token": token, "patient_id": patient_id or "", "action": action, "roles": roles or []}
        resp = await self.http.post(f"{settings.clinical_url}/internal/authz", json=body, headers={"X-Internal-Token": settings.internal_token})
        data = resp.json()
        if resp.status_code != 200 or not data.get("allow"):
            raise HTTPException(status_code=403, detail=data.get("reason", "forbidden"))
        pr = data["principal"]
        staff = pr.get("staff") or {}
        return Principal(username=pr["username"], role=pr["role"], facility_id=staff.get("facility_id"), staff=staff, token=token)

    async def clinical_get(self, p: Principal, path: str) -> Any:
        resp = await self.http.get(f"{settings.clinical_url}{path}", headers={"Authorization": f"Bearer {p.token}"})
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.json().get("error", "clinical service error"))
        return resp.json()


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=lambda v: json.dumps(v, default=_json_default), decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=lambda v: json.dumps(v, default=_json_default), decoder=json.loads, schema="pg_catalog")


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.astimezone(timezone.utc).isoformat()
    raise TypeError(f"not serializable: {type(o)}")


rt = Runtime()


def create_app(service: str, title: str, on_start=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await rt.start(service)
        if on_start:
            await on_start()
        log.info("%s ready", service)
        yield
        await rt.stop()

    app = FastAPI(title=title, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": service}

    return app


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
