import logging
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from database import DatabaseManager
from config import Config
import json

logger = logging.getLogger("dashboard")

app = FastAPI(title="OrderFlow Trading Bot")
templates = Jinja2Templates(directory="templates")

app.state.db = None
app.state.bot_state = {}


def init_dashboard(database: DatabaseManager, config: Config, bot_state: dict):
    app.state.db = database
    app.state.bot_state = bot_state
    logger.info(f"Dashboard init: bot_state has {len(bot_state)} keys: {list(bot_state.keys())}")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/status")
async def api_status():
    state = app.state.bot_state
    try:
        return JSONResponse(content=json.loads(json.dumps(state, default=str)))
    except Exception as e:
        return JSONResponse(content={"error": str(e)})


@app.get("/api/positions")
async def api_positions():
    db = app.state.db
    if not db:
        return JSONResponse([])
    positions = await db.get_open_positions()
    return JSONResponse([json.loads(json.dumps(p.model_dump(), default=str)) for p in positions])


@app.get("/api/history")
async def api_history():
    db = app.state.db
    if not db:
        return JSONResponse([])
    positions = await db.get_recent_positions(50)
    return JSONResponse([json.loads(json.dumps(p.model_dump(), default=str)) for p in positions])


@app.get("/api/signals")
async def api_signals():
    db = app.state.db
    if not db:
        return JSONResponse([])
    signals = await db.get_recent_signals(50)
    return JSONResponse(signals)


@app.get("/api/stats")
async def api_stats():
    db = app.state.db
    if not db:
        return JSONResponse({})
    today = await db.get_today_stats()
    history = await db.get_all_daily_stats(30)
    return JSONResponse({
        "today": json.loads(json.dumps(today.model_dump(), default=str)),
        "history": [json.loads(json.dumps(d.model_dump(), default=str)) for d in history],
    })


@app.post("/api/emergency-close")
async def api_emergency_close():
    app.state.bot_state["emergency_close"] = True
    return JSONResponse({"status": "Emergency close triggered"})
