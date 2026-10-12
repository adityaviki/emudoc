"""The connection pool: sized by the settings, exhausted with a 503 that says to try again
rather than a traceback after a half-minute hang, and handed back by an answer while the model
thinks, so that the answers in flight are not what fills it."""

import time

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.answers import answer, start_answer
from app.api.deps import RETRY_AFTER
from app.config import get_settings
from app.db import BUSY, SessionLocal, engine, make_engine
from app.models import Chat, ChatEvent, ChatMessage
from tests.conftest import FakeTurn


def small_pool(size: int, overflow: int, timeout: int) -> Engine:
    """An engine over the test database with a pool of the given shape."""
    settings = get_settings().model_copy(
        update={"db_pool_size": size, "db_max_overflow": overflow, "db_pool_timeout": timeout}
    )
    return make_engine(settings)


@pytest.fixture
def one_connection():
    """The app's sessions over a pool of one connection and a one-second wait, so that a single
    held connection is exhaustion."""
    small = small_pool(size=1, overflow=0, timeout=1)
    SessionLocal.configure(bind=small)
    try:
        yield small
    finally:
        SessionLocal.configure(bind=engine)
        small.dispose()


def test_the_pool_is_sized_by_the_settings():
    settings = get_settings()
    assert (engine.pool.size(), engine.pool.timeout()) == (
        settings.db_pool_size,
        settings.db_pool_timeout,
    )

    pool = small_pool(size=1, overflow=1, timeout=1)
    try:
        # One kept open and one overflow; the third waits DB_POOL_TIMEOUT, then gives up.
        with pool.connect(), pool.connect():
            started = time.monotonic()
            with pytest.raises(PoolTimeout):
                pool.connect()
            assert 0.5 < time.monotonic() - started < 5
    finally:
        pool.dispose()


def test_an_exhausted_pool_refuses_a_request_with_503_and_a_sentence(
    one_connection, client, caplog
):
    with SessionLocal() as holder:
        # Held from the first statement until the block ends, as a slow request would hold it.
        holder.execute(text("SELECT 1"))
        started = time.monotonic()
        with caplog.at_level("WARNING", logger="app.main"):
            response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json() == {"detail": BUSY}
        assert response.headers["Retry-After"] == str(RETRY_AFTER)
        # The request waited DB_POOL_TIMEOUT, not SQLAlchemy's half minute.
        assert time.monotonic() - started < 5
        assert "GET /api/health was refused: every database connection is in use" in caplog.text
    # With the connection back, the same request goes through.
    assert client.get("/api/health").json() == {"status": "ok"}


def test_an_answer_holds_no_connection_while_the_model_thinks(session, services, account, indexed):
    checked_out: list[int] = []

    class Watched(FakeTurn):
        def __iter__(self):
            checked_out.append(engine.pool.checkedout())
            yield from super().__iter__()

    services.llm.agent_turns = [
        Watched(ToolCallPart("search", {"query": "notice period"}, "call_1")),
        Watched(TextPart("90 days.")),
    ]
    chat = Chat(project_id=indexed.project_id, user_id=account.user.id, title="Notice")
    session.add(chat)
    session.flush()
    _, reply = start_answer(session, chat, "Notice period?")
    session.commit()

    answer(reply.id, services)
    events = session.scalars(select(ChatEvent.event).order_by(ChatEvent.seq)).all()
    assert [event["type"] for event in events] == [
        "tool_call",
        "sources",
        "tool_result",
        "text",
        "done",
    ]
    # Before the first model turn, and again after the search ran and before the second, the
    # answer's session holds nothing: the pool is whole for every other request meanwhile.
    assert checked_out == [0, 0]
    session.expire_all()
    saved = session.scalars(
        select(ChatMessage).where(ChatMessage.chat_id == chat.id).order_by(ChatMessage.id)
    ).all()
    assert [message.role for message in saved] == ["user", "assistant"]
    assert saved[1].content == "90 days." and saved[1].error is None
    assert saved[1].status == "done"
