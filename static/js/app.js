const state = {
  market: "a_share",
  bench: "hs300",
  strategy: "short_term_hot",
  topN: 5,
  dateFrom: "",
  dateTo: "",
  strategies: [],
  accounts: [],
  accountId: null,
  paper: null,
  lastPicks: [],
  lastRebalance: null,
  lastBt: null,
};

const $ = (id) => document.getElementById(id);

function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "-";
  return Number(n).toFixed(d);
}
function scoreClass(s) {
  if (s >= 70) return "high";
  if (s >= 50) return "mid";
  return "low";
}

let toastTimer = null;
function showToast(type, title, body) {
  const el = $("toast");
  if (!el) return;
  el.className = `toast ${type} show`;
  el.innerHTML = `<div class="t-title">${title}</div><div class="t-body">${body || ""}</div>`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 5000);
}
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) {
    let t = r.statusText;
    try { t = (await r.json()).detail || t; } catch (_) {}
    throw new Error(t);
  }
  return r.json();
}
async function postApi(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let t = r.statusText;
    try { t = (await r.json()).detail || t; } catch (_) {}
    throw new Error(t);
  }
  return r.json();
}
function readFilters() {
  state.market = $("market-select").value;
  state.bench = $("bench-select").value || "hs300";
  state.strategy = $("strategy-select").value || state.strategy;
  state.topN = Number($("topn-input").value || 5);
  state.dateFrom = $("date-from").value || "";
  state.dateTo = $("date-to").value || "";
}
function setView(v) {
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  ["strategies","risk","backtest","watch","paper","bench","signals","method"].forEach((k) => {
    const el = $("view-" + k);
    if (el) el.classList.add("hidden");
  });
  const map = {
    strategies: "strategies",
    risk: "risk",
    backtest: "backtest",
    watch: "watch",
    paper: "paper",
    bench: "bench",
    signals: "signals",
    method: "method",
  };
  const target = $("view-" + (map[v] || v));
  if (target) target.classList.remove("hidden");
}

function renderStrategies() {
  const items = state.strategies || [];
  $("strategy-list").innerHTML = items.map((s) => `
    <div class="card" style="margin-bottom:10px">
      <h3>${s.name}
        <span class="badge ${s.source === "joinquant" ? "jq" : "ok"}">${s.source === "joinquant" ? "JoinQuant风格" : "内置"}</span>
        <span class="badge">${s.risk_profile}</span>
      </h3>
      <p>${s.desc}</p>
      <p class="muted">来源：${s.source_label} · 默认持有 ${s.default_holding_days} 交易日</p>
    </div>
  `).join("");
  const sel = $("strategy-select");
  sel.innerHTML = items.map((s) => `<option value="${s.id}">${s.name}</option>`).join("");
  if (state.strategy) sel.value = state.strategy;
}

async function loadStrategies() {
  const data = await api("/api/strategies");
  state.strategies = data.items || [];
  renderStrategies();
  const benches = await api("/api/benchmarks");
  $("bench-select").innerHTML = (benches.items || []).map((b) =>
    `<option value="${b.id}">${b.name}</option>`).join("");
  if (state.bench) $("bench-select").value = state.bench;
  renderBenchInfo(benches);
  const cfg = await api("/api/config");
  renderRisk(cfg.risk);
  renderMethod(cfg);
  $("scan-status").textContent = `就绪 · 基准默认 ${benches.default} · 策略可切换 · 风控已锁定`;
}

function renderRisk(risk) {
  const rules = (risk && risk.rules_text) || [
    "a. 单一个股仓位 ≤30%，组合总仓位 0~100%，允许空仓",
    "b. 个股单笔亏损达到 30% 强制止损",
    "c. 组合回撤超过 25%，降低总仓位至 30% 以下",
    "d. 连续 3 个月跑输基准，触发策略再评估",
  ];
  $("risk-rules").innerHTML = `
    <div class="notice"><b>规则已内置锁定（risk_locked）</b>，回测与模拟盘同时生效。</div>
    <table>
      <thead><tr><th>规则</th><th>参数</th><th>状态</th></tr></thead>
      <tbody>
        <tr><td>单一个股仓位上限</td><td class="num">${fmt(risk?.max_single_position_pct, 0)}%</td><td><span class="badge ok">锁定</span></td></tr>
        <tr><td>组合总仓位</td><td class="num">${fmt(risk?.min_total_position_pct,0)}% ~ ${fmt(risk?.max_total_position_pct,0)}%（允许空仓）</td><td><span class="badge ok">锁定</span></td></tr>
        <tr><td>单票强制止损</td><td class="num">亏损 ≥ ${fmt(risk?.single_stop_loss_pct,0)}%</td><td><span class="badge ok">锁定</span></td></tr>
        <tr><td>组合回撤降仓</td><td class="num">回撤 ≥ ${fmt(risk?.portfolio_drawdown_de_risk_pct,0)}% → 总仓位 ≤ ${fmt(risk?.de_risk_max_total_pct,0)}%</td><td><span class="badge ok">锁定</span></td></tr>
        <tr><td>连续跑输再评估</td><td class="num">≥ ${risk?.underperform_months_reeval ?? 3} 个月</td><td><span class="badge warn">告警</span></td></tr>
      </tbody>
    </table>
    <ul class="muted" style="margin-top:10px">${rules.map((r) => `<li>${r}</li>`).join("")}</ul>
  `;
}

function renderBenchInfo(benches) {
  $("bench-info").innerHTML = (benches.items || []).map((b) => `
    <div class="card" style="margin-bottom:10px">
      <h3>${b.name} ${b.id === benches.default ? '<span class="badge ok">默认</span>' : ""}</h3>
      <p>${b.reason}</p>
    </div>
  `).join("");
}

function renderMethod(cfg) {
  const costs = cfg.costs || {};
  const ash = costs.a_share || {};
  $("method-body").innerHTML = `
    <table>
      <thead><tr><th>项目</th><th>规则</th></tr></thead>
      <tbody>
        <tr><td>选股时点</td><td>T 日收盘数据选股（禁止盘中预知收盘）</td></tr>
        <tr><td>成交时点</td><td>T+1 开盘价成交</td></tr>
        <tr><td>A股成本</td><td>佣金 ${fmt((ash.commission_rate||0)*10000,2)}‱ + 卖出印花税 ${fmt((ash.stamp_tax_sell||0)*100,2)}% + 滑点 ${fmt((ash.slippage_pct||0)*100,2)}%（最低佣金 ${ash.min_commission} 元）</td></tr>
        <tr><td>加密成本</td><td>费率 ${fmt(((costs.crypto||{}).commission_rate||0)*100,2)}% + 滑点 ${fmt(((costs.crypto||{}).slippage_pct||0)*100,2)}%（Binance）</td></tr>
        <tr><td>聚宽策略</td><td>策略广场热门思路的本地可复现实现，可切换；官方广场需授权接口</td></tr>
        <tr><td>合格策略</td><td>${cfg.pass_rule?.rule_text || "跑赢基准≥5pp且回撤更小"}</td></tr>
      </tbody>
    </table>
  `;
}

async function runStrategyScan() {
  readFilters();
  $("strategy-result").innerHTML = `<div class="loading">按「${state.strategy}」选股中…</div>`;
  try {
    const data = await api(`/api/scan/strategy?strategy_id=${state.strategy}&market=${state.market}&top_n=${state.topN}`);
    state.lastPicks = data.items || [];
    const s = data.strategy || {};
    $("strategy-result").innerHTML = `
      <div class="notice">策略：<b>${s.name}</b> · ${s.source_label} · ${s.desc}</div>
      <table>
        <thead><tr><th>代码</th><th>名称</th><th>市场</th><th class="num">得分</th><th class="num">价格</th><th>理由</th></tr></thead>
        <tbody>${state.lastPicks.map((it) => `<tr>
          <td>${it.code}</td><td>${it.name || ""}</td>
          <td>${it.market === "crypto" ? "加密" : "A股"}</td>
          <td class="num"><span class="score ${scoreClass(it.score)}">${fmt(it.score,1)}</span></td>
          <td class="num">${fmt(it.price, it.market==="crypto"?4:2)}</td>
          <td>${it.reason || ""}</td>
        </tr>`).join("") || `<tr><td colspan="6" class="muted">无结果</td></tr>`}</tbody>
      </table>`;
  } catch (e) {
    $("strategy-result").innerHTML = `<div class="notice">失败：${e.message}</div>`;
  }
}

async function applyStrategy() {
  readFilters();
  const btn = $("btn-apply-strategy");
  const box = $("apply-result");
  if (btn) { btn.disabled = true; btn.textContent = "应用中…"; }
  if (box) box.innerHTML = `<div class="muted">正在创建/绑定策略独立账户…</div>`;
  try {
    const data = await postApi("/api/strategies/active", { strategy_id: state.strategy });
    if (data.ok) {
      const acc = data.account || {};
      const warn = (data.warnings || []).join("；");
      const body =
        `${data.reason}\n` +
        `账户: ${acc.account_id}\n` +
        `策略: ${acc.strategy_name}（${acc.strategy_source || "-"}）\n` +
        `初始资金: ${fmt(acc.initial_cash, 0)} · 可用现金: ${fmt(acc.cash, 0)}\n` +
        `持仓: ${acc.open_positions} 只 · 仓位: ${fmt(acc.exposure_pct)}% · 持有天数: ${acc.holding_days}\n` +
        `${acc.created ? "✓ 已新建独立账户" : "✓ 已绑定既有独立账户"}\n` +
        `查看路径：左侧「模拟账户」→ 选择「${acc.strategy_name}」`;
      showToast(warn ? "warn" : "ok", warn ? "已应用（有提醒）" : "应用成功", body);
      if (box) {
        box.innerHTML = `<div class="apply-result ${warn ? "" : "ok"}">
          <b>${warn ? "已应用，但请注意" : "应用成功"}</b>
          <div class="muted" style="margin-top:6px;white-space:pre-wrap">${body}</div>
          ${warn ? `<div style="margin-top:8px;color:var(--accent-2)">原因提醒：${warn}</div>` : ""}
          <div style="margin-top:10px"><button class="btn secondary" onclick="document.querySelector('[data-view=paper]').click()">前往模拟账户</button></div>
        </div>`;
      }
      $("scan-status").textContent = `已应用 → 独立账户 ${acc.account_id}`;
      // 刷新账户下拉
      try { await loadAccounts(acc.account_id); } catch (_) {}
    } else {
      const reason = data.reason || "未知错误";
      const code = data.code || "ERROR";
      showToast("err", "应用失败", `错误码: ${code}\n${reason}${data.available ? `\n可选策略: ${data.available.join(", ")}` : ""}`);
      if (box) {
        box.innerHTML = `<div class="apply-result err"><b>应用失败</b>
          <div class="muted" style="margin-top:6px">${code}: ${reason}</div>
          ${data.available ? `<div class="muted">可选：${data.available.join(", ")}</div>` : ""}
        </div>`;
      }
      $("scan-status").textContent = `应用失败：${reason}`;
    }
  } catch (e) {
    showToast("err", "应用失败", e.message || String(e));
    if (box) box.innerHTML = `<div class="apply-result err"><b>应用失败</b><div class="muted">${e.message}</div></div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "应用到模拟盘组合"; }
  }
}

async function runRebalance() {
  readFilters();
  $("rebalance-orders").innerHTML = `<div class="loading">计算调仓…</div>`;
  try {
    // 确保有目标池
    if (!state.lastPicks.length) await runStrategyScan();
    let paper = state.paper;
    if (!paper) {
      paper = await api("/api/paper/state");
      state.paper = paper;
    }
    const positions = (paper.portfolio?.open_positions || []).map((p) => ({
      code: p.code, name: p.name, market: p.market, strategy: p.strategy,
      shares: p.shares, buy_price: p.buy_price, last_price: p.last_price,
    }));
    const prices = {};
    positions.forEach((p) => { prices[p.code] = Number(p.last_price || p.buy_price || 0); });
    const targets = state.lastPicks.map((it) => {
      const code = it.market === "crypto" ? String(it.code).toUpperCase() : String(it.code).padStart(6, "0");
      prices[code] = Number(it.price || prices[code] || 0);
      return {
        code, name: it.name, market: it.market, strategy: state.strategy,
        weight_pct: 100 / Math.max(state.lastPicks.length, 1),
      };
    });
    const payload = {
      targets,
      positions,
      prices,
      equity: paper.portfolio?.equity || 1000000,
      cash: paper.portfolio?.cash || 0,
    };
    const data = await postApi("/api/risk/rebalance", payload);
    state.lastRebalance = data;
    const rs = data.risk_state || {};
    $("risk-state").innerHTML = `
      <div class="metric"><div class="k">总资产</div><div class="v">${fmt(rs.equity,0)}</div></div>
      <div class="metric"><div class="k">当前仓位</div><div class="v">${fmt(rs.total_exposure_pct)}%</div></div>
      <div class="metric"><div class="k">组合回撤</div><div class="v">${fmt(rs.portfolio_drawdown_pct)}%</div></div>
      <div class="metric"><div class="k">允许总仓位</div><div class="v">${fmt(rs.max_total_position_allowed_pct,0)}%${rs.de_risk_active ? " ⚠降仓" : ""}</div></div>
    `;
    const alerts = (rs.alerts || []).map((a) => `<div class="notice">${a}</div>`).join("");
    $("rebalance-orders").innerHTML = alerts + `
      <div class="muted">买入 ${data.buy_count} / 卖出 ${data.sell_count}</div>
      <table>
        <thead><tr><th>动作</th><th>代码</th><th>名称</th><th class="num">当前/目标股数</th><th class="num">Δ</th><th class="num">参考价</th><th class="num">目标权重</th><th>原因</th></tr></thead>
        <tbody>${(data.orders||[]).map((o) => `<tr>
          <td>${o.action === "buy" ? "买入" : o.action === "sell" ? "卖出" : "观望"}</td>
          <td>${o.code}</td><td>${o.name || ""}</td>
          <td class="num">${fmt(o.current_shares,0)} / ${fmt(o.target_shares,0)}</td>
          <td class="num">${fmt(o.delta_shares,0)}</td>
          <td class="num">${fmt(o.ref_price,4)}</td>
          <td class="num">${fmt(o.target_weight_pct,2)}%</td>
          <td>${o.reason || ""}</td>
        </tr>`).join("")}</tbody>
      </table>`;
  } catch (e) {
    $("rebalance-orders").innerHTML = `<div class="notice">调仓计算失败：${e.message}</div>`;
  }
}

function drawNavChart(canvasId, dates, equity, benchEquity) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const parent = canvas.parentElement;
  const w = parent.clientWidth - 16;
  canvas.width = w;
  canvas.height = 220;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const series = [equity, benchEquity].filter((s) => s && s.length);
  if (!series.length) return;
  const all = series.flat().filter((x) => Number.isFinite(Number(x)));
  const min = Math.min(...all) * 0.995;
  const max = Math.max(...all) * 1.005;
  const pad = 28;
  function draw(arr, color) {
    if (!arr || arr.length < 2) return;
    ctx.beginPath();
    arr.forEach((v, i) => {
      const x = pad + (i / (arr.length - 1)) * (canvas.width - pad * 2);
      const y = canvas.height - pad - ((v - min) / (max - min || 1)) * (canvas.height - pad * 2);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  // 网格
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  for (let i = 0; i < 5; i++) {
    const y = pad + i * ((canvas.height - pad * 2) / 4);
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(canvas.width - pad, y); ctx.stroke();
  }
  draw(benchEquity, "#f5a524");
  draw(equity, "#2dd4bf");
  ctx.fillStyle = "#8b9bb4";
  ctx.font = "11px sans-serif";
  ctx.fillText("策略净值（青） vs 基准（橙）", 10, 14);
}

function heatColor(v) {
  if (v === null || v === undefined) return "#1a2438";
  if (v >= 0) {
    const t = Math.min(v / 8, 1);
    return `rgba(52,211,153,${0.25 + t * 0.7})`;
  }
  const t = Math.min(Math.abs(v) / 8, 1);
  return `rgba(248,113,113,${0.25 + t * 0.7})`;
}

function renderBacktest(data) {
  state.lastBt = data;
  if (data.error) {
    $("bt-result").innerHTML = `<div class="notice">${data.error}</div>`;
    return;
  }
  const bench = data.benchmark;
  const q = data.qualified;
  const qBadge = q === true ? `<span class="badge ok">合格</span>` : q === false ? `<span class="badge bad">不合格</span>` : `<span class="badge warn">缺基准</span>`;
  const months = data.monthly_returns || [];
  const dist = data.pnl_distribution || {};
  const sens = data.sensitivity;
  $("bt-meta").textContent = `${data.strategy?.name || ""} · ${data.execution_rule || ""}`;

  $("bt-result").innerHTML = `
    <div class="metrics">
      <div class="metric"><div class="k">策略累计收益</div><div class="v">${fmt(data.total_return_pct)}%</div></div>
      <div class="metric"><div class="k">基准收益</div><div class="v">${bench ? fmt(bench.total_return_pct)+"%" : "-"}</div></div>
      <div class="metric"><div class="k">最大回撤 / 基准回撤</div><div class="v">${fmt(data.max_drawdown_pct)}% / ${bench?fmt(bench.max_drawdown_pct)+"%":"-"}</div></div>
      <div class="metric"><div class="k">夏普 / 年化</div><div class="v">${fmt(data.sharpe,3)} / ${fmt(data.annualized_pct)}%</div></div>
    </div>
    <div style="margin:8px 0">${qBadge}
      <span class="badge ${data.excess_return_pct >= 5 ? "ok" : "bad"}">超额 ${fmt(data.excess_return_pct)}pp</span>
      <span class="muted">${data.qualified_reason || data.pass_rule || ""}</span>
    </div>
    <div class="chart-box"><canvas id="nav-canvas"></canvas></div>
    <div class="grid2" style="margin-top:14px">
      <div>
        <h3 style="font-size:14px;margin:0 0 6px">月度收益热力图</h3>
        <div class="heat">${months.map((m) =>
          `<div style="background:${heatColor(m.return_pct)}" title="${m.month} ${m.return_pct}%">${m.month.slice(5)}<br/>${fmt(m.return_pct,1)}%</div>`
        ).join("") || "<div class='muted'>暂无</div>"}</div>
      </div>
      <div>
        <h3 style="font-size:14px;margin:0 0 6px">盈亏分布（卖出笔）</h3>
        <div class="muted">样本 ${dist.count || 0} · 均值 ${fmt(dist.avg)}% · 中位 ${fmt(dist.median)}% · 胜率 ${dist.win_rate != null ? fmt(dist.win_rate*100,1)+"%" : "-"}</div>
        <div class="chart-box"><canvas id="pnl-canvas"></canvas></div>
      </div>
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><h2>参数敏感性</h2><div class="desc">${sens?.conclusion || "-"} · ${sens?.overfit_warning || ""}</div></div>
      <table>
        <thead><tr><th>参数</th><th>取值</th><th class="num">累计收益%</th><th class="num">较基准Δpp</th><th class="num">回撤%</th><th>判定</th></tr></thead>
        <tbody>
          <tr><td>base</td><td>${sens?.base?.param || "-"}</td><td class="num">${fmt(sens?.base?.total_return_pct)}</td><td class="num">-</td><td class="num">${fmt(sens?.base?.max_drawdown_pct)}</td><td><span class="badge">基准参数</span></td></tr>
          ${(sens?.variants || []).map((v) => `<tr>
            <td>${v.param}</td><td>${v.value}</td>
            <td class="num">${fmt(v.total_return_pct)}</td>
            <td class="num">${fmt(v.delta_return_pp)}</td>
            <td class="num">${fmt(v.max_drawdown_pct)}</td>
            <td>${v.collapse ? `<span class="badge bad">崩塌</span>` : `<span class="badge ok">可接受</span>`}</td>
          </tr>`).join("") || `<tr><td colspan="6" class="muted">未运行敏感性</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="notice" style="margin-top:10px">
      账户：初始 ${fmt(data.account?.initial_cash,0)} → 期末 ${fmt(data.account?.final_equity,0)} ·
      买/卖 ${data.account?.buy_count}/${data.account?.sell_count} 笔 ·
      风控：单票≤${data.risk?.max_single_position_pct}% 止损${data.risk?.single_stop_loss_pct}% 回撤降仓${data.risk?.portfolio_drawdown_de_risk_pct}%
    </div>
  `;
  drawNavChart("nav-canvas", data.dates, data.equity, null);
  // 基准曲线：用 performance 里没有独立 bench equity 列表时，用收益还原
  if (bench && data.equity?.length) {
    const b0 = data.account?.initial_cash || data.equity[0];
    const bEnd = b0 * (1 + (bench.total_return_pct || 0) / 100);
    const bEq = data.equity.map((_, i) => b0 + (bEnd - b0) * (i / (data.equity.length - 1 || 1)));
    // 更真实：若后端返回 dates+可从benchmark close重建；先用线性近似仅作示意时改为：
  }
  // 重新请求完整 benchmark equity 由后端放 equity.benchmark_equity
  if (data.benchmark_equity && data.benchmark_equity.length) {
    drawNavChart("nav-canvas", data.dates, data.equity, data.benchmark_equity);
  } else if (bench && data.equity?.length) {
    const b0 = data.equity[0] / (1 + (data.total_return_pct||0)/100) * (1 + (bench.total_return_pct||0)/100);
    // 简单按同起点比例映射
    const ratio = (1 + (bench.total_return_pct||0)/100) / (1 + (data.total_return_pct||0)/100);
    const bEq = data.equity.map((v) => v * ratio * (1 + (data.total_return_pct||0)/100) / (1 + (bench.total_return_pct||0)/100));
    drawNavChart("nav-canvas", data.dates, data.equity, data.equity.map((v,i) => (data.equity[0] * (1 + (bench.total_return_pct||0)/100 * (i/(data.equity.length-1||1))))));
  }
  drawPnlBars(dist);
}

function drawPnlBars(dist) {
  const canvas = $("pnl-canvas");
  if (!canvas) return;
  const parent = canvas.parentElement;
  canvas.width = parent.clientWidth - 16;
  canvas.height = 200;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const bins = dist.bins || [];
  if (!bins.length) {
    ctx.fillStyle = "#8b9bb4";
    ctx.fillText("暂无已平仓样本", 20, 40);
    return;
  }
  const maxC = Math.max(...bins.map((b) => b.count), 1);
  const pad = 24;
  const bw = (canvas.width - pad*2) / bins.length;
  bins.forEach((b, i) => {
    const h = (b.count / maxC) * (canvas.height - pad*2);
    const x = pad + i * bw;
    const y = canvas.height - pad - h;
    const mid = (b.from + b.to) / 2;
    ctx.fillStyle = mid >= 0 ? "#34d399" : "#f87171";
    ctx.fillRect(x + 2, y, bw - 4, h);
  });
}

async function runBacktest() {
  readFilters();
  const btn = $("btn-backtest");
  btn.disabled = true;
  btn.textContent = "回测中…（含敏感性，较慢）";
  $("bt-result").innerHTML = `<div class="loading">正在按 T+1 + 成本 + 风控 + 基准回测…</div>`;
  try {
    const q = new URLSearchParams({
      strategy: state.strategy,
      market: state.market,
      top_n: String(state.topN),
      benchmark: state.bench,
      sensitivity: "true",
    });
    const data = await api(`/api/backtest/v2?${q.toString()}`);
    renderBacktest(data);
  } catch (e) {
    $("bt-result").innerHTML = `<div class="notice">回测失败：${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "运行专业回测";
  }
}

function stratLabel(id) {
  const s = (state.strategies || []).find((x) => x.id === id);
  return s ? s.name : id;
}

async function loadAccounts(selected) {
  const data = await api("/api/paper/accounts");
  const accounts = data.accounts || [];
  state.accounts = accounts;
  const sel = $("account-select");
  if (sel) {
    sel.innerHTML = accounts.map((a) => {
      const cname = a.strategy_name || a.strategy_id || "策略账户";
      return `<option value="${a.account_id}">${cname} · ${a.account_id}</option>`;
    }).join("");
    if (selected && accounts.some((a) => a.account_id === selected)) {
      sel.value = selected;
    } else if (state.accountId && accounts.some((a) => a.account_id === state.accountId)) {
      sel.value = state.accountId;
    } else if (accounts[0]) {
      sel.value = accounts[0].account_id;
    }
    state.accountId = sel.value;
  }

  const ov = $("accounts-overview");
  if (ov) {
    ov.innerHTML = `
      <div class="muted" style="margin-bottom:6px">策略账户总览（相互隔离）· 浮动盈亏 = 持仓按最新价相对成本价汇总</div>
      <table>
        <thead><tr>
          <th>账户</th>
          <th class="num">初始资金</th>
          <th class="num">可用现金</th>
          <th class="num">持仓</th>
          <th class="num">买/卖次数</th>
          <th class="num">浮动盈亏</th>
          <th class="num">已实现盈亏</th>
        </tr></thead>
        <tbody>${accounts.map((a) => {
          const cname = a.strategy_name || a.strategy_id || "策略账户";
          return `<tr>
          <td>
            <div style="font-weight:700">${cname}</div>
            <div class="muted" style="font-weight:400"><code>${a.account_id}</code></div>
          </td>
          <td class="num">${fmt(a.initial_cash,0)}</td>
          <td class="num">${fmt(a.cash,0)}</td>
          <td class="num">${a.open_count||0}</td>
          <td class="num">${a.buy_count||0}/${a.sell_count||0}</td>
          <td class="num" ${pnlColorClass(a.unrealized_pnl)}>${fmt(a.unrealized_pnl,0)}</td>
          <td class="num" ${pnlColorClass(a.realized_pnl)}>${fmt(a.realized_pnl,0)}</td>
        </tr>`;
        }).join("") || `<tr><td colspan="7" class="muted">暂无账户，请先在策略中心「应用到模拟盘」</td></tr>`}</tbody>
      </table>`;
  }
  return accounts;
}

function renderPaper(data) {
  state.paper = data;
  const p = data.portfolio || {};
  const st = data.db_stats || {};
  const rs = p.risk_state || {};
  const accId = data.account_id || p.account_id || "-";
  const banner = $("account-banner");
  if (banner) {
    const cname = p.strategy_name || stratLabel(p.strategy_id) || "策略账户";
    banner.innerHTML = `
      <span class="account-chip"><b style="font-weight:700">${cname}</b>&nbsp;<code style="font-weight:400;opacity:.75">${accId}</code></span>
      <span class="account-chip">来源 ${p.strategy_source || "-"}</span>
      <span class="muted">账户中文名加粗，下方/旁侧为 idcode；资金与持仓按账户隔离</span>`;
  }
  $("paper-summary").innerHTML = `
    <div class="metric"><div class="k">账户</div><div class="v" style="font-size:14px">${accId}</div></div>
    <div class="metric"><div class="k">初始资金</div><div class="v">${fmt(p.initial_cash,0)}</div></div>
    <div class="metric"><div class="k">可用现金</div><div class="v">${fmt(p.cash,0)}</div></div>
    <div class="metric"><div class="k">持仓市值/总资产</div><div class="v">${fmt(p.market_value,0)}/${fmt(p.equity,0)}</div></div>
    <div class="metric"><div class="k">仓位</div><div class="v">${fmt(rs.total_exposure_pct)}%${rs.de_risk_active?" ⚠":""}</div></div>
    <div class="metric"><div class="k">本账户买/卖</div><div class="v">${st.buy_count||0}/${st.sell_count||0}</div></div>
    <div class="metric"><div class="k">已实现盈亏</div><div class="v">${fmt(st.realized_pnl,0)}</div></div>
    <div class="metric"><div class="k">浮动盈亏</div><div class="v">${fmt(p.total_pnl,0)}（${fmt(p.total_pnl_pct)}%）</div></div>
  `;
  const opens = p.open_positions || data.positions?.filter((x) => x.status === "open") || [];
  $("paper-positions").innerHTML = `
    <div class="muted">持仓明细 · 持有天数后为持股数量/持股市值 · 市场列在评分前（本表无评分） · 买入后涨幅红涨绿跌</div>
    <table>
      <thead><tr>
        <th>账户</th><th>策略</th><th>代码</th><th>名称</th>
        <th class="num">数量</th><th class="num">买入成交价</th><th class="num">现价</th>
        <th class="num">买入后涨幅</th><th class="num">持有天数</th>
        <th class="num">持股数量</th><th class="num">持股市值</th>
        <th>市场</th><th class="num">浮动盈亏</th>
      </tr></thead>
      <tbody>${opens.map((x) => {
        const chg = x.hold_change_pct ?? x.unrealized_pnl_pct;
        const cname = p.strategy_name || stratLabel(x.strategy) || "策略账户";
        return `<tr>
        <td>
          <div style="font-weight:700">${cname}</div>
          <div class="muted" style="font-weight:400"><code>${x.account_id || accId}</code></div>
        </td>
        <td>${x.strategy_label || stratLabel(x.strategy)}</td>
        <td>${x.code}</td><td>${x.name || ""}</td>
        <td class="num">${fmt(x.shares, x.market==="crypto"?4:0)}</td>
        <td class="num">${fmt(x.buy_price,4)}</td>
        <td class="num">${fmt(x.last_price,4)}</td>
        <td class="num" ${pnlColorClass(chg)}>${fmt(chg)}%</td>
        <td class="num">${x.hold_days ?? x.hold_days_elapsed ?? "-"}</td>
        <td class="num">${fmt(x.shares, x.market==="crypto"?4:0)}</td>
        <td class="num">${fmt(x.market_value,2)}</td>
        <td>${x.market === "crypto" ? "加密" : "A股"}</td>
        <td class="num" ${pnlColorClass(x.unrealized_pnl)}>${fmt(x.unrealized_pnl,2)}</td>
      </tr>`;
      }).join("") || `<tr><td colspan="13" class="muted">该账户暂无持仓</td></tr>`}</tbody>
    </table>`;
  const trades = data.trades || [];
  $("paper-trades").innerHTML = `
    <div class="muted">该账户交易记录（含买入成交价/当日收盘/按收盘收益）</div>
    <table>
      <thead><tr>
        <th>账户</th><th>时间</th><th>动作</th><th>策略</th><th>代码</th>
        <th class="num">成交价</th><th class="num">数量</th>
        <th class="num">当日收盘</th><th class="num">按收盘收益</th><th class="num">按收盘涨幅</th><th class="num">行情涨幅</th>
      </tr></thead>
      <tbody>${trades.slice(0, 25).map((t) => {
        let extra = {};
        try { extra = typeof t.extra_json === "string" ? JSON.parse(t.extra_json) : (t.extra || {}); } catch (_) {}
        const close = extra.close_price ?? extra.closePrice;
        const holdPnl = extra.hold_pnl_close;
        const holdChg = extra.hold_change_close_pct;
        const mktChg = extra.market_change_pct;
        return `<tr>
        <td><code>${t.account_id || "-"}</code></td>
        <td>${(t.trade_ts||t.time||"").slice(0,16)}</td>
        <td>${t.action==="buy"?"买入":"卖出"}</td>
        <td>${stratLabel(t.strategy)}</td>
        <td>${t.code}</td>
        <td class="num">${fmt(t.price,4)}</td>
        <td class="num">${fmt(t.shares,2)}</td>
        <td class="num">${close!=null?fmt(close,4):"-"}</td>
        <td class="num">${holdPnl!=null?fmt(holdPnl,2):(t.pnl!=null?fmt(t.pnl,2):"-")}</td>
        <td class="num">${holdChg!=null?fmt(holdChg)+"%":(t.pnl_pct!=null?fmt(t.pnl_pct)+"%":"-")}</td>
        <td class="num">${mktChg!=null?fmt(mktChg)+"%":"-"}</td>
      </tr>`;
      }).join("") || `<tr><td colspan="11" class="muted">该账户暂无成交</td></tr>`}</tbody>
    </table>
    <div class="muted" style="margin-top:8px">
      口径：按收盘收益/涨幅 = <b>当日收盘价</b> 相对 <b>买入成交价</b>；行情涨幅 = 标的当日收盘涨跌幅。飞书推送为同结构表格。
    </div>`;
}

function pnlColorClass(v) {
  if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return "";
  const n = Number(v);
  if (n > 0) return "style=\"color:#ef4444\"";
  if (n < 0) return "style=\"color:#22c55e\"";
  return "style=\"color:var(--ink)\"";
}

function renderPaperPositions(data) {
  const p = data.portfolio || {};
  const accId = data.account_id || p.account_id || "-";
  const opens = p.open_positions || data.positions?.filter((x) => x.status === "open") || [];
  $("paper-positions").innerHTML = `
    <div class="muted">持仓列表 · 策略来源：${p.strategy_name || "-"} · A股/加密分列展示 · 买入后涨幅：红涨绿跌</div>
    <table>
      <thead><tr>
        <th>账户</th><th>策略</th><th>市场</th><th>代码</th><th>名称</th>
        <th class="num">数量</th><th class="num">买入成交价</th><th class="num">现价</th>
        <th class="num">买入后涨幅</th><th class="num">持有天数</th><th class="num">浮动盈亏</th>
      </tr></thead>
      <tbody>${opens.map((x) => {
        const chg = x.hold_change_pct ?? x.unrealized_pnl_pct;
        return `<tr>
        <td><code>${x.account_id || accId}</code></td>
        <td>${x.strategy_label || stratLabel(x.strategy)}</td>
        <td>${x.market === "crypto" || x.market_type === "crypto" ? "加密" : "A股"}</td>
        <td>${x.code}</td><td>${x.name || ""}</td>
        <td class="num">${fmt(x.shares, x.market==="crypto"?4:0)}</td>
        <td class="num">${fmt(x.buy_price,4)}</td>
        <td class="num">${fmt(x.last_price,4)}</td>
        <td class="num" ${pnlColorClass(chg)}>${fmt(chg)}%</td>
        <td class="num">${x.hold_days ?? x.hold_days_elapsed ?? "-"}</td>
        <td class="num" ${pnlColorClass(x.unrealized_pnl)}>${fmt(x.unrealized_pnl,2)}</td>
      </tr>`;
      }).join("") || `<tr><td colspan="11" class="muted">该账户暂无持仓</td></tr>`}</tbody>
    </table>`;
}

async function fillWatchDates(slot) {
  try {
    const data = await api(`/api/watch/dates?slot=${encodeURIComponent(slot || "morning")}`);
    const dates = data.dates || [];
    const sel = $("watch-date");
    if (!sel) return dates;
    const cur = sel.value;
    sel.innerHTML = `<option value="">实时（今日计算）</option>` +
      dates.map((d) => `<option value="${d}">${d}</option>`).join("");
    if (cur && dates.includes(cur)) sel.value = cur;
    return dates;
  } catch (_) {
    return [];
  }
}

async function loadWatchPage(push) {
  const slot = $("watch-slot")?.value || "morning";
  const reportDate = $("watch-date")?.value || "";
  $("watch-body").innerHTML = `<div class="loading">加载关注/持仓…</div>`;
  try {
    await fillWatchDates(slot);
    let path;
    if (push) {
      path = `/api/signals/watch?slot=${slot}&market=all&push=true`;
    } else if (reportDate) {
      path = `/api/watch/page-data?slot=${slot}&report_date=${encodeURIComponent(reportDate)}`;
    } else {
      path = `/api/watch/page-data?slot=${slot}`;
    }
    const data = push ? await postApi(path) : await api(path);
    if (data.error && !(data.sections || []).length) {
      $("watch-body").innerHTML = `<div class="notice">${data.error}</div>`;
      $("watch-meta").textContent = `时段 ${slot} · 日期 ${reportDate || "实时"} · 无数据`;
      return;
    }
    const sections = data.sections || [];
    const mode = data.mode || (push ? "push" : "live");
    $("watch-meta").textContent =
      `${data.slot_label || slot} · ${mode === "history_db" ? "历史快照" : "实时"} ${data.report_date || data.as_of_date || ""} · ` +
      `策略账户 ${sections.length} · 排序：A股优先/未持仓优先 · 信号价后涨幅按首次信号价 · ` +
      `${push ? "已推送飞书" : "仅页面"}`;
    let html = `<div class="notice">
      颜色：已持仓橙色 [持仓] / 未持仓黑色 · 涨幅：&gt;0 红 / &lt;0 绿 / =0 黑<br/>
      <b>信号价</b>=策略信号日的最新收盘价；<b>连续上榜</b>时 <b>信号价后涨幅</b>=观察日收盘/<b>首次信号价</b>−1（不是每天刷新的最新信号价）。
      持仓明细 <b>买入后涨幅</b>=现价/成交成本−1。
    </div>`;
    sections.forEach((s) => {
      const watch = s.watch || [];
      const holdings = s.holdings || [];
      const accName = s.strategy_name || s.strategy_id || "策略账户";
      html += `<div class="panel">
        <div class="panel-head">
          <h2 style="margin:0;font-weight:700">${accName}</h2>
          <div class="desc"><code style="font-weight:400">${s.account_id}</code> · 资产 ${fmt(s.portfolio?.equity,0)} · 现金 ${fmt(s.portfolio?.cash,0)} · 关注中已持仓 ${s.held_count||0}</div>
        </div>
        <div class="muted">关注列表（A股在前·未持仓在前·市场在评分前）</div>
        <table>
          <thead><tr>
            <th>代码/名称</th>
            <th class="num">最新信号价</th>
            <th class="num">涨幅基准(首次信号价)</th>
            <th class="num">收盘价</th>
            <th class="num">行情涨幅</th>
            <th class="num">信号价后涨幅</th>
            <th class="num">上榜天数</th>
            <th class="num">持有天数</th>
            <th class="num">持股数量</th>
            <th class="num">持股市值</th>
            <th>市场</th>
            <th class="num">评分</th>
          </tr></thead>
          <tbody>${watch.map((r) => {
            const nameHtml = r.held
              ? `<span style="color:#F5A524;font-weight:700">${r.code} ${r.name||""} [持仓]</span>`
              : `<span style="color:var(--ink)">${r.code} ${r.name||""}</span>`;
            const sigChg = r.signal_change_pct;
            const appear = r.signal_appear_days || 1;
            const base = appear > 1
              ? `${fmt(r.first_signal_price, r.market==="加密"?4:2)}（${r.first_signal_date || "-"}）`
              : fmt(r.signal_price, r.market==="加密"?4:2);
            return `<tr>
              <td>${nameHtml}</td>
              <td class="num">${fmt(r.signal_price, r.market==="加密"?4:2)}</td>
              <td class="num">${base}</td>
              <td class="num">${fmt(r.close_price, r.market==="加密"?4:2)}</td>
              <td class="num">${r.market_change_pct!=null?fmt(r.market_change_pct)+"%":"-"}</td>
              <td class="num" ${pnlColorClass(sigChg)}>${sigChg!=null?fmt(sigChg)+"%":"-"}</td>
              <td class="num">${appear}</td>
              <td class="num">${r.hold_days!=null?r.hold_days:"-"}</td>
              <td class="num">${r.shares!=null?fmt(r.shares, r.market==="加密"?4:0):"-"}</td>
              <td class="num">${r.market_value!=null?fmt(r.market_value,2):"-"}</td>
              <td>${r.market}</td>
              <td class="num">${fmt(r.score,1)}</td>
            </tr>`;
          }).join("") || `<tr><td colspan="12" class="muted">无数据</td></tr>`}</tbody>
        </table>
        <div class="muted" style="margin-top:10px">持仓明细（买入后涨幅 = 相对买入成交成本）</div>
        <table>
          <thead><tr>
            <th>代码/名称</th><th class="num">买入成交价</th><th>买入日</th>
            <th class="num">现价</th><th class="num">买入后涨幅</th><th class="num">持有天数</th>
            <th class="num">持股数量</th><th class="num">持股市值</th>
            <th>市场</th><th class="num">浮动盈亏</th>
          </tr></thead>
          <tbody>${holdings.map((h) => {
            const chg = h.hold_change_pct ?? h.unrealized_pnl_pct;
            return `<tr>
              <td><span style="color:#F5A524">${h.code} ${h.name||""}</span></td>
              <td class="num">${fmt(h.buy_price,4)}</td>
              <td>${String(h.buy_date||"").slice(0,10)}</td>
              <td class="num">${fmt(h.last_price,4)}</td>
              <td class="num" ${pnlColorClass(chg)}>${fmt(chg)}%</td>
              <td class="num">${h.hold_days ?? h.hold_days_elapsed ?? "-"}</td>
              <td class="num">${fmt(h.shares, h.market==="crypto"?4:0)}</td>
              <td class="num">${fmt(h.market_value,2)}</td>
              <td>${h.market==="crypto"?"加密":"A股"}</td>
              <td class="num" ${pnlColorClass(h.unrealized_pnl)}>${fmt(h.unrealized_pnl,2)}</td>
            </tr>`;
          }).join("") || `<tr><td colspan="10" class="muted">无持仓</td></tr>`}</tbody>
        </table>
      </div>`;
    });
    $("watch-body").innerHTML = html;
    if (push) {
      showToast((data.push||{}).ok?"ok":"warn", "飞书推送", (data.push||{}).ok?"已发送全策略关注表":((data.push||{}).error||"未推送"));
    }
  } catch (e) {
    $("watch-body").innerHTML = `<div class="notice">加载失败：${e.message}</div>`;
  }
}

async function loadPaper() {
  readFilters();
  const accSel = $("account-select");
  const accountId = accSel?.value || state.accountId || null;
  await loadAccounts(accountId);
  const q = new URLSearchParams();
  const acc = $("account-select")?.value || accountId;
  if (acc) q.set("account_id", acc);
  if (state.dateFrom) q.set("date_from", state.dateFrom);
  if (state.dateTo) q.set("date_to", state.dateTo);
  const data = await api(`/api/paper/state?${q.toString()}`);
  state.accountId = data.account_id;
  if ($("account-select") && data.account_id) $("account-select").value = data.account_id;
  renderPaper(data);
}

async function runPaper() {
  const btn = $("btn-paper-run");
  const acc = $("account-select")?.value || state.accountId || null;
  btn.disabled = true; btn.textContent = "执行中…";
  try {
    const q = new URLSearchParams();
    if (acc) q.set("account_id", acc);
    const data = await postApi(`/api/paper/run?${q.toString()}`);
    renderPaper(data.snapshot || {});
    const run = data.run || {};
    const skips = run.skips || [];
    const skipMsg = skips.map((s) => s.reason || s.action).join("；");
    showToast(
      skips.length && !(run.buys||[]).length ? "warn" : "ok",
      `模拟执行 ${run.date}`,
      `账户: ${run.account_id}\n策略: ${run.strategy_name || run.strategy_id}\n` +
      `买入 ${run.buys?.length||0} / 卖出 ${run.sells?.length||0}${skips.length ? `\n跳过 ${skips.length}：${skipMsg}` : ""}\n` +
      `总资产 ${fmt(run.portfolio?.equity,0)} 现金 ${fmt(run.portfolio?.cash,0)}`
    );
  } catch (e) {
    showToast("err", "模拟执行失败", e.message);
  } finally {
    btn.disabled = false; btn.textContent = "执行该账户模拟（T+1）";
  }
}

async function runMorning() {
  try {
    readFilters();
    const acc = $("account-select")?.value || state.accountId || null;
    const q = new URLSearchParams();
    if (acc) q.set("account_id", acc);
    q.set("market", state.market);
    const data = await postApi(`/api/signals/morning?${q.toString()}`);
    showToast(data.push?.ok ? "ok" : "warn", "早盘提醒", data.push?.ok ? `已推送飞书\n${(data.watchlist_text||"").split("\n").slice(0,4).join("\n")}` : (data.push?.error || "已生成文本"));
  } catch (e) {
    showToast("err", "早盘提醒失败", e.message);
  }
}

async function runWatchSlot(slot) {
  const label = { morning: "早盘", midday: "午盘", evening: "收盘" }[slot] || slot;
  showToast("ok", `${label}关注`, "正在推送全策略关注表（持仓橙色/未持仓黑色，评分置后）…");
  try {
    const data = await api(`/api/signals/watch?slot=${slot}&market=${encodeURIComponent(state.market)}&push=true`);
    const push = data.push || {};
    const n = (data.sections || []).length;
    const held = (data.sections || []).reduce((s, x) => s + (x.held_count || 0), 0);
    showToast(
      push.ok ? "ok" : "warn",
      `${label}关注${push.ok ? "已推送" : "生成完成"}`,
      `策略账户 ${n} 个 | 关注中标记已持仓 ${held} 条\n` +
      `观察日 ${data.as_of_date} · ${push.ok ? "飞书已发送" : (push.error || "见接口返回 markdown")}`
    );
  } catch (e) {
    showToast("err", `${label}关注失败`, e.message);
  }
}

async function runAllAccounts() {
  const btn = $("btn-run-all");
  if (btn) { btn.disabled = true; btn.textContent = "全策略执行中…"; }
  try {
    const data = await postApi("/api/paper/run-all?refresh_signals=true");
    const results = data.results || [];
    const buys = results.reduce((s, r) => s + ((r.buys || []).length), 0);
    const sells = results.reduce((s, r) => s + ((r.sells || []).length), 0);
    const errs = results.filter((r) => r.error).map((r) => `${r.strategy_id}: ${r.error}`);
    showToast(
      errs.length ? "warn" : "ok",
      "全策略模拟执行完成",
      `账户 ${data.count || results.length} 个 · 合计买入 ${buys} / 卖出 ${sells}` +
      (errs.length ? `\n异常:\n${errs.slice(0, 4).join("\n")}` : "\n各策略账户已分别推送飞书成交/简报表")
    );
    await loadPaper();
  } catch (e) {
    showToast("err", "全策略执行失败", e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "全部策略账户执行"; }
  }
}

async function loadNotifyChannels() {
  try {
    const data = await api("/api/notify/channels");
    const chs = data.channels || [];
    const el = $("notify-channels");
    if (!el) return;
    el.innerHTML = `<table>
      <thead><tr><th>渠道</th><th>状态</th><th>启用</th><th>说明</th></tr></thead>
      <tbody>${chs.map((c) => `<tr>
        <td><b>${c.channel}</b></td>
        <td>${c.ready ? `<span class="badge ok">已配置</span>` : `<span class="badge warn">未配置</span>`}</td>
        <td>${c.enabled ? "是" : "否"}</td>
        <td class="muted">${(data.hints||{})[c.channel] || c.message || ""}</td>
      </tr>`).join("")}</tbody></table>`;
    if ($("feishu-status")) {
      const on = chs.filter((c) => c.enabled && c.ready).map((c) => c.channel);
      $("feishu-status").textContent = on.length
        ? `已启用通知渠道：${on.join("、")}`
        : "请在 config.yaml → notify 配置渠道";
    }
  } catch (e) {
    const el = $("notify-channels");
    if (el) el.innerHTML = `<div class="notice">读取通知渠道失败：${e.message}</div>`;
  }
}

async function testNotifyChannel() {
  const channel = $("notify-channel")?.value || "feishu";
  try {
    const data = await postApi("/api/notify/test", { channel });
    showToast(data.ok ? "ok" : "err", `测试 ${channel}`, data.ok ? "发送成功" : (data.channels?.[channel]?.error || "失败"));
  } catch (e) {
    showToast("err", `测试 ${channel} 失败`, e.message);
  }
}

async function generateSignals(push) {
  readFilters();
  $("signals-result").innerHTML = `<div class="loading">生成中…</div>`;
  try {
    const path = push ? `/api/signals/push?market=${state.market}&top_n=20` : `/api/signals/generate?market=${state.market}&top_n=20`;
    const data = await postApi(path);
    const counts = Object.fromEntries(Object.entries(data.signals||{}).map(([k,v])=>[k,(v||[]).length]));
    $("signals-result").innerHTML = `
      <div class="notice">已生成 ${data.report_date} · 入库${data.db_saved?"成功":"失败"} · 推送 ${data.feishu_push?.ok?"成功":(data.feishu_push?.error||"未推送")}</div>
      <div class="muted">${JSON.stringify(counts)}</div>`;
  } catch (e) {
    $("signals-result").innerHTML = `<div class="notice">失败：${e.message}</div>`;
  }
}

function bind() {
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.addEventListener("click", () => {
      setView(b.dataset.view);
      if (b.dataset.view === "paper") loadPaper();
      if (b.dataset.view === "watch") loadWatchPage(false);
      if (b.dataset.view === "signals") loadNotifyChannels();
    });
  });
  $("btn-scan-strategy").addEventListener("click", runStrategyScan);
  $("btn-apply-strategy").addEventListener("click", applyStrategy);
  $("btn-rebalance").addEventListener("click", runRebalance);
  $("btn-backtest").addEventListener("click", runBacktest);
  $("btn-paper-run").addEventListener("click", runPaper);
  $("btn-paper-refresh").addEventListener("click", loadPaper);
  $("btn-morning")?.addEventListener("click", () => runWatchSlot("morning"));
  $("btn-watch-am")?.addEventListener("click", () => runWatchSlot("morning"));
  $("btn-watch-noon")?.addEventListener("click", () => runWatchSlot("midday"));
  $("btn-watch-pm")?.addEventListener("click", () => runWatchSlot("evening"));
  $("btn-run-all")?.addEventListener("click", runAllAccounts);
  $("btn-watch-load")?.addEventListener("click", () => loadWatchPage(false));
  $("btn-watch-push")?.addEventListener("click", () => loadWatchPage(true));
  $("watch-slot")?.addEventListener("change", () => fillWatchDates($("watch-slot")?.value));
  $("watch-date")?.addEventListener("change", () => loadWatchPage(false));
  $("btn-signals").addEventListener("click", () => generateSignals(false));
  $("btn-push").addEventListener("click", () => generateSignals(true));
  $("btn-notify-test")?.addEventListener("click", testNotifyChannel);
  if ($("account-select")) {
    $("account-select").addEventListener("change", () => {
      state.accountId = $("account-select").value;
      loadPaper();
    });
  }
  ["market-select","bench-select","strategy-select","date-from","date-to"].forEach((id) => {
    $(id).addEventListener("change", () => {
      readFilters();
      $("scan-status").textContent = `筛选：${state.market} · 策略 ${state.strategy} · 基准 ${state.bench}`;
    });
  });
}

async function init() {
  bind();
  setView("strategies");
  try {
    await loadStrategies();
  } catch (e) {
    $("scan-status").textContent = `初始化失败：${e.message}`;
  }
}

document.addEventListener("DOMContentLoaded", init);
