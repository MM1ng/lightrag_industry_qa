<script setup lang="ts">
import type { AssistantMessage } from '../../app/stores/session'
import FeedbackActions from './FeedbackActions.vue'
import CitationTag from './CitationTag.vue'
defineProps<{ message: AssistantMessage }>()
const emit = defineEmits<{ selectCitation: [citationId: string]; retry: [question: string]; feedback: [payload: { type: 'helpful' | 'unhelpful'; reason?: string; comment?: string }] }>()
const statusCopy: Record<string, string> = { success: '可执行回答', partial_answer: '部分回答', insufficient_evidence: '证据不足', safety_blocked: '安全拦截', failed: '查询失败', clarification_required: '需要澄清', out_of_scope: '超出当前手册范围' }
const statusTone: Record<string, string> = { success: 'success', partial_answer: 'warning', insufficient_evidence: 'warning', safety_blocked: 'danger', failed: 'danger' }
function renderAnswerMarkdown(markdown: string) {
  const lines = markdown.split(/\r?\n/)
  let html = ''; let list: 'ol' | 'ul' | null = null
  const closeList = () => { if (list) { html += `</${list}>`; list = null } }
  const inline = (value: string) => value.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>')
  for (const raw of lines) {
    const line = escapeHtml(raw.trim())
    if (!line) { closeList(); continue }
    const heading = line.match(/^(#{1,3})\s+(.+)$/)?.[2] ?? (/^(结论|操作步骤|注意事项|处理建议|核验结果)$/.test(line) ? line : '')
    if (heading) { closeList(); html += `<h3>${inline(heading)}</h3>`; continue }
    const ordered = line.match(/^\d+[.)]\s+(.+)$/)?.[1]
    const unordered = line.match(/^[-*]\s+(.+)$/)?.[1]
    if (ordered || unordered) {
      const nextList = ordered ? 'ol' : 'ul'; if (list !== nextList) { closeList(); list = nextList; html += `<${list}>` }
      html += `<li>${inline(ordered || unordered || '')}</li>`; continue
    }
    closeList(); html += `<p>${inline(line)}</p>`
  }
  closeList(); return html
}
function escapeHtml(value: string) { return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;') }
</script>

<template>
  <article class="answer-card" :class="`answer-card--${statusTone[message.result.status] || 'neutral'}`">
    <header><div><span class="answer-kicker">ASSISTANT RESPONSE</span><strong>{{ statusCopy[message.result.status] || '查询结果' }}</strong></div><span class="answer-latency mono" v-if="message.result.latency_ms">{{ message.result.latency_ms }} ms</span></header>
    <div v-if="message.result.status === 'partial_answer' && message.result.partial_reason" class="state-note">{{ message.result.partial_reason }}</div>
    <div v-if="message.result.status === 'insufficient_evidence'" class="state-note">当前手册没有足够证据支持可靠回答。可以换一种描述，或从下面的高频问题开始。</div>
    <div v-if="message.result.status === 'safety_blocked'" class="state-note">这个问题涉及高风险操作，系统不会提供可能导致误操作的步骤。请按现场安全规程执行。</div>
    <div v-if="message.result.status === 'failed'" class="state-note">查询没有完成，原问题仍保留，可以重新查询。</div>
    <div class="answer-body" v-html="renderAnswerMarkdown(message.result.answer)" />
    <div v-if="message.result.claims.length" class="claims"><span class="section-label">核验要点</span><p v-for="claim in message.result.claims" :key="claim.claim_id">{{ claim.text }} <button v-for="id in claim.citation_ids" :key="id" class="inline-citation" @click="emit('selectCitation', id)">[{{ message.result.citations.findIndex((citation) => citation.citation_id === id) + 1 }}]</button></p></div>
    <div v-if="message.result.citations.length" class="citations"><span class="section-label">依据</span><div class="citation-list"><CitationTag v-for="(citation, index) in message.result.citations" :key="citation.citation_id" :citation="citation" :index="index" @select="emit('selectCitation', $event)" /></div></div>
    <div class="answer-footer"><FeedbackActions :submitted="message.feedbackSubmitted" @submit="emit('feedback', $event)" /><button v-if="message.result.status === 'failed' || message.result.status === 'insufficient_evidence'" class="retry" @click="emit('retry', message.question)">重新查询 ↗</button></div>
  </article>
</template>

<style scoped>
.answer-card { padding: 22px 24px; border: 1px solid var(--color-line); border-left: 4px solid var(--color-line); border-radius: var(--radius-panel); background: var(--color-surface); box-shadow: var(--shadow-panel); } .answer-card--success { border-left-color: var(--color-cobalt); } .answer-card--warning { border-left-color: var(--color-amber); } .answer-card--danger { border-left-color: var(--color-danger); } header { display: flex; justify-content: space-between; gap: 12px; align-items: start; } header div { display: grid; gap: 4px; } .answer-kicker, .section-label { color: var(--color-muted); font: 10px Bahnschrift, monospace; letter-spacing: .1em; text-transform: uppercase; } .answer-latency { color: var(--color-muted); font-size: 11px; } .answer-body { margin-top: 18px; font-size: 15px; line-height: 1.85; } .answer-body :deep(p) { margin: 0 0 12px; } .answer-body :deep(h3) { margin: 18px 0 8px; color: var(--color-ink); font-size: 15px; } .answer-body :deep(ol), .answer-body :deep(ul) { margin: 0 0 14px; padding-left: 22px; } .answer-body :deep(li) { margin: 5px 0; } .answer-body :deep(code) { padding: 2px 4px; border-radius: 4px; color: var(--color-cobalt); background: var(--color-cobalt-soft); font-family: Bahnschrift, monospace; font-size: .92em; } .state-note { margin-top: 14px; padding: 10px 12px; color: var(--color-amber); background: var(--color-amber-soft); font-size: 13px; line-height: 1.6; } .answer-card--danger .state-note { color: var(--color-danger); background: #fff0ee; } .claims, .citations { margin-top: 22px; } .claims p { margin: 9px 0 0; padding-left: 12px; border-left: 2px solid var(--color-line); line-height: 1.7; } .inline-citation { min-height: 24px; padding: 0 3px; border: 0; color: var(--color-cobalt); background: transparent; font-weight: 800; } .citation-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 9px; } .citation-tag { display: flex; align-items: center; gap: 8px; min-height: 38px; padding: 0 10px; border: 1px solid var(--color-line); border-left: 3px solid var(--color-cobalt); border-radius: 7px; color: var(--color-ink); background: #fbfcfd; text-align: left; font-size: 12px; } .citation-tag:hover { border-color: #9ebcf1; background: var(--color-cobalt-soft); } .citation-tag b { color: var(--color-cobalt); } .answer-footer { display: flex; align-items: end; justify-content: space-between; gap: 10px; } .retry { min-height: 32px; border: 0; color: var(--color-cobalt); background: transparent; font-size: 12px; font-weight: 700; }
</style>
