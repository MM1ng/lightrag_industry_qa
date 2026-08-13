<script setup lang="ts">
const emit = defineEmits<{ submit: [question: string] }>()
const groups = [
  { title: '启动与停机', code: 'START / STOP', questions: ['离心泵启动前需要检查哪些项目？', '离心泵正常停机的操作步骤是什么？'] },
  { title: '故障排查', code: 'FAULT FINDING', questions: ['离心泵运行中振动突然增大怎么排查？', '离心泵不上液或流量不足有哪些原因？'] },
  { title: '维修安全', code: 'MAINTENANCE SAFETY', questions: ['离心泵检修前如何执行断电和泄压？', '拆卸泵体前有哪些安全注意事项？'] },
]
</script>

<template>
  <div class="prompt-groups">
    <section v-for="group in groups" :key="group.title" class="prompt-group">
      <div class="group-heading"><span class="group-rule" /><div><span class="group-code">{{ group.code }}</span><h3>{{ group.title }}</h3></div></div>
      <button v-for="question in group.questions" :key="question" class="prompt" @click="emit('submit', question)"><span>{{ question }}</span><b>↗</b></button>
    </section>
  </div>
</template>

<style scoped>
.prompt-groups { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; } .prompt-group { min-width: 0; padding: 18px; border: 1px solid var(--color-line); border-radius: var(--radius-panel); background: var(--color-surface); } .group-heading { display: flex; gap: 10px; margin-bottom: 16px; } .group-rule { width: 3px; min-height: 38px; background: var(--color-cobalt); } .prompt-group:nth-child(2) .group-rule { background: var(--color-amber); } .prompt-group:nth-child(3) .group-rule { background: var(--color-success); } .group-code { color: var(--color-muted); font: 10px Bahnschrift, monospace; letter-spacing: .09em; } h3 { margin: 4px 0 0; font-size: 16px; } .prompt { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; min-height: 54px; margin-top: 8px; padding: 9px 10px; border: 1px solid transparent; border-radius: 9px; color: var(--color-ink); background: #f6f8fa; text-align: left; line-height: 1.5; } .prompt:hover { border-color: #b8cdf5; color: var(--color-cobalt); background: var(--color-cobalt-soft); } .prompt b { color: var(--color-muted); font-size: 16px; font-weight: 400; } @media (max-width: 1050px) { .prompt-groups { grid-template-columns: 1fr; } .prompt-group { display: grid; grid-template-columns: minmax(170px, .35fr) 1fr 1fr; gap: 8px; align-items: start; } .group-heading { margin: 0; } .prompt { margin: 0; } } @media (max-width: 640px) { .prompt-group { display: block; } .group-heading { margin-bottom: 12px; } .prompt { margin-top: 8px; } }
</style>
