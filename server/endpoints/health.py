"""Health check endpoint implementation."""

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request

from server.state import ServerState

logger = structlog.get_logger()
router = APIRouter()


def get_server_state(request: Request) -> Any:
    """Dependency to get server state from app state."""
    return request.app.state.server_state


@router.get("/health")
async def health_check(
    state: ServerState = Depends(get_server_state),
) -> dict[str, Any]:
    """
    Health check endpoint.

    Returns a simple OK status to confirm the server is running and responsive.
    This endpoint is not subject to concurrency limits or failure injections.
    """
    uptime = state.get_uptime_seconds()

    logger.debug("Health check requested", uptime_seconds=round(uptime, 2))

    return {"status": "ok", "uptime_seconds": round(uptime, 2)}
