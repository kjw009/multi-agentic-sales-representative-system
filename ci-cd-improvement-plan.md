# CI/CD Improvement Plan

## Current state

`buildspec.yml` has a single `build` phase that immediately deploys to EC2 via SSM —
no lint, type checks, or tests run before code hits production.

---

## Proposed additions

### 1. `buildspec.yml` — add `install` and `pre_build` phases before the deploy

**`install` phase** — sets up runtimes and installs dependencies:
- Runtime: `aws/codebuild/standard:7.0` (Python 3.12, Node 20, Docker)
- `pip install uv` → `uv sync --extra dev`
- `npm --prefix apps/web ci`

**`pre_build` phase** (`on-failure: ABORT` — deploy never runs if any step fails):

| Step | Command |
|------|---------|
| Lint | `ruff check .` |
| Format check | `ruff format --check .` |
| Type check | `mypy apps packages workers` |
| Tests | `pytest --cov` (all mock-based, no live DB needed) |
| Docker build | `docker build .` (validates Dockerfile; requires privileged mode) |
| Frontend build | `npm --prefix apps/web run build` (catches TypeScript errors) |

**`build` phase** — unchanged SSM deploy to EC2.

### 2. CodeBuild project settings required

- **Privileged mode** enabled (for `docker build`)
- **Environment variables**: `AWS_REGION`, `EC2_INSTANCE_ID` (already in use)
- **S3 cache bucket** (optional) — enables caching of `~/.cache/uv` and `apps/web/node_modules`
  to speed up subsequent builds

### 3. Makefile additions

- `make mypy` — runs `mypy apps packages workers`
- `make ci` — runs `fmt → lint → mypy → test` in sequence; mirrors `pre_build` locally

### 4. Test coverage expansion

#### `tests/test_pricing_agent.py`
- Unit tests for `adaptive_search` logic
- Mocks for eBay Browse API interactions
- Validation of price prediction outputs (Agent 2)

#### `tests/test_intake_graph.py`
- Edge cases for multi-turn conversations
- Failed tool execution paths

#### `tests/test_integration_pipeline.py` (new file)
- End-to-end test of the `intake → pricing → publisher` flow using mocked external services
