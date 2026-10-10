// Payment Anomaly Auditor v0.1 — offline, dependency-free, USD only.
// All findings are potential duplicates, NOT verified recoveries.
// Copyright (c) 2026. All rights reserved pending licensing decision.
import { readFile, writeFile } from 'node:fs/promises';

export function parseCSV(input) {
  const s = input.replace(/^\uFEFF/, '');
  const records = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quoted) {
      if (c === '"' && s[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') {
      if (cell !== '') throw new Error('Invalid CSV quote position.');
      quoted = true;
    } else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\r' || c === '\n') {
      row.push(cell); if (row.some(v => v.trim() !== '')) records.push(row);
      row = []; cell = ''; if (c === '\r' && s[i + 1] === '\n') i++;
    } else cell += c;
  }
  if (quoted) throw new Error('Unclosed quoted CSV cell.');
  if (cell.length || row.length) {
    row.push(cell); if (row.some(v => v.trim() !== '')) records.push(row);
  }
  return records;
}

function norm(v) {
  return String(v ?? '').toLowerCase().normalize('NFKC').replace(/[^a-z0-9]/g,'');
}
function parseMoney(raw) {
  const original = String(raw ?? '').trim();
  const negative = /^\(.*\)$/.test(original);
  const body = original.replace(/[,$()\s]/g,'').replace(/^\$/,'');
  if (!/^[-+]?\d+(?:\.\d{1,2})?$/.test(body)) throw new Error('Invalid USD amount: ' + original);
  const [whole, fraction=''] = body.replace(/^\+/,'').replace(/^-/, '').split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2,'0'));
  if (!Number.isSafeInteger(cents)) throw new Error('USD amount exceeds safe range.');
  return (negative || body.startsWith('-')) ? -cents : cents;
}
function parseDay(raw) {
  const v = String(raw ?? '').trim();
  let y,m,d;
  if (/^\d{4}-\d{1,2}-\d{1,2}$/.test(v)) [y,m,d] = v.split('-').map(Number);
  else if (/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(v)) [m,d,y] = v.split('/').map(Number);
  else throw new Error('Invalid date (use YYYY-MM-DD or MM/DD/YYYY): ' + v);
  const ms = Date.UTC(y,m-1,d);
  const check = new Date(ms);
  if (check.getUTCFullYear()!==y || check.getUTCMonth()!==m-1 || check.getUTCDate()!==d) throw new Error('Invalid calendar date: ' + v);
  return check.toISOString().slice(0,10);
}

const aliases = {
  vendor: ['vendor','supplier','payee','merchant','vendor_name'],
  invoice: ['invoice_id','invoice','invoice_number','invoice_no','reference','reference_number'],
  date: ['date','payment_date','paid_date','transaction_date','posted_at'],
  amount: ['amount','amount_usd','payment_amount','total','gross_amount'],
  status: ['status','payment_status','state']
};
export function analyzeCSV(input) {
  const table = parseCSV(input);
  if (table.length < 2) throw new Error('CSV must contain headers and at least one data row.');
  const header = table.shift().map(norm);
  const idx = {};
  for (const [field, options] of Object.entries(aliases)) {
    idx[field] = header.findIndex(h => options.some(a => norm(a) === h));
  }
  for (const required of ['vendor','date','amount']) {
    if (idx[required]<0) throw new Error('Missing required column: ' + required);
  }
  const entries=[], omitted=[];
  table.forEach((cols,rowIndex) => {
    if (cols.every(x=>String(x).trim()==='')) return;
    const get=k=>idx[k]<0 ? '' : String(cols[idx[k]]??'').trim();
    const sourceRow=rowIndex+2;
    try {
      const vendor=get('vendor'), date=parseDay(get('date')), cents=parseMoney(get('amount'));
      const status=get('status').toLowerCase();
      if (!vendor) throw new Error('Empty vendor');
      if (cents <= 0 || /^(void|refunded|unpaid|pending|cancelled|canceled|failed|reversed|open)$/.test(status)) {
        omitted.push({row:sourceRow,reason:'Not a positive completed payment'}); return;
      }
      entries.push({row:sourceRow,vendor,normalizedVendor:norm(vendor),invoice:get('invoice'),normalizedInvoice:norm(get('invoice')),date,amountCents:cents});
    } catch (err) {
      omitted.push({row:sourceRow,reason:String(err.message)});
    }
  });
  const matches=[], usedPairs=new Set();
  const add=(a,b,rule,confidence) => {
    const key=[Math.min(a.row,b.row),Math.max(a.row,b.row)].join('-');
    if (usedPairs.has(key)) return;
    usedPairs.add(key);
    matches.push({rule,confidence,rows:[a.row,b.row],vendor:a.vendor,amountUSD:(a.amountCents/100).toFixed(2),dates:[a.date,b.date],invoiceIds:[a.invoice,b.invoice],reviewRequired:true});
  };
  const byInvoice=new Map(), bySameDay=new Map();
  for (const e of entries) {
    if (e.normalizedInvoice) {
      const k=e.normalizedVendor+'|'+e.normalizedInvoice+'|'+e.amountCents;
      for (const prior of byInvoice.get(k)??[]) add(prior,e,'same_vendor_invoice_and_amount','high');
      byInvoice.set(k,[...(byInvoice.get(k)??[]),e]);
    }
    const k=e.normalizedVendor+'|'+e.date+'|'+e.amountCents;
    for (const prior of bySameDay.get(k)??[]) {
      if (!e.normalizedInvoice || !prior.normalizedInvoice || e.normalizedInvoice!==prior.normalizedInvoice)
        add(prior,e,'same_vendor_date_and_amount','review');
    }
    bySameDay.set(k,[...(bySameDay.get(k)??[]),e]);
  }
  // No amount aggregation: overlapping pairs can inflate claimed recoveries.
  return {
    version:'0.1.0',currency:'USD',recordsRead:table.length,
    validPositivePayments:entries.length,excludedRows:omitted,
    potentialDuplicatePairs:matches.length,findings:matches,
    estimatedRecoverableUSD:null,
    disclaimer:'Matches are leads for human review, not proof of duplicate payment or recoverable money. Do not automatically charge or contact payees based solely on this report.'
  };
}

const isCLI=process.argv[1] && new URL('file:'+process.argv[1]).pathname === new URL(import.meta.url).pathname;
if (isCLI) {
  const [input,option,output]=process.argv.slice(2);
  if (!input || input==='--help' || (option && option!=='--output') || (option==='--output'&&!output)) {
    console.log('Usage: node audit.mjs payments.csv [--output findings.json]');
    process.exitCode = input === '--help' ? 0 : 2;
  } else {
    try {
      const result=analyzeCSV(await readFile(input,'utf8'));
      const json=JSON.stringify(result,null,2)+'\n';
      if (output) { await writeFile(output,json); console.log('Report saved: '+output); }
      else process.stdout.write(json);
    } catch(e) { console.error('Audit failed: '+e.message); process.exitCode=1; }
  }
}
