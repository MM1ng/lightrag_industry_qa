<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiClient, ApiClientError } from '../../api/client'
import { useSessionStore } from '../../app/stores/session'

const token = ref('')
const error = ref('')
const checking = ref(false)
const session = useSessionStore()
const router = useRouter()
const route = useRoute()

async function enter() {
  if (!token.value.trim()) { error.value = '请输入管理员 Bearer 凭据。'; return }
  checking.value = true; error.value = ''
  try {
    await new ApiClient('', () => token.value.trim()).verifyAdminAccess()
    session.enterAdminMode(token.value.trim())
    await router.replace(typeof route.query.redirect === 'string' ? route.query.redirect : '/admin/knowledge-bases')
  } catch (cause) {
    error.value = cause instanceof ApiClientError && cause.status === 403 ? '凭据有效，但没有管理员权限。' : '无法验证凭据，请检查服务状态后重试。'
  } finally { checking.value = false }
}
</script>

<template>
  <section class="gate-page">
    <div class="gate-copy"><span class="eyebrow">PROTECTED OPERATIONS</span><h1>进入管理员控制台</h1><p>管理知识库、文档更新、任务和 Generation 生命周期。管理员凭据只保存在当前页面内存中，刷新页面后需要重新验证。</p><div class="gate-note"><span>!</span><span>前端入口只是导航保护，所有管理请求仍由 FastAPI 校验 Bearer 权限。</span></div></div>
    <form class="gate-card" @submit.prevent="enter"><label for="admin-token">管理员 Bearer 凭据</label><input id="admin-token" v-model="token" type="password" autocomplete="off" placeholder="输入凭据，不含 Bearer 前缀" :disabled="checking" /><p v-if="error" class="error" role="alert">{{ error }}</p><button class="submit" :disabled="checking">{{ checking ? '验证中…' : '验证并进入' }}</button><small>不会写入 URL、Local Storage 或构建产物。</small></form>
  </section>
</template>

<style scoped>
.gate-page { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 8%; align-items: center; min-height: 65vh; max-width: 980px; margin: 0 auto; } .eyebrow { color: var(--color-amber); font: 11px Bahnschrift, monospace; letter-spacing: .12em; } h1 { margin: 14px 0; font-size: clamp(30px, 5vw, 48px); letter-spacing: -.04em; } .gate-copy p { max-width: 590px; color: var(--color-muted); font-size: 16px; line-height: 1.9; } .gate-note { display: flex; gap: 10px; margin-top: 24px; max-width: 560px; padding: 14px; border-left: 3px solid var(--color-amber); color: var(--color-muted); background: var(--color-amber-soft); font-size: 13px; line-height: 1.6; } .gate-note span:first-child { color: var(--color-amber); font-weight: 800; }
.gate-card { display: grid; gap: 12px; padding: 24px; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: var(--color-surface); box-shadow: var(--shadow-panel); } .gate-card label { font-weight: 700; } input { width: 100%; padding: 0 12px; border: 1px solid var(--color-line); border-radius: var(--radius-control); } .submit { border: 0; border-radius: var(--radius-control); color: white; background: var(--color-cobalt); font-weight: 700; } .submit:disabled { opacity: .6; cursor: wait; } .gate-card small { color: var(--color-muted); line-height: 1.5; } .error { margin: 0; color: var(--color-danger); font-size: 13px; }
@media (max-width: 800px) { .gate-page { grid-template-columns: 1fr; min-height: 0; padding-top: 24px; } }
</style>
