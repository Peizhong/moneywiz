---
name: moneywiz
description: 红利投资筛选快照与分析。运行筛选脚本生成当日快照（当天重复运行自动覆盖），并对比历史快照分析榜单、信号、分数与板块温度计的变化。当用户想查看红利股票/基金筛选结果、跟踪标的排名变化或做历史对比时使用。
---

# 红利筛选快照与分析

在 moneywiz 仓库内运行。快照目录 `snapshots/`，每天最多一份
（`YYYY-MM-DD.json` 结构化结果 + `YYYY-MM-DD.txt` 报告文本）。

## 运行

```bash
.venv/bin/python snapshot.py
```

- 快照落盘 `snapshots/YYYY-MM-DD.{json,txt}`；**当天重复运行覆盖同名文件**（只保留最后一次）；
- 脚本打印 JSON 快照路径与报告摘要行；
- 不进入交互式详情（详情由本 skill 按需从 JSON 数据组织）。

## 分析（每次运行后必做）

1. 列出历史快照 `snapshots/*.json`，选参照点：**昨日**（最近一份更早日期的快照）、
   **约 7 天前**最近一份、**约 30 天前**最近一份——存在才用，不存在跳过；
2. 先读当前 txt 与参照 txt 抓全局变化，再回 JSON 核对具体数值；
3. 对比维度：
   - 买入区新进/跌出（`output.buy_top_n` 名额内）与名次显著升降；
   - 信号变化（买入↔观察↔末位）及归因：对照 `item.scores`/`item.values` 的变化；
   - 总分漂移显著的标的：对照 `item.values` 逐指标归因；
   - 板块温度计趋势：`market` 的利差（spread）、上证红利 PE 分位（index_pe_position）；
   - 红标变化：`item.sustainability_flags` 新增/消失；
   - `item.new_constituent` 新成分股；
4. 输出中文分析：变化摘要 + 值得注意的标的 + 带具体数字与快照日期的归因。

## 注意事项

- **数值对比只从 JSON 取**，不从 txt 表格解析（txt 是格式化渲染产物，数值有截断）；
- `item.missing` 数据不足导致的变化要如实说明，不当成真实变化；
- 快照是当日最后一次运行的结果；
- 不修改 `config/` 与 `snapshots/` 下的历史文件；
- 用户想看单只标的详情时，用 JSON 里该标的 `item` 的数据组织回答
  （`reporter.render_detail` 需要的字段都在 item 里），不要再跑一遍取数。
