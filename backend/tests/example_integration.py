"""Check that Compose examples are complete and repeated seeding changes nothing."""
import asyncio

from sqlalchemy import func, select

from app.database import SessionFactory, engine
from app.example_datasets import EXAMPLE_OWNER, EXAMPLES, seed_example_datasets
from app.models import Dataset, DatasetRecord, DatasetUniqueValue, User


async def snapshot(session):
    result = {}
    for name, _, _ in EXAMPLES:
        candidates = (await session.scalars(select(Dataset).where(
            Dataset.name.collate("utf8mb4_bin") == name))).all()
        dataset = next((row for row in candidates if row.name == name), None)
        assert dataset is not None, name
        count = await session.scalar(select(func.count(DatasetRecord.id)).where(
            DatasetRecord.dataset_id == dataset.id))
        result[name] = (dataset.id, count, dataset.owner_id)
    return result


async def main():
    try:
        async with SessionFactory.begin() as session:
            before = await snapshot(session)
            owner = await session.scalar(select(User).where(User.username == EXAMPLE_OWNER))
            if owner is not None:
                assert not owner.is_active
                if before["example-iris"][2] == owner.id:
                    assert before["example-iris"][1] == 150
                    unique_count = await session.scalar(select(func.count(DatasetUniqueValue.id)).where(
                        DatasetUniqueValue.dataset_id == before["example-iris"][0]))
                    assert unique_count == 150
                if before["example-vega-stocks"][2] == owner.id:
                    assert before["example-vega-stocks"][1] == 560
                    first = await session.scalar(select(DatasetRecord).where(
                        DatasetRecord.dataset_id == before["example-vega-stocks"][0]).order_by(DatasetRecord.id))
                    assert first.payload["date"]["kind"] == "datetime"
            await seed_example_datasets(session)
            assert await snapshot(session) == before
        print("PASS: exact example names exist; repeated seed preserves dataset IDs and row counts")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
