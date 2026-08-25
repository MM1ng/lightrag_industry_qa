import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ChatView from '../../views/ChatView.vue'
import HighFrequencyPrompts from './HighFrequencyPrompts.vue'
import { useSessionStore } from '../../app/stores/session'

const result = { request_id: 'request-1', status: 'success', answer: '结论\n\n请先停机。', citations: [], claims: [], evidence: [], latency_ms: 19 }

beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })

describe('chat workbench', () => {
  it('emits one question from the high-frequency prompt groups', async () => {
    const wrapper = mount(HighFrequencyPrompts)
    expect(wrapper.text()).toContain('启动与停机')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('submit')).toHaveLength(1)
    expect(wrapper.emitted('submit')?.[0]).toEqual(['离心泵启动前需要检查哪些项目？'])
  })

  it('locks the composer while loading and renders the returned answer', async () => {
    let resolvePending!: (response: Response) => void
    const pending = new Promise<Response>((resolve) => { resolvePending = resolve })
    vi.spyOn(globalThis, 'fetch').mockReturnValue(pending)
    const store = useSessionStore(); store.selectKnowledgeBase('kb-1')
    const wrapper = mount(ChatView)
    await wrapper.get('textarea').setValue('泵为什么振动？')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    resolvePending(new Response(JSON.stringify(result), { status: 200 }))
    await vi.waitFor(() => expect(wrapper.text()).toContain('请先停机。'))
    expect(wrapper.text()).toContain('可执行回答')
  })

  it('renders insufficient evidence and preserves the retry question', async () => {
    const insufficient = { ...result, status: 'insufficient_evidence', answer: '当前手册没有足够证据。' }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(insufficient), { status: 200 }))
    const store = useSessionStore(); store.selectKnowledgeBase('kb-1')
    const wrapper = mount(ChatView)
    await wrapper.get('textarea').setValue('特殊问题')
    await wrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('证据不足'))
    expect(wrapper.text()).toContain('重新查询')
    expect(store.messages.find((message) => message.role === 'assistant')?.question).toBe('特殊问题')
  })
})
