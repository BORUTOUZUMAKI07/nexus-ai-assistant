"""Durable post-answer outbox.

Importing the package registers ``PostAnswerTask`` with ``SQLModel.metadata`` so
``create_all`` on a fresh database matches the Alembic revision. Production
databases get the table from ``f9a0b1c2d3e4`` instead.
"""
from backend.app.services.outbox.models import PostAnswerTask

__all__ = ["PostAnswerTask"]
