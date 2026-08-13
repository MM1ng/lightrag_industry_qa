import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ApiClient } from '../../api/client'
import { useSessionStore } from '../../app/stores/session'
import GenerationsView from './GenerationsView.vue'

const generations = [{ id: 'g-1', knowledge_base_id: 'kb-1', generation: 'gen-001', status: 'ready', backend: 'nano', created_at: '2026-08-10T00:00:00Z' }]

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
  const store = useSessionStore(); store.selectKnowledgeBase('kb-1'); store.enterAdminMode('admin-secret')
})

describe('admin views', () => {
  it('sends admin bearer credentials', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(generations), { status: 200 }))
    await new ApiClient('', () => 'admin-secret').listGenerations('kb-1')
    const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers)
    expect(headers.get('Authorization')).toBe('Bearer admin-secret')
  })

  it('does not promote a Generation until the confirmation is accepted', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') return new Response(JSON.stringify({ status: 'promoted' }), { status: 200 })
      return new Response(JSON.stringify(generations), { status: 200 })
    })
    const wrapper = mount(GenerationsView, { attachTo: document.body })
    await vi.waitFor(() => expect(wrapper.text()).toContain('gen-001'))
    await wrapper.findAll('.actions button')[2].trigger('click')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).toContain('提升这个 Generation？')
    const confirmButton = [...document.body.querySelectorAll('button')].find((button) => button.textContent?.includes('确认 Promote'))
    confirmButton?.click()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' })
  })
})
