"""
main.py
-------
Entry point for the FastAPI application. Wires together the users,
products, and orders routers, configures middleware, exception
handlers, startup/shutdown events, and a handful of top-level
utility endpoints.

Run with:
    uvicorn main:app --reload
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import orders
import products
import users

logger = logging.getLogger("main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

APP_START_TIME = time.time()
APP_VERSION = "1.0.0"

app = FastAPI(
    title="Demo Commerce API",
    description="A demo FastAPI application with Users, Products, and Orders modules.",
    version=APP_VERSION,
)

# --------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id_and_timing(request: Request, call_next: Callable):
    """Attach a unique request ID and log processing time for every request."""
    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    logger.info("Request %s started: %s %s", request_id, request.method, request.url.path)

    response = await call_next(request)

    duration_ms = (time.perf_counter() - start_time) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"

    logger.info(
        "Request %s finished: %s %s -> %d in %.2fms",
        request_id, request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


# --------------------------------------------------------------------------
# Exception handlers
# --------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return a consistent JSON error shape for all HTTPExceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "status_code": exc.status_code,
            "detail": exc.detail,
            "path": str(request.url.path),
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected errors."""
    logger.exception("Unhandled exception on %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": True,
            "status_code": 500,
            "detail": "Internal server error",
            "path": str(request.url.path),
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


# --------------------------------------------------------------------------
# Startup / shutdown events
# --------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    """Seed demo data and log application start."""
    logger.info("Starting Demo Commerce API v%s", APP_VERSION)
    users.seed_demo_users(count=5)
    products.seed_demo_products(count=8)
    orders.seed_demo_orders(count=3)
    logger.info("Startup complete. Demo data seeded.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Log application shutdown."""
    uptime = time.time() - APP_START_TIME
    logger.info("Shutting down Demo Commerce API after %.2f seconds of uptime", uptime)


# --------------------------------------------------------------------------
# Routers
# --------------------------------------------------------------------------
app.include_router(users.router)
app.include_router(products.router)
app.include_router(orders.router)


# --------------------------------------------------------------------------
# Top-level models
# --------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    timestamp: datetime


class EchoRequest(BaseModel):
    message: str
    repeat: int = 1


class EchoResponse(BaseModel):
    original: str
    echoed: str
    repeat_count: int


# --------------------------------------------------------------------------
# Top-level routes
# --------------------------------------------------------------------------
@app.get("/", tags=["Meta"])
def read_root() -> dict:
    """Root endpoint with basic API information."""
    return {
        "service": "Demo Commerce API",
        "version": APP_VERSION,
        "docs": "/docs",
        "endpoints": ["/users", "/products", "/orders", "/health", "/echo"],
    }


@app.get("/health", response_model=HealthResponse, tags=["Meta"])
def health_check() -> HealthResponse:
    """Simple health check endpoint used for liveness/readiness probes."""
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        uptime_seconds=round(time.time() - APP_START_TIME, 2),
        timestamp=datetime.utcnow(),
    )


@app.post("/echo", response_model=EchoResponse, tags=["Meta"])
def echo(payload: EchoRequest) -> EchoResponse:
    """Echo back the given message, optionally repeated multiple times."""
    if payload.repeat < 1 or payload.repeat > 20:
        raise HTTPException(status_code=400, detail="repeat must be between 1 and 20")

    echoed = " ".join([payload.message] * payload.repeat)
    return EchoResponse(
        original=payload.message,
        echoed=echoed,
        repeat_count=payload.repeat,
    )


@app.get("/stats/overview", tags=["Meta"])
def stats_overview() -> dict:
    """Aggregate a quick overview combining stats from all three modules."""
    user_stats = users.user_stats_summary()
    revenue_stats = orders.revenue_report()
    low_stock = products.low_stock_report(threshold=5)

    return {
        "users": user_stats,
        "revenue": revenue_stats,
        "low_stock_product_count": len(low_stock),
        "generated_at": datetime.utcnow().isoformat(),
    }


@app.get("/version", tags=["Meta"])
def get_version() -> dict:
    """Return the current API version."""
    return {"version": APP_VERSION}


@app.get("/ping", tags=["Meta"])
def ping() -> dict:
    """Minimal liveness check."""
    return {"pong": True}


# --------------------------------------------------------------------------
# Utility functions (non-route helpers, kept here for app-wide convenience)
# --------------------------------------------------------------------------
def format_uptime(seconds: float) -> str:
    """Convert a duration in seconds into a human-readable string."""
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


@app.get("/uptime", tags=["Meta"])
def uptime_human_readable() -> dict:
    """Return uptime in both raw seconds and a human-readable format."""
    seconds = time.time() - APP_START_TIME
    return {
        "uptime_seconds": round(seconds, 2),
        "uptime_human": format_uptime(seconds),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
