import requests
import urllib3
from bs4 import BeautifulSoup
import pandas as pd
import time
import os
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# 設定重試策略
retry_strategy = Retry(
    total=5,
    backoff_factor=1, # 1, 2, 4, 8, 16 seconds
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)

BASE_URL = "https://www.sitca.org.tw/ROC/Industry/IN2201.aspx?pid=IN2221_01"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

def extract_hidden_fields(soup):
    """從 BeautifulSoup 物件中取出 ASP.NET 隱藏欄位"""
    hidden = {}
    for tag in soup.find_all("input", type="hidden"):
        name = tag.get("name")
        if name:
            hidden[name] = tag.get("value", "")
    return hidden

def fix_mojibake(text):
    """
    SITCA伺服器資料庫有時會混用編碼（例如2025/12），導致同一頁面中部分基金名稱為正常UTF-8，
    部分卻是被誤以 Latin-1 解碼呈現的 UTF-8 亂碼 (例如 `è·¯` 代替 `路`)。
    此函數透過重新編碼與解碼來修復這些受損的字串。
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

def extract_data_table(soup, year_month):
    """從 BeautifulSoup 物件中提取基金資料表 (高精確版)"""
    # 特徵欄位，用來確定是我們想要的表格
    features = ["基金名稱", "基金統編", "單位淨值"]
    all_tables = soup.find_all("table")
    
    for table in all_tables:
        trs = table.find_all("tr")
        if len(trs) < 5: continue
        
        header_row_index = -1
        header_texts = []
        
        # 1. 精確尋找表頭列
        for idx, tr in enumerate(trs):
            # 使用 lxml 解析器時，即使 HTML 標籤不對稱 (例如 <td>...</th>)，
            # recursive=False 也能正確抓到 17 個單獨的 cells。
            cells = tr.find_all(["th", "td"], recursive=False)
            txts = [c.get_text(" ", strip=True).replace("\n", "").replace("\r", "") for c in cells]
            full_txt = "".join(txts)
            
            # 2007年約10欄，2014年後約17-20欄以上，給予彈性範圍
            if sum(1 for f in features if f in full_txt) >= 2 and 5 <= len(cells) <= 45:
                header_row_index = idx
                header_texts = txts
                print(f"DEBUG: Found header at row {idx} with {len(cells)} cells. Text: {' | '.join(txts)}")
                break
        
        if header_row_index == -1: continue
        
        # 2. 欄位索引映射 (使用優先權與排除邏輯防止衝突)
        col_indices = {
            "類型代號": -1, "基金統編": -1, "基金名稱": -1, 
            "計價幣別": -1, "單位淨值": -1, "類型": -1
        }
        
        for i, h_txt in enumerate(header_texts):
            h_txt_clean = h_txt.replace(" ", "").replace("\u3000", "")
            if "類型代號" in h_txt_clean or ("類型" in h_txt_clean and "代號" in h_txt_clean):
                col_indices["類型代號"] = i
            elif "基金統編" in h_txt_clean or "基金代碼" in h_txt_clean:
                col_indices["基金統編"] = i
            elif "基金名稱" in h_txt_clean:
                col_indices["基金名稱"] = i
            elif "計價幣別" in h_txt_clean or "幣別" in h_txt_clean:
                col_indices["計價幣別"] = i
            elif "單位淨值" in h_txt_clean or "淨值" in h_txt_clean:
                # 優先選擇台幣淨值
                if "台幣" in h_txt_clean:
                    col_indices["單位淨值"] = i
                elif col_indices["單位淨值"] == -1:
                    col_indices["單位淨值"] = i
            elif h_txt_clean == "類型" or "基金類型" in h_txt_clean:
                col_indices["類型"] = i
        
        print(f"DEBUG: Column Mapping: {col_indices}")

        # 核心欄位檢驗
        if col_indices["基金名稱"] == -1 or col_indices["基金統編"] == -1:
            continue
            
        # 3. 解析資料列
        parsed = []
        for tr in trs[header_row_index + 1:]:
            cells = tr.find_all(["th", "td"], recursive=False)
            # 過濾結構不符的行 (例如小計行、跨行描述行)
            if not (5 <= len(cells) <= 45): continue
            
            c_texts = [c.get_text(strip=True) for c in cells]
            
            try:
                raw_name = fix_mojibake(c_texts[col_indices["基金名稱"]])
                raw_id = fix_mojibake(c_texts[col_indices["基金統編"]])
                raw_type_code = fix_mojibake(c_texts[col_indices["類型代號"]]) if col_indices["類型代號"] != -1 else ""
                
                # 排除投信公司統計列與總計列
                clean_id = "".join(filter(str.isdigit, raw_id))
                
                # 過濾規則：
                # 1. 基金名稱不可短於 2 個字或為空 (過濾掉純規模數值的欄位)
                # 2. 基金名稱或類型代號不包含統計關鍵字，且名稱不包含欄位名稱
                # 3. 統編必須是 6-10 碼數字 (常見為 8 碼，過濾掉長數位如規模值 3,851,381,921 或空值)
                if not raw_name or len(raw_name) < 2: continue
                if any(x in raw_name for x in ["投信", "合計", "總計", "基金名稱"]): continue
                if any(x in raw_type_code for x in ["投信", "總計"]): continue
                if not (6 <= len(clean_id) <= 10): continue
                
                # 提取資產值
                nav_str = c_texts[col_indices["單位淨值"]] if col_indices["單位淨值"] != -1 else "0"
                nav_str = nav_str.replace(",", "")
                nav_float = float(nav_str) if nav_str else 0.0
                
                row_data = {
                    "年月": str(year_month),
                    "類型代號": raw_type_code,
                    "基金統編": clean_id,
                    "基金名稱": raw_name,
                    "類型": fix_mojibake(c_texts[col_indices["類型"]]) if col_indices["類型"] != -1 else "",
                    "計價幣別": fix_mojibake(c_texts[col_indices["計價幣別"]]) if col_indices["計價幣別"] != -1 else "",
                    "單位淨值(台幣)": nav_float
                }
                
                parsed.append(row_data)
            except:
                continue
                
        if parsed: return parsed
    return None

    return None

def generate_months(start_year=2007, start_month=7):
    months = []
    now = datetime.now()
    year, month = start_year, start_month
    while (year, month) <= (now.year, now.month):
        months.append(f"{year:04d}{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months

def main():
    # 產生從 2007/07 到現在的年月清單
    start_date = datetime(2007, 7, 1)
    end_date = datetime.now()
    months = []
    curr = start_date
    while curr <= end_date:
        if curr.month in (3, 6, 9, 12):
            months.append(curr.strftime("%Y%m"))
        if curr.month == 12:
            curr = datetime(curr.year + 1, 1, 1)
        else:
            curr = datetime(curr.year, curr.month + 1, 1)

    print(f"啟動全量爬取流程：共計 {len(months)} 個月資料...")

    output_file = "sitca_open_equity_domestic.csv"
    all_rows = []
    session = requests.Session()
    session.verify = False  # 全域關閉 SSL 驗證

    # 取得初始頁面和隱藏欄位
    print("取得初始 ViewState...")
    resp = session.get(BASE_URL, headers=HEADERS, timeout=30)
    r_init = session.get(BASE_URL, headers=HEADERS, timeout=30)
    hidden = extract_hidden_fields(BeautifulSoup(r_init.text, "lxml"))
    
    # 這裡可以根據隱藏欄位初步確認是否成功取得
    if not hidden.get("__VIEWSTATE"):
        print("初始化失敗，請檢查網路。")
        return

    # 支援續傳
    existing_months = set()
    if os.path.exists(output_file):
        try:
            df_existing = pd.read_csv(output_file, dtype=str)
            if '年月' in df_existing.columns:
                existing_months = set(df_existing['年月'].unique())
                print(f"找到既有資料表，已包含 {len(existing_months)} 個月的資料，將進行續傳。")
        except Exception as e:
            print(f"解析既有資料表失敗: {e}")

    fail_count = 0
    last_save_year = None
    try:
        total_months = len(months)
        for i, ym in enumerate(months, 1):
            current_year = ym[:4]
            if ym in existing_months:
                print(f"[{i:3d}/{total_months}] {ym[:4]}年{ym[4:]}月 ... 已存在，跳過")
                continue

            # 如果換年了，就先存檔一次
            if last_save_year and current_year != last_save_year and all_rows:
                df_temp = pd.DataFrame(all_rows)
                mode = 'a' if os.path.exists(output_file) else 'w'
                header = False if os.path.exists(output_file) else True
                df_temp.to_csv(output_file, mode=mode, header=header, index=False, encoding="utf-8-sig")
                print(f"\n[系統] 已自動儲存至 {last_save_year} 年的新增資料 (累計 {len(all_rows)} 筆)")
                all_rows = [] # 儲存過就清空記憶體緩衝
            
            last_save_year = current_year

            print(f"[{i:3d}/{total_months}] {ym[:4]}年{ym[4:]}月 ... ", end="", flush=True)
            
            try:
                # --- Step 1: 同步 YM (PostBack) ---
                payload = hidden.copy()
                payload.update({"__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlQ_YM", "ctl00$ContentPlaceHolder1$ddlQ_YM": ym})
                for k in ["ctl00$ContentPlaceHolder1$BtnQuery", "ctl00$ContentPlaceHolder1$BtnExport"]:
                    payload.pop(k, None)
                
                r = session.post(BASE_URL, headers=HEADERS, data=payload, timeout=30)
                hidden = extract_hidden_fields(BeautifulSoup(r.text, "lxml"))

                # --- Step 2: 點擊 [類型] Radio 並設定 AA1 ---
                payload = hidden.copy()
                payload.update({
                    "__EVENTTARGET": "ctl00$ContentPlaceHolder1$rbType",
                    "ctl00$ContentPlaceHolder1$ddlQ_YM": ym,
                    "ctl00$ContentPlaceHolder1$rdo1": "rbType", 
                    "ctl00$ContentPlaceHolder1$ddlQ_CLASS": "AA1",
                })
                for k in ["ctl00$ContentPlaceHolder1$BtnQuery", "ctl00$ContentPlaceHolder1$BtnExport"]:
                    payload.pop(k, None)
                
                r = session.post(BASE_URL, headers=HEADERS, data=payload, timeout=30)
                hidden = extract_hidden_fields(BeautifulSoup(r.text, "lxml"))

                # --- Step 3: 正式查詢 ---
                payload = hidden.copy()
                payload.update({
                    "__EVENTTARGET": "",
                    "ctl00$ContentPlaceHolder1$ddlQ_YM": ym,
                    "ctl00$ContentPlaceHolder1$rdo1": "rbType",
                    "ctl00$ContentPlaceHolder1$ddlQ_CLASS": "AA1",
                    "ctl00$ContentPlaceHolder1$BtnQuery": "查詢",
                })
                resp = session.post(BASE_URL, headers=HEADERS, data=payload, timeout=40)
                # 不強制設定 resp.encoding，交給 requests 自動根據 headers 解析為 utf-8
                # 更新 hidden 給下一輪使用，並解析
                soup = BeautifulSoup(resp.text, "lxml")
                new_h = extract_hidden_fields(soup)
                if new_h.get("__VIEWSTATE"): hidden = new_h
                
                rows = extract_data_table(soup, ym)
                if rows:
                    all_rows.extend(rows)
                    print(f"成功 ({len(rows)} 筆)")
                    fail_count = 0 
                else:
                    if "無符合條件之資料" in resp.text:
                        print("此月無資料")
                    else:
                        print("找不到資料表 (可能結構變動)")
                
                # 禮貌延遲
                time.sleep(1)
                    
            except Exception as e:
                fail_count += 1
                print(f"失敗 ({e})")
                if fail_count >= 3:
                    print("連續失敗多次，暫停 20 秒並重置連線...")
                    time.sleep(20)
                    fail_count = 0
                    # 強制重置 Session
                    try:
                        session = requests.Session()
                        session.mount("https://", adapter)
                        r_init = session.get(BASE_URL, headers=HEADERS, timeout=20)
                        hidden = extract_hidden_fields(BeautifulSoup(r_init.text, "lxml"))
                    except: pass

            time.sleep(1.2)
            
    except KeyboardInterrupt:
        print("\n使用者手動中斷，將存儲目前已抓取的資料...")
    
    finally:
        if all_rows:
            df = pd.DataFrame(all_rows, columns=["年月", "類型代號", "基金統編", "基金名稱", "類型", "計價幣別", "單位淨值(台幣)"])
            mode = 'a' if os.path.exists(output_file) else 'w'
            header = False if os.path.exists(output_file) else True
            df.to_csv(output_file, mode=mode, header=header, index=False, encoding="utf-8-sig")
            
            # 讀取最終完整檔案並印出
            df_final = pd.read_csv(output_file, dtype=str)
            print(f"\n✓ 任務結束！最終資料表總共 {len(df_final)} 筆，已存檔至 {output_file}")
            if len(df_final) > 0:
                print(df_final.tail(5).to_string())
        else:
            print("\n本次執行沒有抓取到新資料。")

if __name__ == "__main__":
    main()
