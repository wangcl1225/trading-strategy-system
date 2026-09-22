from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import DISCLAIMER, __version__
from app.api.routes import router
from app.config import ROOT, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="交易策略系统",
    description=DISCLAIMER,
    version=__version__,
)

static_dir = ROOT / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(router)


@app.get("/")
def home():
    from fastapi.responses import FileResponse

    return FileResponse(static_dir / "index.html")


def run() -> None:
    import uvicorn

    cfg = load_config()["app"]
    uvicorn.run(
        "app.main:app",
        host=str(cfg.get("host", "127.0.0.1")),
        port=int(cfg.get("port", 8787)),
        reload=False,
    )


if __name__ == "__main__":
    run()
