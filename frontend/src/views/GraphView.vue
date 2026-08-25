<script setup lang="ts">
import { computed, ref } from 'vue'
import NativeGraphFrame from '../components/graph/NativeGraphFrame.vue'
import GraphFilters from '../components/graph/GraphFilters.vue'

const mode = ref('全局概览'); const query = ref(''); const hops = ref<1 | 2>(1); const showNodeLabels = ref(true); const showEdgeLabels = ref(false)
const nativeGraphUrl = computed(() => { const params = new URLSearchParams({ limit: '50', hops: String(hops.value), show_node_labels: String(showNodeLabels.value), show_edge_labels: String(showEdgeLabels.value) }); if (query.value) params.set('query', query.value); return `/v1/graph/native?${params.toString()}` })
function loadOverview() { query.value = ''; hops.value = 1; mode.value = '全局概览' }
function search(value: string, nextHops: 1 | 2) { query.value = value; hops.value = nextHops; mode.value = `实体邻域 · ${value}` }
</script>

<template>
  <section class="graph-view"><div class="page-heading"><div><span class="eyebrow">WORKBENCH / 02 · READ ONLY</span><h1>知识图谱</h1><p>直接使用 LightRAG 原生图谱显示；当前视图只读，支持拖动、缩放、悬停查看关系与来源。</p></div><div class="mode-tag"><span>当前视图</span><strong>{{ mode }}</strong></div></div><GraphFilters @overview="loadOverview" @search="search" @settings="showNodeLabels = $event.showNodeLabels; showEdgeLabels = $event.showEdgeLabels" /><NativeGraphFrame :src="nativeGraphUrl" /></section>
</template>

<style scoped>
.graph-view { max-width: 1280px; margin: 0 auto; } .page-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 24px; } .eyebrow { color: var(--color-cobalt); font: 11px Bahnschrift, monospace; letter-spacing: .12em; } h1 { margin: 9px 0 7px; font-size: 38px; letter-spacing: -.05em; } .page-heading p { margin: 0; color: var(--color-muted); line-height: 1.6; } .mode-tag { display: grid; gap: 5px; padding: 11px 14px; border-left: 3px solid var(--color-cobalt); background: var(--color-cobalt-soft); } .mode-tag span { color: var(--color-muted); font-size: 11px; } .mode-tag strong { font-size: 13px; } .error { margin: 14px 0; padding: 11px 13px; color: var(--color-danger); background: #fff0ee; font-size: 13px; } .error button { min-height: 30px; margin-left: 8px; border: 0; color: var(--color-danger); background: transparent; font-weight: 700; } .selected-note { color: var(--color-muted); font-size: 12px; } @media (max-width: 650px) { .page-heading { align-items: start; flex-direction: column; } h1 { font-size: 32px; } }
</style>
