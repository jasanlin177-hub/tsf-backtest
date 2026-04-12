"""
fetch_0050_total_return.py — 計算 0050 ETF 真實「含息報酬」(Total Return)

方法：
1. 從 yfinance 取得分割還原後收盤價（含分割調整，不含除息調整）
2. 從 TWSE 取得歷年股利發放紀錄
3. 手動複利計算「股利再投入」的含息報酬指數

說明：
- yfinance auto_adjust=False 給出「未還原」原始收盤
- yfinance auto_adjust=True 給出「只還原分割」的收盤 (Adj Close)
  ← Yahoo 對台股的除息調整只還原股票股利，不還原現金股利
- 因此 0050 現金配息必須手動取得並複利還原
"""
import yfinance as yf
import requests
import pandas as pd
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HEADERS = {'User-Agent': 'Mozilla/5.0'}

def fetch_dividends_twse(stock_no):
    """從 TWSE API 取得歷年除息資料"""
    # TWSE 股利資料 API
    url = f"https://www.twse.com.tw/exchangeReport/TWT49U?response=json&stockNo={stock_no}&STR_DATE=20070101&END_DATE=20260101"
    try:
        r = requests.get(url, headers=HEADERS, verify=False, timeout=10)
        d = r.json()
        print(f"TWSE 除息 API status: {d.get('stat', 'unknown')}")
        print(f"Fields: {d.get('fields')}")
        if d.get('data'):
            print("Sample row:", d['data'][:3])
            return d
    except Exception as e:
        print(f"TWSE Error: {e}")
    return None

def fetch_dividends_goodinfo():
    """用 yfinance 直接取得配息紀錄"""
    ticker = yf.Ticker("0050.TW")
    divs = ticker.dividends
    print("\n0050 Dividend History (yfinance):")
    print(divs)
    return divs

def build_total_return_index():
    """建構含息報酬指數 (Total Return Index)"""
    # 1. 取得配息紀錄
    ticker = yf.Ticker("0050.TW")
    divs = ticker.dividends  # 單位: 每股配息金額

    # 2. 取得分割調整後收盤 (auto_adjust=False, 但取 Adj Close 欄)
    df = yf.download("0050.TW", start="2009-01-01", end="2026-01-01", auto_adjust=False)
    
    # 取月底收盤
    if ('Close', '0050.TW') in df.columns:
        close = df[('Close', '0050.TW')]
    else:
        close = df['Close']
    
    # 3. 建構 Total Return Index
    # 對每個配息日，計算「當日價格」下的再投入倍數
    # TRI = TRI_prev * (1 + 配息/除息前收盤)
    
    monthly_close = close.resample('ME').last().dropna()
    monthly_close.index = pd.to_datetime(monthly_close.index)
    
    # 建立月份 -> 配息 mapping (配息在除息日有效)
    div_monthly = {}
    for dt, amt in divs.items():
        ym = pd.Timestamp(dt).strftime('%Y-%m')
        div_monthly[ym] = div_monthly.get(ym, 0) + amt
    
    print("\n配息紀錄 (月份):")
    for ym, amt in sorted(div_monthly.items()):
        print(f"  {ym}: {amt:.4f}")
    
    # 計算 Total Return Index
    tri = 1.0
    prev_price = None
    tri_series = {}
    
    for dt, price in monthly_close.items():
        ym = dt.strftime('%Y-%m')
        # 如果這個月有配息，計算再投入收益
        if prev_price is not None:
            price_return = price / prev_price - 1
            div_return = div_monthly.get(ym, 0) / prev_price if prev_price else 0
            total_return = price_return + div_return
            tri *= (1 + total_return)
        tri_series[dt.strftime('%Y%m')] = tri
        prev_price = price
    
    # 輸出年底摘要
    print("\n含息報酬指數 (Total Return Index, 2009/01=1.0):")
    for ym, v in sorted(tri_series.items()):
        if ym.endswith('12'):
            print(f"  {ym}: {v:.4f}")
    
    # CAGR
    valid_items = sorted(tri_series.items())
    s_ym, s_val = valid_items[0]
    e_ym, e_val = valid_items[-1]
    years = (int(e_ym[:4]) + int(e_ym[4:])/12.0) - (int(s_ym[:4]) + int(s_ym[4:])/12.0)
    cagr = (e_val / s_val) ** (1 / years) - 1
    print(f"\n0050 Total Return CAGR: {cagr*100:.2f}% ({s_ym} - {e_ym}, {years:.1f} 年)")
    
    return tri_series

if __name__ == "__main__":
    print("=== Checking dividends from yfinance ===")
    fetch_dividends_goodinfo()
    print("\n=== Building Total Return Index ===")
    build_total_return_index()
