"""Commit job rows before enqueueing the reconstruct Celery task."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Job
from workers.tasks.reconstruct import reconstruct as reconstruct_task


async def commit_and_dispatch_reconstruct(
    session: AsyncSession,
    *,
    capture_id: uuid.UUID,
    job: Job,
) -> uuid.UUID:
    """Commit ``capture``/``job`` rows, enqueue Celery, persist task id.

    The worker opens a separate DB session. If we enqueue before commit, a
    fast worker can observe no job and return a terminal failure. This helper
    makes the ordering explicit for every route that creates reconstruct jobs.
    """

    await session.flush()
    job_id = job.id
    await session.commit()

    async_result = reconstruct_task.apply_async(
        args=[str(capture_id)],
        task_id=str(job_id),
    )
    job.celery_task_id = async_result.id
    await session.commit()
    return job_id


__all__ = ["commit_and_dispatch_reconstruct"]
