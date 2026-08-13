import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSessionStore } from './session'

beforeEach(() => setActivePinia(createPinia()))

describe('session store', () => {
  it('enters and leaves admin mode without persistence', () => {
    const store = useSessionStore()
    store.enterAdminMode('secret')
    expect(store.hasAdminAccess).toBe(true)
    expect(store.adminToken).toBe('secret')
    store.leaveAdminMode()
    expect(store.hasAdminAccess).toBe(false)
    expect(store.adminToken).toBeNull()
  })

  it('clears messages while preserving the selected knowledge base', () => {
    const store = useSessionStore()
    store.selectKnowledgeBase('kb-1')
    store.addUserMessage('如何停机？')
    store.clearChat()
    expect(store.messages).toHaveLength(0)
    expect(store.activeKnowledgeBaseId).toBe('kb-1')
  })
})
