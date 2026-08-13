<script setup lang="ts">
defineProps<{ label: string; tone?: 'success' | 'warning' | 'danger' | 'neutral' }>()
</script>

<template><span class="status-badge" :class="`status-badge--${tone ?? 'neutral'}`"><i aria-hidden="true" />{{ label }}</span></template>

<style scoped>
.status-badge { display: inline-flex; align-items: center; gap: 7px; padding: 5px 10px; border: 1px solid var(--color-line); border-radius: 999px; color: var(--color-muted); font-size: 12px; white-space: nowrap; }
.status-badge i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.status-badge--success { color: var(--color-success); background: #e8f7f1; border-color: #bfe7d8; }
.status-badge--warning { color: var(--color-amber); background: var(--color-amber-soft); border-color: #f1d294; }
.status-badge--danger { color: var(--color-danger); background: #fff0ee; border-color: #f4c5bf; }
</style>
