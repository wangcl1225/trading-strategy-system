from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from app.config import load_config
from app.data.market_extra import MarketExtraFactors

logger = logging.getLogger(__name__)


class ShortTermHotV2:
    """兼容包装：由 StrategyEngine 调用 run_pool 时注入龙虎榜/资金流。"""


def enrich_with_extra_factors(
    items: list[dict[str, Any]],
    extras: MarketExtraFactors,
    lhb_map: dict[str, dict[str, Any]],
    weight_lhb: float = 0.15,
    weight_flow: float = 0.15,
    weight_base: float = 0.70,
) -> list[dict[str, Any]]:
    """在基础量价得分上叠加龙虎榜与资金流因子。"""
    if not items:
        return []
    out = []
    for item in items:
        code = str(item.get("code") or "").split(":")[-1].zfill(6) if item.get("market") != "crypto" else str(item.get("code"))
        # A股补零，加密保持原样
        if item.get("market") == "a_share":
            code = str(item.get("code") or "").zfill(6)
        else:
            code = str(item.get("code") or "")

        base = float(item.get("score") or 0)
        lhb_entry = lhb_map.get(code)
        lhb_s = extras.lhb_score(lhb_entry)
        flow = None
        flow_s = 50.0
        if item.get("market") == "a_share":
            flow = extras.fund_flow(code)
            flow_s = extras.fund_flow_score(flow)

        # 加密货币无A股龙虎榜：提高基础权重
        if item.get("market") == "crypto":
            new_score = base
            lhb_s = 0.0
        else:
            w_sum = weight_base + weight_lhb + weight_flow
            new_score = (base * weight_base + lhb_s * weight_lhb + flow_s * weight_flow) / w_sum

        components = dict(item.get("components") or {})
        components["lhb_score"] = round(lhb_s, 1) if item.get("market") != "crypto" else None
        components["fund_flow_score"] = round(flow_s, 1) if item.get("market") != "crypto" else None
        if lhb_entry:
            components["lhb_net_amt_yi"] = round(float(lhb_entry.get("net_amt_sum") or 0) / 1e8, 3)
            components["lhb_appear_days"] = lhb_entry.get("appear_days")
        if flow:
            components["fund_main_inflow_wan"] = round(float(flow.get("main_inflow_sum") or 0) / 1e4, 1)
            components["fund_net_inflow_wan"] = round(float(flow.get("net_inflow_sum") or 0) / 1e4, 1)

        reason = item.get("reason") or ""
        extra_bits = []
        if lhb_entry and item.get("market") == "a_share":
            net = float(lhb_entry.get("net_amt_sum") or 0)
            if net > 0:
                extra_bits.append(f"龙虎榜净买{net/1e8:.2f}亿/{lhb_entry.get('appear_days')}次")
            else:
                extra_bits.append(f"龙虎榜上榜净卖{abs(net)/1e8:.2f}亿")
        if flow and item.get("market") == "a_share":
            main = float(flow.get("main_inflow_sum") or 0)
            if abs(main) >= 1e7:
                direction = "主力净流入" if main > 0 else "主力净流出"
                extra_bits.append(f"{direction}{abs(main)/1e8:.2f}亿")
        if extra_bits:
            reason = "；".join([reason] + extra_bits) if reason else "；".join(extra_bits)

        new_item = dict(item)
        new_item["score"] = round(float(new_score), 2)
        new_item["components"] = components
        new_item["reason"] = reason
        if lhb_entry:
            new_item["lhb"] = lhb_entry
        if flow:
            new_item["fund_flow"] = flow
        out.append(new_item)

    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


def build_lhb_highlight(extras: MarketExtraFactors, names: dict[str, str] | None = None, top_n: int = 15) -> list[dict[str, Any]]:
    """龙虎榜净买靠前名单，供控制台与飞书推送使用。"""
    names = names or {}
    board_map = extras.lhb_map()
    rows = []
    for code, entry in board_map.items():
        if float(entry.get("net_amt_sum") or 0) <= 0:
            continue
        row = dict(entry)
        row["name"] = names.get(code) or entry.get("name") or code
        row["market"] = "a_share"
        row["score"] = extras.lhb_score(entry)
        rows.append(row)
    rows.sort(key=lambda x: float(x.get("net_amt_sum") or 0), reverse=True)
    return rows[:top_n]
