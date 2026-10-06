# 分红筛选器（Dividend Screener）

一款基于 Python 的命令行投资辅助工具：维护自选股票与基金列表，运行后自动拉取
行情、分红、估值数据（数据源 [akshare](https://akshare.akfamily.xyz/)，免费、无需注册），
按红利策略多因子加权打分，在终端输出按总分降序排列的股票表、基金表和摘要。

## 环境安装

需要 Python 3.12+：

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # 运行依赖
.venv/bin/pip install -r requirements-dev.txt    # 开发依赖（pytest）
```

## 配置文件

三个 YAML 都在 `config/` 下，缺失或格式/字段不合法会在启动时报错并指出具体文件：

### `config/stocks.yaml` — 自选股票

```yaml
stocks:
  - code: "000001"    # 6 位代码，字符串
    name: "平安银行"
```

### `config/funds.yaml` — 自选基金

```yaml
funds:
  - code: "510880"
    name: "红利ETF"
    type: etf         # etf | lof | normal
    index: "上证红利"  # 可选；须在 akshare stock_index_pe_lg 支持的指数名单内
```

`type` 决定数据来源与折溢价口径：`etf` 用行情表的 IOPV，`lof` 用最新单位净值，
`normal`（场外开放式）无市价，折溢价指标不可评分。`index` 留空时按基金概况的
跟踪标的自动推断是否可算指数估值分位。

### `config/rules.yaml` — 权重与阈值

- `stocks.indicators` / `funds.indicators`：每个指标的 `weight`（各自总和必须为 100）
  与阈值档位；指标值按阈值分为 1.0 / 0.5 / 0.0 三档；
- `data`：`kline_days`（K 线/净值窗口天数，默认 120）、`pe_cache_days`
  （行业 PE/PB 缓存有效期，默认 7 天）；
- `output`：`buy_top_n`（前 N 名标「买入」）、`avoid_bottom_n`（末尾 N 名标「回避」）。

## 运行

```bash
.venv/bin/python main.py     # 或先激活 venv，再执行 python main.py
```

在项目根目录执行；配置与缓存分别读写 `config/` 与 `cache/`（行业 PE/PB 缓存
`cache/pe_cache.json`，过期为 `pe_cache_days` 天）。配置错误时打印错误并返回退出码 1，
正常结束返回 0。

## 输出说明

```
【股票】
  排名      代码  名称       总分  信号    亮点              风险      技术
----  ------  ----  -----  ----  ----  ----  ----
   1  000001  平安银行   92.5  买入    股息率、连续分红    -        MACD 金叉 RSI 56
   2  600036  招商银行   25.0  观察    -                 PB估值    RSI 42
【基金】
  排名      代码  名称      总分  信号    亮点            风险    技术
----  ------  -----  ----  ----  ----  ----  ----
   1  510880  红利ETF   75.0  买入    折溢价、基金规模   -        -
扫描 3 只标的（股票 2 / 基金 1），数据不足 0，耗时 12.3s
```

- **总分**：各指标按阈值分档（1.0 / 0.5 / 0.0）后按权重加权，满分 100；只对能拿到
  数据的指标重新归一化，不会因个别指标缺失而拉低分数。全部指标都拿不到数据时显示
  `N/A`，信号为「数据不足」。
- **信号**：按表内排名给出 —— 前 `buy_top_n` 名「买入」、末尾 `avoid_bottom_n` 名
  「回避」、其余「观察」；无分标的固定为「数据不足」，不占用买入/回避名额。
- **亮点 / 风险**：该标的得满分档（1.0）与零分档（0.0）的指标中文名，各最多两个；
  无则显示 `-`。
- **技术**：股票的 MACD 信号（金叉/死叉）与 RSI 数值，仅作参考，不参与打分。
- **摘要行**：扫描标的数（股票/基金）、数据不足数、耗时。

## 测试

```bash
.venv/bin/pytest            # 全量
.venv/bin/pytest tests/test_main.py -v
```

所有测试均 monkeypatch 数据层，不联网。

## 已知限制

- **akshare 上游接口变更**：接口不可用或字段改名时会记录 warning，日志与错误信息
  指向具体的 akshare 函数名（如 `stock_zh_a_spot_em`），便于定位是哪个上游接口失效；
  该数据源对应的指标降级为「数据不足」，不会中断整体运行。
- **沙箱网络限制**：本项目的开发沙箱无法访问 `push2.eastmoney.com` /
  `push2delay.eastmoney.com`，因此依赖这些域名的行情类接口（`stock_zh_a_spot_em`、
  `stock_zh_a_hist`、`fund_etf_spot_em`/`fund_lof_spot_em`、行业板块接口）未能真实联调，
  需在放行上述域名的网络环境下验证；`stock_history_dividend_detail`、
  `fund_open_fund_info_em`、`fund_overview_em`、`stock_index_pe_lg` 已验证可用。
  测试全部使用 mock，不受网络影响。
- **数据时效**：行情/净值来自公开接口，可能有延迟；行业 PE/PB 最多缓存
  `pe_cache_days` 天，指数 PE 分位取历史区间近似分位，均非投资建议。
