"""Persist monitoring rules, count recent writes and record anomalies."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.dataset_rules import decode_payload
from app.datasets import find_dataset, schema_at
from app.dependencies import CurrentUser, Session, require_owner
from app.models import Dataset, DatasetMonitor, DatasetMonitorAlert, DatasetRecord, utcnow
from app.schemas import StrictModel

router = APIRouter(prefix="/api/v1/monitors", tags=["monitors"])


class AlertRule(StrictModel):
    field: str
    kind: str
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if self.kind not in {"integer", "decimal", "string"}:
            raise ValueError("Only numeric and string fields can be monitored")
        if self.kind in {"integer", "decimal"}:
            if (self.minimum is None or self.maximum is None or
                not self.minimum.is_finite() or not self.maximum.is_finite() or
                self.minimum > self.maximum):
                raise ValueError("A finite minimum and maximum are required")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("String rules do not accept numeric ranges")
        return self


class MonitorCreate(StrictModel):
    dataset_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    chart_type: str
    window_seconds: int = Field(ge=5, le=86400)
    interval_seconds: int = Field(ge=1, le=3600)
    rules: list[AlertRule] = Field(default_factory=list, max_length=100)
    activate: bool = False

    @model_validator(mode="after")
    def valid_chart(self):
        if self.chart_type not in {"line", "bar"} or self.interval_seconds > self.window_seconds:
            raise ValueError("Invalid chart type or interval")
        if (self.window_seconds + self.interval_seconds - 1) // self.interval_seconds > 1000:
            raise ValueError("Chart cannot exceed 1000 time intervals")
        if len({rule.field for rule in self.rules}) != len(self.rules):
            raise ValueError("Each alert field may appear only once")
        return self


def monitor_output(monitor, dataset_name=None):
    return {"id": monitor.id, "dataset_id": monitor.dataset_id, "dataset_name": dataset_name,
            "name": monitor.name, "chart_type": monitor.chart_type,
            "window_seconds": monitor.window_seconds, "interval_seconds": monitor.interval_seconds,
            "rules": [{key: value for key, value in rule.items() if key != "seen"}
                      for rule in monitor.rules], "active": monitor.active,
            "activated_at": monitor.activated_at.replace(tzinfo=timezone.utc).isoformat()
                            if monitor.activated_at else None}


async def monitor_for_user(session, monitor_id, user, write=False):
    monitor = await session.get(DatasetMonitor, monitor_id)
    if monitor is None:
        raise HTTPException(404, "Monitor not found")
    dataset = await find_dataset(session, monitor.dataset_id, user, write=write)
    if write:
        require_owner(user, monitor.owner_id)
    return monitor, dataset


async def field_values(session, dataset_id, names):
    values = {name: [] for name in names}
    if not names:
        return values
    result = await session.stream_scalars(select(DatasetRecord.payload)
        .where(DatasetRecord.dataset_id == dataset_id))
    async for payload in result:
        for name in names:
            cell = payload.get(name)
            if cell and cell["kind"] != "null":
                values[name].append(cell["value"])
    return values


@router.get("/datasets/{dataset_id}/fields")
async def monitor_fields(dataset_id: int, session: Session, user: CurrentUser):
    dataset = await find_dataset(session, dataset_id, user)
    _, definition = await schema_at(session, dataset)
    fields = {field.name: field.data_type for field in definition.fields
              if field.data_type in {"integer", "decimal", "string"}}
    values = await field_values(session, dataset_id, fields)
    result = {}
    for name, kind in fields.items():
        if kind in {"integer", "decimal"}:
            numbers = [Decimal(str(value)) for value in values[name]]
            result[name] = {"kind": kind, "minimum": str(min(numbers)) if numbers else None,
                            "maximum": str(max(numbers)) if numbers else None}
        else:
            result[name] = {"kind": kind, "known_count": len(set(values[name]))}
    return result


@router.post("", status_code=201)
async def create_monitor(data: MonitorCreate, session: Session, user: CurrentUser):
    dataset = await find_dataset(session, data.dataset_id, user, write=True)
    _, definition = await schema_at(session, dataset)
    kinds = {field.name: field.data_type for field in definition.fields}
    for rule in data.rules:
        if kinds.get(rule.field) != rule.kind:
            raise HTTPException(422, f"Unknown or mismatched alert field: {rule.field}")
    strings = {rule.field for rule in data.rules if rule.kind == "string"}
    existing = await field_values(session, dataset.id, strings)
    rules = [{"field": rule.field, "kind": rule.kind,
              "minimum": str(rule.minimum) if rule.minimum is not None else None,
              "maximum": str(rule.maximum) if rule.maximum is not None else None,
              "seen": sorted(set(existing[rule.field])) if rule.kind == "string" else []}
             for rule in data.rules]
    monitor = DatasetMonitor(dataset_id=dataset.id, owner_id=user.id, name=data.name,
        chart_type=data.chart_type, window_seconds=data.window_seconds,
        interval_seconds=data.interval_seconds, rules=rules, active=data.activate,
        activated_at=utcnow() if data.activate else None)
    session.add(monitor)
    await session.commit()
    return monitor_output(monitor, dataset.name)


@router.get("")
async def list_monitors(session: Session, user: CurrentUser):
    rows = (await session.execute(select(DatasetMonitor, Dataset.name)
        .join(Dataset, Dataset.id == DatasetMonitor.dataset_id)
        .order_by(DatasetMonitor.id.desc()))).all()
    return [monitor_output(monitor, name) for monitor, name in rows]


@router.post("/{monitor_id}/start")
async def start_monitor(monitor_id: int, session: Session, user: CurrentUser):
    monitor, dataset = await monitor_for_user(session, monitor_id, user, write=True)
    if not monitor.active:
        strings = {rule["field"] for rule in monitor.rules if rule["kind"] == "string"}
        existing = await field_values(session, dataset.id, strings)
        rules = monitor.rules
        for rule in rules:
            if rule["kind"] == "string":
                rule["seen"] = sorted(set(existing[rule["field"]]))
        if strings:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(monitor, "rules")
        monitor.active = True
        monitor.activated_at = utcnow()
        await session.commit()
    return monitor_output(monitor, dataset.name)


@router.post("/{monitor_id}/stop")
async def stop_monitor(monitor_id: int, session: Session, user: CurrentUser):
    monitor, dataset = await monitor_for_user(session, monitor_id, user, write=True)
    monitor.active = False
    await session.commit()
    return monitor_output(monitor, dataset.name)


def alert_reasons(payload, rules):
    reasons = []
    for rule in rules:
        cell = payload.get(rule["field"])
        if not cell or cell["kind"] == "null":
            continue
        value = cell["value"]
        if rule["kind"] in {"integer", "decimal"}:
            try:
                outside = not Decimal(rule["minimum"]) <= Decimal(str(value)) <= Decimal(rule["maximum"])
            except (InvalidOperation, ValueError):
                outside = True
            if outside:
                reasons.append({"field": rule["field"], "reason": "outside_range", "value": value})
        elif value not in rule["seen"]:
            reasons.append({"field": rule["field"], "reason": "unseen_string", "value": value})
            rule["seen"].append(value)
    return reasons


async def record_alerts(session, dataset_id, records):
    """Call inside the same transaction as a manual, imported or simulated write."""
    monitors = (await session.scalars(select(DatasetMonitor).where(
        DatasetMonitor.dataset_id == dataset_id, DatasetMonitor.active.is_(True)).with_for_update())).all()
    for monitor in monitors:
        rules = monitor.rules
        changed = False
        for record in records:
            reasons = alert_reasons(record.payload, rules)
            if reasons:
                session.add(DatasetMonitorAlert(monitor_id=monitor.id, record_id=record.id,
                                               reasons=reasons))
                changed = changed or any(reason["reason"] == "unseen_string" for reason in reasons)
        if changed:
            # SQLAlchemy does not detect in-place JSON mutations.
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(monitor, "rules")


@router.get("/{monitor_id}/snapshot")
async def monitor_snapshot(monitor_id: int, session: Session, user: CurrentUser,
    alert_limit: int = Query(10, ge=1, le=1000), alert_offset: int = Query(0, ge=0)):
    monitor, dataset = await monitor_for_user(session, monitor_id, user)
    now = utcnow()
    start = max(now - timedelta(seconds=monitor.window_seconds), monitor.activated_at or now)
    count = (monitor.window_seconds + monitor.interval_seconds - 1) // monitor.interval_seconds
    times = (await session.scalars(select(DatasetRecord.created_at).where(
        DatasetRecord.dataset_id == dataset.id, DatasetRecord.created_at >= start,
        DatasetRecord.created_at <= now))).all() if monitor.active else []
    bins = [0] * count
    for stamp in times:
        index = min(int((stamp - start).total_seconds() // monitor.interval_seconds), count - 1)
        if index >= 0:
            bins[index] += 1
    points = [{"time": (start + timedelta(seconds=i * monitor.interval_seconds))
               .replace(tzinfo=timezone.utc).isoformat(), "count": value}
              for i, value in enumerate(bins)]
    total = await session.scalar(select(func.count(DatasetMonitorAlert.id)).where(
        DatasetMonitorAlert.monitor_id == monitor.id))
    alerts = (await session.execute(select(DatasetMonitorAlert, DatasetRecord).join(
        DatasetRecord, DatasetRecord.id == DatasetMonitorAlert.record_id).where(
        DatasetMonitorAlert.monitor_id == monitor.id).order_by(DatasetMonitorAlert.id.desc())
        .offset(alert_offset).limit(alert_limit))).all()
    return {"monitor": monitor_output(monitor, dataset.name), "points": points,
        "alert_total": total, "alerts": [{"id": alert.id, "record_id": record.id,
        "created_at": alert.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "reasons": alert.reasons, "data": decode_payload(record.payload)} for alert, record in alerts]}


@router.get("/{monitor_id}/alerts")
async def list_monitor_alerts(monitor_id: int, session: Session, user: CurrentUser,
    limit: int = Query(500, ge=1, le=500), offset: int = Query(0, ge=0)):
    await monitor_for_user(session, monitor_id, user)
    rows = (await session.execute(select(DatasetMonitorAlert, DatasetRecord).join(
        DatasetRecord, DatasetRecord.id == DatasetMonitorAlert.record_id).where(
        DatasetMonitorAlert.monitor_id == monitor_id).order_by(DatasetMonitorAlert.id)
        .offset(offset).limit(limit))).all()
    return [{"id": alert.id, "record_id": record.id,
        "created_at": alert.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "reasons": alert.reasons, "data": decode_payload(record.payload)} for alert, record in rows]
