# TradeLab 交易策略系统

多策略 A股 / 加密货币筛选、风控、回测、模拟盘与多渠道通知控制台。

> **免责声明**：本项目输出的是规则化研究信号与模拟结果，**不构成投资建议**，不保证收益。市场有风险，决策需独立。

---

## 功能总览

| 模块 | 说明 |
|------|------|
| 策略中心 | 内置短线/长线/抄底 + JoinQuant 策略广场风格（本地实现），可切换、可独立账户 |
| 风控与调仓 | 内置锁定：单票仓位/止损/组合降仓/跑输再评估；调仓买卖股数计算 |
| 回测报告 | T+1 成交、佣金印花税滑点、基准对比、净值/回撤/夏普/月度热力/参数敏感性 |
| 模拟账户 | 按策略隔离账户；14:45 T+1 买入；成本与滑点；SQLite 全量回溯 |
| 关注/持仓 | 早/中/晚关注表；A股优先、未持仓优先；信号价后涨幅、买入后涨幅、持有天数 |
| 多渠道通知 | 飞书 / 企业微信 / 钉钉 / Telegram / 邮件 / 通用 Webhook |

**合格策略标准（内置）**：累计收益 ≥ 基准 + 5pp，**且** 最大回撤 < 基准回撤。

**默认基准**：A股沪深300；加密 BTC 买入持有；混合 70% HS300 + 30% BTC。

---

## 快速开始（Windows）

```powershell
cd D:\PycharmProjects\trading-strategy-system

# 1) 虚拟环境与依赖
C:\Python314\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) 配置私密项（Webhook 等，不会提交到 Git）
Copy-Item config.local.example.yaml config.local.yaml
# 编辑 config.local.yaml 填入飞书/企微等 Webhook

# 3) 启动 Web 控制台
.\.venv\Scripts\python.exe run.py
# 浏览器打开 http://127.0.0.1:8787/
```

### 常用命令

```powershell
# 早盘 / 午盘 / 收盘 全策略关注推送
.\.venv\Scripts\python.exe scripts\watch_slot.py morning
.\.venv\Scripts\python.exe scripts\watch_slot.py midday
.\.venv\Scripts\python.exe scripts\watch_slot.py evening

# 全部策略账户模拟盘（T+1 买入/到期卖出）
.\.venv\Scripts\python.exe scripts\paper_run_all.py

# 生成信号并推送
.\.venv\Scripts\python.exe run_daily.py --market all --push
```

### Windows 计划任务（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File register_tasks.ps1
# 或手动 schtasks /create 参见 register_tasks.ps1
# 默认：09:20 / 12:30 / 14:45 / 17:30 / 17:35
```

---

## 目录结构

```
trading-strategy-system/
├── app/
│   ├── api/              # FastAPI 路由
│   ├── analytics/        # 绩效：回撤/夏普/月度/敏感性
│   ├── backtest/         # T+1 + 成本回测引擎
│   ├── data/             # A股(新浪/腾讯) + 加密(Binance) + 龙虎榜/资金流
│   ├── notify/           # 多渠道通知
│   ├── paper_trade/      # 模拟账户与调仓
│   ├── risk/             # 风控规则与调仓函数
│   ├── signals/          # 信号生成 + 飞书
│   └── strategies/       # 内置 + 聚宽风格策略注册表
├── scripts/              # 计划任务入口脚本
├── static/               # Web 控制台
├── data/                 # SQLite（gitignore）
├── cache/ reports/       # 行情缓存 / 信号报告（gitignore）
├── config.yaml           # 可提交配置（无私密）
├── config.local.example.yaml
├── register_tasks.ps1
├── run.py / run_daily.py / run_morning.py / run_paper_trade.py
└── requirements.txt
```

---

## 数据源

| 市场 | 源 | 说明 |
|------|----|------|
| A股列表/成交 | 新浪成交额榜、腾讯/新浪 K 线 | 主通道腾讯，东财回退 |
| 龙虎榜 | 东财数据中心 | 近 N 日上榜净买 |
| 资金流 | 新浪 MoneyFlow | 主力/全单净流入 |
| 加密货币 | **Binance API**（data-api.binance.vision） | 公开 K 线/价格，无需 Key |

---

## 策略说明（可切换）

**内置**：短线热门（量价+龙虎榜+资金流）、长线布局（趋势/估值分位）、抄底潜伏（超跌/缩量/企稳）

**JoinQuant 策略广场风格 · 本地实现**（官方广场需授权，此处为可复现模板）：

- 双动量轮动、小市值动量轮动、低波动质量、海龟突破、超跌反转、网格/均值回归

每个策略对应独立模拟账户 `acc_<strategy_id>`，资金/持仓/成交隔离。

**信号价**：策略生成信号当日最新收盘价；连续上榜时「信号价后涨幅」按**首次信号价**计算。

**执行**：T 日收盘选股 → T+1 开盘成交；A股成本 佣金万1.5 + 印花税 + 滑点 0.1%。

---

## 风控（内置锁定）

- 单一个股仓位 ≤ 30%，组合总仓位 0~100%（允许空仓）
- 个股单笔亏损 ≥ 30% 强制止损
- 组合回撤 ≥ 25% 时总仓位强制 ≤ 30%
- 连续 3 个月跑输基准 → 策略再评估告警

---

## 多渠道通知

在 `config.local.yaml` 配置后，`notify.enabled_channels` 可同时启用多个：

- 飞书自定义机器人
- 企业微信群机器人（微信生态推荐）
- 钉钉群机器人
- Telegram Bot
- 邮件 SMTP
- 通用 HTTP Webhook

控制台「信号/推送」可查看渠道状态并发送测试消息。

---

## 迭代计划（Roadmap）

### v0.5 当前基线
- 多策略注册与独立账户
- 风控/调仓、T+1 成本回测、绩效与基准合格判定
- 早/中/晚关注表、模拟盘入库、多渠道通知
- Windows 计划任务

### v0.6（下一迭代）
- [ ] 全市场扫描队列与增量行情库（SQLite/DuckDB）
- [ ] 聚宽官方策略广场 API 对接（需用户 Token）
- [ ] 页面可视化净值/回撤曲线组件完善
- [ ] 参数敏感性多维网格（持有期×TopN×费率）

### v0.7+
- [ ] 实盘交易网关抽象（仅研究接口，默认关闭）
- [ ] 多账户对比报告导出 PDF/Excel
- [ ] 组合优化（风险平价/等权约束）
- [ ] 样本外 Walk-forward 验证

欢迎按 Roadmap 提 Issue / PR 迭代。

---

## 配置要点

| 配置 | 文件 | 说明 |
|------|------|------|
| 业务参数 | `config.yaml` | 市场、股票池、因子权重、风控阈值、持有天数 |
| 密钥 | `config.local.yaml` | Webhook / SMTP / Telegram（gitignore） |
| 数据库 | `data/tradelab.db` | 信号、账户、持仓、成交、推送日志 |

---

## API 摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康与通知渠道 |
| GET | `/api/strategies` | 策略列表 |
| POST | `/api/strategies/active` | 应用策略到独立账户 |
| GET | `/api/scan/strategy` | 按策略选股 |
| GET | `/api/backtest/v2` | 专业回测（成本/T+1/基准/敏感性） |
| GET | `/api/risk/config` | 风控规则 |
| POST | `/api/risk/rebalance` | 调仓计算 |
| GET | `/api/watch/page-data` | 关注/持仓（可按日期） |
| POST | `/api/paper/run` | 模拟盘执行 |
| POST | `/api/paper/run-all` | 全策略账户执行 |
| POST | `/api/notify/test` | 多渠道测试推送 |

---

## 风险提示

- 回测/模拟含费用与滑点近似，仍无法覆盖涨跌停、停牌、冲击成本。
- 历史或模拟跑赢不代表未来。
- 禁止将本系统输出直接作为实盘下单依据。
