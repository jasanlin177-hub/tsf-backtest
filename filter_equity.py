# filter_equity.py
import csv
BASE = r'C:\Users\機動小隊\Projects\tsf-backtest'
FILE_IN  = BASE + r'\sitca_fund_final.csv'
FILE_OUT = BASE + r'\sitca_fund_equity.csv'

kept = 0
with open(FILE_IN, 'r', encoding='utf-8-sig') as fin, \
     open(FILE_OUT, 'w', newline='', encoding='utf-8-sig') as fout:
    reader = csv.reader(fin)
    writer = csv.writer(fout)
    writer.writerow(next(reader))
    for row in reader:
        t = row[1].strip()
        if t in ('2', 'AA1'):
            writer.writerow(row)
            kept += 1

print(f'國內股票型基金明細列: {kept}')
print(f'輸出: {FILE_OUT}')