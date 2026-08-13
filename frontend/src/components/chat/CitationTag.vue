<script setup lang="ts">
import type { Citation } from '../../types/api'
defineProps<{ citation: Citation; index: number }>()
const emit = defineEmits<{ select: [citationId: string] }>()
</script>

<template><button class="citation-tag" :aria-label="`查看依据：${citation.document_name}，第 ${citation.page} 页`" @click="emit('select', citation.citation_id)"><b>[{{ index + 1 }}]</b><span>{{ citation.document_name }} · p.{{ citation.page }}</span></button></template>

<style scoped>
.citation-tag { display: flex; align-items: center; gap: 8px; min-height: 38px; padding: 0 10px; border: 1px solid var(--color-line); border-left: 3px solid var(--color-cobalt); border-radius: 7px; color: var(--color-ink); background: #fbfcfd; text-align: left; font-size: 12px; } .citation-tag:hover { border-color: #9ebcf1; background: var(--color-cobalt-soft); } .citation-tag b { color: var(--color-cobalt); }
</style>
