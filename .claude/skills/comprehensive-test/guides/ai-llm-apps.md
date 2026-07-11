# Test: AI / LLM Apps

## OUTPUT TESTING
Test properties, not specific content (non-deterministic):
- Property assertions: format(JSON/XML), keyword presence, length bounds, latency
- 2-layer eval: automated metrics (BERTScore/ROUGE) + human reviewers
- Hallucination: metamorphic testing — perturb prompt Nx, flag statistical divergence
- RAG-grounded <2% hallucination vs 15-52% ungrounded
- Log prod failures → regression dataset ("data flywheel")
- Mock LLM for reproducible CI; stochastic tests with statistical thresholds (95/100 passes)

## EVAL FRAMEWORKS
| Tool | Best For |
|---|---|
| **Promptfoo** | CLI CI/CD gates, 50+ red-team scans, YAML |
| **DeepEval** | Pytest-native, G-Eval LLM-as-judge |
| **RAGAS** | RAG faithfulness/context precision/recall |
| **Giskard** | 40+ vulnerability probes, multi-turn attacks |

Run `promptfoo eval` on every PR → block merge on score drop. Separate retrieval metrics from generation metrics.

## RAG (3 Pillars)
1. **Retrieval:** context precision (relevant?) + recall (complete?) + NDCG (ranking)
2. **Faithfulness:** claims supported by context (hallucination metric)
3. **Completeness:** response addresses query given context

Test retrieval+generation independently · measure context utilization · test chunk boundaries · queries spanning multiple chunks · monitor retrieval drift by category

## AGENT TESTING
95% single-turn → 14% failure in multi-turn flows.

- Trace every step: tool calls, reasoning, state transitions (OpenTelemetry)
- Test trajectory, not just outcome: right tools in right order
- Separate plan eval from execution eval
- Mock tool responses for CI determinism
- Verifier Agents in prod: inspect CoT+tool outputs real-time
- Error recovery: inject tool failures → verify graceful recovery
- Multi-step regression: 3+ tool call test cases (most likely to regress)

## TOKEN & COST
- `max_tokens` on every call; test coherence when truncated
- Budget in prompts ("3 short bullets") + API cap
- Per-user daily token limit; test degradation path
- Circuit breaker: spend>threshold → cheaper model or cached fallback
- Track cost per eval run; CI alert on budget exceed
- Test with prod-representative context sizes

## STREAMING
- Validate SSE: `text/event-stream`, `no-cache`, `data:<payload>\n\n`
- Incomplete chunk: partial lines across TCP segments → client buffers correctly
- Drop mid-stream / timeout / client abort → partial response handled
- TTFT (time-to-first-token) SLA
- Concatenated chunks = valid complete response
- Load test SSE (Gatling); cross-model (OpenAI/Anthropic/Gemini chunk formats differ)

## SAFETY (OWASP LLM Top 10)
1.Prompt Injection 2.Info Disclosure 3.Supply Chain 4.Data Poisoning 5.Improper Output 6.Excessive Agency 7.System Prompt Leakage 8.Embedding Weakness 9.Misinformation 10.Unbounded Consumption

- Red team in CI: Promptfoo 50+ / Giskard 40+ probes on every deploy
- Test injection at EVERY input: user input, RAG docs, tool outputs, external data
- System prompts override user messages — test user can't override constraints
- Output to downstream: test XSS/SQLi/command injection in LLM output
- System prompt extraction attempts → verify no leak
- Excessive agency: LLM can't exceed granted tool permissions
- Defense in depth: input validation + instruction hierarchy + output monitoring + rate limiting

## EMBEDDINGS
- Benchmark MTEB + domain-specific test set with human-judged relevance
- NDCG + Top-K per query category
- Similar queries → high cosine sim; unrelated → low
- Dimensionality trade-offs; embedding drift after model update
- Cross-model comparison on your data before committing

## FALLBACK
- Simulate 429/5xx/timeout/auth fail → auto fallback
- Failover latency SLA
- Fallback model equivalent capability + quality parity (eval suite both)
- Circuit breaker: N failures → open → cooldown → close
- Cost-aware routing: simple→cheap, complex→expensive
- Pull primary key mid-load-test → verify transition under load

## CHATBOT
- Conversation completeness: extract intentions, verify each satisfied
- Knowledge retention across turns
- Role/persona adherence over extended conversations
- Context window limits → graceful degradation (summarization)
- Adversarial: topic change, contradiction, ambiguity, manipulation
- Turn-level metrics: each turn evaluated for relevance/coherence/groundedness

## LLM-AS-JUDGE
Calibrate vs human · randomize option order (position bias) · instruct ignore length (length bias) · different model family (family bias) · rubric-based · ensemble+minority-veto
