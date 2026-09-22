from __future__ import annotations

import json
import logging
from typing import Any

import requests

from app.config import load_config
from app.strategies.engine import STRATEGY_META

logger = logging.getLogger(__name__)

STRATEGY_ORDER = ["short_term_hot", "long_term_layout", "bottom_fishing"]


class FeishuPusher:
    """飞书自定义机器人 Webhook 推送（文本 + 交互卡片）。"""

    def __init__(self) -> None:
        cfg = load_config().get("signals") or {}
        self.webhook = str(cfg.get("feishu_webhook") or cfg.get("push_webhook") or "").strip()
        self.secret = str(cfg.get("feishu_secret") or "").strip()
        self.enabled = bool(cfg.get("feishu_enabled") or cfg.get("push_enabled"))
        self.min_score = float(cfg.get("min_score", 55))

    def is_ready(self) -> bool:
        return bool(self.webhook) and self.webhook.startswith("http")

    @staticmethod
    def _sign(secret: str, timestamp: int) -> str:
        import base64
        import hashlib
        import hmac

        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_ready():
            return {"ok": False, "error": "feishu webhook 未配置（config.yaml -> signals.feishu_webhook）"}
        body = dict(payload)
        if self.secret:
            import time

            ts = int(time.time())
            body["timestamp"] = str(ts)
            body["sign"] = self._sign(self.secret, ts)
        try:
            resp = requests.post(self.webhook, json=body, timeout=10)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text}
            ok = resp.ok and (
                data.get("code") in (0, None)
                or data.get("StatusCode") in (0, None)
                or data.get("msg") in ("success", "ok", None)
            )
            return {"ok": ok, "status": resp.status_code, "body": data}
        except Exception as e:
            logger.warning("feishu push failed: %s", e)
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _format_signal_lines(items: list[dict[str, Any]], limit: int = 8) -> str:
        if not items:
            return "（无达标信号）"
        lines = []
        for it in items[:limit]:
            code = it.get("code")
            name = it.get("name") or ""
            score = it.get("score")
            price = it.get("price")
            reason = (it.get("reason") or "")[:48]
            market = "加密" if str(it.get("market") or "").startswith("crypto") else "A股"
            lines.append(
                f"• [{market}] {code} {name}  得分 {score}  价 {price}  {reason}"
            )
        if len(items) > limit:
            lines.append(f"…另 {len(items) - limit} 条")
        return "\n".join(lines)

    def build_text(self, report: dict[str, Any]) -> str:
        meta = report.get("strategy_meta") or STRATEGY_META
        lines = [
            "【TradeLab 策略信号】",
            f"时间：{report.get('generated_at')}",
            f"市场：{report.get('market')}  股票池：{report.get('universe_size')}  阈值分：{report.get('min_score')}",
            "",
        ]
        signals = report.get("signals") or {}
        for key in STRATEGY_ORDER:
            if key not in signals:
                continue
            label = (meta.get(key) or {}).get("label", key)
            lines.append(f"▍{label}（{len(signals[key])}）")
            lines.append(self._format_signal_lines(signals[key]))
            lines.append("")

        # 龙虎榜专题
        lhb_items = signals.get("dragon_tiger") or report.get("lhb_highlight") or []
        if lhb_items:
            lines.append(f"▍龙虎榜关注（{len(lhb_items)}）")
            for it in lhb_items[:8]:
                net = it.get("net_amt_sum") or it.get("billboard_net_amt") or 0
                try:
                    net_yi = float(net) / 1e8
                    net_txt = f"{net_yi:.2f}亿"
                except Exception:
                    net_txt = str(net)
                lines.append(
                    f"• {it.get('code')} {it.get('name')} 净买 {net_txt}  {(it.get('reason') or '')[:30]}"
                )
            lines.append("")

        lines.append("提示：规则化筛选信号，不构成投资建议。")
        return "\n".join(lines)

    def build_card(self, report: dict[str, Any]) -> dict[str, Any]:
        text = self.build_text(report)
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {
                        "tag": "plain_text",
                        "content": f"TradeLab 策略信号 · {report.get('generated_at', '')}",
                    },
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": text.replace("\n", "\n\n"),
                        },
                    },
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": "本消息由本地交易策略系统生成，仅供研究复盘。",
                            }
                        ],
                    },
                ],
            },
        }

    def push_report(self, report: dict[str, Any], as_card: bool = True) -> dict[str, Any]:
        payload = self.build_card(report) if as_card else {"msg_type": "text", "content": {"text": self.build_text(report)}}
        result = self._post(payload)
        # 若卡片失败，回退纯文本
        if not result.get("ok") and as_card:
            fallback = {"msg_type": "text", "content": {"text": self.build_text(report)}}
            result = self._post(fallback)
            result["fallback_text"] = True
        return result

    def push_text(self, text: str) -> dict[str, Any]:
        return self._post({"msg_type": "text", "content": {"text": text}})

    @staticmethod
    def format_md_table(headers: list[str], rows: list[list[Any]]) -> str:
        """生成 Markdown 表格（飞书卡片 lark_md 可用；失败时会回退纯文本）。"""
        def cell(v: Any) -> str:
            if v is None or v == "":
                return "-"
            return str(v)

        head = "| " + " | ".join(cell(h) for h in headers) + " |"
        sep = "| " + " | ".join(["---"] * len(headers)) + " |"
        body = ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
        return "\n".join([head, sep, *body])

    @staticmethod
    def format_text_table(headers: list[str], rows: list[list[Any]]) -> str:
        """等宽纯文本表（卡片表格不可用时的回退）。"""
        def s(v: Any) -> str:
            return "-" if v is None or v == "" else str(v)

        cols = [[s(h) for h in headers]]
        for r in rows:
            cols.append([s(c) for c in r])
        widths = [max(len(row[i]) for row in cols) for i in range(len(headers))]
        lines = []
        for ri, row in enumerate(cols):
            line = " | ".join(row[i].ljust(widths[i]) for i in range(len(headers)))
            lines.append(line)
            if ri == 0:
                lines.append("-+-".join("-" * w for w in widths))
        return "\n".join(lines)

    def push_card(self, title: str, markdown: str, template: str = "blue") -> dict[str, Any]:
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": markdown}},
                    {
                        "tag": "note",
                        "elements": [
                            {"tag": "plain_text", "content": "TradeLab 模拟盘消息 · 不构成投资建议"}
                        ],
                    },
                ],
            },
        }
        result = self._post(payload)
        if not result.get("ok"):
            result = self.push_text(f"【{title}】\n{markdown}")
            result["fallback_text"] = True
        return result

    def push_table_card(
        self,
        title: str,
        headers: list[str],
        rows: list[list[Any]],
        footer: str = "",
        subtitle: str = "",
        template: str = "blue",
    ) -> dict[str, Any]:
        md_table = self.format_md_table(headers, rows)
        text_table = self.format_text_table(headers, rows)
        parts = []
        if subtitle:
            parts.append(subtitle)
            parts.append("")
        parts.append(md_table)
        if footer:
            parts.append("")
            parts.append(footer)
        md = "\n".join(parts)
        result = self.push_card(title, md, template=template)
        if not result.get("ok"):
            fallback_parts = [subtitle, "", text_table]
            if footer:
                fallback_parts += ["", footer]
            result = self.push_text(f"【{title}】\n" + "\n".join(fallback_parts))
            result["fallback_text"] = True
            result["table_text"] = text_table
        result["markdown"] = md
        result["table_text"] = text_table
        return result


def save_feishu_markdown(report: dict[str, Any], path: str) -> str:
    """同步生成一份 Markdown，便于手动粘贴或导入飞书文档。"""
    meta = report.get("strategy_meta") or STRATEGY_META
    lines = [
        f"# TradeLab 策略信号 {report.get('generated_at', '')}",
        "",
        f"- 市场：`{report.get('market')}`",
        f"- 股票池：{report.get('universe_size')}",
        f"- 得分阈值：{report.get('min_score')}",
        "",
        "> 规则化筛选信号，不构成投资建议。",
        "",
    ]
    signals = report.get("signals") or {}
    for key in STRATEGY_ORDER:
        if key not in signals:
            continue
        label = (meta.get(key) or {}).get("label", key)
        lines.append(f"## {label}")
        lines.append("")
        items = signals[key]
        if not items:
            lines.append("_无达标信号_")
            lines.append("")
            continue
        lines.append("| 代码 | 名称 | 市场 | 得分 | 价格 | 原因 |")
        lines.append("|---|---|---|---:|---:|---|")
        for it in items:
            reason = str(it.get("reason") or "").replace("|", "/")
            lines.append(
                f"| {it.get('code')} | {it.get('name')} | {it.get('market')} | {it.get('score')} | {it.get('price')} | {reason} |"
            )
        lines.append("")

    lhb_items = signals.get("dragon_tiger") or report.get("lhb_highlight") or []
    if lhb_items:
        lines.append("## 龙虎榜关注")
        lines.append("")
        lines.append("| 代码 | 名称 | 净买(元) | 原因 |")
        lines.append("|---|---|---:|---|")
        for it in lhb_items:
            lines.append(
                f"| {it.get('code')} | {it.get('name')} | {it.get('net_amt_sum')} | {(it.get('reason') or '')} |"
            )
        lines.append("")

    md = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path
