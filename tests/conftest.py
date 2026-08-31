import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import buylog.models as models

DEBUG=False


@pytest.fixture(scope='session')
def engine():
    return create_engine('sqlite:///:memory:', echo=DEBUG)


@pytest.fixture(scope='session')
def tables(engine):
    models.init_db(engine)
    yield
    models.Base.metadata.drop_all(engine)


@pytest.fixture
def dbsession(engine, tables):
    """Returns an sqlalchemy session, and after the test tears down everything properly."""
    connection = engine.connect()
    # begin the nested transaction
    transaction = connection.begin()
    # use the connection with the already started transaction
    session = Session(bind=connection)

    yield session

    session.close()
    # roll back the broader transaction only if still active
    if transaction.is_active:
        transaction.rollback()
    # put back the connection to the connection pool
    connection.close()
