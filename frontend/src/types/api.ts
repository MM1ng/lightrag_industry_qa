export type ApiStatus =
  | 'success'
  | 'partial_answer'
  | 'insufficient_evidence'
  | 'safety_blocked'
  | 'clarification_required'
  | 'out_of_scope'
  | 'failed'

export interface ChatHistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export interface Citation {
  citation_id: string
  document_name: string
  page: number
  chunk_id?: string
  document_id?: string | null
  generation_id?: string | null
  evidence_id?: string | null
}

export interface Claim {
  claim_id: string
  text: string
  citation_ids: string[]
  evidence_ids?: string[]
}

export interface Evidence {
  evidence_id: string
  citation_id?: string | null
  document_name: string
  document_id?: string | null
  page: number
  chunk_id?: string
  generation_id?: string | null
  section_path?: string[]
  excerpt: string
  source_type?: string
  context_role?: string
  supports_claim_ids?: string[]
  completion_reason?: string | null
  relevance_label?: string
}

export interface QueryResult {
  request_id: string
  status: ApiStatus
  answer: string
  citations: Citation[]
  claims: Claim[]
  evidence: Evidence[]
  partial_reason?: string | null
  latency_ms: number
}

export interface KnowledgeBase {
  id: string
  name: string
  description?: string | null
  status: string
  document_count: number
  active_document_count?: number
  chunk_count?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface KnowledgeBaseDetail extends KnowledgeBase {
  parser_name?: string
  parser_version?: string | null
  chunking_strategy?: string
  embedding_model?: string
  vector_backend?: string
  active_vector_generation?: string | null
  entity_count?: number | null
  relation_count?: number | null
  last_error?: string | null
}

export interface DocumentSummary {
  id: string
  knowledge_base_id: string
  original_file_name: string
  file_hash: string
  file_size: number
  version: number
  status: string
  parse_status: string
  index_status: string
  page_count?: number | null
  parent_chunk_count?: number
  child_chunk_count?: number
  last_error?: string | null
}

export interface UpdateJob {
  job_id: string
  operation: string
  document_id?: string | null
  status: string
  current_stage?: string | null
  retry_count: number
  error_code?: string | null
  sanitized_error_message?: string | null
  created_at?: string | null
  finished_at?: string | null
}

export interface GenerationSummary {
  id: string
  knowledge_base_id: string
  generation: string
  status: string
  backend: string
  created_at?: string | null
  activated_at?: string | null
  last_error?: string | null
}

export interface FeedbackInput {
  request_id: string
  feedback_type: 'helpful' | 'unhelpful'
  feedback_reason?: string
  feedback_comment?: string
}

export interface GraphNode {
  id: string
  label: string
  type: string
  x: number
  y: number
  degree?: number
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  label?: string
}

export interface GraphPayload {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: { node_count: number; edge_count: number; mode?: string; query?: string | null }
}

export interface ApiErrorShape {
  code: string
  message: string
  retryable: boolean
}
