"""Create the bundled example datasets once during Compose initialization."""
import csv
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.dataset_rules import DatasetDefinition, DatasetField, unique_value_hash, validate_rows
from app.models import (AuditLog, Dataset, DatasetImportBatch, DatasetRecord,
                        DatasetSchemaVersion, DatasetUniqueValue, User)
from app.security import password_hash


EXAMPLE_OWNER = "example-seed-system"  # Hyphens are invalid in public usernames.
EXAMPLES = (
    ("example-iris", "example-Iris.csv", (
        DatasetField(name="Id", data_type="integer", unique=True),
        DatasetField(name="SepalLengthCm", data_type="decimal"),
        DatasetField(name="SepalWidthCm", data_type="decimal"),
        DatasetField(name="PetalLengthCm", data_type="decimal"),
        DatasetField(name="PetalWidthCm", data_type="decimal"),
        DatasetField(name="Species", data_type="string"))),
    ("example-vega-stocks", "example-vega-stocks.csv", (
        DatasetField(name="symbol", data_type="string"),
        DatasetField(name="date", data_type="datetime"),
        DatasetField(name="price", data_type="decimal"))),
)


def read_example(path: Path, definition: DatasetDefinition, stock_dates: bool):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        header = next(reader, None)
        expected = [field.name for field in definition.fields]
        if header != expected:
            raise ValueError(f"Example CSV columns do not match the schema: {path.name}")
        rows = []
        for line, cells in enumerate(reader, 2):
            if len(cells) != len(expected):
                raise ValueError(f"Example CSV has {len(cells)} columns on line {line}: {path.name}")
            row = dict(zip(expected, cells))
            if stock_dates:
                row["date"] = datetime.strptime(row["date"], "%b %d %Y").replace(
                    tzinfo=timezone.utc).isoformat()
            rows.append(row)
    if not rows:
        raise ValueError(f"Example CSV contains no data: {path.name}")
    return validate_rows(rows, definition, "csv")


async def seed_example_datasets(session, data_dir: Path | None = None):
    data_dir = data_dir or Path(__file__).resolve().parents[1] / "data"
    owner = None
    for name, filename, fields in EXAMPLES:
        if await has_exact_name(session, name):
            logging.info("Example dataset already exists, skipping: %s", name)
            continue
        definition = DatasetDefinition(fields=list(fields))
        path = data_dir / filename
        payloads = read_example(path, definition, name == "example-vega-stocks")
        if owner is None:
            owner = await session.scalar(select(User).where(User.username == EXAMPLE_OWNER))
            if owner is None:
                owner = User(username=EXAMPLE_OWNER, role="viewer", is_active=False,
                             password_hash=password_hash.hash(secrets.token_urlsafe(48)))
                session.add(owner)
                await session.flush()
            elif owner.is_active:
                raise ValueError("Reserved example owner account must remain inactive")
        dataset = Dataset(owner_id=owner.id, name=name, description="內建範例數據集")
        session.add(dataset)
        await session.flush()
        version = DatasetSchemaVersion(dataset_id=dataset.id, number=1,
            definition=definition.model_dump(), rules_hash=definition.digest())
        session.add(version)
        await session.flush()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        batch = DatasetImportBatch(dataset_id=dataset.id, schema_version_id=version.id,
            owner_id=owner.id, idempotency_key=hashlib.sha256(f"example:{name}".encode()).hexdigest(),
            fingerprint=digest, row_count=len(payloads), schema_snapshot=version.definition)
        session.add(batch)
        await session.flush()
        records = [DatasetRecord(dataset_id=dataset.id, schema_version_id=version.id,
                   owner_id=owner.id, import_batch_id=batch.id, source="import", payload=payload)
                   for payload in payloads]
        session.add_all(records)
        await session.flush()
        session.add_all(DatasetUniqueValue(dataset_id=dataset.id, record_id=record.id,
            field_name=field.name, value_hash=unique_value_hash(record.payload[field.name]))
            for record in records for field in fields if field.unique)
        session.add(AuditLog(actor_id=owner.id, action="dataset.example.seed", resource_id=dataset.id))
        logging.info("Example dataset created: %s (%s records)", name, len(records))


async def has_exact_name(session, name):
    # MariaDB's default collation ignores case; compare the returned name in Python too.
    candidates = (await session.scalars(select(Dataset.name).where(
        Dataset.name.collate("utf8mb4_bin") == name))).all()
    return name in candidates
