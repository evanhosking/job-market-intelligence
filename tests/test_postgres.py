"""Optional disposable PostgreSQL integration suite, also executed by CI."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from market.config import Settings
from market.contracts import MonitorConfig
from market.db import Base, make_engine
from market.models import Monitor, QueryVersion, Run
from market.pipeline import enqueue


def test_postgres_migrations_and_concurrent_enqueue():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not supplied; requires disposable PostgreSQL")
    assert url.startswith("postgresql"), "Refusing to reset a non-PostgreSQL database"
    engine = make_engine(url)
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
        from sqlalchemy import text

        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": url},
        check=True,
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        config = MonitorConfig(name="Concurrent test")
        monitor = Monitor(name=config.name)
        db.add(monitor)
        db.flush()
        mid = monitor.id
        db.add(QueryVersion(monitor_id=mid, version=1, config=config.model_dump()))
    settings = Settings(_env_file=None, collection_mode="offline")

    def trigger(_):
        with factory.begin() as db:
            return enqueue(db, mid, settings, datetime(2026, 10, 29, 12, tzinfo=UTC)).id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(trigger, range(8)))
    assert len(set(ids)) == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 1
    engine.dispose()
