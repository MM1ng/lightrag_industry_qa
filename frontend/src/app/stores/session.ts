import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { ApiStatus, QueryResult } from '../../types/api'

export interface UserMessage {
  id: string
  role: 'user'
  content: string
  createdAt: string
}

export interface AssistantMessage {
  id: string
  role: 'assistant'
  question: string
  result: QueryResult
  createdAt: string
  feedbackSubmitted?: boolean
}

export type ChatMessage = UserMessage | AssistantMessage

export const useSessionStore = defineStore('session', () => {
  const activeKnowledgeBaseId = ref<string | null>(null)
  const role = ref<'user' | 'admin'>('user')
  const adminToken = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const chatStatus = ref<'idle' | 'loading' | 'error'>('idle')

  const hasAdminAccess = computed(() => role.value === 'admin' && Boolean(adminToken.value))

  function enterAdminMode(token: string) {
    adminToken.value = token
    role.value = 'admin'
  }

  function leaveAdminMode() {
    adminToken.value = null
    role.value = 'user'
  }

  function selectKnowledgeBase(id: string) {
    activeKnowledgeBaseId.value = id
  }

  function clearChat() {
    messages.value = []
    chatStatus.value = 'idle'
  }

  function addUserMessage(content: string): UserMessage {
    const message: UserMessage = { id: makeId(), role: 'user', content, createdAt: new Date().toISOString() }
    messages.value.push(message)
    return message
  }

  function addAssistantMessage(question: string, result: QueryResult): AssistantMessage {
    const message: AssistantMessage = { id: makeId(), role: 'assistant', question, result, createdAt: new Date().toISOString() }
    messages.value.push(message)
    return message
  }

  function markFeedbackSubmitted(messageId: string) {
    const message = messages.value.find((item): item is AssistantMessage => item.id === messageId && item.role === 'assistant')
    if (message) message.feedbackSubmitted = true
  }

  function historyForQuery(): { role: 'user' | 'assistant'; content: string }[] {
    return messages.value
      .filter((message) => message.role === 'user' || message.result.status !== ('failed' as ApiStatus))
      .slice(-6)
      .map((message) => ({ role: message.role, content: message.role === 'user' ? message.content : message.result.answer }))
  }

  return { activeKnowledgeBaseId, role, adminToken, messages, chatStatus, hasAdminAccess, enterAdminMode, leaveAdminMode, selectKnowledgeBase, clearChat, addUserMessage, addAssistantMessage, markFeedbackSubmitted, historyForQuery }
})

function makeId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
}
