"""Celery application factory.

Redis serves as both the broker and the result backend. The app is built
without autodiscovery: tasks are imported explicitly from
:mod:`workers.tasks` at module-import time so worker boot is deterministic.
"""
from __future__ import annotations

from celery import Celery

from app.config import settings

# Lazy-resolve at runtime so tests can monkeypatch settings before workers boot.
broker = settings.effective_celery_broker
backend = settings.effective_celery_backend

celery_app = Celery(
    "blocktool",
    broker=broker,
    backend=backend,
    include=["workers.tasks.reconstruct"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_send_task_events=True,
    task_send_sent_event=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)


__all__ = ["celery_app"]
