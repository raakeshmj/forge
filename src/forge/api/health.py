"""Health endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    """Liveness: the process is serving requests. Never checks dependencies."""
    return {"status": "ok"}
