# AnythingLLM + Historian shared-memory integration

Historian remains the read-only source of project memory.
AnythingLLM is the operator cockpit and agent runtime.

The integration uses two local custom agent skills:

- `historian_evidence`: fetches bounded canonical Historian evidence for a question.
- `historian_query`: asks Historian's grounded Qwen reasoner for an answer.

Both skills talk only to the loopback Historian service:

- `GET  http://127.0.0.1:8765/v1/health`
- `POST http://127.0.0.1:8765/v1/evidence`
- `POST http://127.0.0.1:8765/v1/query`

AnythingLLM is configured to reason over the evidence it receives.
Historian does not receive write authority from AnythingLLM.

When final answers materially rely on Historian evidence, cite only the stable
Historian record IDs that materially support the answer. Do not cite every
retrieved record.

When `historian_query` is used, the agent must continue after the tool result
and render a final assistant response. It must not stop at tool-call syntax or
dump raw tool output to the user.

The local skill sources live in:

- `integrations/anythingllm/skills/historian_evidence/`
- `integrations/anythingllm/skills/historian_query/`

Use `scripts/sync_anythingllm_skills.sh` to copy them into the Desktop storage area:

```bash
scripts/sync_anythingllm_skills.sh ~/.config/anythingllm-desktop/storage/plugins/agent-skills
```

The sync script never deletes a destination directory. It replaces only the files owned by the two Historian skills (`handler.js` and `plugin.json`) and refuses to touch a destination skill directory that does not carry the matching Historian `hubId` in its `plugin.json`, so unrelated operator content is preserved.

The Desktop storage location on Linux is:

- `~/.config/anythingllm-desktop/storage/`

The agent-skill install location used by the backend is:

- `~/.config/anythingllm-desktop/storage/plugins/agent-skills/`

Historian evidence stays authoritative in Historian; AnythingLLM workspace memory is not canonical project history.

## Workspace instruction boundary

The AnythingLLM workspace prompt should reinforce:

- Historian is the authoritative read-only memory layer.
- `historian_evidence` is the primary shared-memory tool for historical context.
- `historian_query` is secondary when a grounded answer is preferable.
- final answers should cite stable Historian IDs when the answer materially relies on Historian evidence.
- the assistant should continue after `historian_query` returns and produce a final grounded response.
- AnythingLLM workspace memory is not authoritative project history.
