"""SQL Server engine and session construction."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from windsor_widget.config import RuntimeSettings


def create_database_engine(settings: RuntimeSettings, *, echo: bool = False) -> Engine:
    settings.validate()
    return create_engine(
        settings.database.sqlalchemy_url(),
        echo=echo,
        pool_pre_ping=True,
        future=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
