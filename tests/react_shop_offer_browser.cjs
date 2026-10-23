const assert = require('node:assert/strict');
const { test } = require('node:test');
const { chromium } = require('playwright');
const { expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const catalog = () => ({ currency: 'CNY', telegram: '', wechat: 'my_shop', status: 'ready', updated_at: 1000,
  stats: { views: 0, sold: 0 }, products: [{ id: '2', title: 'ChatGPT Pro 200', category: 'GPT', badge: '学生价',
    description: '', after_sales: '', views: 0, sold: 0, variants: [
      { id: '2:7', label: '新开', price_cents: 128800, promo_price_cents: 118800, available: false, quantity: null, sales: null },
      { id: '2:2', label: '续费', price_cents: 138800, promo_price_cents: null, available: true, quantity: null, sales: null },
    ] }] });

async function preview(run) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1024, height: 900 } });
    const data = catalog();
    await page.route('**/api/v1/shop/catalog', route => route.fulfill({ json: data }));
    await page.route('**/api/v1/shop/visit', route => route.fulfill({ json: { ok: true } }));
    await run(page, data);
  } finally { await browser.close(); }
}

test('card advertises only available offers; selected unavailable detail remains explicit', async () => preview(async (page, data) => {
  const card = page.locator('.shop-product-row');
  await page.goto(process.env.PREVIEW_BASE_URL);
  const price = card.locator('.shop-product-price');
  await expect(card.locator('.shop-product-availability')).toHaveText('可咨询购买');
  await expect(price.locator('strong')).toHaveText('¥1388.00');
  await expect(price.locator('s')).toHaveCount(0);
  await expect(price.locator('.shop-product-from')).toHaveCount(0);
  // A sold-out cheaper offer must not undercut the available one.
  data.products[0].variants[1].promo_price_cents = 128800;
  await page.reload();
  await expect(price.locator('strong')).toHaveText('¥1288.00');
  await expect(price.locator('s')).toHaveText('原价 ¥1388.00');
  await expect(price.locator('.shop-product-from')).toHaveCount(0);
  // With both available, the minimum and “起” are meaningful again.
  data.products[0].variants[0].available = true;
  await page.reload();
  await expect(price.locator('strong')).toHaveText('¥1188.00');
  await expect(price.locator('.shop-product-from')).toHaveText('起');
  await expect(price.locator('s')).toHaveText('原价 ¥1288.00');
  // With nothing available, no 优惠价 is advertised: the lowest regular price stays, greyed.
  data.products[0].variants.forEach(variant => { variant.available = false; });
  await page.reload();
  await expect(price.locator('strong')).toHaveText('¥1288.00');
  await expect(price.locator('strong')).not.toHaveClass('is-promo');
  await expect(price.locator('s')).toHaveCount(0);
  await expect(card).toHaveClass(/is-unavailable/);
  await expect(card.getByRole('button', { name: /暂不可购买/ })).toBeDisabled();
  await page.goto(`${process.env.PREVIEW_BASE_URL}/?product=2`);
  await expect(page.locator('.shop-detail-price .shop-promo-price')).toHaveText('学生价：¥1188.00');
  await expect(page.getByRole('button', { name: '立即购买', exact: true })).toBeDisabled();
}));

test('long valid offer label never intersects the card cart button', async () => preview(async (page, data) => {
  data.products[0].badge = '开学季学生优惠价';
  data.products[0].variants[0].available = true;
  for (const width of [1024, 1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(process.env.PREVIEW_BASE_URL);
    const card = page.locator('.shop-product-row');
    const offer = card.locator('.shop-product-price');
    await expect(offer.locator('.shop-promo-badge')).toHaveText('开学季学生优惠价');
    await expect(offer.locator('s')).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // “起” travels with the amount, including when a long badge makes the row wrap.
    const amount = await offer.locator('strong').boundingBox();
    const from = await offer.locator('.shop-product-from').boundingBox();
    assert.ok(from.x >= amount.x + amount.width && from.y < amount.y + amount.height,
      `price suffix shares the amount's line at ${width}px`);
    const baselines = await offer.evaluate(el => {
      const markers = ['strong', '.shop-product-from'].map(selector => {
        const marker = document.createElement('span');
        marker.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
        el.querySelector(selector).append(marker);
        return marker;
      });
      const result = markers.map(marker => marker.getBoundingClientRect().top);
      markers.forEach(marker => marker.remove());
      return result;
    });
    assert.ok(Math.abs(baselines[0] - baselines[1]) < 1, `price suffix aligns with amount baseline at ${width}px`);
    const b = await card.locator('.shop-product-cart').boundingBox();
    const bounds = await card.boundingBox();
    // The label, the price and the struck regular price each stay inside the card and clear of the cart.
    for (const part of ['.shop-promo-badge', 'strong', 's']) {
      const a = await offer.locator(part).boundingBox();
      const overlaps = a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
      assert.equal(overlaps, false, `${part} overlaps the cart at ${width}px: ${JSON.stringify({ a, b })}`);
      assert.ok(a.x + a.width <= bounds.x + bounds.width, `${part} fits the card at ${width}px`);
    }
    if (width === 1024 && process.env.REACT_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, 'offer-1024.png'), fullPage: true });
    }
  }
}));

test('store totals emphasize the counts and wrap whole badges on small screens', async () => preview(async (page, data) => {
  for (const stats of [{ views: 62, sold: 18 }, { views: 9999, sold: 9999 }, { views: 0, sold: 18 }, { views: 62, sold: 0 }]) {
    data.stats = stats;
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(process.env.PREVIEW_BASE_URL);
      const totals = page.getByRole('group', { name: '店铺数据' });
      await expect(totals.locator('.shop-store-stat')).toHaveCount(Number(Boolean(stats.views)) + Number(Boolean(stats.sold)));
      await expect(totals.locator('strong')).toHaveText([stats.views, stats.sold].filter(Boolean).map(n => n.toLocaleString('zh-CN')));
      for (const badge of await totals.locator('.shop-store-stat').all()) {
        const fits = await badge.evaluate(el => el.scrollWidth <= el.clientWidth + 1);
        const box = await badge.boundingBox();
        assert.ok(fits && box.x >= 0 && box.x + box.width <= width, `store total fits ${width}px`);
      }
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `store totals fit ${width}px`);
    }
  }
  data.stats = { views: 0, sold: 0 };
  await page.reload();
  await expect(page.getByRole('group', { name: '店铺数据' })).toHaveCount(0);
}));

test('store totals refresh after counting the visit and poll later views and sales', async () => preview(async (page, data) => {
  await page.clock.install();
  data.stats = { views: 63, sold: 18 };
  let completeVisit;
  const visitPending = new Promise(resolve => { completeVisit = resolve; });
  await page.route('**/api/v1/shop/visit', async route => {
    await visitPending;
    data.stats.views = 64;
    await route.fulfill({ status: 204 });
  });
  await page.goto(process.env.PREVIEW_BASE_URL);
  const totals = page.getByRole('group', { name: '店铺数据' }).locator('strong');
  await expect(totals).toHaveText(['63', '18']);
  completeVisit();
  // No timer advancement or page reload: count completion triggers the update.
  await expect(totals).toHaveText(['64', '18']);
  data.stats = { views: 65, sold: 19 };
  await page.clock.runFor(61_000);
  await expect(totals).toHaveText(['65', '19']);
}));

test('an expired last new product returns the new filter to the complete catalog', async () => preview(async (page, data) => {
  const now = Date.now();
  await page.clock.install({ time: now });
  data.products[0].new_until = Math.floor(now / 1000) + 30;
  await page.goto(process.env.PREVIEW_BASE_URL);
  await page.getByRole('button', { name: /^新品/ }).click();
  await expect(page.locator('.shop-product-row')).toHaveCount(1);
  await page.clock.runFor(61_000);
  await expect(page.getByRole('button', { name: /^新品/ })).toHaveCount(0);
  await expect(page.locator('.shop-product-row')).toHaveCount(1);
  await expect(page.getByRole('button', { name: /^全部/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.shop-new-badge')).toHaveCount(0);
}));

test('maximum-length announcement copy fits tablet and phone layouts', async () => preview(async (page, data) => {
  data.announcement = { text: 'A'.repeat(200), items: ['会员'.repeat(12)] };
  for (const width of [1440, 1024, 800, 768, 640, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(process.env.PREVIEW_BASE_URL);
    await expect(page.getByRole('region', { name: '店铺公告' })).toBeVisible();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `announcement fits ${width}px`);
    assert.ok(await page.locator('.shop-announcement p').evaluate(el => el.scrollWidth <= el.clientWidth + 1), `announcement text wraps at ${width}px`);
  }
}));

test('a brand\'s plans show short names and all stay in view', async () => preview(async (page, data) => {
  const plan = (id, title) => ({ ...data.products[0], id, title, badge: '', variants: data.products[0].variants.map(variant => ({ ...variant, id: `${id}:1`, available: true })).slice(0, 1) });
  // codex额度充值 does not start with ChatGPT; the others still lose the brand word and their region.
  data.products = [plan('1', 'ChatGPT Plus｜菲律宾区'), plan('2', 'ChatGPT Go｜埃及区'), plan('3', 'ChatGPT Pro 100｜智利区'),
    plan('4', 'ChatGPT Pro 200｜菲律宾区'), plan('5', 'ChatGPT Pro 500｜卡付充值'), plan('6', 'codex额度充值'), plan('7', 'ChatGPT 学生认证')];
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${process.env.PREVIEW_BASE_URL}/?product=4`);
    const plans = page.getByRole('navigation', { name: 'GPT 其他商品' });
    await expect(plans.locator('.shop-plans-name')).toHaveText(['Plus', 'Go', 'Pro 100', 'Pro 200', 'Pro 500', 'codex额度充值', '学生认证']);
    const box = await plans.boundingBox();
    for (const link of await plans.getByRole('link').all()) {
      const chip = await link.boundingBox();
      assert.ok(chip.x >= box.x - 1 && chip.x + chip.width <= box.x + box.width + 1, `plan chip out of view at ${width}px`);
    }
  }
}));

test('SMS and VPN share a service row while Grok keeps its own rows', async () => preview(async (page, data) => {
  const card = (id, title, category) => ({ ...data.products[0], id, title, category, badge: '', variants: [{ ...data.products[0].variants[1], id: `${id}:1` }] });
  data.products = [...[1, 2, 3, 4, 5, 6].map(n => card(String(n), `ChatGPT ${n}`, 'GPT')),
    card('1000000000005', 'Claude Pro 直充', 'Claude'), card('1000000000007', 'Claude 普号', 'Claude'),
    ...['07', '08', '09', '10'].map(n => card(`60000000000${n}`, `Grok ${n}`, 'Grok')),
    card('2000000000001', 'Gemini AI Pro', 'Gemini'), card('2000000000002', 'Muse AI', 'Muse AI'),
    card('2000000000003', 'Duolingo Super', '多邻国'), card('6000000000001', '短信接码激活码', '短信接码'),
    card('6000000000002', 'VPN 搭建指导', 'VPN')];
  const layout = () => page.evaluate(() => {
    const cards = [...document.querySelectorAll('.shop-product-row')].map(row => ({ brand: row.closest('[data-category]').dataset.category, box: row.getBoundingClientRect() }));
    const tops = [...new Set(cards.map(item => Math.round(item.box.top)))].sort((a, b) => a - b);
    const rows = tops.map(top => cards.filter(item => Math.round(item.box.top) === top).sort((a, b) => a.box.left - b.box.left));
    const body = document.querySelector('.shop-catalog-body').getBoundingClientRect();
    return { rows: rows.map(row => row.map(item => item.brand)), widths: [...new Set(cards.map(item => Math.round(item.box.width)))],
      edges: rows.map(row => [Math.round(row[0].box.left - body.left), Math.round(body.right - row.at(-1).box.right)]) };
  });
  const gpt = n => Array(n).fill('GPT'), grok = Array(4).fill('Grok');
  const small = ['Gemini', 'Muse AI', '多邻国'], services = [['短信接码', 'VPN']];
  const desktop = [gpt(5), gpt(1), ['Claude', 'Claude'], grok, small, ...services];
  for (const [width, rows] of [
    [2560, desktop],
    [1920, desktop],
    [1440, [gpt(4), gpt(2), ['Claude', 'Claude'], grok, small, ...services]],
    [1024, [gpt(3), gpt(3), ['Claude', 'Claude'], grok.slice(0, 3), ['Grok'], small, ...services]],
    [580, [...gpt(6), 'Claude', 'Claude', ...grok, ...small, '短信接码', 'VPN'].map(brand => [brand])],
    [390, [...gpt(6), 'Claude', 'Claude', ...grok, ...small, '短信接码', 'VPN'].map(brand => [brand])],
  ]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(process.env.PREVIEW_BASE_URL);
    await expect(page.locator('.shop-product-row')).toHaveCount(17);
    const result = await layout();
    assert.deepEqual(result.rows, rows, `brand rows at ${width}px`);
    assert.equal(result.widths.length, 1, `standalone cards keep normal widths at ${width}px`);
    result.edges.forEach(([left, right], index) => {
      assert.ok(left === 0 && right >= -1, `row ${index} stays within the catalog at ${width}px: ${left}, ${right}`);
    });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `catalog fits ${width}px`);
    for (const brand of ['Claude', 'Grok', '短信接码', 'VPN']) {
      const group = page.locator(`.shop-category-group[data-category="${brand}"]`);
      const [heading, first] = await Promise.all([group.locator('.shop-group-heading').boundingBox(), group.locator('.shop-product-row').first().boundingBox()]);
      assert.ok(Math.abs(heading.x - first.x) < 1 && heading.y < first.y, `${brand} heading above its cards at ${width}px`);
    }
  }
  // Returning from a product preserves the service row and Grok's dedicated rows.
  await page.setViewportSize({ width: 2560, height: 900 });
  await page.goto(`${process.env.PREVIEW_BASE_URL}/?product=6000000000007`);
  await page.getByRole('link', { name: '商品目录', exact: true }).click();
  await expect(page.locator('.shop-product-row').first()).toBeVisible();
  assert.deepEqual((await layout()).rows, desktop);

  // Adding products to a dedicated brand wraps only within that brand.
  data.products.push(card('6000000000011', 'Grok 11', 'Grok'), card('6000000000012', 'Grok 12', 'Grok'));
  await page.reload();
  await expect(page.locator('.shop-product-row')).toHaveCount(19);
  assert.deepEqual((await layout()).rows, [gpt(5), gpt(1), ['Claude', 'Claude'], Array(5).fill('Grok'), ['Grok'], small, ...services]);
}));

test('Grok plans compare as priced tiles, and store-owned copy renders as Markdown', async () => preview(async (page, data) => {
  const plan = (id, title, cents, quota) => ({ id, title, category: 'Grok', badge: '', views: 0, sold: 0, tags: ['新版', quota, '质保30天'],
    description: `## ${title}\n\n**正规充值 · 无需上号**\n\n### 套餐信息\n\n- **套餐额度：** ${quota}。\n- **订阅质保：** 30 天。\n\n> **充值成功后不退不换。**`,
    after_sales: '### 订阅质保\n\n提供 **30 天订阅质保**。',
    variants: [{ id: `${id}:1`, label: '1 个月', price_cents: cents, promo_price_cents: null, available: true, quantity: null, sales: null }] });
  data.products = [plan('6000000000007', 'Grok Plus 1x（新版 · 1个月）', 7300, 'Plus标准额度'),
    plan('6000000000008', 'Grok Premium 4x（新版 · 1个月）', 12900, '4倍Plus额度'),
    plan('6000000000009', 'Grok Super 15x（新版 · 1个月）', 38900, '15倍Plus额度'),
    plan('6000000000010', 'Grok Ultra 50x（新版 · 1个月）', 75900, '50倍Plus额度')];
  data.products[0].variants[0].promo_price_cents = 6900;
  data.products[3].variants[0].available = false;
  const plans = page.getByRole('navigation', { name: 'Grok 其他商品' });
  for (const [width, columns] of [[1440, 4], [390, 2]]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${process.env.PREVIEW_BASE_URL}/?product=6000000000008`);
    // The qualifier every plan shares stays in the title; each tile keeps its name and lowest price.
    await expect(page.locator('#shop-detail-title')).toHaveText('Grok Premium 4x（新版 · 1个月）');
    await expect(plans.locator('.shop-plans-name')).toHaveText(['Plus 1x', 'Premium 4x', 'Super 15x', 'Ultra 50x']);
    await expect(plans.locator('.shop-plans-price')).toHaveText(['¥69.00', '¥129.00', '¥389.00', '暂不可购买']);
    await expect(plans.locator('.shop-plans-price').first()).toHaveClass(/is-promo/);
    await expect(plans.getByRole('link', { name: 'Premium 4x', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(plans.getByRole('link', { name: 'Plus 1x', exact: true })).toHaveAccessibleDescription('¥69.00');
    // Tiles share one width and fill whole rows: four across on a wide screen, two by two on a phone.
    const boxes = await Promise.all((await plans.getByRole('link').all()).map(link => link.boundingBox()));
    assert.equal(new Set(boxes.map(box => Math.round(box.width))).size, 1, `plan tiles differ in width at ${width}px`);
    assert.equal(new Set(boxes.map(box => Math.round(box.y))).size, 4 / columns, `plan tile rows at ${width}px`);
    const nav = await plans.boundingBox();
    assert.ok(Math.abs(boxes[columns - 1].x + boxes[columns - 1].width - nav.x - nav.width) < 2, `plan tiles fill the row at ${width}px`);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `product page fits ${width}px`);
    await expect(page.locator('.shop-detail-heading .shop-tag-badge')).toHaveText(['新版', '4倍Plus额度', '质保30天']);
    // Store-owned copy is Markdown: headings, lists, emphasis and callouts, never their symbols.
    const copy = page.locator('.shop-detail-copy');
    await expect(copy.getByRole('heading', { name: '套餐信息', exact: true })).toBeVisible();
    await expect(copy.locator('li').first()).toHaveText('套餐额度： 4倍Plus额度。');
    await expect(copy.locator('blockquote')).toHaveText('充值成功后不退不换。');
    await expect(copy.locator('strong', { hasText: '30 天订阅质保' })).toBeVisible();
    for (const symbol of ['##', '**', '- ', '> ']) await expect(copy).not.toContainText(symbol);
    if (process.env.REACT_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.REACT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.REACT_SCREENSHOT_DIR, `grok-plans-${width}.png`), fullPage: true });
    }
  }
  // A qualifier only some plans carry tells them apart, so it stays.
  data.products.push(plan('6000000000004', 'SuperGrok 充值（旧版 · iOS）', 13500, '旧版'));
  await page.reload();
  await expect(plans.locator('.shop-plans-name')).toHaveText(['Plus 1x（新版 · 1个月）', 'Premium 4x（新版 · 1个月）', 'Super 15x（新版 · 1个月）', 'Ultra 50x（新版 · 1个月）', 'SuperGrok 充值（旧版 · iOS）']);
}));

test('text tags remain readable with or without optional highlight metadata', async () => preview(async (page, data) => {
  data.products[0].tags = ['支持二验三验', '支持Codex'];
  await page.goto(process.env.PREVIEW_BASE_URL);
  await expect(page.locator('.shop-product-row .shop-tag-badge')).toHaveText(data.products[0].tags);
  await expect(page.locator('.shop-product-row .shop-tag-badge.is-hot')).toHaveCount(0);
  data.products[0].hot_tags = ['支持Codex'];
  await page.reload();
  await expect(page.locator('.shop-product-row .shop-tag-badge.is-hot')).toHaveText('支持Codex');
  await page.locator('[data-shop-card="2"] h3 a').click();
  await expect(page.locator('.shop-detail-heading .shop-tag-badge')).toHaveText(data.products[0].tags);
  await expect(page.locator('.shop-detail-heading .shop-tag-badge.is-hot')).toHaveText('支持Codex');
}));
