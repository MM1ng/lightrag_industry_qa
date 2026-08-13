<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiClient, ApiClientError } from '../../api/client'
import { useSessionStore } from '../../app/stores/session'
import type { UpdateJob } from '../../types/api'
const session = useSessionStore(); const client = new ApiClient('', () => session.adminToken); const jobs = ref<UpdateJob[]>([]); const error = ref(''); const kbId = computed(() => session.activeKnowledgeBaseId)
async function load() { if (!kbId.value) return; try { jobs.value = await client.listUpdateJobs(kbId.value) } catch (cause) { error.value = cause instanceof ApiClientError ? cause.message : '任务列表暂时无法读取。' } }
watch(kbId, load); onMounted(load)
</script>

<template><section class="admin-page"><div class="heading"><div><span class="eyebrow">ADMIN / UPDATE JOBS</span><h1>更新任务</h1><p>跟踪文档解析、索引和 Generation 更新的安全状态。</p></div><button class="refresh" @click="load">刷新任务</button></div><p v-if="!kbId" class="empty">请先在顶部选择知识库。</p><p v-else-if="error" class="error">{{ error }}</p><div v-else class="table-wrap"><table><thead><tr><th>任务</th><th>操作</th><th>阶段</th><th>状态</th><th>重试</th><th>错误码</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.job_id"><td><strong>{{ job.job_id.slice(0, 12) }}</strong><small>{{ job.document_id || '知识库级任务' }}</small></td><td>{{ job.operation }}</td><td>{{ job.current_stage || '—' }}</td><td><span class="status">{{ job.status }}</span></td><td>{{ job.retry_count }}</td><td class="mono">{{ job.error_code || '—' }}</td></tr><tr v-if="!jobs.length"><td colspan="6" class="empty">暂无更新任务。</td></tr></tbody></table></div></section></template>

<style scoped>
.admin-page { max-width: 1180px; margin: 0 auto; } .heading { display: flex; justify-content: space-between; align-items: end; margin-bottom: 24px; } .eyebrow { color: var(--color-amber); font: 11px Bahnschrift, monospace; letter-spacing: .12em; } h1 { margin: 9px 0 7px; font-size: 38px; letter-spacing: -.05em; } .heading p { margin: 0; color: var(--color-muted); } .refresh { min-height: 40px; padding: 0 13px; border: 1px solid var(--color-line); border-radius: var(--radius-control); color: var(--color-cobalt); background: white; font-weight: 700; } .table-wrap { overflow: auto; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: white; } table { width: 100%; border-collapse: collapse; min-width: 700px; } th, td { padding: 14px 16px; border-bottom: 1px solid var(--color-line); text-align: left; font-size: 13px; } th { color: var(--color-muted); background: #f6f8fa; font: 10px Bahnschrift, monospace; letter-spacing: .08em; text-transform: uppercase; } td small { display: block; margin-top: 4px; color: var(--color-muted); font-size: 11px; } .status { color: var(--color-success); font-weight: 700; } .mono { font-family: Bahnschrift, monospace; font-size: 11px; } .empty, .error { padding: 18px; color: var(--color-muted); } .error { color: var(--color-danger); }
</style>
