import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.connectors.base import RateLimited
from app.db.init_db import init_db
from app.engine.scanner import scanner
from app.services.repository import recent_opportunities, recent_trades

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# backend/app/main.py -> parents[0]=app, [1]=backend, [2]=project root.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def require_auth(x_api_token: str | None = Header(default=None)) -> None:
    """Reject every control request unless a real token is configured and matches.

    This used to return early when API_AUTH_TOKEN was still the shipped
    placeholder, which left start/stop/scan/kill-switch wide open on any
    deployment that had not edited .env.
    """
    if not settings.auth_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "API_AUTH_TOKEN is not configured. Set a long random "
                "API_AUTH_TOKEN in the environment to use control endpoints."
            ),
        )
    if not settings.verify_api_token(x_api_token):
        raise HTTPException(status_code=401, detail="invalid API token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await scanner.restore_state()
    if not settings.auth_configured:
        logger.warning(
            "API_AUTH_TOKEN is unset or still the placeholder: control endpoints "
            "are disabled (HTTP 503) until it is set."
        )
    await scanner.start()
    try:
        yield
    finally:
        await scanner.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
else:  # pragma: no cover - misdeployment guard
    logger.error("frontend directory not found at %s; dashboard disabled", FRONTEND_DIR)


@app.get("/")
async def index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="dashboard not deployed")
    return FileResponse(str(index_file))


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Browsers request /favicon.ico at the root whatever the <link> says."""
    icon = FRONTEND_DIR / "favicon.svg"
    if not icon.is_file():
        raise HTTPException(status_code=404, detail="no favicon")
    return FileResponse(str(icon), media_type="image/svg+xml")


@app.get("/api/ping")
async def ping():
    """Unauthenticated liveness probe.

    The unlock screen needs to show whether the bot is reachable before it has
    a token. It deliberately reveals nothing but that the process is up.
    """
    return {"ok": True}


@app.get("/api/status", dependencies=[Depends(require_auth)])
async def status():
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "live_trading": settings.live_trading,
        "execution_enabled": settings.execution_enabled,
        "live_execution_allowed": settings.live_execution_allowed,
        "auth_configured": settings.auth_configured,
        "running": scanner.running,
        "rate_limited": scanner.rate_limited,
        "last_scan_error": scanner.last_scan_error,
        "scan_interval_seconds": settings.effective_scan_interval_seconds,
        "scan_interval_was_raised": settings.scan_interval_was_raised,
        "kill_switch": scanner.risk.kill_switch,
        "kill_switch_reason": scanner.risk.kill_switch_reason,
        "pair": settings.pair,
        "min_net_spread_bps": settings.min_net_spread_bps,
        "trade_size_usd": settings.effective_trade_size_usd,
        "max_trade_usd": settings.max_trade_usd,
        "daily_pnl_usd": scanner.risk.daily_pnl_usd,
        "failed_trade_count": scanner.risk.failed_trade_count,
        "trade_count": len(scanner.trades),
    }


@app.get("/api/auth/verify", dependencies=[Depends(require_auth)])
async def auth_verify():
    """Check a token without causing any side effect.

    The dashboard's unlock screen calls this. Every other protected endpoint
    changes state (starts, stops, scans, toggles the kill switch), so none of
    them is safe to use merely to test a token.
    """
    return {"ok": True}


@app.get("/api/health", dependencies=[Depends(require_auth)])
async def health():
    return await scanner.health()


@app.get("/api/quotes", dependencies=[Depends(require_auth)])
async def quotes():
    return scanner.latest_quotes


@app.get("/api/opportunities", dependencies=[Depends(require_auth)])
async def opportunities():
    return await recent_opportunities(50)


@app.get("/api/trades", dependencies=[Depends(require_auth)])
async def trades():
    return await recent_trades(50)


@app.post("/api/scan-once", dependencies=[Depends(require_auth)])
async def scan_once():
    """Run one scan now.

    The background loop handles venue failures itself; this endpoint used to
    let them escape as a bare 500 with no explanation, so the dashboard could
    only report "500 (Internal Server Error)". Map them onto statuses that say
    what actually went wrong.
    """
    try:
        return await scanner.scan_once()
    except RateLimited as exc:
        # Not a trading failure, exactly as in the scan loop - so this does not
        # touch the consecutive-failure budget either.
        headers = {}
        if exc.retry_after:
            headers["Retry-After"] = str(int(exc.retry_after))
        raise HTTPException(
            status_code=429,
            detail=(
                f"{exc.venue} is rate limiting this client. The scanner backs "
                "off automatically; try again shortly."
            ),
            headers=headers,
        ) from exc
    except Exception as exc:
        logger.exception("manual scan failed")
        scanner.risk.record_failure(f"manual scan error: {exc}")
        await scanner.sync_risk_state()
        raise HTTPException(
            status_code=502, detail=f"scan failed: {type(exc).__name__}: {exc}"
        ) from exc


@app.post("/api/start", dependencies=[Depends(require_auth)])
async def start():
    await scanner.start()
    return {"running": scanner.running}


@app.post("/api/stop", dependencies=[Depends(require_auth)])
async def stop():
    await scanner.stop()
    return {"running": scanner.running}


@app.post("/api/kill-switch/on", dependencies=[Depends(require_auth)])
async def kill_switch_on():
    scanner.risk.enable_kill_switch("manual via API")
    await scanner.sync_risk_state()
    return {"kill_switch": scanner.risk.kill_switch}


@app.post("/api/kill-switch/off", dependencies=[Depends(require_auth)])
async def kill_switch_off():
    scanner.risk.disable_kill_switch()
    await scanner.sync_risk_state()
    return {"kill_switch": scanner.risk.kill_switch}
