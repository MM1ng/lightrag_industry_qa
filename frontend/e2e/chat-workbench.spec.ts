import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/v1/knowledge-bases', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'kb-1', name: '离心泵运行手册', status: 'ready', document_count: 2, active_document_count: 2, chunk_count: 42 }] }) }))
  await page.route('**/v1/knowledge-bases/kb-1/query', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ request_id: 'e2e-request-1', status: 'success', answer: '结论\n\n请确认入口阀门已打开。', citations: [{ citation_id: 'c-1', document_name: '离心泵运行手册.pdf', page: 12, chunk_id: 'hidden' }], claims: [], evidence: [{ evidence_id: 'e-1', citation_id: 'c-1', document_name: '离心泵运行手册.pdf', page: 12, chunk_id: 'hidden', excerpt: '确认入口阀门已打开。', relevance_label: '核心依据' }], latency_ms: 25 }) }))
  await page.route('**/v1/graph/overview**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ nodes: [], edges: [], stats: { node_count: 0, edge_count: 0, mode: 'overview', query: null } }) }))
})

test('ordinary operator can ask, inspect evidence, and reach graph without admin navigation', async ({ page }) => {
  await page.goto('/chat')
  await expect(page.getByText('启动与停机')).toBeVisible()
  await expect(page.getByText('管理员入口')).toBeVisible()
  await page.getByRole('button', { name: /离心泵启动前需要检查哪些项目/ }).click()
  await expect(page.getByText('请确认入口阀门已打开。')).toBeVisible()
  await page.getByRole('button', { name: /查看依据/ }).click()
  await expect(page.getByRole('dialog', { name: '证据抽屉' })).toContainText('第 12 页')
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('link', { name: '知识图谱' }).click()
  await expect(page).toHaveURL(/\/graph$/)
  await expect(page.getByText('管理员入口')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Generations' })).toHaveCount(0)
})
