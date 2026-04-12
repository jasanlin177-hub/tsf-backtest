"""
TSF-Top5 回測引擎
資料來源：sitca_open_equity_domestic.csv（SITCA 每季末 NAV 快照）
評分模型：532 = R1y×50% + R3y×30% + R5y×20%
調整機制：每半年審核（6月底/12月底），緩衝門檻 = 0.6×σ(Top20)
基準比較：TAIEX 報酬指數、0050 TRI（data/taiex.json, data/etf-0050.json）
"""

import pandas as pd
import numpy as np
import json
import sys
import io
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

NAV_COL = '單位淨值(台幣)'
ROBECO_ID = 42532205  # 路博邁台灣5G 統編，只保留T累積級別


# ─────────────────────────────────────────────
# 0. 基準指數載入（TAIEX / 0050 日頻 → 期末值）
# ─────────────────────────────────────────────

def load_benchmark_nav(path):
    """載入 data/taiex.json 或 data/etf-0050.json，回傳 {date_str: nav} dict"""
    raw = json.load(open(path, encoding='utf-8'))
    return {v['date']: v['nav'] for v in raw.values()}


def nav_at_period_end(nav_dict, yyyymm):
    """
    找 YYYYMM 月份最後一個有資料的交易日 NAV。
    先找當月最大日期，若無則往前回溯最多 10 天。
    """
    year, month = int(yyyymm[:4]), int(yyyymm[4:])
    # 月底日期（28~31）
    if month == 12:
        last_day = datetime(year, 12, 31)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)
    for delta in range(15):
        d = (last_day - timedelta(days=delta)).strftime('%Y-%m-%d')
        if d in nav_dict:
            return nav_dict[d]
    return None


# ─────────────────────────────────────────────
# 1. 資料載入與清洗
# ─────────────────────────────────────────────

def load_sitca(path='sitca_open_equity_domestic.csv'):
    df = pd.read_csv(path, encoding='utf-8-sig')

    # 排除零值 NAV（機構 I 類別）
    df = df[df[NAV_COL] > 0].copy()

    # 排除外幣計價
    df = df[df['計價幣別'] == 'TWD'].copy()

    # 路博邁5G：只保留 T累積級別
    rob_mask = df['基金統編'] == ROBECO_ID
    rob_keep = df[rob_mask & df['基金名稱'].str.contains('T累積', na=False)]
    df = pd.concat([df[~rob_mask], rob_keep], ignore_index=True)

    # 同一 (年月, 統編) 有多筆時：優先保留非配息、非月配的類別
    df['_prio'] = (
        df['基金名稱'].str.contains('月配|配息', na=False).astype(int) * 2 +
        df['基金名稱'].str.contains('I類型|I級別', na=False).astype(int)
    )
    df = df.sort_values(['年月', '基金統編', '_prio'])
    df = df.drop_duplicates(subset=['年月', '基金統編'], keep='first')
    df = df.drop(columns=['_prio'])

    return df


# ─────────────────────────────────────────────
# 2. 532 評分計算
# ─────────────────────────────────────────────

def compute_scores(df, review_ym, all_periods):
    """
    在 review_ym 計算全市場 532 評分。
    排除：成立未滿 5 年（無法取得 20 季前 NAV）。
    回傳 DataFrame，index = 基金統編，含 score / R1y / R3y / R5y / 基金名稱。
    """
    if review_ym not in all_periods:
        return None

    idx = all_periods.index(review_ym)

    def period_back(n):
        return all_periods[idx - n] if idx - n >= 0 else None

    p_1y = period_back(4)    # 1年 = 4季
    p_3y = period_back(12)   # 3年 = 12季
    p_5y = period_back(20)   # 5年 = 20季

    if not all([p_1y, p_3y, p_5y]):
        return None

    def nav_at(period):
        return df[df['年月'] == period].set_index('基金統編')[NAV_COL]

    nav_now = nav_at(review_ym)
    nav_1y  = nav_at(p_1y)
    nav_3y  = nav_at(p_3y)
    nav_5y  = nav_at(p_5y)

    eligible = (
        nav_now.index
        .intersection(nav_1y.index)
        .intersection(nav_3y.index)
        .intersection(nav_5y.index)
    )

    scores = pd.DataFrame({
        'nav_now': nav_now[eligible],
        'nav_1y':  nav_1y[eligible],
        'nav_3y':  nav_3y[eligible],
        'nav_5y':  nav_5y[eligible],
    })

    scores['R1y'] = (scores['nav_now'] / scores['nav_1y'] - 1) * 100
    scores['R3y'] = (scores['nav_now'] / scores['nav_3y'] - 1) * 100
    scores['R5y'] = (scores['nav_now'] / scores['nav_5y'] - 1) * 100
    scores['score'] = scores['R1y'] * 0.5 + scores['R3y'] * 0.3 + scores['R5y'] * 0.2

    names = df[df['年月'] == review_ym].set_index('基金統編')['基金名稱']
    scores['基金名稱'] = names.reindex(eligible)

    return scores.sort_values('score', ascending=False)


# ─────────────────────────────────────────────
# 3. 緩衝換股機制
# ─────────────────────────────────────────────

def select_top5(scores, incumbent_ids=None):
    """
    首次選股：直接取前 5。
    後續審核：緩衝門檻 = 0.6×σ(Top20)；
    換股條件：挑戰者分數 > 衛冕者分數 + 門檻 才執行換股。
    迭代順序：從分數最低的衛冕者開始逐一挑戰（最弱的最先被汰換）。
    回傳 (selected_ids list, threshold, changes list)
    """
    top20 = scores.head(20)
    threshold = 0.6 * top20['score'].std()

    if incumbent_ids is None:
        selected = scores.head(5).index.tolist()
        return selected, threshold, [{'action': '初始建倉', 'in': fid,
                                      'in_name': scores.loc[fid, '基金名稱'],
                                      'in_score': round(float(scores.loc[fid, 'score']), 2)}
                                     for fid in selected]

    # 現有持倉：若已不在母體（清算/改名），直接標記需補位
    valid_incumbents   = [fid for fid in incumbent_ids if fid in scores.index]
    missing_incumbents = [fid for fid in incumbent_ids if fid not in scores.index]

    # 挑戰者池（非現任成員，依評分排列）
    challenger_pool = [fid for fid in scores.index if fid not in incumbent_ids]

    # 現有持倉依分數由低到高排列（最弱先被挑戰）
    valid_sorted = sorted(valid_incumbents, key=lambda fid: scores.loc[fid, 'score'])

    portfolio = set(valid_incumbents)
    changes = []

    # 補位（基金消滅/改名）
    used_challengers = set()
    for missing_id in missing_incumbents:
        for chall_id in challenger_pool:
            if chall_id not in used_challengers and chall_id not in portfolio:
                portfolio.add(chall_id)
                used_challengers.add(chall_id)
                changes.append({'action': '強制補位（基金消滅）',
                                 'out': missing_id, 'out_name': str(missing_id),
                                 'in': chall_id,
                                 'in_name': scores.loc[chall_id, '基金名稱'],
                                 'in_score': round(float(scores.loc[chall_id, 'score']), 2)})
                break

    # 緩衝換股（由弱到強逐一檢查）
    for inc_id in valid_sorted:
        if inc_id not in portfolio:
            continue  # 已被其他輪次汰換
        inc_score = scores.loc[inc_id, 'score']
        for chall_id in challenger_pool:
            if chall_id in used_challengers or chall_id in portfolio:
                continue
            chall_score = scores.loc[chall_id, 'score']
            if chall_score > inc_score + threshold:
                portfolio.remove(inc_id)
                portfolio.add(chall_id)
                used_challengers.add(chall_id)
                changes.append({'action': '換股',
                                 'out': inc_id,
                                 'out_name': scores.loc[inc_id, '基金名稱'],
                                 'out_score': round(float(inc_score), 2),
                                 'in': chall_id,
                                 'in_name': scores.loc[chall_id, '基金名稱'],
                                 'in_score': round(float(chall_score), 2),
                                 'gap': round(float(chall_score - inc_score), 2),
                                 'threshold': round(float(threshold), 2)})
                break  # 每個衛冕者最多被換一次

    if not changes:
        changes.append({'action': '全員衛冕', 'threshold': round(float(threshold), 2)})

    selected = list(portfolio)
    return selected, threshold, changes


# ─────────────────────────────────────────────
# 4. 指數建構
# ─────────────────────────────────────────────

def build_tsf_index(df, start_period=201306,
                    taiex_path='data/taiex.json',
                    etf0050_path='data/etf-0050.json'):
    all_periods = sorted(df['年月'].unique())

    # 只在 6月底(06) 和 12月底(12) 審核
    review_periods = [
        p for p in all_periods
        if str(p)[4:] in ('06', '12') and p >= start_period
    ]

    # 載入基準指數（日頻）
    taiex_nav  = load_benchmark_nav(taiex_path)
    etf0050_nav = load_benchmark_nav(etf0050_path)

    index_value   = 100.0
    taiex_base    = None
    etf0050_base  = None
    taiex_idx     = 100.0
    etf0050_idx   = 100.0
    incumbent_ids = None
    results       = []

    for i, period in enumerate(review_periods[:-1]):
        next_period = review_periods[i + 1]

        scores = compute_scores(df, period, all_periods)
        if scores is None or len(scores) < 5:
            print(f'[跳過] {period}：可評分基金不足（{len(scores) if scores is not None else 0}）')
            continue

        selected_ids, threshold, changes = select_top5(scores, incumbent_ids)

        # 計算持有期報酬（等權重 20%）
        nav_start = df[df['年月'] == period].set_index('基金統編')[NAV_COL]
        nav_end   = df[df['年月'] == next_period].set_index('基金統編')[NAV_COL]

        period_return = 0.0
        fund_details  = []

        for fid in selected_ids:
            if fid not in nav_start.index or fid not in nav_end.index:
                print(f'  [警告] 基金 {fid} 在 {period} 或 {next_period} 無 NAV，以 0% 報酬替代')
                fund_details.append({'id': int(fid), 'nav_start': None, 'nav_end': None, 'return_pct': 0})
                continue

            r = nav_end[fid] / nav_start[fid] - 1
            period_return += r * 0.2

            fname = scores.loc[fid, '基金名稱'] if fid in scores.index else str(fid)
            sc    = scores.loc[fid, 'score'] if fid in scores.index else None
            r1y   = scores.loc[fid, 'R1y']   if fid in scores.index else None
            r3y   = scores.loc[fid, 'R3y']   if fid in scores.index else None
            r5y   = scores.loc[fid, 'R5y']   if fid in scores.index else None
            is_new = (incumbent_ids is None or fid not in incumbent_ids)

            fund_details.append({
                'id':         int(fid),
                'name':       fname,
                'score':      round(float(sc), 2)  if sc  is not None else None,
                'R1y':        round(float(r1y), 2) if r1y is not None else None,
                'R3y':        round(float(r3y), 2) if r3y is not None else None,
                'R5y':        round(float(r5y), 2) if r5y is not None else None,
                'nav_start':  float(nav_start[fid]),
                'nav_end':    float(nav_end[fid]),
                'return_pct': round(float(r * 100), 2),
                'is_new':     is_new,
            })

        index_end = index_value * (1 + period_return)

        # 基準指數同期報酬
        yyyymm_start = str(period)
        yyyymm_end   = str(next_period)

        taiex_s  = nav_at_period_end(taiex_nav,   yyyymm_start)
        taiex_e  = nav_at_period_end(taiex_nav,   yyyymm_end)
        etf050_s = nav_at_period_end(etf0050_nav, yyyymm_start)
        etf050_e = nav_at_period_end(etf0050_nav, yyyymm_end)

        taiex_period_ret  = (taiex_e  / taiex_s  - 1) if (taiex_s  and taiex_e)  else None
        etf050_period_ret = (etf050_e / etf050_s - 1) if (etf050_s and etf050_e) else None

        # 初始化基準指數基準值
        if taiex_base is None and taiex_s:
            taiex_base   = taiex_s
            etf0050_base = etf050_s

        if taiex_base:
            taiex_idx_start  = (taiex_s  / taiex_base)   * 100 if taiex_s   else None
            taiex_idx_end    = (taiex_e  / taiex_base)   * 100 if taiex_e   else None
            etf050_idx_start = (etf050_s / etf0050_base) * 100 if etf050_s  else None
            etf050_idx_end   = (etf050_e / etf0050_base) * 100 if etf050_e  else None
        else:
            taiex_idx_start = taiex_idx_end = etf050_idx_start = etf050_idx_end = None

        results.append({
            'period_start':         int(period),
            'period_end':           int(next_period),
            'index_start':          round(index_value, 2),
            'index_end':            round(index_end, 2),
            'period_return_pct':    round(period_return * 100, 2),
            'threshold':            round(threshold, 2),
            'eligible_funds':       len(scores),
            'changes':              changes,
            'funds':                fund_details,
            'benchmark': {
                'taiex_period_pct':   round(taiex_period_ret  * 100, 2) if taiex_period_ret  is not None else None,
                'etf0050_period_pct': round(etf050_period_ret * 100, 2) if etf050_period_ret is not None else None,
                'taiex_index_start':  round(taiex_idx_start,  2) if taiex_idx_start  else None,
                'taiex_index_end':    round(taiex_idx_end,    2) if taiex_idx_end    else None,
                'etf0050_index_start':round(etf050_idx_start, 2) if etf050_idx_start else None,
                'etf0050_index_end':  round(etf050_idx_end,   2) if etf050_idx_end   else None,
            },
        })

        index_value   = index_end
        incumbent_ids = selected_ids

    return results


# ─────────────────────────────────────────────
# 5. 主程式
# ─────────────────────────────────────────────

if __name__ == '__main__':
    print('=== TSF-Top5 回測引擎 ===\n')
    print('載入並清洗 SITCA 資料...')
    df = load_sitca()
    print(f'清洗後：{len(df)} 筆，{df["年月"].nunique()} 個期別\n')

    print('建構 TSF 指數（起點 2013-06，基點 = 100）...\n')
    results = build_tsf_index(df, start_period=201306)

    print(f'\n共完成 {len(results)} 個持有期\n')
    header = f'{"期間":<17} {"TSF":>8} {"TAIEX":>8} {"0050":>8}  {"換股":>4}  成分基金'
    print(header)
    print('─' * 100)

    for r in results:
        bm = r['benchmark']
        taiex_str = f"{bm['taiex_period_pct']:>+7.2f}%" if bm['taiex_period_pct'] is not None else '    N/A'
        e050_str  = f"{bm['etf0050_period_pct']:>+7.2f}%" if bm['etf0050_period_pct'] is not None else '    N/A'
        n_changes = sum(1 for c in r['changes'] if c['action'] == '換股')
        funds_str = ' / '.join(f['name'][:10] for f in r['funds'])
        print(f"{r['period_start']}→{r['period_end']}  "
              f"{r['period_return_pct']:>+7.2f}%  {taiex_str}  {e050_str}  "
              f"{n_changes:>2}股  {funds_str}")

    if results:
        final   = results[-1]['index_end']
        taiex_f = results[-1]['benchmark']['taiex_index_end']
        e050_f  = results[-1]['benchmark']['etf0050_index_end']
        print(f'\n{"─"*60}')
        print(f'TSF-Top5  累積指數：{final:.2f}  ({(final/100-1)*100:+.2f}%)')
        if taiex_f:
            print(f'TAIEX     累積指數：{taiex_f:.2f}  ({(taiex_f/100-1)*100:+.2f}%)')
        if e050_f:
            print(f'0050 TRI  累積指數：{e050_f:.2f}  ({(e050_f/100-1)*100:+.2f}%)')

        # 年化報酬（以第一期起點到最後期末）
        n_periods = len(results)
        years = n_periods * 0.5
        cagr = (final / 100) ** (1 / years) - 1
        print(f'\n持有期數：{n_periods}（{years:.1f} 年）')
        print(f'TSF-Top5 CAGR：{cagr*100:.2f}%')

    out_path = 'data/tsf_index.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n已儲存 {out_path}')
