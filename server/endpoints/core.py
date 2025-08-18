"""Core /msg endpoint implementation."""

import asyncio
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from server.cache import IdempotencyCache
from server.state import ServerState

logger = structlog.get_logger()
router = APIRouter()


def get_server_state(request: Request) -> ServerState:
    """Dependency to get server state from app state."""
    return request.app.state.server_state


def get_idempotency_cache(request: Request) -> IdempotencyCache:
    """Dependency to get idempotency cache from app state."""
    return request.app.state.idempotency_cache


@router.get("/msg")
async def get_message(
    delay: int = Query(
        0, description="Delay in milliseconds before response", ge=0, le=30000
    ),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    state: ServerState = Depends(get_server_state),
    cache: IdempotencyCache = Depends(get_idempotency_cache),
):
    """
    Core endpoint that returns a message with a unique UUID.

    Processing Logic (as per specification):
    1. Acquire concurrency semaphore (handled by middleware)
    2. Check failure state - fail before idempotency check
    3. Check idempotency cache if X-Request-ID provided
    4. Apply delay if requested
    5. Generate new UUID
    6. Cache response if idempotent
    7. Return response
    8. Release semaphore (handled by middleware)
    """

    # Step 2: Check failure state BEFORE idempotency check
    should_fail = await state.failure_manager.should_fail()
    if should_fail:
        logger.debug(
            "Inducing failure as configured", request_id=x_request_id, delay=delay
        )
        raise HTTPException(status_code=500, detail="Induced server failure")

    # Step 4: Check idempotency cache if X-Request-ID is provided
    if x_request_id:
        cached_response = await cache.get_response(x_request_id)
        if cached_response is not None:
            logger.debug(
                "Returning cached response",
                request_id=x_request_id,
                message_id=cached_response,
            )
            return {"message_id": cached_response}

        logger.debug("Cache miss for request ID", request_id=x_request_id)

    # Step 6: Apply delay if requested
    if delay > 0:
        logger.debug(
            "Applying requested delay", delay_ms=delay, request_id=x_request_id
        )
        await asyncio.sleep(delay / 1000.0)  # Convert ms to seconds

    # Step 7: Generate new UUID
    message_id = str(uuid.uuid4())

    # Step 8: Cache response if X-Request-ID was provided
    if x_request_id:
        await cache.store_response(x_request_id, message_id)
        logger.debug(
            "Stored response in cache", request_id=x_request_id, message_id=message_id
        )

    logger.debug(
        "Generated new message",
        message_id=message_id,
        request_id=x_request_id,
        delay=delay,
        cached=x_request_id is not None,
    )

    # Step 9: Return response
    return {"message_id": message_id}
