# 台股基金五虎將指數回測

**Live Demo → https://jasanlin177-hub.github.io/tsf-backtest/**

台灣股票型基金量化選股回測工具。以「Tiger Score 532」評分模型，每半年從台灣境內股票型基金中動態篩選前 5 名，組成 **TSF-Top5 指數**，並與 TAIEX 報酬指數、元大 0050 進行績效比較。

---

## 功能

- **單筆投入回測**：指定金額、區間，計算 TSF vs TAIEX vs 0050 的總報酬與 CAGR
- **定期定額回測**：每季投入，模擬長期 DCA 績效
- **淨值走勢圖**：互動式折線圖，區間基準 = 100
- **回撤分析圖**：最大回撤走勢
- **當前成分股**：最新一期五虎將及其 R1Y / R3Y / R5Y 報酬
- **換股紀錄**：每一期的進出場基金明細
- **自動更新**：GitHub Actions 每月自動爬取最新淨值並重新部署

---

## 評分模型：Tiger Score 532

每半年（6 月底 / 12 月底）對台灣股票型基金評分：

```
Tiger Score = R1Y × 50% + R3Y × 30% + R5Y × 20%
```

| 參數 | 說明 |
|------|------|
| R1Y  | 近 1 年報酬率 |
| R3Y  | 近 3 年報酬率 |
| R5Y  | 近 5 年報酬率 |

- 取分數前 5 名為成分股，加設**緩衝門檻**（0.6 × Top20 標準差）避免過度換股
- 基金須有完整 5 年淨值紀錄才具評分資格
- 資料來源：[基金資訊觀測站 SITCA](https://www.sitca.org.tw/)

---

## 資料管道

```
sitca_scraper.py          → sitca_open_equity_domestic.csv   (基金月度淨值)
fetch_taiex_tri.js        → data/taiex.json                  (TAIEX 報酬指數)
fetch_0050_total_return.py → data/etf-0050.json              (0050 含息報酬)
        ↓
tsf_backtest_engine.py    → data/tsf_index.json              (TSF 指數全歷史)
        ↓
tsf_backtest_with_real_data.html                             (前端回測介面)
```

GitHub Actions 每月 1 日自動執行完整管道並部署至 GitHub Pages。

---

## 本地執行

```bash
# 安裝依賴
pip install -r requirements.txt
npm install

# 更新各資料來源
python sitca_scraper.py
node fetch_taiex_tri.js
python fetch_0050_total_return.py

# 重建 TSF 指數
python tsf_backtest_engine.py

# 啟動本地預覽（http://localhost:8000）
python -m http.server 8000
```

---

## 專案結構

```
tsf-backtest/
├── tsf_backtest_with_real_data.html  # 前端回測介面（即 GitHub Pages 首頁）
├── tsf_backtest_engine.py            # 回測引擎：評分、選股、指數計算
├── sitca_scraper.py                  # SITCA 基金淨值爬蟲
├── fetch_taiex_tri.js                # TAIEX 報酬指數爬蟲
├── fetch_0050_total_return.py        # 0050 含息報酬計算
├── data/
│   ├── tsf_index.json                # TSF 指數完整歷史（前端載入此檔）
│   ├── taiex.json                    # TAIEX 報酬指數日頻數據
│   └── etf-0050.json                 # 0050 含息報酬指數數據
├── sitca_open_equity_domestic.csv    # SITCA 原始淨值資料
├── requirements.txt
├── package.json
└── .github/workflows/update-data.yml  # 自動更新排程
```

---

## 技術棧

| 層 | 技術 |
|----|------|
| 前端 | HTML / Tailwind CSS / Chart.js |
| 回測引擎 | Python（pandas、numpy） |
| 爬蟲 | Python（requests、BeautifulSoup）、Node.js（axios） |
| 部署 | GitHub Pages + GitHub Actions |

---

## 免責聲明

本工具僅供歷史回測研究用途，不構成任何投資建議。過去績效不代表未來報酬。
