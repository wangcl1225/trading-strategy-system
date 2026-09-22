import json
import urllib.request

BASE = "http://127.0.0.1:8787"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())

def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

print("health", get("/api/health").get("version"))
r1 = post("/api/strategies/active", {"strategy_id": "jq_dual_momentum"})
print("apply_ok", r1.get("ok"), "|", r1.get("reason"))
print("account", r1.get("account"))
r2 = post("/api/strategies/active", {"strategy_id": "not_exist_strategy"})
print("apply_bad", r2.get("ok"), r2.get("code"), r2.get("reason"))
accs = get("/api/paper/accounts")
print("accounts", [(a.get("account_id"), a.get("cash"), a.get("open_count")) for a in accs.get("accounts") or []])
st = get("/api/paper/state?account_id=acc_jq_dual_momentum")
print(
    "state_acc",
    st.get("account_id"),
    st.get("portfolio", {}).get("strategy_name"),
    "cash",
    st.get("portfolio", {}).get("cash"),
)
run = post("/api/paper/run?account_id=acc_jq_dual_momentum")
rr = run.get("run") or {}
print("run", rr.get("account_id"), "buys", len(rr.get("buys") or []), "skips", len(rr.get("skips") or []))
print("skip_sample", (rr.get("skips") or [None])[0])
