"""Exercise persisted monitoring, alerts, chart counts and WebSocket simulation."""
import asyncio
import csv
import io
import json
import os
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect

async def main():
    username = os.getenv("MONITOR_TEST_USERNAME", f"uitest_monitor_{uuid4().hex[:10]}")
    password = "MonitorCheck123!"
    base = os.getenv("API_BASE_URL", "http://api:8000")
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        try:
            response = await client.post("/api/v1/users", json={"username": username, "password": password})
            response.raise_for_status()
            token = (await client.post("/api/v1/auth/tokens",
                json={"username": username, "password": password})).json()["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"
            dataset = (await client.post("/api/v1/datasets", json={"name": "monitor-check",
                "fields": [{"name": "value", "data_type": "decimal"},
                           {"name": "label", "data_type": "string"}]})).json()
            did = dataset["id"]
            response = await client.post(f"/api/v1/datasets/{did}/records",
                json={"schema_version": 1, "data": {"value": "5", "label": "known"}})
            response.raise_for_status()
            stats = (await client.get(f"/api/v1/monitors/datasets/{did}/fields")).json()
            assert stats["value"]["minimum"] == "5"
            monitor = (await client.post("/api/v1/monitors", json={"dataset_id": did,
                "name": "test", "chart_type": "line", "window_seconds": 30,
                "interval_seconds": 1, "rules": [
                    {"field": "value", "kind": "decimal", "minimum": "0", "maximum": "10"},
                    {"field": "label", "kind": "string"}], "activate": False})).json()
            mid = monitor["id"]
            assert monitor["active"] is False
            response = await client.post(f"/api/v1/monitors/{mid}/start")
            response.raise_for_status()
            response = await client.post(f"/api/v1/datasets/{did}/records",
                json={"schema_version": 1, "data": {"value": "12.5", "label": "new"}})
            response.raise_for_status()
            anomalous_id = response.json()["id"]
            response = await client.post(f"/api/v1/datasets/{did}/records",
                json={"schema_version": 1, "data": {"value": "5", "label": "new"}})
            response.raise_for_status()
            file = io.StringIO()
            writer = csv.writer(file)
            writer.writerows([['value', 'label'], ['20', 'another']])
            response = await client.post(f"/api/v1/datasets/{did}/imports",
                params={"schema_version": 1, "skip_first_row": True},
                headers={"Idempotency-Key": uuid4().hex},
                files={"file": ("import.csv", file.getvalue().encode())})
            response.raise_for_status()
            snapshot = (await client.get(f"/api/v1/monitors/{mid}/snapshot")).json()
            assert snapshot["alert_total"] == 2, snapshot
            assert sum(point["count"] for point in snapshot["points"]) >= 2
            alerts = (await client.get(f"/api/v1/monitors/{mid}/alerts")).json()
            assert {item["record_id"] for item in alerts} >= {anomalous_id}
            assert {reason["reason"] for item in alerts for reason in item["reasons"]} == {
                "outside_range", "unseen_string"}
            await client.post(f"/api/v1/monitors/{mid}/stop")
            response = await client.post(f"/api/v1/datasets/{did}/records",
                json={"schema_version": 1, "data": {"value": "50", "label": "later"}})
            response.raise_for_status()
            assert (await client.get(f"/api/v1/monitors/{mid}/snapshot")).json()["alert_total"] == 2
            await client.post(f"/api/v1/monitors/{mid}/start")
            response = await client.post(f"/api/v1/datasets/{did}/records",
                json={"schema_version": 1, "data": {"value": "5", "label": "later"}})
            response.raise_for_status()
            assert (await client.get(f"/api/v1/monitors/{mid}/snapshot")).json()["alert_total"] == 2
            async with connect(f"ws://api:8000/api/v1/generator/ws") as socket:
                await socket.send(json.dumps({"token": token, "dataset_id": did}))
                assert json.loads(await socket.recv())["status"] == "started"
                created = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
                assert created["status"] == "created", created
                await socket.send('{"action":"stop"}')
                assert json.loads(await asyncio.wait_for(socket.recv(), timeout=5))["status"] == "stopped"
            delete = await client.delete(f"/api/v1/datasets/{did}/records/{anomalous_id}")
            delete.raise_for_status()
            assert (await client.get(f"/api/v1/monitors/{mid}/snapshot")).json()["alert_total"] >= 1
            print("Monitor integration passed")
        finally:
            print(f"Test fixture: {username}; remove with tests.cleanup_ui")


if __name__ == "__main__":
    asyncio.run(main())
