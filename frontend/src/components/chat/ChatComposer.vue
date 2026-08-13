<script setup lang="ts">
import { ref } from 'vue'
defineProps<{ disabled?: boolean; error?: string }>()
const emit = defineEmits<{ submit: [question: string] }>()
const question = ref('')
function submit() { const value = question.value.trim(); if (!value) return; emit('submit', value); question.value = '' }
function setQuestion(value: string) { question.value = value }
defineExpose({ setQuestion })
</script>

<template>
  <form class="composer" @submit.prevent="submit"><label for="question">向当前手册提问</label><div class="composer-row"><textarea id="question" v-model="question" rows="2" :disabled="disabled" placeholder="描述设备现象，或直接输入想核验的操作…" @keydown.enter.exact.prevent="submit" /><button class="send" :disabled="disabled || !question.trim()" aria-label="发送问题">{{ disabled ? '检索中' : '发送' }}<span>↗</span></button></div><p v-if="error" class="composer-error">{{ error }}</p><small>回答会附上原手册片段；涉及安全的操作请以现场规程和授权为准。</small></form>
</template>

<style scoped>
.composer { padding: 18px; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: var(--color-surface); box-shadow: var(--shadow-panel); } label { display: block; margin-bottom: 10px; color: var(--color-muted); font-size: 12px; font-weight: 700; } .composer-row { display: flex; gap: 10px; } textarea { flex: 1; min-height: 72px; resize: vertical; padding: 12px; border: 1px solid var(--color-line); border-radius: var(--radius-control); color: var(--color-ink); background: #fbfcfd; line-height: 1.6; } .send { align-self: stretch; min-width: 86px; border: 0; border-radius: var(--radius-control); color: white; background: var(--color-cobalt); font-weight: 700; } .send span { margin-left: 6px; font-size: 18px; } .send:disabled { opacity: .55; cursor: not-allowed; } small { display: block; margin-top: 10px; color: var(--color-muted); font-size: 11px; } .composer-error { margin: 8px 0 0; color: var(--color-danger); font-size: 13px; } @media (max-width: 540px) { .composer-row { display: block; } .send { width: 100%; margin-top: 8px; } }
</style>
