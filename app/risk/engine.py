from __future__ import annotations

"""风控与调仓函数（规则内置，默认不可事后关闭）。

内置风控：
a. 单一个股仓位 ≤30%，组合总仓位 0~100%，允许空仓
b. 个股单笔亏损达到 30% 强制止损
c. 组合整体回撤超过 25%，降低总仓位至 30% 以下
d. 连续 3 个月策略跑输基准，触发策略再评估（告警）
"""

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.config import load_config

DEFAULT_RISK = {
    "max_single_position_pct": 30.0,
    "max_total_position_pct": 100.0,
    "min_total_position_pct": 0.0,
    "single_stop_loss_pct": 30.0,
    "portfolio_drawdown_de_risk_pct": 25.0,
    "de_risk_max_total_pct": 30.0,
    "underperform_months_reeval": 3,
    "risk_locked": True,
}

RULES_TEXT = [
    "a. 单一个股仓位 ≤30%，组合总仓位 0~100%，允许空仓",
    "b. 个股单笔亏损达到 30% 强制止损（按成本价）",
    "c. 组合净值从高点回撤超过 25% 时，强制将总仓位降至 ≤30%",
    "d. 连续 3 个自然月策略收益跑输基准 → 标记「策略再评估」告警",
]


@dataclass
class RiskConfig:
    max_single_position_pct: float = 30.0
    max_total_position_pct: float = 100.0
    min_total_position_pct: float = 0.0
    single_stop_loss_pct: float = 30.0
    portfolio_drawdown_de_risk_pct: float = 25.0
    de_risk_max_total_pct: float = 30.0
    underperform_months_reeval: int = 3
    risk_locked: bool = True

    @classmethod
    def load(cls) -> "RiskConfig":
        raw = dict(DEFAULT_RISK)
        user = load_config().get("risk") or {}
        for k, v in user.items():
            if k in raw and k != "risk_locked":
                raw[k] = v
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rules_text"] = RULES_TEXT
        return d


@dataclass
class RiskState:
    equity: float
    cash: float
    market_value: float
    total_exposure_pct: float
    peak_equity: float
    portfolio_drawdown_pct: float
    de_risk_active: bool
    max_total_position_allowed_pct: float
    stop_loss_flags: list[dict[str, Any]] = field(default_factory=list)
    underperform_months: int = 0
    need_reevaluate: bool = False
    alerts: list[str] = field(default_factory=list)


@dataclass
class RebalanceOrder:
    action: str
    code: str
    name: str
    market: str
    strategy: str
    current_shares: float
    target_shares: float
    delta_shares: float
    ref_price: float
    target_weight_pct: float
    reason: str


def compute_risk_state(
    equity: float,
    cash: float,
    positions: list[dict[str, Any]],
    peak_equity: float,
    underperform_months: int = 0,
    risk: RiskConfig | None = None,
) -> RiskState:
    risk = risk or RiskConfig.load()
    market_value = 0.0
    stop_flags: list[dict[str, Any]] = []
    alerts: list[str] = []
    for p in positions:
        shares = float(p.get("shares") or 0)
        last = float(p.get("last_price") or p.get("buy_price") or 0)
        cost = float(p.get("buy_price") or 0)
        mv = shares * last
        market_value += mv
        pnl_pct = ((last - cost) / cost * 100) if cost else 0.0
        if cost > 0 and pnl_pct <= -risk.single_stop_loss_pct:
            stop_flags.append(
                {
                    "code": p.get("code"),
                    "name": p.get("name"),
                    "pnl_pct": round(pnl_pct, 2),
                    "rule": f"单票亏损≥{risk.single_stop_loss_pct}% 强制止损",
                }
            )
    if equity <= 0:
        equity = cash + market_value
    exposure = market_value / equity * 100 if equity > 0 else 0.0
    peak = max(float(peak_equity or 0), equity)
    dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
    de_risk = dd >= risk.portfolio_drawdown_de_risk_pct
    max_total = risk.de_risk_max_total_pct if de_risk else risk.max_total_position_pct
    if de_risk:
        alerts.append(
            f"组合回撤 {dd:.1f}% ≥ {risk.portfolio_drawdown_de_risk_pct}%，"
            f"总仓位上限强制降至 {max_total:.0f}%"
        )
    if stop_flags:
        alerts.append(f"触发单票止损 {len(stop_flags)} 只")
    need_reeval = underperform_months >= risk.underperform_months_reeval
    if need_reeval:
        alerts.append(f"连续 {underperform_months} 个月跑输基准，触发策略再评估")
    return RiskState(
        equity=equity,
        cash=cash,
        market_value=market_value,
        total_exposure_pct=round(exposure, 2),
        peak_equity=peak,
        portfolio_drawdown_pct=round(dd, 2),
        de_risk_active=de_risk,
        max_total_position_allowed_pct=max_total,
        stop_loss_flags=stop_flags,
        underperform_months=underperform_months,
        need_reevaluate=need_reeval,
        alerts=alerts,
    )


def _floor(x: float) -> int:
    return int(math.floor(x + 1e-9))


def calculate_rebalance(
    target_codes: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    prices: dict[str, float],
    equity: float,
    cash: float,
    risk: RiskConfig | None = None,
    underperform_months: int = 0,
    peak_equity: float | None = None,
    cost_rate: float = 0.002,
) -> dict[str, Any]:
    """调仓：根据目标标的计算买入/卖出，叠加内置风控。"""
    risk = risk or RiskConfig.load()
    prices = dict(prices or {})
    for p in positions:
        code = str(p.get("code"))
        if code not in prices or not prices[code]:
            prices[code] = float(p.get("last_price") or p.get("buy_price") or 0)

    market_value = sum(
        float(p.get("shares") or 0) * float(prices.get(str(p.get("code")), 0) or 0)
        for p in positions
    )
    equity = float(equity or (cash + market_value))
    peak = float(peak_equity if peak_equity is not None else equity)
    state = compute_risk_state(equity, cash, positions, peak, underperform_months, risk)

    orders: list[RebalanceOrder] = []
    stop_codes = {str(f.get("code")) for f in state.stop_loss_flags}

    for p in positions:
        code = str(p.get("code"))
        last = float(prices.get(code) or p.get("last_price") or 0)
        if code in stop_codes:
            orders.append(
                RebalanceOrder(
                    action="sell",
                    code=code,
                    name=str(p.get("name") or code),
                    market=str(p.get("market") or "a_share"),
                    strategy=str(p.get("strategy") or ""),
                    current_shares=float(p.get("shares") or 0),
                    target_shares=0.0,
                    delta_shares=-float(p.get("shares") or 0),
                    ref_price=last,
                    target_weight_pct=0.0,
                    reason="风控b：单票亏损达止损线，强制清仓",
                )
            )

    max_total = state.max_total_position_allowed_pct / 100.0
    max_single = risk.max_single_position_pct / 100.0
    budget = equity * max_total

    targets: list[dict[str, Any]] = []
    raw_sum = 0.0
    for t in target_codes or []:
        code = str(t.get("code"))
        if code in stop_codes:
            continue
        w = float(t.get("weight_pct") or (100.0 / max(len(target_codes or []), 1)))
        raw_sum += max(w, 0.0)
        targets.append({**t, "code": code, "raw_w": max(w, 0.0)})
    if raw_sum <= 0:
        raw_sum = 1.0
    for t in targets:
        w = t["raw_w"] / raw_sum * max_total
        t["w"] = min(w, max_single)

    sold_cash = 0.0
    for p in positions:
        code = str(p.get("code"))
        if code in stop_codes:
            continue
        last = float(prices.get(code) or 0)
        target = next((t for t in targets if t["code"] == code), None)
        cur_shares = float(p.get("shares") or 0)
        if target is None:
            orders.append(
                RebalanceOrder(
                    action="sell",
                    code=code,
                    name=str(p.get("name") or code),
                    market=str(p.get("market") or "a_share"),
                    strategy=str(p.get("strategy") or ""),
                    current_shares=cur_shares,
                    target_shares=0.0,
                    delta_shares=-cur_shares,
                    ref_price=last,
                    target_weight_pct=0.0,
                    reason="调仓：移出目标持仓",
                )
            )
            if last > 0:
                sold_cash += cur_shares * last * (1 - cost_rate)
        else:
            target_shares = _floor(budget * target["w"] / last) if last > 0 else 0
            delta = target_shares - cur_shares
            if abs(delta) < 1e-9:
                continue
            reason = "调仓：向目标权重靠拢" + (
                f"；风控单票≤{risk.max_single_position_pct:.0f}%"
            )
            if state.de_risk_active:
                reason += f"；降仓模式总仓位≤{state.max_total_position_allowed_pct:.0f}%"
            orders.append(
                RebalanceOrder(
                    action="buy" if delta > 0 else "sell",
                    code=code,
                    name=str(p.get("name") or code),
                    market=str(p.get("market") or "a_share"),
                    strategy=str(p.get("strategy") or ""),
                    current_shares=cur_shares,
                    target_shares=float(target_shares),
                    delta_shares=float(delta),
                    ref_price=last,
                    target_weight_pct=round(target["w"] * 100, 2),
                    reason=reason,
                )
            )
            if delta < 0 and last > 0:
                sold_cash += (-delta) * last * (1 - cost_rate)

    held = {str(p.get("code")) for p in positions}
    avail_cash = float(cash) + sold_cash
    for t in targets:
        code = t["code"]
        if code in held:
            continue
        last = float(prices.get(code) or 0)
        if last <= 0:
            continue
        target_shares = _floor(budget * t["w"] / last)
        need = target_shares * last * (1 + cost_rate)
        if target_shares <= 0 or need > avail_cash + 1e-6:
            affordable = _floor(avail_cash / (last * (1 + cost_rate)))
            if affordable <= 0:
                orders.append(
                    RebalanceOrder(
                        action="hold",
                        code=code,
                        name=str(t.get("name") or code),
                        market=str(t.get("market") or "a_share"),
                        strategy=str(t.get("strategy") or ""),
                        current_shares=0,
                        target_shares=0,
                        delta_shares=0,
                        ref_price=last,
                        target_weight_pct=round(t["w"] * 100, 2),
                        reason="现金不足/预算受限，暂不买入",
                    )
                )
                continue
            target_shares = affordable
            need = target_shares * last * (1 + cost_rate)
        avail_cash -= need
        orders.append(
            RebalanceOrder(
                action="buy",
                code=code,
                name=str(t.get("name") or code),
                market=str(t.get("market") or "a_share"),
                strategy=str(t.get("strategy") or ""),
                current_shares=0.0,
                target_shares=float(target_shares),
                delta_shares=float(target_shares),
                ref_price=last,
                target_weight_pct=round(t["w"] * 100, 2),
                reason="调仓：建仓至目标权重"
                + ("；降仓模式" if state.de_risk_active else ""),
            )
        )

    return {
        "risk_state": asdict(state),
        "risk_config": risk.to_dict(),
        "orders": [asdict(o) for o in orders],
        "buy_count": len([o for o in orders if o.action == "buy"]),
        "sell_count": len([o for o in orders if o.action == "sell"]),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "notes": RULES_TEXT,
    }
