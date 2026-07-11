---
source: framework
---

## AI / LLM Apps

- Hard per-session and per-user token budget with gateway-level enforcement.
- Set max_tokens on every LLM call. Auto-compact at 85-90% context window.
- Never pass unsanitized user input into system prompts. Separate with delimiters.
- Infrastructure timeouts must exceed expected LLM response times. Streaming idle timeout with retry.
