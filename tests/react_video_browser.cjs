const fs = require('node:fs');

const pageSource = fs.readFileSync('frontend/src/features/video/VideoPage.tsx', 'utf8');
if (!pageSource.includes('运行工作流') || !pageSource.includes('工作流模板')) throw new Error('video page controls are missing');
if (!process.env.VIDEO_BASE_URL) {
  console.log('video browser smoke skipped: VIDEO_BASE_URL is not set');
  process.exit(0);
}
(async () => {
  const { chromium } = await import('@playwright/test');
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(`${process.env.VIDEO_BASE_URL}/admin/video`, { waitUntil: 'networkidle' });
    await page.getByRole('heading', { name: 'AI 视频' }).waitFor();
    await page.getByRole('button', { name: '运行工作流' }).waitFor();
    await page.getByText('提示词 → 文生图 → 图生视频').waitFor();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
