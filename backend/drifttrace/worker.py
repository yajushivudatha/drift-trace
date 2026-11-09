"""arq worker that runs traces."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from arq.connections import RedisSettings
from sqlalchemy import update

from drifttrace.config import get_settings
from drifttrace.db.models import Trace
from drifttrace.db.session import session_scope
from drifttrace.pipeline.trace import run_trace

logging.basicConfig(level=logging.INFO)
JOB_TIMEOUT_SECONDS = 60 * 60 * 2


async def trace_job(_ctx: dict, trace_id: str) -> None:
    await run_trace(uuid.UUID(trace_id))


async def startup(_ctx: dict) -> None:
    """Fail traces orphaned by a previous crash, so they do not look as if they are still running."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=JOB_TIMEOUT_SECONDS)
    async with session_scope() as session:
        await session.execute(
            update(Trace)
            .where(Trace.status.in_(["running", "cancelling"]), Trace.created_at < cutoff)
            .values(status="failed", error="The worker stopped before this trace finished. Start it again.")
        )
        await session.commit()


class WorkerSettings:
    functions = [trace_job]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = JOB_TIMEOUT_SECONDS
    max_jobs = 2
    keep_result = 0
