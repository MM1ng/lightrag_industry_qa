<script setup lang="ts">
import type { ChatMessage } from '../../app/stores/session'
import AnswerMessage from './AnswerMessage.vue'
defineProps<{ messages: ChatMessage[] }>()
const emit = defineEmits<{ selectCitation: [messageId: string, citationId: string]; retry: [question: string]; feedback: [messageId: string, payload: { type: 'helpful' | 'unhelpful'; reason?: string; comment?: string }] }>()
</script>

<template>
  <div class="timeline"><div v-for="message in messages" :key="message.id" class="timeline-item" :class="`timeline-item--${message.role}`"><div class="avatar">{{ message.role === 'user' ? '你' : '答' }}</div><div class="message-content"><span class="message-role">{{ message.role === 'user' ? '现场提问' : '手册回答' }}</span><div v-if="message.role === 'user'" class="question-bubble">{{ message.content }}</div><AnswerMessage v-else :message="message" @select-citation="emit('selectCitation', message.id, $event)" @retry="emit('retry', $event)" @feedback="emit('feedback', message.id, $event)" /></div></div></div>
</template>

<style scoped>
.timeline { display: grid; gap: 22px; } .timeline-item { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; } .avatar { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px; color: white; background: var(--color-cobalt); font-size: 12px; font-weight: 800; } .timeline-item--assistant .avatar { color: var(--color-cobalt); border: 1px solid #b9cdf3; background: var(--color-cobalt-soft); } .message-content { min-width: 0; } .message-role { display: block; margin: 5px 0 8px; color: var(--color-muted); font-size: 11px; } .question-bubble { display: inline-block; padding: 12px 14px; border-radius: 12px 12px 3px 12px; color: white; background: var(--color-ink); line-height: 1.6; }
</style>
