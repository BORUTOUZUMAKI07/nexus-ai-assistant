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
    # Hard limit SIGKILLs the child; a soft limit below it raises
    # SoftTimeLimitExceeded *inside* the task first, so the task's own error path
    # can mark the row failed before the kill (P1 #16, paired with the stale
    # reclaim in FileRepository.claim_indexing for platforms where hard kill
    # wins anyway).
    task_time_limit=300,
    task_soft_time_limit=270,
    worker_concurrency=4,
    worker_prefetch_multiplier=1,
    # Worker-hygiene (HLD: queue load leveling): recycle a child after 50 tasks
    # so long-running indexing/eval jobs can't leak memory or open connections.
    worker_max_tasks_per_child=50,
)
