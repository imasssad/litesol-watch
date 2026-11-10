from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import settings
from app.db.init_db import init_db
from app.engine.scanner import scanner
from app.services.repository import recent_opportunities, recent_trades

FRONTEND_DIR = Path(__file__).resolve().parents[2] / ".." / "frontend"


def require_auth(x_api_token: str | None = Header(default=None)) -> None:
    if settings.api_auth_token == "change-me-long-random-token":
        return
    if x_api_token != settings.api_auth_token:
        raise HTTPException(status_code=401, detail="invalid API token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await scanner.start()
    yield
    await scanner.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/api/status")
async def status():
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "live_trading": settings.live_trading,
        "execution_enabled": settings.execution_enabled,
        "live_execution_allowed": settings.live_execution_allowed,
        "running": scanner.running,
        "kill_switch": scanner.risk.kill_switch,
        "pair": settings.pair,
        "min_net_spread_bps": settings.min_net_spread_bps,
        "trade_size_usd": settings.trade_size_usd,
        "max_trade_usd": settings.max_trade_usd,
        "daily_pnl_usd": scanner.risk.daily_pnl_usd,
        "failed_trade_count": scanner.risk.failed_trade_count,
        "trade_count": len(scanner.trades),
    }


@app.get("/api/health")
async def health():
    return await scanner.health()


@app.get("/api/quotes")
async def quotes():
    return scanner.latest_quotes


@app.get("/api/opportunities")
async def opportunities():
    return await recent_opportunities(50)


@app.get("/api/trades")
async def trades():
    return await recent_trades(50)


@app.post("/api/scan-once", dependencies=[Depends(require_auth)])
async def scan_once():
    return await scanner.scan_once()


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
    scanner.risk.enable_kill_switch()
    return {"kill_switch": scanner.risk.kill_switch}


@app.post("/api/kill-switch/off", dependencies=[Depends(require_auth)])
async def kill_switch_off():
    scanner.risk.disable_kill_switch()
    return {"kill_switch": scanner.risk.kill_switch}
