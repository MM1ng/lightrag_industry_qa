<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '../app/stores/session'
import { apiClient } from '../api/client'
import type { KnowledgeBase } from '../types/api'
import AppStatusBadge from '../components/AppStatusBadge.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const session = useSessionStore()
const route = useRoute()
const router = useRouter()
const knowledgeBases = ref<KnowledgeBase[]>([])
const kbError = ref('')
const pendingKb = ref('')
const showClearDialog = ref(false)
const showMobileNav = ref(false)
const isAdminPage = computed(() => Boolean(route.meta.requiresAdmin) || route.path === '/admin/login')

onMounted(async () => {
  try {
    knowledgeBases.value = await apiClient.listKnowledgeBases()
    if (!session.activeKnowledgeBaseId && knowledgeBases.value[0]) session.selectKnowledgeBase(knowledgeBases.value[0].id)
  } catch { kbError.value = '知识库列表暂不可用' }
})

function requestKnowledgeBaseChange(id: string) {
  if (id === session.activeKnowledgeBaseId) return
  if (session.messages.length) { pendingKb.value = id; return }
  session.selectKnowledgeBase(id)
}
function confirmKnowledgeBaseChange() { session.clearChat(); session.selectKnowledgeBase(pendingKb.value); pendingKb.value = '' }
function clearChat() { if (session.messages.length) showClearDialog.value = true }
function leaveAdmin() { session.leaveAdminMode(); router.push('/chat') }
</script>

<template>
  <div class="workspace-shell">
    <div v-if="showMobileNav" class="mobile-backdrop" @click="showMobileNav = false" />
    <aside class="rail" :class="{ 'rail--open': showMobileNav }">
      <div class="brand"><span class="brand-mark">泵</span><div><strong>泵检</strong><small>KNOWLEDGE WORKBENCH</small></div></div>
      <nav aria-label="主导航" @click="showMobileNav = false">
        <p class="nav-label">现场工具</p>
        <RouterLink to="/chat"><span>问</span>智能问答</RouterLink>
        <RouterLink to="/graph"><span>图</span>知识图谱</RouterLink>
        <p v-if="session.hasAdminAccess" class="nav-label nav-label--admin">管理控制</p>
        <RouterLink v-if="session.hasAdminAccess" to="/admin/knowledge-bases"><span>库</span>知识库</RouterLink>
        <RouterLink v-if="session.hasAdminAccess" to="/admin/documents"><span>文</span>文档与更新</RouterLink>
        <RouterLink v-if="session.hasAdminAccess" to="/admin/jobs"><span>任</span>更新任务</RouterLink>
        <RouterLink v-if="session.hasAdminAccess" to="/admin/generations"><span>代</span>Generations</RouterLink>
      </nav>
      <div class="rail-footer">
        <RouterLink v-if="!session.hasAdminAccess" class="admin-entry" to="/admin/login"><span>锁</span>管理员入口</RouterLink>
        <button v-else class="admin-entry" @click="leaveAdmin"><span>出</span>退出管理员模式</button>
        <small>只读证据 · 现场优先</small>
      </div>
    </aside>
    <main class="main-column">
      <header class="topbar">
        <button class="mobile-menu" aria-label="打开导航" @click="showMobileNav = !showMobileNav">☰</button>
        <div class="context"><span class="eyebrow">{{ isAdminPage ? 'ADMIN CONSOLE' : 'FIELD KNOWLEDGE' }}</span><span class="context-title">{{ isAdminPage ? '知识库控制台' : '离心泵手册问答' }}</span></div>
        <div class="topbar-tools">
          <label class="kb-picker"><span>当前手册</span><select :value="session.activeKnowledgeBaseId ?? ''" @change="requestKnowledgeBaseChange(($event.target as HTMLSelectElement).value)"><option value="" disabled>选择知识库</option><option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</option></select></label>
          <AppStatusBadge :label="kbError || 'API 就绪'" :tone="kbError ? 'warning' : 'success'" />
        </div>
      </header>
      <div v-if="route.path === '/chat' && session.messages.length" class="session-actions"><span>本次对话 · {{ session.messages.filter((m) => m.role === 'user').length }} 个问题</span><button @click="clearChat">清空会话</button></div>
      <div class="page-content"><slot /></div>
    </main>
    <ConfirmDialog :open="Boolean(pendingKb)" title="切换手册会清空当前对话" description="当前时间线中的问题和回答只保存在本次浏览器会话中。确认切换后，它们会被清除。" confirm-label="切换并清空" @cancel="pendingKb = ''" @confirm="confirmKnowledgeBaseChange" />
    <ConfirmDialog :open="showClearDialog" title="清空当前对话？" description="这只会清除本地时间线，不会删除知识库或反馈记录。" confirm-label="清空会话" @cancel="showClearDialog = false" @confirm="showClearDialog = false; session.clearChat()" />
  </div>
</template>

<style scoped>
.workspace-shell { min-height: 100vh; display: grid; grid-template-columns: 248px minmax(0, 1fr); background: var(--color-canvas); }
.rail { position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; padding: 26px 16px 18px; border-right: 1px solid var(--color-line); background: #edf1f4; }
.brand { display: flex; align-items: center; gap: 11px; padding: 0 10px 28px; } .brand-mark { display: grid; place-items: center; width: 38px; height: 38px; color: white; border-radius: 10px; background: var(--color-cobalt); font-weight: 800; box-shadow: 4px 4px 0 #aac4f6; } .brand strong { display: block; font-size: 17px; letter-spacing: .06em; } .brand small { display: block; margin-top: 3px; color: var(--color-muted); font: 9px Bahnschrift, monospace; letter-spacing: .08em; }
.nav-label { margin: 0 10px 8px; color: var(--color-muted); font: 11px Bahnschrift, monospace; letter-spacing: .08em; text-transform: uppercase; } .nav-label--admin { margin-top: 28px; color: var(--color-amber); }
nav { display: flex; flex-direction: column; gap: 4px; } nav a, .admin-entry { display: flex; align-items: center; gap: 12px; min-height: 44px; padding: 0 10px; border: 0; border-radius: var(--radius-control); color: var(--color-muted); background: transparent; text-decoration: none; text-align: left; } nav a:hover, nav a.router-link-active { color: var(--color-cobalt); background: var(--color-cobalt-soft); } nav a span, .admin-entry span { display: grid; place-items: center; width: 24px; height: 24px; border: 1px solid currentColor; border-radius: 7px; font-size: 12px; }
.rail-footer { margin-top: auto; } .admin-entry { width: 100%; color: var(--color-amber); } .admin-entry:hover { background: var(--color-amber-soft); } .rail-footer small { display: block; margin: 18px 10px 0; color: #8895a1; font-size: 11px; }
.main-column { min-width: 0; } .topbar { min-height: 78px; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 0 36px; border-bottom: 1px solid var(--color-line); background: rgba(255,255,255,.74); } .context { display: grid; gap: 4px; } .eyebrow { color: var(--color-cobalt); font: 11px Bahnschrift, monospace; letter-spacing: .12em; } .context-title { font-size: 16px; font-weight: 700; } .topbar-tools { display: flex; align-items: center; gap: 16px; } .kb-picker { display: grid; gap: 2px; } .kb-picker span { color: var(--color-muted); font-size: 11px; } .kb-picker select { min-height: 36px; padding: 0 30px 0 0; border: 0; color: var(--color-ink); background: transparent; font-weight: 700; }
.session-actions { display: flex; justify-content: space-between; padding: 12px 36px 0; color: var(--color-muted); font-size: 12px; } .session-actions button { min-height: 32px; padding: 0 8px; border: 0; color: var(--color-danger); background: transparent; } .page-content { min-height: calc(100vh - 78px); padding: 30px 36px 48px; }
.mobile-menu, .mobile-backdrop { display: none; }
@media (max-width: 900px) { .workspace-shell { display: block; } .rail { position: fixed; z-index: 20; left: 0; width: 248px; transform: translateX(-100%); transition: transform .2s ease; box-shadow: 12px 0 40px rgba(23,33,43,.12); } .rail--open { transform: translateX(0); } .mobile-backdrop { position: fixed; z-index: 19; inset: 0; display: block; background: rgba(23,33,43,.25); } .mobile-menu { display: inline-grid; place-items: center; width: 44px; border: 0; background: transparent; font-size: 20px; } .topbar { padding: 0 20px; } .page-content { padding: 24px 20px 40px; } }
@media (max-width: 600px) { .topbar-tools .status-badge { display: none; } .kb-picker { max-width: 150px; } .context { display: none; } .topbar { justify-content: space-between; } .page-content { padding-inline: 14px; } }
</style>
