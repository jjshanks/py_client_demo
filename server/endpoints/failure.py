"""Failure injection endpoints for testing client resilience."""

import structlog
from fastapi import APIRouter, Depends, Path, Request

from server.state import ServerState

logger = structlog.get_logger()
router = APIRouter(prefix="/fail", tags=["failure-injection"])


def get_server_state(request: Request) -> ServerState:
    """Dependency to get server state from app state."""
    return request.app.state.server_state


@router.post("/count/{count}")
async def set_failure_count(
    count: int = Path(..., description="Number of requests that should fail", ge=0, le=1000),
    state: ServerState = Depends(get_server_state)
):
    """
    Configure the server to fail for a specific number of subsequent requests.
    
    The failure count decrements with each failed request until it reaches zero.
    This mode operates independently of duration-based failures.
    """
    await state.failure_manager.set_fail_count(count)

    logger.info(
        "Failure count mode configured",
        fail_requests_count=count,
        endpoint="/fail/count"
    )

    return {
        "message": f"Server will fail the next {count} requests",
        "fail_requests_count": count
    }


@router.post("/duration/{seconds}")
async def set_failure_duration(
    seconds: int = Path(..., description="Duration in seconds for which requests should fail", ge=0, le=3600),
    state: ServerState = Depends(get_server_state)
):
    """
    Configure the server to fail for a specific duration.
    
    All requests during this time period will fail with 500 errors.
    This mode operates independently of count-based failures.
    """
    await state.failure_manager.set_fail_duration(seconds)

    logger.info(
        "Failure duration mode configured",
        duration_seconds=seconds,
        endpoint="/fail/duration"
    )

    return {
        "message": f"Server will fail requests for the next {seconds} seconds",
        "duration_seconds": seconds
    }


@router.post("/reset")
async def reset_failures(state: ServerState = Depends(get_server_state)):
    """
    Reset all failure configurations.
    
    Clears both count-based and duration-based failure modes.
    The server will return to normal operation immediately.
    """
    await state.failure_manager.reset_failures()

    logger.info("All failure modes reset", endpoint="/fail/reset")

    return {
        "message": "All failure modes have been reset",
        "status": "normal_operation"
    }


@router.get("/status")
async def get_failure_status(state: ServerState = Depends(get_server_state)):
    """
    Get the current failure injection status.
    
    This is a diagnostic endpoint to check the current state of failure modes.
    Not part of the original spec but useful for debugging and monitoring.
    """
    status = await state.failure_manager.get_status()

    logger.debug("Failure status requested", status=status)

    return {
        "failure_status": status,
        "currently_failing": status["currently_failing"]
    }
