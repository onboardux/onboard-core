# langgraph-support — the S1.5 AI-deployment fixture

A LangGraph-shaped support agent, written to exercise `05` S1.5's extractor pack
and **nothing else**. It is read, never run: no package metadata, no entry point,
no test suite, and nothing here imports anything it does not declare.

Its shape is the sprint's own list — *"prompts in three locations, a floating
pin, four tools, a retrieval config, an eval set"* — plus the one case the
outside-VCS discipline exists for.

| Where behaviour lives | Files | What it exercises |
|---|---|---|
| Prompt **files** | `prompts/answer_grounded.md`, `prompts/triage_classifier.md` | `prompt` under `namespace='file'`, readable, in version control |
| Prompt **template literal** | `app/prompts.py` | the third `ai.prompts` source: a template declared in code |
| Prompt **console** | `app/prompts.py` (`ConsolePrompt("support-greeting")`) | `namespace='console'`, **outside VCS**, **opaque** — the body is not in this tree and nothing invents one |
| Prompt **db** | `app/prompts.py` (`DbPrompt("faq_answer")`) | `namespace='db'`, outside VCS, opaque |
| Model pins | `app/graph.py` | one **pinned** (dated id), one **floating** (`-latest`), one **runtime-resolved** from the environment |
| Tools | `app/tools.py` | four `tool_schema` identities under `namespace='langgraph'` |
| Retrieval | `config/retrieval.yaml` | `retrieval_config` under `namespace='pgvector'`, one identity per parameter |
| Eval set | `evals/support_quality.yaml` | `config_key` under `namespace='evalset'` |
| Environment | `deploy/.env.example` | outside-VCS configuration and one secret **reference** |

**The values in `deploy/.env.example` are placeholders and canaries.** Nothing
here is a live credential; the two credential-shaped keys exist so the
planted-secret property suite has something to prove it never emits.
