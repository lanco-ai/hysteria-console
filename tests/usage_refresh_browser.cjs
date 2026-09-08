const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox']});
 try {
  const p=await browser.newPage();let count=0;
  await p.route('**/admin/analytics.json*',r=>r.fulfill({json:{stats:{current_hour_bytes:1073741824,today_bytes:2147483648,last_7d_bytes:3221225472,cycle_bytes:4294967296,online:7,cycle_day:2,cycle_total_days:30}}}));
  await p.route('**/admin/usage-history',r=>r.fulfill({contentType:'text/html',body:`<p data-test-history>快照 ${++count}</p>`}));
  await p.goto('http://127.0.0.1:18764/admin/usage');
  await p.waitForFunction(()=>document.querySelector('[data-role=usage-online]').textContent==='7');
  assert.equal(await p.locator('[data-stat=current_hour] .metric-v').textContent(),'1.00 GB');
  assert.equal(await p.locator('[data-stat=today] .metric-v').textContent(),'2.00 GB');
  assert.equal(await p.locator('[data-stat=last_7d] .metric-v').textContent(),'3.00 GB');
  assert.equal(await p.locator('[data-stat=cycle] .metric-v').textContent(),'4.00 GB');
  await p.locator('#usage-history summary').click();
  await p.waitForFunction(()=>document.querySelector('[data-test-history]')?.textContent==='快照 1');
  await p.locator('#usage-refresh-now').click();
  await p.waitForFunction(()=>document.querySelector('[data-test-history]')?.textContent==='快照 2');
  await p.locator('#usage-history summary').click();
  await p.locator('#usage-refresh-now').click();
  await p.waitForFunction(()=>!document.querySelector('#usage-refresh-now').disabled);
  assert.equal(count,2,'Collapsed history stays lazy');
  await p.locator('#usage-history summary').click();
  await p.waitForFunction(()=>document.querySelector('[data-test-history]')?.textContent==='快照 3');
  console.log('PASS live metrics, explicit history refresh, collapsed invalidation');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
