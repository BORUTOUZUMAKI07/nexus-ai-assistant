"""
Account lifecycle API — GDPR data export + right-to-erasure.
"""
from datetime import UTC, datetime

import structlog
from backend.app.api.deps import get_account_service, get_current_user
from backend.app.domain.user.models import User
from backend.app.services.account_service import AccountService
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/export")
async def export_account_data(
    current_user: User = Depends(get_current_user),
    account_svc: AccountService = Depends(get_account_service),
) -> JSONResponse:
    """
    GDPR Article 20: portable, machine-readable export of everything the
    authenticated user owns (conversations, messages, files, memories, usage).
    """
    try:
        payload = await account_svc.export_user_data(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    filename = f"nexus-export-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/json",
        },
    )


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_account_data(
    current_user: User = Depends(get_current_user),
    account_svc: AccountService = Depends(get_account_service),
):
    """
    GDPR Article 17: erase the account and every dependent record
    (conversations, files, memories, API keys, usage, webhooks, org memberships).
    """
    try:
        await account_svc.delete_account(current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    logger.info("account_deleted_via_api", user_id=str(current_user.id))
    return {"status": "deleted"}