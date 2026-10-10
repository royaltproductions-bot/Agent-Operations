import test from 'node:test';
import assert from 'node:assert/strict';
import {parseCSV,analyzeCSV} from './audit.mjs';

test('Quoted CSV with commas, escaped quotes, CRLF',()=>{
  assert.deepEqual(parseCSV('a,b\r\n"X, Y","Z""Q"\r\n'),[['a','b'],['X, Y','Z"Q']]);
});
test('Repeated invoice becomes a high-confidence review lead, not claimed cash',()=>{
  const csv='vendor,invoice,date,amount,status\nAcme LLC,INV-21,2026-03-10,$420.00,paid\nAcme LLC,INV21,2026-03-14,420.00,paid\n';
  const r=analyzeCSV(csv);
  assert.equal(r.potentialDuplicatePairs,1);
  assert.equal(r.findings[0].rule,'same_vendor_invoice_and_amount');
  assert.equal(r.findings[0].confidence,'high');
  assert.equal(r.estimatedRecoverableUSD,null);
});
test('Different legitimate amounts do not match',()=>{
  const r=analyzeCSV('vendor,invoice,date,amount\nAcme,A1,2026-01-01,45\nAcme,A2,2026-01-02,46\n');
  assert.equal(r.potentialDuplicatePairs,0);
});
test('Same vendor, day and amount with different invoice flags for review',()=>{
  const r=analyzeCSV('vendor,invoice,date,amount\nAcme,A1,2026-01-01,50\nAcme,A2,2026-01-01,50\n');
  assert.equal(r.findings[0].confidence,'review');
});
test('Skips reversed, pending and negative entries',()=>{
  const r=analyzeCSV('vendor,invoice,date,amount,status\nAcme,A1,2026-01-01,50,pending\nAcme,A1,2026-01-01,-50,paid\nAcme,A1,2026-01-01,50,paid\n');
  assert.equal(r.validPositivePayments,1);
  assert.equal(r.excludedRows.length,2);
});
test('Bad source row is separated, not silently treated as valid',()=>{
  const r=analyzeCSV('vendor,invoice,date,amount\nAcme,A1,2026-02-30,50\nAcme,A2,2026-02-27,50\n');
  assert.equal(r.excludedRows.length,1);
  assert.equal(r.validPositivePayments,1);
});
test('Missing required columns fails clearly',()=>{
  assert.throws(()=>analyzeCSV('a,b\n1,2\n'),/Missing required column/);
});
