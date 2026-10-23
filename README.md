# DevOps Repository Auditing Agent

AI-powered agent that autonomously inspects **public GitHub repositories** for DevOps best-practice violations and writes an evidence-based JSON report with actionable recommendations.

This project implements a custom **Observe → Reason → Select Tool → Execute Tool → Interpret Results → Report** loop in plain Python. It does **not** use LangChain, LangGraph, n8n, or other agent orchestration frameworks.

---

## Features

- Accepts a public GitHub repository URL and clones it read-only for inspection
- Uses an LLM to **dynamically select follow-up tools** based on observations (not a fixed checklist + summary)
- Required tools: `list_files`, `read_file`, `search_code`, `git_log`
- Additional tools: `git_status`, `list_ci_workflows`, `submit_report`
- Emits the assignment-compliant JSON report (empty `violations` array when none found)
- Never modifies the inspected repository; redacts obvious secrets in tool output
- Graceful handling of invalid URLs, clone failures, API errors, and timeouts

---

## Repository layout

| Path | Purpose |
|------|---------|
| `agent.py` | CLI entry point (`--repo`, `--output`) |
| `agent_loop.py` | Explicit LLM ↔ tool control loop |
| `tools.py` | Inspection tool implementations + tool schemas |
| `llm_client.py` | Thin OpenAI / Google Gemini / Anthropic SDK wrappers |
| `prompts.py` | System prompt and audit instructions |
| `report.py` | JSON report validation & serialization |
| `repo_manager.py` | URL normalization, shallow clone, cleanup |
| `requirements.txt` | Installable dependencies |
| `.env.example` | Required environment variable names |
| `sample_report.json` | Example valid agent output |
| `tests/` | Automated tests for tools, report schema, and agent loop |

---

## Architecture

```
┌────────────┐     clone      ┌──────────────┐
│  agent.py  │ ─────────────► │ local clone  │
└─────┬──────┘                └──────┬───────┘
      │                              │
      ▼                              ▼
┌─────────────┐   tool calls   ┌─────────────┐
│ agent_loop  │ ◄────────────► │   tools.py  │
│  (control)  │                └─────────────┘
└──────┬──────┘
       │ reason / select tool
       ▼
┌─────────────┐
│ llm_client  │  (OpenAI, Google Gemini, or Anthropic SDK)
└─────────────┘
       │
       ▼
┌─────────────┐
│ report.json │  validated schema
└─────────────┘
```

### Agent cycle

1. **Observe** — start with repository URL + clone path; early tools reveal structure
2. **Reason** — LLM interprets prior tool results against DevOps inspection categories
3. **Select tool** — model chooses the next tool(s) via native function/tool calling
4. **Execute** — `ToolRegistry` runs the tool against the local clone (no target mutation)
5. **Interpret** — tool JSON is appended to the conversation for the next reason step
6. **Report** — model calls `submit_report` with structured violations (or `[]`)

Precision-oriented rules are encoded in the system prompt: do not treat absent optional files as defects when equivalents exist; do not invent remote branch-protection settings; never echo secret values.

---

## Prerequisites

- Python **3.10+**
- **Git** on `PATH` (used to shallow-clone public repositories)
- An LLM API key:
  - **Google Gemini** (`GOOGLE_API_KEY` / `GEMINI_API_KEY`) — recommended default, or
  - OpenAI (`OPENAI_API_KEY`), or
  - Anthropic (`ANTHROPIC_API_KEY`), or
  - Any OpenAI-compatible endpoint via `OPENAI_BASE_URL`

---

## Installation

```bash
git clone <this-repository-url>
cd <this-repository-directory>

python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set your credentials (never commit `.env`):

```env
LLM_PROVIDER=google
LLM_MODEL=gemini-3.5-flash-lite
GOOGLE_API_KEY=your-google-api-key-here
```

Create a Gemini key at [Google AI Studio](https://aistudio.google.com/apikey). The project linked to the key must be allowed to call `generateContent` (a key that can only list models is not enough).

OpenAI example:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
```

Anthropic example:

```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-latest
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Execution

Required interface:

```bash
python agent.py --repo <github_repository_url> --output report.json
```

Examples:

```bash
python agent.py --repo https://github.com/pallets/flask --output report.json
python agent.py --repo https://github.com/psf/requests --output report.json --verbose
python agent.py --repo pallets/click --output out/report.json --trace trace.json
```

### CLI options

| Flag | Description |
|------|-------------|
| `--repo` | Public GitHub URL or `owner/repo` shorthand (**required**) |
| `--output` | Output JSON path (**required**) |
| `--verbose` | Print step / tool progress |
| `--max-steps` | Cap agent loop iterations (default `AGENT_MAX_STEPS` or 30) |
| `--trace` | Write tool-call trace JSON (useful for demonstrating genuine agent behavior) |
| `--keep-clone` | Keep temp clone for debugging |
| `--work-dir` | Custom directory for clones |

On success the process exits `0` and writes valid JSON. On fatal errors it exits nonzero with a useful message on stderr.

---

## Output contract

```json
{
  "repository": "https://github.com/example/demo-project",
  "violations": [
    {
      "rule_id": "SEC-001",
      "category": "security",
      "severity": "critical",
      "file": "config.py",
      "line": 17,
      "description": "Hardcoded API credential",
      "evidence": "Credential assigned directly in source code",
      "recommendation": "Use a secrets manager or environment variable"
    }
  ]
}
```

- Severities: `critical` | `high` | `medium` | `low`
- Use JSON `null` for inapplicable `file` / `line`
- Secrets are never reproduced in the report

See `sample_report.json` for a fuller authentic example.

### Inspection categories covered

Repository hygiene · Dependency management · Code quality · Testing · GitHub Actions / CI · Security · Configuration · Collaboration · Build and release

---

## Tests

```bash
pytest -q
```

Coverage includes:

- Tool behavior on a local fixture repository (`list_files`, `read_file`, `search_code`, `git_log`, …)
- Report schema validation / deduplication
- URL normalization
- Scripted LLM driving the real agent loop (tool selection → execute → `submit_report`) without network calls

---

## Safety & constraints

- Targets are public GitHub repositories only; the clone is deleted after the run unless `--keep-clone`
- Untrusted repository code is **not** executed (no `pytest`/`ruff` execution against the target)
- Path traversal outside the clone root is rejected
- Missing optional tooling is not automatically treated as a defect
- No hardcoded findings for specific repositories

---

## Grading / evaluation notes

This submission is designed for the course hidden-repository evaluation:

- The LLM **directs** investigation via iterative tool calls (inspectable with `--trace`)
- Findings must be evidence-backed to protect precision
- Healthy repositories should yield an empty or near-empty `violations` list

---

## Author / course

**Course:** MLOps & Production AI Engineering  
**Assignment 2:** Build an AI Agent for DevOps Repository Auditing  
**Language:** Python  

Submission channel should receive **only** this public GitHub repository URL. All setup and usage details are documented in this README.
