from pathlib import Path

root = Path(r"D:\PycharmProjects\trading-strategy-system-wt-v06")

h = root / "static/index.html"
ht = h.read_text(encoding="utf-8")
old = '            <button class="btn" id="btn-backtest">运行专业回测</button>'
new = """            <button class="btn" id="btn-backtest">运行专业回测</button>
            <button class="btn secondary" id="btn-export-html">导出HTML报告</button>
            <button class="btn secondary" id="btn-export-md">导出Markdown</button>
            <button class="btn secondary" id="btn-sync-market">同步全市场行情</button>"""
if "btn-export-html" not in ht and old in ht:
    ht = ht.replace(old, new, 1)
    h.write_text(ht, encoding="utf-8")
    print("html ok")
else:
    print("html skip", "btn-export-html" in ht, old in ht)

j = root / "static/js/app.js"
jt = j.read_text(encoding="utf-8")
fn = r'''
async function exportBacktest(format) {
  if (!state.lastBt) { showToast("warn","无回测结果","请先运行专业回测再导出"); return; }
  try {
    const data = await postApi("/api/backtest/export", { result: state.lastBt, format });
    showToast("ok","导出成功", data.path);
    const a = document.createElement("a");
    a.href = "/api/backtest/export/file?path=" + encodeURIComponent(data.path);
    a.download = data.download_name || "";
    a.click();
  } catch (e) { showToast("err","导出失败", e.message); }
}
async function syncMarket() {
  const btn = $("btn-sync-market"); if (btn) { btn.disabled = true; btn.textContent = "同步中…"; }
  try {
    readFilters();
    const data = await postApi("/api/market/sync", { market: state.market || "all", days: 250, max_codes: 50 });
    showToast("ok","行情同步完成", "全量 " + data.full_synced + " · 增量 " + data.incremental_synced + " · 库 " + data.db_bars);
  } catch (e) { showToast("err","同步失败", e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "同步全市场行情"; } }
}
'''
if "exportBacktest" not in jt:
    jt = jt.replace("async function runBacktest()", fn + "\nasync function runBacktest()", 1)
if "btn-export-html" not in jt:
    needle = '$("btn-backtest").addEventListener("click", runBacktest);'
    repl = needle + """
  $("btn-export-html")?.addEventListener("click", () => exportBacktest("html"));
  $("btn-export-md")?.addEventListener("click", () => exportBacktest("md"));
  $("btn-sync-market")?.addEventListener("click", syncMarket);"""
    if needle in jt:
        jt = jt.replace(needle, repl, 1)
j.write_text(jt, encoding="utf-8")
print("js", "exportBacktest" in jt, "btn-export-html" in jt)
