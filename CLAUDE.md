# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## 專案概述

TSF-Top5 回測工具，用於台灣股票型基金（統一投信五檔旗艦基金）的淨值數據爬取與投資策略回測。

五檔基金代號：`tsf-leader`（統一奔騰）、`anchor`、`rising-star`、`warrior`、`gatekeeper`；基準指數：`taiex`、`etf-0050`。

## 核心文件

| 文件 | 功能 |
|------|------|
| `tsf_data_service.js` | 後端服務：DataStore / DataScraper / BacktestEngine / APIService |
| `tsf_backtest_with_real_data.html` | 前端回測界面（Chart.js 雷達圖） |
| `tsf_data_integration_guide.md` | 數據源選型文檔 |
| `data/*.json` | 本地 JSON 數據存儲 |

## 啟動與運行

```bash
# 安裝依賴（首次）
npm install axios cheerio cron

# 啟動後端服務（自動爬蟲 + API）
node tsf_data_service.js

# 純前端預覽（無後端）
python -m http.server 8000
# 訪問 http://localhost:8000/tsf_backtest_with_real_data.html
```

## 架構說明

後端 `tsf_data_service.js` 分四個模組：

- **DataStore** — 讀寫 `./data/*.json`，管理歷史 NAV 數據
- **DataScraper** — 爬蟲：主源 MoneyDJ / 備源 YesFund；ETF 0050 主源元大投信 / 備源鉅亨網 / 最終備源 Yahoo Finance
- **BacktestEngine** — 單筆投資與 DCA 策略績效計算
- **APIService** — 封裝對外接口，`initializeService()` 回傳 API 實例

前端連接後端只需修改 HTML 第 314 行，將 `mockData` 靜態對象替換為 `fetch('/api/nav/all')` 呼叫。

定時更新排程為每日 **15:30**（台灣收盤後）。

## 數據源降級策略

```
基金淨值: MoneyDJ (主) → YesFund (備)
ETF 0050: 元大投信 (主) → 鉅亨網 (備) → Yahoo Finance (最終備)
```

## API 用法

```javascript
const { initializeService } = require('./tsf_data_service');
const api = await initializeService();

api.getLatestNAV();                                     // 所有基金最新淨值
api.executeBacktest('2021-01-01', '2026-01-01', 'lump-sum');  // 單筆回測
api.executeBacktest('2021-01-01', '2026-01-01', 'dca');       // DCA 回測
```

Express 部署範例見 `QUICK_START.md`。
