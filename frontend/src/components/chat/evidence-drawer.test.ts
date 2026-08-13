import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import EvidenceDrawer from './EvidenceDrawer.vue'

const evidence = [
  { evidence_id: 'e-1', citation_id: 'c-1', document_name: '离心泵运行手册.pdf', page: 12, chunk_id: 'hidden', section_path: ['启动'], excerpt: '确认入口阀门已打开。', relevance_label: '核心依据' },
  { evidence_id: 'e-2', citation_id: 'c-2', document_name: '离心泵运行手册.pdf', page: 13, chunk_id: 'hidden-2', excerpt: '观察压力表。', relevance_label: '补充依据' },
]

describe('EvidenceDrawer', () => {
  it('opens with the selected evidence and can switch citations', async () => {
    const wrapper = mount(EvidenceDrawer, { props: { visible: true, evidence, selectedCitationId: 'c-1' }, attachTo: document.body })
    expect(document.body.textContent).toContain('确认入口阀门已打开。')
    await document.querySelectorAll('.evidence-switch button')[1].dispatchEvent(new MouseEvent('click'))
    expect(wrapper.emitted('select')?.[0]).toEqual(['c-2'])
  })

  it('closes on Escape and explains empty evidence', async () => {
    const wrapper = mount(EvidenceDrawer, { props: { visible: true, evidence: [], selectedCitationId: null }, attachTo: document.body })
    expect(document.body.textContent).toContain('没有可展示的证据')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
