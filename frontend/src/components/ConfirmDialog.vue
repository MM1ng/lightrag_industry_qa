<script setup lang="ts">
defineProps<{ open: boolean; title: string; description: string; confirmLabel?: string }>()
const emit = defineEmits<{ confirm: []; cancel: [] }>()
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="dialog-backdrop" role="presentation" @click.self="emit('cancel')">
      <section class="dialog" role="dialog" aria-modal="true" :aria-label="title">
        <p class="dialog-kicker">需要确认</p><h2>{{ title }}</h2><p>{{ description }}</p>
        <div class="dialog-actions"><button class="button button--quiet" @click="emit('cancel')">取消</button><button class="button button--primary" @click="emit('confirm')">{{ confirmLabel ?? '确认' }}</button></div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop { position: fixed; inset: 0; z-index: 30; display: grid; place-items: center; padding: 20px; background: rgba(23,33,43,.34); }
.dialog { width: min(440px, 100%); padding: 26px; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: var(--color-surface); box-shadow: 0 20px 60px rgba(23,33,43,.18); }
.dialog-kicker { margin: 0 0 8px; color: var(--color-amber); font: 700 12px Bahnschrift, 'Cascadia Mono', monospace; letter-spacing: .08em; text-transform: uppercase; }
h2 { margin: 0 0 10px; font-size: 20px; } .dialog p:not(.dialog-kicker) { margin: 0; color: var(--color-muted); line-height: 1.7; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.button { min-height: 44px; padding: 0 16px; border: 1px solid var(--color-line); border-radius: var(--radius-control); font-weight: 700; }
.button--quiet { color: var(--color-ink); background: var(--color-surface); } .button--primary { color: white; border-color: var(--color-cobalt); background: var(--color-cobalt); }
</style>
