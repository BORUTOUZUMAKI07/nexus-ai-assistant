"""
Master API v1 Router.
Aggregates all domain routes into a single router.
"""
from backend.app.api.v1 import (
    account,
    admin,
    artifacts,
    audio,
    auth,
    conversations,
    evaluation,
    files,
    messages,
    orgs,
    plans,
    prompts,
    settings,
    shares,
    tools,
    usage,
    webhooks,
)
from fastapi import APIRouter

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(messages.router)
api_router.include_router(audio.router)
api_router.include_router(files.router)
api_router.include_router(tools.router)
api_router.include_router(prompts.router)
api_router.include_router(settings.router)
api_router.include_router(usage.router)
api_router.include_router(account.router)
api_router.include_router(orgs.router)
api_router.include_router(shares.router)
api_router.include_router(webhooks.router)
api_router.include_router(admin.router)
api_router.include_router(evaluation.router)
api_router.include_router(shares.public_router)
api_router.include_router(plans.router)
api_router.include_router(artifacts.router)
