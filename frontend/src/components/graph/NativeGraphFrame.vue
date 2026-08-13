<script setup lang="ts">
defineProps<{ src: string }>()
</script>

<template>
  <div class="native-graph-frame">
    <iframe :src="src" title="LightRAG 原生知识图谱" loading="eager" />
    <div class="native-graph-note mono">LightRAG native · read only</div>
  </div>
</template>

<style scoped>
.native-graph-frame { position: relative; min-height: 700px; overflow: hidden; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: #fff; }
iframe { display: block; width: 100%; height: 700px; border: 0; background: #fff; }
.native-graph-note { position: absolute; right: 14px; bottom: 12px; z-index: 2; padding: 5px 8px; border-radius: 5px; color: var(--color-muted); background: rgba(255,255,255,.9); font-size: 10px; }
</style>
