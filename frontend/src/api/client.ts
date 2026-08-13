import type {
  ApiErrorShape,
  ChatHistoryItem,
  FeedbackInput,
  GraphPayload,
  DocumentSummary,
  GenerationSummary,
  KnowledgeBase,
  KnowledgeBaseDetail,
  QueryResult,
  UpdateJob,
} from '../types/api'

export class ApiClientError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly status: number

  constructor(error: ApiErrorShape, status: number) {
    super(error.message)
    this.name = 'ApiClientError'
    this.code = error.code
    this.retryable = error.retryable
    this.status = status
  }
}

type TokenProvider = () => string | null

export class ApiClient {
  constructor(
    private readonly baseUrl = '',
    private readonly tokenProvider: TokenProvider = () => null,
  ) {}

  async listKnowledgeBases(): Promise<KnowledgeBase[]> {
    const payload = await this.request<{ items?: KnowledgeBase[] } | KnowledgeBase[]>('/v1/knowledge-bases')
    return Array.isArray(payload) ? payload : payload.items ?? []
  }

  async queryKnowledgeBase(kbId: string, question: string, history: ChatHistoryItem[]): Promise<QueryResult> {
    const result = await this.request<QueryResult>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/query`, {
      method: 'POST',
      body: JSON.stringify({ query: question, history }),
    })
    return projectPublicQueryResult(result)
  }

  async submitFeedback(input: FeedbackInput): Promise<void> {
    await this.request('/v1/feedback', { method: 'POST', body: JSON.stringify(input) })
  }

  async verifyAdminAccess(): Promise<void> {
    await this.request('/v1/admin/feedback/metrics')
  }

  async getKnowledgeBase(kbId: string): Promise<KnowledgeBaseDetail> {
    return this.request<KnowledgeBaseDetail>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}`)
  }

  async listDocuments(kbId: string): Promise<DocumentSummary[]> {
    const payload = await this.request<{ items?: DocumentSummary[] }>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents`)
    return payload.items ?? []
  }

  async uploadDocument(kbId: string, file: File): Promise<unknown> {
    const body = new FormData(); body.append('file', file)
    return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents`, { method: 'POST', body })
  }

  async getDocument(kbId: string, documentId: string): Promise<DocumentSummary> {
    return this.request<DocumentSummary>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(documentId)}`)
  }

  async replaceDocument(kbId: string, documentId: string, file: File): Promise<unknown> {
    const body = new FormData(); body.append('file', file)
    return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(documentId)}`, { method: 'PUT', body })
  }

  async deleteDocument(kbId: string, documentId: string): Promise<unknown> {
    return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' })
  }

  async listUpdateJobs(kbId: string): Promise<UpdateJob[]> {
    const payload = await this.request<{ items?: UpdateJob[] }>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/update-jobs`)
    return payload.items ?? []
  }

  async listGenerations(kbId: string): Promise<GenerationSummary[]> {
    return this.request<GenerationSummary[]>(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations`)
  }

  async validateGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/validate`, { method: 'POST' }) }
  async promoteGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/promote`, { method: 'POST' }) }
  async rollbackGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/rollback`, { method: 'POST' }) }
  async diffGeneration(kbId: string, generationId: string): Promise<unknown> { return this.request(`/v1/knowledge-bases/${encodeURIComponent(kbId)}/generations/${encodeURIComponent(generationId)}/diff`) }

  async getGraphOverview(limit = 50): Promise<GraphPayload> {
    return this.request<GraphPayload>(`/v1/graph/overview?limit=${limit}`)
  }

  async getGraphNeighborhood(query: string, hops: 1 | 2): Promise<GraphPayload> {
    return this.request<GraphPayload>(`/v1/graph/neighborhood?query=${encodeURIComponent(query)}&hops=${hops}`)
  }

  async request<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    const token = this.tokenProvider()
    if (token) headers.set('Authorization', `Bearer ${token}`)

    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, { ...init, headers })
    } catch {
      throw new ApiClientError({ code: 'network_error', message: '服务暂时不可达，请检查服务状态后重试。', retryable: true }, 0)
    }

    if (!response.ok) {
      let error: Partial<ApiErrorShape> = {}
      try {
        const body = await response.json() as { code?: string; message?: string; detail?: { code?: string; message?: string } }
        error = body.detail ?? body
      } catch {
        // Deliberately ignore non-JSON bodies so raw backend text cannot reach the UI.
      }
      throw new ApiClientError({
        code: error.code || `http_${response.status}`,
        message: error.message || (response.status >= 500 ? '服务暂时不可用，请稍后重试。' : '请求未获授权或无法完成。'),
        retryable: response.status === 408 || response.status === 429 || response.status >= 500,
      }, response.status)
    }

    if (response.status === 204) return undefined as T
    return await response.json() as T
  }
}

export const apiClient = new ApiClient(import.meta.env.VITE_API_BASE_URL ?? '')

function projectPublicQueryResult(result: QueryResult): QueryResult {
  return {
    request_id: result.request_id,
    status: result.status,
    answer: result.answer,
    citations: result.citations.map(({ citation_id, document_name, page, evidence_id }) => ({ citation_id, document_name, page, evidence_id })),
    claims: result.claims.map(({ claim_id, text, citation_ids, evidence_ids }) => ({ claim_id, text, citation_ids, evidence_ids })),
    evidence: result.evidence.map(({ evidence_id, citation_id, document_name, page, section_path, excerpt, relevance_label }) => ({ evidence_id, citation_id, document_name, page, section_path, excerpt, relevance_label })),
    partial_reason: result.partial_reason,
    latency_ms: result.latency_ms,
  }
}
