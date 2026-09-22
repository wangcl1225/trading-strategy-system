from __future__ import annotations

"""聚宽 JoinQuant 官方 API 适配层。

- 官方数据/策略广场需 jqdatasdk 账号
- 未配置时降级为本地聚宽风格策略
- 只读行情/因子，不含实盘下单

pip install jqdatasdk

config.local.yaml:
  joinquant:
    enabled: true
    username: "手机号或邮箱"
    password: ""   # 或 token
    token: ""
"""

import logging
from typing import Any

import pandas as pd

from app.config import load_config

logger = logging.getLogger(__name__)


class JoinQuantClient:
    def __init__(self) -> None:
        self.cfg = load_config().get("joinquant") or {}
        self.enabled = bool(self.cfg.get("enabled"))
        self._jq = None
        self._ready = False
        self._error = ""

    def status(self) -> dict[str, Any]:
        info = {
            "enabled": self.enabled,
            "ready": self._ready,
            "error": self._error,
            "package_installed": False,
            "username_set": bool(self.cfg.get("username") or self.cfg.get("token")),
            "note": "官方策略广场需授权账号；本地 JoinQuant 风格策略始终可用",
        }
        try:
            import jqdatasdk  # noqa: F401

            info["package_installed"] = True
        except Exception:
            info["error"] = info["error"] or "未安装 jqdatasdk（pip install jqdatasdk）"
        return info

    def login(self) -> bool:
        if not self.enabled:
            self._error = "joinquant.enabled=false"
            return False
        try:
            import jqdatasdk as jq
        except Exception as e:
            self._error = f"jqdatasdk 未安装: {e}"
            return False
        try:
            token = str(self.cfg.get("token") or "").strip()
            if token:
                jq.auth(self.cfg.get("username") or "", token)
            else:
                jq.auth(str(self.cfg.get("username") or ""), str(self.cfg.get("password") or ""))
            self._jq = jq
            self._ready = True
            self._error = ""
            return True
        except Exception as e:
            self._error = str(e)
            self._ready = False
            return False

    def _require(self):
        if self._ready and self._jq is not None:
            return self._jq
        if self.login() and self._jq is not None:
            return self._jq
        raise RuntimeError(self._error or "JoinQuant 未登录")

    def get_price(
        self,
        security: str,
        start_date: str,
        end_date: str,
        frequency: str = "daily",
        fields: list[str] | None = None,
    ) -> pd.DataFrame | None:
        jq = self._require()
        try:
            df = jq.get_price(
                security,
                start_date=start_date,
                end_date=end_date,
                frequency=frequency,
                fields=fields or ["open", "high", "low", "close", "volume", "money"],
            )
            if isinstance(df, pd.Series):
                df = df.to_frame().T
            df = df.reset_index().rename(columns={"index": "date", "time": "date"})
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:
            logger.warning("jq get_price failed: %s", e)
            return None

    def get_trade_days(self, start_date: str, end_date: str) -> list[str]:
        jq = self._require()
        try:
            days = jq.get_trade_days(start_date=start_date, end_date=end_date)
            return [str(d)[:10] for d in days]
        except Exception as e:
            logger.warning("jq trade_days failed: %s", e)
            return []

    def get_fundamentals(self, query: Any) -> pd.DataFrame | None:
        jq = self._require()
        try:
            return jq.get_fundamentals(query)
        except Exception as e:
            logger.warning("jq fundamentals failed: %s", e)
            return None

    def local_strategies_fallback(self) -> list[dict[str, Any]]:
        from app.strategies.registry import list_strategies

        return [s for s in list_strategies() if s.get("source") == "joinquant"]


def joinquant_status() -> dict[str, Any]:
    return JoinQuantClient().status()
