import { test, expect } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  const health = await request.get('/api/health');
  expect((await health.json()).mode, 'Browser acceptance requires the synthetic demo environment').toBe('demo');
});

test('inquiry, confirmation survives refresh, receipt persists', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('用户名').fill('alice');
  await page.getByLabel('密码').fill('demo-alice-123');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('button', { name: '＋ 新建会话' }).click();
  await page.getByLabel('消息', { exact: true }).fill('O1002 能退款吗？');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('订单 O1002，状态：processing。可以申请退款；咨询不会创建申请。')).toBeVisible();
  await expect(page.getByRole('button', { name: '确认申请', exact: true })).toHaveCount(0);
  await page.getByLabel('消息', { exact: true }).fill('帮我申请');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认申请', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: '确认申请', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '确认申请', exact: true }).click();
  await expect(page.getByText('已登记', { exact: true })).toBeVisible();
  await expect(page.locator('.result')).toContainText('并非款项已退回');
  const receipt = await page.locator('.result').textContent();
  await page.reload();
  await expect(page.locator('.result')).toHaveText(receipt!);
  await page.locator('.proposal').scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'test-results/support-desktop.png', fullPage: true });
});

test('cancel proposal without creating a new request and isolate accounts', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('用户名').fill('bob');
  await page.getByLabel('密码').fill('demo-bob-123');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.locator('.order')).toHaveCount(1);
  await expect(page.locator('.order')).toContainText('O2001');
  await page.getByRole('button', { name: '＋ 新建会话' }).click();
  await page.getByLabel('消息', { exact: true }).fill('帮我申请 O2001');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByText('已取消', { exact: true })).toBeVisible();
  await expect(page.locator('.result')).toContainText('没有创建退款申请');
});

test('mobile login and chat fit viewport', async ({ page }) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await page.getByLabel('用户名').fill('bob');
  await page.getByLabel('密码').fill('demo-bob-123');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('button', { name: '＋ 新建会话' }).click();
  await expect(page.getByLabel('消息', {exact:true})).toBeVisible();
  await expect(page.getByRole('heading', {name:'我们从哪张订单开始？'})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/support-mobile.png', fullPage:true});
});
