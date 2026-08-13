<script setup lang="ts">
import type { GenerationSummary } from '../../types/api'
defineProps<{ generation: GenerationSummary; busy?: boolean }>()
const emit = defineEmits<{ action: [kind: 'validate' | 'promote' | 'rollback' | 'diff', generationId: string] }>()
</script>

<template><div class="actions"><button @click="emit('action', 'validate', generation.id)" :disabled="busy">验证</button><button @click="emit('action', 'diff', generation.id)" :disabled="busy">Diff</button><button class="danger" @click="emit('action', 'promote', generation.id)" :disabled="busy">Promote</button><button class="danger" @click="emit('action', 'rollback', generation.id)" :disabled="busy">Rollback</button></div></template>

<style scoped>.actions { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; } .actions button { min-height: 32px; padding: 0 8px; border: 1px solid var(--color-line); border-radius: 6px; color: var(--color-cobalt); background: white; font-size: 11px; } .actions button.danger { color: var(--color-amber); } .actions button:disabled { opacity: .45; }</style>
