from __future__ import annotations

"""多通道消息推送：飞书 / 企业微信 / 钉钉 / Telegram / 邮件 / 通用 Webhook。"""

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests

from app.config import load_config

logger = logging.getLogger(__name__)

CHANNELS = ["feishu", "wecom", "dingtalk", "telegram", "email", "webhook"]


def _cfg_channels() -> dict[str, Any]:
    return (load_config().get("notify") or {})


class MultiNotifier:
    """同时或按配置向多个渠道推送。text/markdown 均可。"""

    def __init__(self) -> None:
        cfg = _cfg_channels()
        self.cfg = cfg
        self.enabled_channels = [
            str(c) for c in (cfg.get("enabled_channels") or ["feishu"]) if str(c) in CHANNELS
        ]

    def channel_ready(self, channel: str) -> tuple[bool, str]:
        c = self.cfg.get(channel) or {}
        if channel == "feishu":
            url = str((c.get("webhook") or "")).strip()
            # 兼容 signals.feishu_webhook
            if not url:
                sig = load_config().get("signals") or {}
                url = str(sig.get("feishu_webhook") or sig.get("push_webhook") or "").strip()
            return (url.startswith("http"), url and "ok" or "未配置 webhook")
        if channel == "wecom":
            url = str((c.get("webhook") or "")).strip()
            return (url.startswith("http"), "ok" if url.startswith("http") else "未配置企业微信机器人 webhook")
        if channel == "dingtalk":
            url = str((c.get("webhook") or "")).strip()
            return (url.startswith("http"), "ok" if url.startswith("http") else "未配置钉钉机器人 webhook")
        if channel == "telegram":
            token = str((c.get("bot_token") or "")).strip()
            chat_id = str((c.get("chat_id") or "")).strip()
            ok = bool(token and chat_id)
            return (ok, "ok" if ok else "未配置 bot_token/chat_id")
        if channel == "email":
            host = str((c.get("smtp_host") or "")).strip()
            to = c.get("to") or []
            ok = bool(host and to)
            return (ok, "ok" if ok else "未配置 SMTP/收件人")
        if channel == "webhook":
            url = str((c.get("url") or "")).strip()
            return (url.startswith("http"), "ok" if url.startswith("http") else "未配置通用 webhook URL")
        return (False, "未知渠道")

    def push_text(self, title: str, text: str, channels: list[str] | None = None) -> dict[str, Any]:
        targets = channels or self.enabled_channels
        results: dict[str, Any] = {}
        for ch in targets:
            ready, msg = self.channel_ready(ch)
            if not ready:
                results[ch] = {"ok": False, "error": msg}
                continue
            try:
                if ch == "feishu":
                    results[ch] = self._push_feishu_text(f"【{title}】\n{text}")
                elif ch == "wecom":
                    results[ch] = self._push_wecom_text(f"【{title}】\n{text}")
                elif ch == "dingtalk":
                    results[ch] = self._push_dingtalk_text(f"【{title}】\n{text}")
                elif ch == "telegram":
                    results[ch] = self._push_telegram(f"{title}\n{text}")
                elif ch == "email":
                    results[ch] = self._push_email(title, text)
                elif ch == "webhook":
                    results[ch] = self._push_generic(title, text)
                else:
                    results[ch] = {"ok": False, "error": f"不支持渠道 {ch}"}
            except Exception as e:
                logger.warning("notify %s failed: %s", ch, e)
                results[ch] = {"ok": False, "error": str(e)}
        return {"ok": any(v.get("ok") for v in results.values()) if results else False, "channels": results}

    def push_card(self, title: str, markdown: str, channels: list[str] | None = None) -> dict[str, Any]:
        """卡片/Markdown：飞书用 interactive，其他渠道用文本退化。"""
        targets = channels or self.enabled_channels
        results: dict[str, Any] = {}
        for ch in targets:
            ready, msg = self.channel_ready(ch)
            if not ready:
                results[ch] = {"ok": False, "error": msg}
                continue
            try:
                if ch == "feishu":
                    results[ch] = self._push_feishu_card(title, markdown)
                elif ch == "wecom":
                    results[ch] = self._push_wecom_markdown(title, markdown)
                elif ch == "dingtalk":
                    results[ch] = self._push_dingtalk_markdown(title, markdown)
                elif ch == "telegram":
                    results[ch] = self._push_telegram(f"{title}\n{markdown}")
                elif ch == "email":
                    results[ch] = self._push_email(title, markdown)
                elif ch == "webhook":
                    results[ch] = self._push_generic(title, markdown)
            except Exception as e:
                results[ch] = {"ok": False, "error": str(e)}
        return {"ok": any(v.get("ok") for v in results.values()) if results else False, "channels": results}

    # ---- channels ----

    def _feishu_url(self) -> str:
        c = self.cfg.get("feishu") or {}
        url = str(c.get("webhook") or "").strip()
        if not url:
            sig = load_config().get("signals") or {}
            url = str(sig.get("feishu_webhook") or sig.get("push_webhook") or "").strip()
        return url

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(url, json=payload, timeout=10)
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}
        ok = resp.ok and (
            data.get("code") in (0, None)
            or data.get("errcode") in (0, None)
            or data.get("StatusCode") in (0, None)
        )
        return {"ok": ok, "status": resp.status_code, "body": data}

    def _push_feishu_text(self, text: str) -> dict[str, Any]:
        url = self._feishu_url()
        return self._post_json(url, {"msg_type": "text", "content": {"text": text}})

    def _push_feishu_card(self, title: str, markdown: str) -> dict[str, Any]:
        url = self._feishu_url()
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": markdown}}],
            },
        }
        result = self._post_json(url, payload)
        if not result.get("ok"):
            result = self._push_feishu_text(f"【{title}】\n{markdown}")
            result["fallback_text"] = True
        return result

    def _wecom_url(self) -> str:
        return str(((self.cfg.get("wecom") or {}).get("webhook")) or "").strip()

    def _push_wecom_text(self, text: str) -> dict[str, Any]:
        return self._post_json(self._wecom_url(), {"msgtype": "text", "text": {"content": text[:2000]}})

    def _push_wecom_markdown(self, title: str, markdown: str) -> dict[str, Any]:
        content = f"**{title}**\n{markdown}"
        return self._post_json(
            self._wecom_url(),
            {"msgtype": "markdown", "markdown": {"content": content[:4000]}},
        )

    def _dingtalk_url(self) -> str:
        return str(((self.cfg.get("dingtalk") or {}).get("webhook")) or "").strip()

    def _push_dingtalk_text(self, text: str) -> dict[str, Any]:
        return self._post_json(self._dingtalk_url(), {"msgtype": "text", "text": {"content": text[:2000]}})

    def _push_dingtalk_markdown(self, title: str, markdown: str) -> dict[str, Any]:
        return self._post_json(
            self._dingtalk_url(),
            {
                "msgtype": "markdown",
                "markdown": {"title": title[:64], "text": f"### {title}\n{markdown}"[:4000]},
            },
        )

    def _push_telegram(self, text: str) -> dict[str, Any]:
        c = self.cfg.get("telegram") or {}
        token = str(c.get("bot_token") or "")
        chat_id = str(c.get("chat_id") or "")
        api = str(c.get("api_base") or "https://api.telegram.org").rstrip("/")
        url = f"{api}/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text[:4000], "parse_mode": c.get("parse_mode") or "Markdown"},
            timeout=12,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:400]}
        return {"ok": bool(data.get("ok") or resp.ok), "status": resp.status_code, "body": data}

    def _push_email(self, title: str, text: str) -> dict[str, Any]:
        c = self.cfg.get("email") or {}
        host = str(c.get("smtp_host") or "")
        port = int(c.get("smtp_port") or 465)
        user = str(c.get("username") or "")
        password = str(c.get("password") or "")
        to = c.get("to") or []
        if isinstance(to, str):
            to = [to]
        use_ssl = bool(c.get("ssl", True))
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = user
        msg["To"] = ",".join(to)
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(f"<pre>{text}</pre>", "html", "utf-8"))
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()
        try:
            server.login(user, password)
            server.sendmail(user, to, msg.as_string())
        finally:
            server.quit()
        return {"ok": True, "to": to}

    def _push_generic(self, title: str, text: str) -> dict[str, Any]:
        c = self.cfg.get("webhook") or {}
        url = str(c.get("url") or "")
        method = str(c.get("method") or "POST").upper()
        headers = c.get("headers") or {"Content-Type": "application/json"}
        body_tpl = c.get("body_template") or {
            "title": "{title}",
            "text": "{text}",
            "content": "{text}",
            "msg": "{text}",
        }
        body: Any
        if isinstance(body_tpl, dict):
            def fill(v: Any) -> Any:
                if isinstance(v, str):
                    return v.replace("{title}", title).replace("{text}", text)
                return v
            body = {k: fill(v) for k, v in body_tpl.items()}
        else:
            body = str(body_tpl).replace("{title}", title).replace("{text}", text)
        if method == "GET":
            resp = requests.get(url, params={"title": title, "text": text}, headers=headers, timeout=10)
        else:
            resp = requests.post(url, json=body, headers=headers, timeout=10)
        return {"ok": resp.ok, "status": resp.status_code, "body": resp.text[:300]}


def notify_status() -> dict[str, Any]:
    n = MultiNotifier()
    out = []
    for ch in CHANNELS:
        ready, msg = n.channel_ready(ch)
        out.append({"channel": ch, "ready": ready, "message": msg, "enabled": ch in n.enabled_channels})
    return {"enabled_channels": n.enabled_channels, "channels": out}
