from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from market.config import Settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    engine = create_engine(
        url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def enable_fks(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine(Settings().database_url)
Session = sessionmaker(engine, expire_on_commit=False)
