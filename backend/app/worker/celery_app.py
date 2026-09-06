"""
Celery Worker Application.
Configures async task queue backed by Redis with pre-configured task routes and serializers.
"""
from backend.app.core.config import settings
from celery import Celery

celery_app = Celery(
    "nexus_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["backend.app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
    worker_concurrency=4,
    worker_prefetch_multiplier=1,
)
