"""WebSocket-driven simulator: one dataset write and response per second."""
import asyncio
import logging
import random
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.database import SessionFactory
from app.dataset_rules import validate_rows
from app.datasets import check_unique_values, find_dataset, record_output, schema_at
from app.models import DatasetRecord, DatasetUniqueValue, User
from app.monitors import record_alerts
from app.security import decode_token

router = APIRouter(tags=["generator"])
logger = logging.getLogger(__name__)


def simulated_row(definition, sample_strings):
    moment = datetime.now(timezone.utc)
    identity = int(moment.timestamp() * 1_000_000)
    row = {}
    for field in definition.fields:
        kind = field.data_type
        if kind == "integer":
            row[field.name] = identity if field.unique else random.randint(0, 100)
        elif kind == "decimal":
            row[field.name] = str(identity) if field.unique else f"{random.uniform(0, 100):.3f}"
        elif kind == "datetime":
            row[field.name] = moment.isoformat()
        elif kind == "boolean":
            row[field.name] = bool(random.getrandbits(1))
        else:
            samples = sample_strings.get(field.name, [])
            row[field.name] = (random.choice(samples) if samples and not field.unique and random.random() < 0.8
                               else f"sample-{identity}")
    return row


@router.websocket("/api/v1/generator/ws")
async def generator_socket(websocket: WebSocket):
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        user_id = int(decode_token(first["token"])["sub"])
        dataset_id = int(first["dataset_id"])
        async with SessionFactory() as session:
            user = await session.get(User, user_id)
            if user is None or not user.is_active:
                raise ValueError("Invalid user")
            dataset = await find_dataset(session, dataset_id, user, write=True)
            _, definition = await schema_at(session, dataset)
            string_fields = [field.name for field in definition.fields if field.data_type == "string"]
            sample_strings = {name: [] for name in string_fields}
            if string_fields:
                payloads = await session.stream_scalars(select(DatasetRecord.payload)
                    .where(DatasetRecord.dataset_id == dataset_id).limit(500))
                async for payload in payloads:
                    for name in string_fields:
                        cell = payload.get(name)
                        if cell and cell["kind"] == "string" and cell["value"] not in sample_strings[name]:
                            sample_strings[name].append(cell["value"])
        await websocket.send_json({"status": "started", "dataset_id": dataset_id})
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=1)
                if message.get("action") == "stop":
                    await websocket.send_json({"status": "stopped"})
                    break
            except TimeoutError:
                pass
            async with SessionFactory() as session:
                user = await session.get(User, user_id)
                if user is None or not user.is_active:
                    raise ValueError("User no longer active")
                dataset = await find_dataset(session, dataset_id, user, write=True)
                version, definition = await schema_at(session, dataset)
                row = simulated_row(definition, sample_strings)
                payload = validate_rows([row], definition, "manual")[0]
                claims = await check_unique_values(session, dataset.id, definition, [payload])
                record = DatasetRecord(dataset_id=dataset.id, schema_version_id=version.id,
                    owner_id=user.id, payload=payload, source="simulate")
                session.add(record)
                await session.flush()
                session.add_all([DatasetUniqueValue(dataset_id=dataset.id, record_id=record.id,
                    field_name=name, value_hash=value_hash) for _, name, value_hash in claims])
                await record_alerts(session, dataset.id, [record])
                await session.commit()
                output = record_output(record, version.number)
            await websocket.send_json({"status": "created", "record": output})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError, HTTPException) as exc:
        try:
            await websocket.send_json({"status": "error", "message": str(exc)})
        except (RuntimeError, WebSocketDisconnect):
            pass
    except Exception:
        logger.exception("Simulated dataset generator failed")
        try:
            await websocket.send_json({"status": "error", "message": "Generator stopped due to server error"})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
