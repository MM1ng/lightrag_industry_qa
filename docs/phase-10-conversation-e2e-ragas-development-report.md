# Phase 10 Conversation E2E Ragas Development Report

Status: **BLOCKED**
Ragas: `0.3.9`
Cases: `18`

## Dataset and judge

- Dataset fingerprint: `{'source_path': 'data\\evaluation\\conversation_retrieval_development.jsonl', 'raw_sha256': 'd326aff3a96a025c79a36a6566e37015419c3c7ab9763a783b7da2963bde4094', 'semantic_sha256': '59dc0e2658a9445cca7901a8e9688f631649e1c5cb93f1cae1cdb5125e5a3579', 'case_count': 18, 'case_ids': ['conv-s001', 'conv-s002', 'conv-s003', 'conv-s004', 'conv-s005', 'conv-s006', 'conv-s007', 'conv-s008', 'conv-s009', 'conv-s010', 'conv-s011', 'conv-d001', 'conv-d002', 'conv-d003', 'conv-d004', 'conv-d005', 'conv-d006', 'conv-d007']}`
- Judge config: `{'ragas_version': '0.3.9', 'metrics': ['Faithfulness', 'ResponseRelevancy'], 'judge_provider': 'openai-compatible-dashscope', 'judge_model': 'qwen-plus-2025-07-28', 'embedding_provider': 'openai-compatible-dashscope', 'embedding_model': 'text-embedding-v4', 'temperature': 0.0, 'seed': None, 'timeout_seconds': 60, 'retry': 2, 'max_concurrency': 1}`

## BASELINE → CANDIDATE

- hit_recall_at_5: `0.6111111111111112` → `0.9444444444444444`
- mrr_at_5: `0.449074074074074` → `0.7592592592592593`
- supporting_recall: `{'status': 'metric_unavailable', 'reason': 'frozen Development dataset has no expected answer-point support labels', 'value': None}` → `{'status': 'metric_unavailable', 'reason': 'frozen Development dataset has no expected answer-point support labels', 'value': None}`
- false_rejection_rate: `{'status': 'available', 'value': 0.1111111111111111, 'denominator': 18}` → `{'status': 'available', 'value': 0.05555555555555555, 'denominator': 18}`
- question_level_citation_accuracy: `{'status': 'metric_unavailable', 'reason': 'frozen Development dataset has no question-level citation gold', 'value': None}` → `{'status': 'metric_unavailable', 'reason': 'frozen Development dataset has no question-level citation gold', 'value': None}`
- unsupported_answer_rate: `{'status': 'available', 'value': 0.8333333333333334, 'denominator': 18}` → `{'status': 'available', 'value': 0.7777777777777778, 'denominator': 18}`
- expected_answer_coverage: `{'status': 'metric_unavailable', 'reason': 'frozen Development dataset has no trusted reference answer', 'value': None}` → `{'status': 'metric_unavailable', 'reason': 'frozen Development dataset has no trusted reference answer', 'value': None}`
- Faithfulness: `{'mean': None, 'median': None, 'baseline_mean': None, 'candidate_mean': None, 'case_level_delta': []}`
- Response Relevancy: `{'mean': None, 'median': None, 'baseline_mean': None, 'candidate_mean': None, 'case_level_delta': []}`
- Semantic execution: `{'status': 'BLOCKED', 'reason': "InternalServerError: Error code: 500 - {'error': {'message': '<500> InternalError.Algo: An error occurred in model serving, error message is: [Inference engine abort. Finish reason: [UNKNOWN].]', 'type': 'internal_server_error', 'param': None, 'code': 'internal_server_error'}, 'id': 'chatcmpl-17c85a05-3657-90e0-9bb4-9ac3ca62b081', 'request_id': '17c85a05-3657-90e0-9bb4-9ac3ca62b081'}", 'formal_case_scoring_executed': False}`
- Improved / unchanged / regressed: `{'improved': 9, 'unchanged': 9, 'regressed': 0}`
- Judge errors: `1`
- Failure layers: `{}`

## Next phase recommendation

Do not start the next phase from this report; review the paired guardrails and semantic case-level deltas first.
