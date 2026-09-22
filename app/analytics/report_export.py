from __future__ import annotations

"""回测报告导出：HTML（自包含 SVG 图表）/ Markdown / JSON。

无需额外绘图库，页面可直接下载或写入 reports/export/。
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT, load_config, reports_dir


def _fmt(v: Any, digits: int = 2) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "-"


def _svg_line_chart(
    dates: list[str],
    equity: list[float],
    bench: list[float] | None = None,
    width: int = 920,
    height: int = 320,
) -> str:
    if not equity:
        return "<p>无净值数据</p>"
    all_y = list(equity) + (bench or [])
    ymin, ymax = min(all_y), max(all_y)
    if math.isclose(ymin, ymax):
        ymax = ymin + 1.0
    pad_l, pad_r, pad_t, pad_b = 56, 16, 18, 36
    w, h = width, height
    def x(i: int) -> float:
        n = max(len(equity) - 1, 1)
        return pad_l + i * (w - pad_l - pad_r) / n
    def y(v: float) -> float:
        return pad_t + (ymax - v) * (h - pad_t - pad_b) / (ymax - ymin)
    def path(vals: list[float]) -> str:
        pts = [f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals)]
        return "M" + " L".join(pts)
    grid = []
    for k in range(5):
        yy = pad_t + k * (h - pad_t - pad_b) / 4
        val = ymax - k * (ymax - ymin) / 4
        grid.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{w-pad_r}" y2="{yy:.1f}" stroke="#243047" stroke-width="1"/>')
        grid.append(f'<text x="8" y="{yy+4:.1f}" fill="#8b9bb4" font-size="11">{val:.0f}</text>')
    bench_path = (
        f'<path d="{path(bench)}" fill="none" stroke="#f5a524" stroke-width="2"/>' if bench and len(bench) == len(equity) else ""
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" height="{height}">
  <rect width="{w}" height="{h}" fill="#0d1524"/>
  {''.join(grid)}
  {bench_path}
  <path d="{path(equity)}" fill="none" stroke="#2dd4bf" stroke-width="2"/>
  <text x="{pad_l}" y="14" fill="#8b9bb4" font-size="12">净值曲线（青=策略，橙=基准） · x 轴: {dates[0] if dates else ""} → {dates[-1] if dates else ""}</text>
</svg>'''


def _svg_heatmap(months: list[dict[str, Any]]) -> str:
    if not months:
        return "<p class='muted'>无月度数据</p>"
    cols = 6
    cell_w, cell_h, gap = 140, 52, 8
    rows = math.ceil(len(months) / cols)
    w = cols * (cell_w + gap) + gap
    h = rows * (cell_h + gap) + gap + 24
    parts = []
    for i, m in enumerate(months):
        r, c = divmod(i, cols)
        x = gap + c * (cell_w + gap)
        y = 24 + gap + r * (cell_h + gap)
        v = float(m.get("return_pct") or 0)
        if v > 0:
            t = min(v / 8, 1)
            fill = f"rgba(52,211,153,{0.25 + t * 0.7})"
        elif v < 0:
            t = min(abs(v) / 8, 1)
            fill = f"rgba(248,113,113,{0.25 + t * 0.7})"
        else:
            fill = "#1a2438"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" rx="6" fill="{fill}"/>'
            f'<text x="{x + cell_w/2}" y="{y + cell_h/2 + 4}" text-anchor="middle" fill="#041018" font-size="12">'
            f"{m.get('month')} {v:+.2f}%</text>"
        )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" height="auto">{" ".join(parts)}</svg>'


def _svg_hist(dist: dict[str, Any]) -> str:
    bins = dist.get("bins") or []
    if not bins:
        return "<p class='muted'>无盈亏样本</p>"
    w, h = 920, 240
    pad = 36
    max_c = max(int(b.get("count") or 0) for b in bins) or 1
    bw = (w - pad * 2) / len(bins)
    bars = []
    for i, b in enumerate(bins):
        cnt = int(b.get("count") or 0)
        bh = (cnt / max_c) * (h - pad * 2)
        x = pad + i * bw
        y = h - pad - bh
        mid = (float(b.get("from") or 0) + float(b.get("to") or 0)) / 2
        color = "#34d399" if mid >= 0 else "#f87171"
        bars.append(f'<rect x="{x+2:.1f}" y="{y:.1f}" width="{max(bw-4,2):.1f}" height="{bh:.1f}" fill="{color}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" height="{h}"><rect width="{w}" height="{h}" fill="#0d1524"/>{"".join(bars)}<text x="{pad}" y="18" fill="#8b9bb4" font-size="12">盈亏分布（卖出笔收益率 %）</text></svg>'


def export_backtest_report(
    result: dict[str, Any],
    fmt: str = "html",
    filename: str | None = None,
) -> dict[str, Any]:
    """导出回测报告到 reports/export/。"""
    out_dir = reports_dir() / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    strategy = result.get("strategy") or {}
    base_name = filename or f"backtest_{strategy.get('id') or 'strategy'}_{ts}"

    dates = result.get("dates") or []
    equity = result.get("equity") or []
    bench_eq = result.get("benchmark_equity") or None
    months = result.get("monthly_returns") or []
    dist = result.get("pnl_distribution") or {}

    meta = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": strategy,
        "total_return_pct": result.get("total_return_pct"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "sharpe": result.get("sharpe"),
        "annualized_pct": result.get("annualized_pct"),
        "benchmark": result.get("benchmark"),
        "qualified": result.get("qualified"),
        "qualified_reason": result.get("qualified_reason"),
        "sensitivity": result.get("sensitivity"),
        "account": result.get("account"),
        "execution_rule": result.get("execution_rule"),
    }

    path: Path
    if fmt.lower() in ("json",):
        path = out_dir / f"{base_name}.json"
        path.write_text(json.dumps({"meta": meta, "result": result}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    elif fmt.lower() in ("md", "markdown"):
        path = out_dir / f"{base_name}.md"
        path.write_text(_markdown(result, meta), encoding="utf-8")
    else:
        path = out_dir / f"{base_name}.html"
        path.write_text(_html(result, meta, dates, equity, bench_eq, months, dist), encoding="utf-8")

    return {
        "ok": True,
        "format": fmt.lower(),
        "path": str(path),
        "download_name": path.name,
        "meta": meta,
    }


def _markdown(result: dict[str, Any], meta: dict[str, Any]) -> str:
    bench = result.get("benchmark") or {}
    lines = [
        f"# 回测报告 · { (result.get('strategy') or {}).get('name') }",
        "",
        f"- 导出时间：{meta.get('exported_at')}",
        f"- 累计收益：{_fmt(result.get('total_return_pct'))}%",
        f"- 最大回撤：{_fmt(result.get('max_drawdown_pct'))}%",
        f"- 夏普：{_fmt(result.get('sharpe'), 3)} / 年化：{_fmt(result.get('annualized_pct'))}%",
        f"- 基准：{bench.get('name')} 收益 {_fmt(bench.get('total_return_pct'))}% 回撤 {_fmt(bench.get('max_drawdown_pct'))}%",
        f"- 超额：{_fmt(result.get('excess_return_pct'))}pp · 合格：{result.get('qualified')}",
        f"- 判定：{result.get('qualified_reason') or ''}",
        f"- 规则：{result.get('execution_rule') or ''}",
        "",
        "> 不构成投资建议。",
        "",
        "## 月度收益",
        "",
        "| 月份 | 收益% |",
        "|---|---:|",
    ]
    for m in result.get("monthly_returns") or []:
        lines.append(f"| {m.get('month')} | {_fmt(m.get('return_pct'))} |")
    return "\n".join(lines) + "\n"


def _html(
    result: dict[str, Any],
    meta: dict[str, Any],
    dates: list[str],
    equity: list[float],
    bench_eq: list[float] | None,
    months: list[dict[str, Any]],
    dist: dict[str, Any],
) -> str:
    bench = result.get("benchmark") or {}
    sens = result.get("sensitivity") or {}
    rows = ""
    for m in months:
        v = float(m.get("return_pct") or 0)
        color = "#ef4444" if v > 0 else ("#22c55e" if v < 0 else "inherit")
        rows += f"<tr><td>{m.get('month')}</td><td style='color:{color}'>{_fmt(v)}%</td></tr>"
    q = result.get("qualified")
    q_txt = "合格" if q is True else ("不合格" if q is False else "缺基准")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>回测报告 {(meta.get('strategy') or {}).get('name')}</title>
<style>
body{{font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;background:#0b1220;color:#e8eef7;margin:24px;}}
h1,h2{{font-weight:700}} .muted{{color:#8b9bb4}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}
.card{{background:#121a2b;border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px}}
.k{{color:#8b9bb4;font-size:12px}} .v{{font-family:ui-monospace,monospace;font-size:22px;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{padding:8px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left}}
.badge{{padding:2px 10px;border-radius:999px;background:rgba(52,211,153,.15);color:#34d399}}
.badge.bad{{background:rgba(248,113,113,.15);color:#f87171}}
pre{{white-space:pre-wrap;background:#0d1524;padding:12px;border-radius:8px}}
</style></head><body>
<h1>回测报告 · {(meta.get('strategy') or {}).get('name') or ''}</h1>
<p class="muted">导出 {meta.get('exported_at')} · {(meta.get('strategy') or {}).get('source') or ''} · {result.get('execution_rule') or ''}</p>
<p><span class="badge {'bad' if q is False else ''}">{q_txt}</span> {result.get('qualified_reason') or result.get('pass_rule') or ''}</p>
<div class="grid">
  <div class="card"><div class="k">累计收益</div><div class="v">{_fmt(result.get('total_return_pct'))}%</div></div>
  <div class="card"><div class="k">最大回撤 / 基准</div><div class="v">{_fmt(result.get('max_drawdown_pct'))}% / {_fmt(bench.get('max_drawdown_pct'))}%</div></div>
  <div class="card"><div class="k">夏普 / 年化</div><div class="v">{_fmt(result.get('sharpe'),3)} / {_fmt(result.get('annualized_pct'))}%</div></div>
  <div class="card"><div class="k">超额收益</div><div class="v">{_fmt(result.get('excess_return_pct'))}pp</div></div>
</div>
<h2>净值曲线</h2>
{_svg_line_chart(dates, equity, bench_eq)}
<h2>月度收益热力图</h2>
{_svg_heatmap(months)}
<h2>盈亏分布</h2>
{_svg_hist(dist)}
<h2>月度明细</h2>
<table><thead><tr><th>月份</th><th>收益</th></tr></thead><tbody>{rows or '<tr><td colspan=2>无</td></tr>'}</tbody></table>
<h2>参数敏感性</h2>
<pre>{json.dumps(sens, ensure_ascii=False, indent=2)}</pre>
<p class="muted">本报告仅供研究，不构成投资建议。</p>
</body></html>"""
