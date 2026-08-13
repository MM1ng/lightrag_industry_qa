<script setup lang="ts">
import type { DocumentSummary } from '../../types/api'
defineProps<{ document: DocumentSummary }>()
const emit = defineEmits<{ replace: [documentId: string]; delete: [documentId: string] }>()
</script>

<template><div class="actions"><button @click="emit('replace', document.id)">替换</button><button @click="emit('delete', document.id)">删除文档</button></div></template>

<style scoped>.actions button { min-height: 34px; padding: 0 10px; border: 1px solid #f2c3bd; border-radius: 7px; color: var(--color-danger); background: #fff8f7; font-size: 12px; }</style>
