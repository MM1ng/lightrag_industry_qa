<script setup lang="ts">
import { ref } from 'vue'
const props = defineProps<{ submitted?: boolean }>()
const emit = defineEmits<{ submit: [payload: { type: 'helpful' | 'unhelpful'; reason?: string; comment?: string }] }>()
const expanded = ref(false); const reason = ref('answer_incomplete'); const comment = ref('')
function send(type: 'helpful' | 'unhelpful') { if (type === 'helpful') emit('submit', { type }); else expanded.value = true }
function submitNegative() { emit('submit', { type: 'unhelpful', reason: reason.value, comment: comment.value.trim() || undefined }); expanded.value = false }
</script>

<template>
  <div v-if="!props.submitted" class="feedback"><span>这条回答有帮助吗？</span><button @click="send('helpful')">有帮助</button><button @click="send('unhelpful')">没帮助</button><div v-if="expanded" class="feedback-form"><label>主要问题<select v-model="reason"><option value="answer_incorrect">回答不正确</option><option value="citation_unsupported">引用无法支持</option><option value="answer_incomplete">回答不完整</option><option value="answer_not_found">没有找到答案</option><option value="unsafe_or_unnecessary_answer">安全或必要性问题</option><option value="other">其他</option></select></label><label>补充说明（可选）<textarea v-model="comment" rows="2" /></label><button class="feedback-submit" @click="submitNegative">提交反馈</button></div></div><span v-else class="feedback-done">已记录反馈，谢谢。</span>
</template>

<style scoped>
.feedback { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-top: 18px; color: var(--color-muted); font-size: 12px; } .feedback > button { min-height: 32px; padding: 0 9px; border: 1px solid var(--color-line); border-radius: 7px; color: var(--color-muted); background: white; font-size: 12px; } .feedback > button:hover { color: var(--color-cobalt); border-color: var(--color-cobalt); } .feedback-form { display: grid; gap: 10px; width: 100%; margin-top: 8px; padding: 12px; border-left: 2px solid var(--color-amber); background: var(--color-amber-soft); } .feedback-form label { display: grid; gap: 5px; color: var(--color-ink); font-weight: 700; } select, textarea { min-height: 36px; padding: 7px; border: 1px solid #e1c57f; border-radius: 7px; background: white; } .feedback-submit { justify-self: start; min-height: 36px; padding: 0 12px; border: 0; border-radius: 7px; color: white; background: var(--color-amber); font-weight: 700; } .feedback-done { display: block; margin-top: 18px; color: var(--color-success); font-size: 12px; }
</style>
