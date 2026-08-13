import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import GraphView from '../../views/GraphView.vue'

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})

describe('GraphView', () => {
  it('loads the read-only overview and exposes entity search controls', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ nodes: [{ id: 'n1', label: '离心泵', type: '设备', x: 0, y: 0, degree: 1 }], edges: [], stats: { node_count: 1, edge_count: 0, mode: 'overview', query: null } }), { status: 200 }))
    const wrapper = mount(GraphView, { global: { stubs: { VueFlow: { template: '<div><slot /></div>' } } } })
    await vi.waitFor(() => expect(wrapper.text()).toContain('离心泵'))
    expect(wrapper.text()).toContain('常用实体')
    expect(wrapper.text()).toContain('只读')
  })

  it('embeds the native LightRAG graph document', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ nodes: [], edges: [], stats: { node_count: 0, edge_count: 0, mode: 'overview', query: null } }), { status: 200 }))
    const wrapper = mount(GraphView, { global: { stubs: { VueFlow: { template: '<div><slot /></div>' } } } })
    await vi.waitFor(() => expect(wrapper.find('iframe').exists()).toBe(true))
    expect(wrapper.find('iframe').attributes('src')).toContain('/v1/graph/native')
  })
})
