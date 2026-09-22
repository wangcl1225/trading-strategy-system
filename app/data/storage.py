from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.config import cache_dir


class CacheStore:
    """简单文件缓存，降低对行情接口的重复请求。"""

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or cache_dir()
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.base / f"{safe}.json"

    def get(self, key: str, max_age_seconds: int) -> Any | None:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        ts = payload.get("_ts", 0)
        if time.time() - ts > max_age_seconds:
            return None
        return payload.get("data")

    def set(self, key: str, data: Any) -> None:
        p = self._path(key)
        payload = {"_ts": time.time(), "data": data}
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
