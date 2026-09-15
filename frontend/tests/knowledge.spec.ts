import { test, expect, type Page } from '@playwright/test';

async function login(page: Page, username: string) {
  await page.goto('/');
  await page.getByLabel('用户名').fill(username);
  await page.getByLabel('密码').fill(`demo-${username}-123`);
  await page.getByRole('button', {name: '进入工作台'}).click();
  await expect(page.getByRole('button', {name: '＋ 新建会话'})).toBeVisible();
}

test('draft, publish, historical citation and rollback through the browser', async ({page, browser, request}) => {
  expect((await (await request.get('/api/health')).json()).mode).toBe('demo');
  const marker = `nebula${Date.now()}`;
  const slug = `e2e-${marker}`;
  await login(page, 'admin');
  await page.getByRole('button', {name:'知识库管理', exact:true}).click();
  await page.getByLabel('文档标识', {exact:true}).fill(slug);
  await page.getByLabel('文档标题', {exact:true}).fill('浏览器验收政策');
  await page.getByLabel('Markdown 正文', {exact:true}).fill(`# 验收\n\n${marker} amber first edition.`);
  await page.getByRole('button', {name:'保存草稿并预览'}).click();
  await expect(page.getByRole('status')).toContainText('草稿已保存');
  const doc = page.locator('.knowledge-document').filter({has:page.getByRole('heading', {name:slug, exact:true})});
  const customer = await browser.newContext();
  const chat = await customer.newPage();
  try {
    await login(chat, 'alice');
    await expect(chat.getByRole('button', {name:'知识库管理', exact:true})).toHaveCount(0);
    const draft = await customer.request.post('/api/knowledge/search', {headers:{'X-Nexus-Request':'1'},data:{query:marker}});
    expect((await draft.json()).found).toBe(false);
    await doc.getByRole('button', {name:'发布此版本',exact:true}).click();
    await page.getByRole('button', {name:'确认发布',exact:true}).click();
    await expect(page.getByRole('status')).toContainText('已发布');
    await chat.getByRole('button', {name:'＋ 新建会话'}).click();
    await chat.getByLabel('消息', {exact:true}).fill(`${marker} policy`);
    await chat.getByRole('button', {name:'发送',exact:true}).click();
    const citation = chat.locator('.source-buttons button').first();
    await expect(citation).toContainText('v1');
    await citation.click();
    await expect(chat.getByRole('region', {name:'引用原文'})).toContainText('amber first edition');
    await page.getByLabel('Markdown 正文', {exact:true}).fill(`# 验收\n\n${marker} violet second edition.`);
    await page.getByRole('button', {name:'保存草稿并预览'}).click();
    await expect(page.getByRole('status')).toContainText('草稿已保存');
    await doc.getByRole('button', {name:'发布此版本',exact:true}).click();
    await page.getByRole('button', {name:'确认发布',exact:true}).click();
    await expect(page.getByRole('status')).toContainText('v2');
    await chat.reload();
    await chat.locator('.source-buttons button').first().click();
    await expect(chat.getByRole('region', {name:'引用原文'})).toContainText('历史版本，已非当前政策');
    await expect(chat.getByRole('region', {name:'引用原文'})).toContainText('amber first edition');
    const current = await customer.request.post('/api/knowledge/search', {headers:{'X-Nexus-Request':'1'},data:{query:marker}});
    expect((await current.json()).sources[0].version).toBe(2);
    await chat.screenshot({path:'test-results/knowledge-citation.png',fullPage:true});
    await doc.locator('.version-row').filter({hasText:'v1'}).getByRole('button', {name:'回滚至此版本',exact:true}).click();
    await page.getByRole('button', {name:'确认发布',exact:true}).click();
    await expect(page.getByRole('status')).toContainText('v1');
    const rollback = await customer.request.post('/api/knowledge/search', {headers:{'X-Nexus-Request':'1'},data:{query:marker}});
    expect((await rollback.json()).sources[0].version).toBe(1);
    await page.screenshot({path:'test-results/knowledge-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:'test-results/knowledge-mobile.png',fullPage:true});
  } finally {await customer.close();}
});
