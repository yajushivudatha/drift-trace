import hashlib
import os

import numpy as np
import pytest

test_db = os.environ.get("TEST_DATABASE_URL")
if test_db:
    os.environ["DATABASE_URL"] = test_db
os.environ.setdefault("EMBEDDING_DIM", "768")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("CONTACT_EMAIL", "tests@example.org")
os.environ.setdefault("LLM_PRICE_INPUT_PER_MTOK", "4")
os.environ.setdefault("LLM_PRICE_OUTPUT_PER_MTOK", "20")

from sqlalchemy import text  # noqa: E402

from drifttrace.db.models import Base  # noqa: E402
from drifttrace.db.session import get_engine  # noqa: E402
from drifttrace.ingest.embeddings import set_embedder  # noqa: E402
from drifttrace.llm import set_transport  # noqa: E402


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="run tests that call real external APIs")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "live" in item.keywords and not config.getoption("--live"):
            item.add_marker(pytest.mark.skip(reason="needs --live"))
        if "db" in item.keywords and not test_db:
            item.add_marker(pytest.mark.skip(reason="needs TEST_DATABASE_URL"))


def fake_embed(texts: list[str]) -> list[list[float]]:
    vectors = []
    for value in texts:
        rng = np.random.default_rng(int(hashlib.sha256(value.encode()).hexdigest()[:8], 16))
        vector = rng.normal(size=768)
        vectors.append((vector / np.linalg.norm(vector)).tolist())
    return vectors


@pytest.fixture(autouse=True)
def stub_embedder(request):
    if "live" in request.keywords:
        yield
        return
    set_embedder(fake_embed)
    yield
    set_embedder(None)


@pytest.fixture
async def clean_db():
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
    set_transport(None)
