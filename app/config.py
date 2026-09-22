from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
CONFIG_LOCAL_PATH = ROOT / "config.local.yaml"

_DEFAULTS: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8787,
        "cache_dir": "cache",
        "reports_dir": "reports",
    },
    "market": {"default": "all", "crypto_exchange": "binance"},
    "market_db": {"path": "data/market_bars.db", "sync_days": 250, "refresh_tail_days": 5},
    "joinquant": {"enabled": False, "username": "", "password": "", "token": ""},
    "universe": {
        "a_share": {
            "source": "index",
            "index_codes": ["000001"],
            "custom_codes": [],
            "exclude_st": True,
            "min_list_days": 60,
            "max_count": 300,
        },
        "crypto": {"symbols": ["BTC-USDT", "ETH-USDT", "SOL-USDT"]},
    },
    "strategies": {
        "short_term_hot": {
            "lookback_days": 20,
            "top_n": 20,
            "weights": {
                "volume_surge": 0.25,
                "turnover": 0.15,
                "momentum_5d": 0.20,
                "momentum_10d": 0.15,
                "amplitude": 0.10,
                "consecutive_up": 0.10,
                "rel_strength": 0.05,
            },
        },
        "long_term_layout": {
            "lookback_days": 250,
            "top_n": 20,
            "weights": {
                "valuation_score": 0.25,
                "trend_score": 0.25,
                "volatility_score": 0.15,
                "quality_proxy": 0.20,
                "recovery_score": 0.15,
            },
        },
        "bottom_fishing": {
            "lookback_days": 120,
            "top_n": 20,
            "weights": {
                "drawdown_score": 0.25,
                "rsi_score": 0.20,
                "volume_dry_score": 0.20,
                "support_score": 0.20,
                "stabilization_score": 0.15,
            },
            "min_price": 2.0,
            "max_drawdown_pct": 35,
        },
    },
    "backtest": {
        "short_term_hot": {"holding_days": 5, "top_k": 10, "fee_rate": 0.001},
        "long_term_layout": {"holding_days": 20, "top_k": 10, "fee_rate": 0.0005},
        "bottom_fishing": {"holding_days": 10, "top_k": 10, "fee_rate": 0.001},
        "max_rebalance_points": 12,
    },
    "signals": {"min_score": 55, "push_enabled": False, "push_webhook": ""},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    cfg = _DEFAULTS
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(_DEFAULTS, user_cfg)
    if CONFIG_LOCAL_PATH.exists():
        with CONFIG_LOCAL_PATH.open("r", encoding="utf-8") as f:
            local_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, local_cfg)
    return cfg


def cache_dir() -> Path:
    d = ROOT / load_config()["app"]["cache_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = ROOT / load_config()["app"]["reports_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d
