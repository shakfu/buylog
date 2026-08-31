"""Tests for the audit and configuration modules, which had no coverage."""

import logging

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as SessionType

from buylog import models
from buylog.audit import AuditAction, AuditLog, AuditService
from buylog.config import Config, get_config
from buylog.services import BrandService


@pytest.fixture
def audit_session(tmp_path):
    """A session against a temporary database with every table created."""
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    models.init_db(engine)
    session = SessionType(bind=engine)
    yield session
    session.close()


def test_log_action_writes_a_database_row(audit_session):
    AuditService.log_action(
        AuditAction.CREATE,
        entity_type="brand",
        entity_id=1,
        details="Created brand: Apple",
        session=audit_session,
    )
    rows = audit_session.execute(select(AuditLog)).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "create"
    assert rows[0].entity_type == "brand"
    assert rows[0].user == "anonymous"


def test_brand_creation_is_audited(audit_session):
    BrandService.create(audit_session, "Apple")
    rows = audit_session.execute(select(AuditLog)).scalars().all()
    assert [(r.action, r.entity_type) for r in rows] == [("create", "brand")]


def test_failed_audit_write_leaves_the_session_usable(dbsession):
    """A broken audit write must not poison the caller's session.

    The `dbsession` fixture has no audit_log table, which is exactly the state
    that used to raise PendingRollbackError on every later operation.
    """
    AuditService.log_action(
        AuditAction.CREATE, entity_type="brand", entity_id=1, session=dbsession
    )
    # The session still works.
    brand = BrandService.create(dbsession, "Apple")
    assert brand.id is not None


def test_importing_audit_creates_no_log_file(tmp_path):
    """audit.py used to mkdir ~/.buylog and open a FileHandler at import time.

    Run in a subprocess with a fake HOME: re-importing the module in-process
    would re-register AuditLog on the shared metadata.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ, HOME=str(tmp_path))
    env.pop("BUYER_DB_PATH", None)
    result = subprocess.run(
        [sys.executable, "-c", "import buylog.audit"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    # ~/.buylog itself is created by Config, but the audit log inside it must
    # not appear until something is actually audited.
    assert not (tmp_path / ".buylog" / "audit.log").exists()


def test_audit_logger_has_a_file_handler_after_first_write(audit_session):
    AuditService.log_action(AuditAction.READ, session=audit_session)
    logger = logging.getLogger("buylog.audit")
    assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)


def test_db_path_follows_the_environment(monkeypatch, tmp_path):
    import importlib

    import buylog.config

    monkeypatch.setenv("BUYER_DB_PATH", str(tmp_path / "custom.db"))
    reloaded = importlib.reload(buylog.config)
    try:
        assert reloaded.Config.get_db_path() == tmp_path / "custom.db"
    finally:
        monkeypatch.delenv("BUYER_DB_PATH")
        importlib.reload(buylog.config)


def test_get_config_selects_the_named_environment():
    assert get_config("testing").ENV == "testing"
    assert get_config("production").ENV == "production"
    assert get_config("nonsense").ENV == "development"


def test_engine_enables_sqlite_foreign_keys(tmp_path, monkeypatch):
    """SQLite leaves foreign-key enforcement off by default."""
    from sqlalchemy import text

    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "fk.db")
    monkeypatch.setattr(Config, "DB_URL", f"sqlite:///{tmp_path / 'fk.db'}")
    engine = Config.get_engine()
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
