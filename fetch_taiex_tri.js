/**
 * fetch_taiex_tri.js
 * 從 TWSE MFI94U 端點抓取「發行量加權股價報酬指數」日頻歷史資料
 * 覆蓋範圍：2003-01-01 至今
 * 輸出：data/taiex.json（覆蓋現有檔案）
 */

const https = require('https');
const fs = require('fs');

function fetchMonth(yyyymm01) {
  return new Promise((res, rej) => {
    const path = `/indicesReport/MFI94U?response=json&date=${yyyymm01}`;
    const opt = {
      hostname: 'www.twse.com.tw',
      path,
      headers: { 'User-Agent': 'Mozilla/5.0' },
      rejectUnauthorized: false
    };
    https.get(opt, r => {
      const chunks = [];
      r.on('data', c => chunks.push(c));
      r.on('end', () => res(Buffer.concat(chunks).toString()));
    }).on('error', rej);
  });
}

function rocToGregorian(rocDateStr) {
  // ' 95/01/02' or '115/01/02' → '2006-01-02'
  const s = rocDateStr.trim();
  const parts = s.split('/');
  const year = parseInt(parts[0]) + 1911;
  const month = parts[1].padStart(2, '0');
  const day = parts[2].padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  const START_YEAR = 2003;
  const START_MONTH = 1;
  const now = new Date();
  const END_YEAR = now.getFullYear();
  const END_MONTH = now.getMonth() + 1;

  const records = {}; // date → nav
  let totalMonths = 0;
  let totalRecords = 0;
  let failedMonths = [];

  // 產生所有月份
  const months = [];
  for (let y = START_YEAR; y <= END_YEAR; y++) {
    const mEnd = (y === END_YEAR) ? END_MONTH : 12;
    for (let m = START_MONTH; m <= mEnd; m++) {
      months.push(`${y}${String(m).padStart(2, '0')}01`);
    }
  }

  console.log(`準備抓取 ${months.length} 個月份（${months[0]} ~ ${months[months.length - 1]}）`);

  for (let i = 0; i < months.length; i++) {
    const ym = months[i];
    process.stdout.write(`[${i + 1}/${months.length}] ${ym}... `);

    let retries = 3;
    let ok = false;
    while (retries > 0 && !ok) {
      try {
        const body = await fetchMonth(ym);
        const j = JSON.parse(body);
        if (j.stat === 'OK' && j.data && j.data.length > 0) {
          for (const row of j.data) {
            const date = rocToGregorian(row[0]);
            const val = parseFloat(row[1].replace(/,/g, ''));
            if (!isNaN(val)) {
              records[date] = { date, nav: val, source: 'twse-mfi94u' };
              totalRecords++;
            }
          }
          process.stdout.write(`${j.data.length} 筆\n`);
          ok = true;
        } else {
          process.stdout.write(`無資料 (${j.stat})\n`);
          ok = true; // 跳過，不重試
        }
      } catch (e) {
        retries--;
        if (retries > 0) {
          process.stdout.write(`錯誤，重試... `);
          await sleep(1000);
        } else {
          process.stdout.write(`失敗: ${e.message}\n`);
          failedMonths.push(ym);
        }
      }
    }

    totalMonths++;
    await sleep(350); // 避免過快請求
  }

  // 整理成陣列並按日期排序
  const sorted = Object.values(records).sort((a, b) => a.date.localeCompare(b.date));

  // 輸出格式：與現有 etf-0050.json 相同（數字索引）
  const output = {};
  sorted.forEach((entry, idx) => {
    output[String(idx)] = entry;
  });

  const outPath = 'data/taiex.json';
  fs.writeFileSync(outPath, JSON.stringify(output, null, 2), 'utf8');

  console.log(`\n✓ 完成！`);
  console.log(`  總筆數：${sorted.length}`);
  console.log(`  日期範圍：${sorted[0].date} → ${sorted[sorted.length - 1].date}`);
  console.log(`  輸出：${outPath}`);
  if (failedMonths.length > 0) {
    console.log(`  失敗月份：${failedMonths.join(', ')}`);
  }
}

main().catch(console.error);
