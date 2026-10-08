from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/live")
async def live() -> Response:
    return Response(content="OK", status_code=200)


@router.get("/ready")
async def ready() -> Response:
    return Response(content="OK", status_code=200)
