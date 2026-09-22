# TradeLab 迭代说明

## 分支模型
- `main`：稳定可运行基线（对应 CHANGELOG 版本）
- `feat/*`：功能迭代
- `fix/*`：缺陷修复

## 当前迭代 v0.6 焦点
1. 全市场增量行情缓存
2. 聚宽官方 API 适配层
3. 回测图表与报告导出
4. Walk-forward 样本外验证

## 提交约定
```
feat: <功能>
fix: <修复>
docs: <文档>
chore: <杂项>
```

## 发布检查清单
- [ ] `python -c "from app.main import app"` 可导入
- [ ] 计划任务脚本可跑通一次
- [ ] config.yaml 无真实 Webhook
- [ ] CHANGELOG 已更新
