<script setup lang="ts">
import { computed, ref } from 'vue'
import { ApiClientError, apiClient } from '../api/client'
import { useSessionStore, type AssistantMessage } from '../app/stores/session'
import HighFrequencyPrompts from '../components/chat/HighFrequencyPrompts.vue'
import ChatTimeline from '../components/chat/ChatTimeline.vue'
import ChatComposer from '../components/chat/ChatComposer.vue'
import EvidenceDrawer from '../components/chat/EvidenceDrawer.vue'
import type { QueryResult } from '../types/api'

const session = useSessionStore()
const error = ref('')
const selectedCitationId = ref<string | null>(null)
const selectedAssistantId = ref<string | null>(null)
const composer = ref<InstanceType<typeof ChatComposer> | null>(null)
const isEmpty = computed(() => session.messages.length === 0)
const selectedEvidence = computed(() => session.messages.find((message): message is AssistantMessage => message.role === 'assistant' && message.id === selectedAssistantId.value)?.result.evidence ?? [])

async function submitQuestion(question: string) {
  if (session.chatStatus === 'loading') return
  if (!session.activeKnowledgeBaseId) { error.value = '请先选择一个知识库。'; return }
  const history = session.historyForQuery()
  session.addUserMessage(question); session.chatStatus = 'loading'; error.value = ''
  try {
    const result = await apiClient.queryKnowledgeBase(session.activeKnowledgeBaseId, question, history)
    session.addAssistantMessage(question, result)
  } catch (cause) {
    const message = cause instanceof ApiClientError ? cause.message : '查询没有完成，请检查服务状态后重试。'
    const failed: QueryResult = { request_id: '', status: 'failed', answer: message, citations: [], claims: [], evidence: [], latency_ms: 0 }
    session.addAssistantMessage(question, failed)
    error.value = cause instanceof ApiClientError && cause.retryable ? message : ''
  } finally { session.chatStatus = 'idle' }
}

function handlePrompt(question: string) { composer.value?.setQuestion(question); submitQuestion(question) }
function selectCitation(messageId: string, citationId: string) { selectedAssistantId.value = messageId; selectedCitationId.value = citationId }
function handleFeedback(messageId: string, payload: { type: 'helpful' | 'unhelpful'; reason?: string; comment?: string }) {
  const message = session.messages.find((item): item is AssistantMessage => item.id === messageId && item.role === 'assistant')
  if (!message || !message.result.request_id || message.feedbackSubmitted) return
  apiClient.submitFeedback({ request_id: message.result.request_id, feedback_type: payload.type, feedback_reason: payload.reason, feedback_comment: payload.comment }).then(() => session.markFeedbackSubmitted(messageId)).catch(() => { error.value = '反馈未提交，请稍后重试。' })
}
</script>

<template>
  <section class="chat-view">
    <div class="chat-intro"><div><span class="eyebrow">WORKBENCH / 01</span><h1>先说现象，<em>再核验依据。</em></h1><p>从当前离心泵手册中提取可执行步骤，并在每个关键结论旁打开原文证据。</p></div><div class="intro-mark" aria-hidden="true"><span>◆</span><small>FIELD<br>READY</small></div></div>
    <div v-if="isEmpty" class="empty-state"><div class="empty-copy"><span class="empty-index mono">01 / ASK THE MANUAL</span><h2>从一个现场问题开始</h2><p>选择下方高频问题，或描述你看到的设备现象。回答会把结论、操作步骤和注意事项分开呈现。</p></div><HighFrequencyPrompts @submit="handlePrompt" /></div>
    <ChatTimeline v-else :messages="session.messages" @select-citation="selectCitation" @retry="submitQuestion" @feedback="handleFeedback" />
    <div v-if="session.chatStatus === 'loading'" class="loading-row" role="status"><span class="loading-dot" />正在检索当前手册并核对证据…</div>
    <ChatComposer ref="composer" :disabled="session.chatStatus === 'loading'" :error="error" @submit="submitQuestion" />
    <p class="scope-note">当前范围：离心泵知识问答 · 只读图谱 · 管理功能需要单独验证</p>
    <EvidenceDrawer :visible="Boolean(selectedCitationId)" :evidence="selectedEvidence" :selected-citation-id="selectedCitationId" @close="selectedCitationId = null" @select="selectedCitationId = $event" />
  </section>
</template>

<style scoped>
.chat-view { max-width: 1180px; margin: 0 auto; } .chat-intro { display: flex; justify-content: space-between; align-items: end; gap: 30px; margin-bottom: 28px; } .eyebrow, .empty-index { color: var(--color-cobalt); font: 11px Bahnschrift, monospace; letter-spacing: .12em; } h1 { margin: 10px 0 8px; font-size: clamp(30px, 5vw, 54px); letter-spacing: -.06em; line-height: 1.12; } h1 em { color: var(--color-cobalt); font-style: normal; } .chat-intro p, .empty-copy p { max-width: 570px; margin: 0; color: var(--color-muted); line-height: 1.7; } .intro-mark { display: grid; place-items: center; width: 86px; height: 86px; border: 1px solid #b9cdf3; border-radius: 50%; color: var(--color-cobalt); background: var(--color-cobalt-soft); text-align: center; transform: rotate(-9deg); } .intro-mark span { font-size: 25px; } .intro-mark small { font: 9px Bahnschrift, monospace; letter-spacing: .12em; } .empty-state { margin-bottom: 26px; } .empty-copy { margin-bottom: 22px; } .empty-copy h2 { margin: 7px 0 7px; font-size: 22px; } .loading-row { display: flex; align-items: center; gap: 9px; margin: 13px 0; color: var(--color-cobalt); font-size: 13px; } .loading-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--color-cobalt); animation: pulse 1s infinite ease-in-out; } .scope-note { color: #8895a1; font-size: 11px; text-align: center; } @keyframes pulse { 50% { opacity: .3; transform: scale(.7); } } @media (max-width: 600px) { .chat-intro { align-items: start; } .intro-mark { width: 66px; height: 66px; flex: 0 0 auto; } }
</style>
