from pathlib import Path

p = Path(r"D:\PycharmProjects\trading-strategy-system-wt-v06\app\config.py")
t = p.read_text(encoding="utf-8")
t = t.replace(
    'CONFIG_LOCAL_PATH = ROOT / "config.local.yaml"\nCONFIG_LOCAL_PATH = ROOT / "config.local.yaml"\n',
    'CONFIG_LOCAL_PATH = ROOT / "config.local.yaml"\n',
)
old1 = '"market": {"default": "a_share", "crypto_exchange": "okx"},'
old2 = '"market": {"default": "all", "crypto_exchange": "binance"},'
new = (
    '"market": {"default": "all", "crypto_exchange": "binance"},\n'
    '    "market_db": {"path": "data/market_bars.db", "sync_days": 250, "refresh_tail_days": 5},\n'
    '    "joinquant": {"enabled": False, "username": "", "password": "", "token": ""},'
)
if old1 in t:
    t = t.replace(old1, new, 1)
elif old2 in t and "market_db" not in t:
    t = t.replace(old2, new, 1)
else:
    print("already or missing")
p.write_text(t, encoding="utf-8")
print("ok", "market_db" in t)

# config.yaml add market_db + joinquant if missing
c = Path(r"D:\PycharmProjects\trading-strategy-system-wt-v06\config.yaml")
ct = c.read_text(encoding="utf-8")
if "market_db:" not in ct:
    ct = ct.replace(
        "market:\n  default: all\n  crypto_exchange: binance\n",
        "market:\n  default: all\n  crypto_exchange: binance\n\n"
        "market_db:\n  path: data/market_bars.db\n  sync_days: 250\n  refresh_tail_days: 5\n\n"
        "joinquant:\n  enabled: false\n  username: \"\"\n  password: \"\"\n  token: \"\"\n",
        1,
    )
    c.write_text(ct, encoding="utf-8")
    print("config.yaml patched")
else:
    print("config.yaml already")
