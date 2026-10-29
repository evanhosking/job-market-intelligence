import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from market.api import create_app
from market.config import Settings
from market.contracts import MonitorConfig
from market.db import Base, make_engine
from market.models import Monitor, QueryVersion


@pytest.fixture
def rows():
    return json.loads(Path("tests/fixtures/jobs.json").read_text())


@pytest.fixture
def clock():
    return datetime(2026, 10, 29, 12, tzinfo=UTC)


@pytest.fixture
def context(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, collection_mode="offline")
    with factory.begin() as db:
        config = MonitorConfig(name="Engineers")
        monitor = Monitor(name=config.name)
        db.add(monitor)
        db.flush()
        qv = QueryVersion(monitor_id=monitor.id, version=1, config=config.model_dump())
        db.add(qv)
        db.flush()
        mid, qid = monitor.id, qv.id
    yield factory, settings, mid, qid
    engine.dispose()


@pytest.fixture
def client(context):
    factory, settings, _, _ = context
    return TestClient(create_app(factory, settings))
