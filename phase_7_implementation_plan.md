# Phase 7 — Polish + Launch — Implementation Plan

> Companion to **`implementation_plan_v2.md`** § Phase 7. Read that first.
> **Goal:** take the working agent system from "functional" to "a product real
> sellers can sign up for, pay for, and trust" — UI polish, a guided onboarding
> flow, Stripe billing, a public demo, docs, and the ECS Fargate worker move.
>
> **Why Phase 7 before Phase 6:** the Phase 6 retraining loop produces nothing
> until real sellers are on the platform and items actually sell. Phase 7 is what
> brings sellers in — it is the prerequisite for Phase 6 to be worth building.
>
> **Status when this plan was written (2026-05-15):** Phases 0–5 complete.
> One Phase 7 item is already done — lint + pytest phases are in `buildspec.yml`.
> Remaining: UI polish, onboarding, Stripe, ECS Fargate, README, demo mode.

---

## 1. Current state & scope notes

Findings from the codebase that shape this plan:

- **The web app has no component library.** `apps/web/` is Next.js 15 (app
  router) + React 19 + Tailwind 3.4, ~1,500 lines of hand-rolled JSX across 5
  pages (`/login`, `/chat`, `/inbox`, `/settings`, `/` redirect). No
  `components/` directory, no shadcn/ui, no design primitives. The UI polish
  task is therefore a **refactor + restyle**, not a fresh paint job.
- **`Seller` has no billing or onboarding columns.** Adding Stripe and a
  guided onboarding flow both need a schema migration.
- **The SQS worker runs on EC2** via `docker compose --profile worker`, sharing
  the heavy API image (pre-baked HuggingFace + spaCy models). Moving it to
  Fargate forces a CI/CD change — see § 1.1.
- **No registry in the build pipeline.** CodeBuild does `git pull && docker
  compose build` directly on the EC2 host. Nothing is pushed to ECR today.

### 1.1 ECS Fargate is the highest-effort, lowest-user-value item — sequence it last

The v2 Phase 7 list includes the Fargate worker move. Be aware it is not a
contained task: Fargate cannot pull a locally-built image, so 7.4 **requires**
the CodeBuild pipeline to start building and pushing the image to **ECR**, plus
new VPC/subnet wiring, an IAM task role, target-tracking autoscaling, and a
CloudWatch alarm. It delivers zero user-visible improvement — the EC2
`--profile worker` setup works fine at low volume.

**Recommendation:** do 7.4 **last** within Phase 7, and only if the single EC2
host is a genuine reliability/throughput problem. If it is not, defer it to
post-launch. The plan keeps it as § 7.4 but flags it as deferrable.

### 1.2 Slip Phase 6.0 in early — in parallel with Phase 7

Phase 6's value is gated on training data accumulating from real users. **Ship
Phase 6.0 (the schema + prediction-logging in Agent 2 — see
`phase_6_implementation_plan.md` § 6.0) before or alongside Phase 7**, so the
first cohort of paying sellers' pricing decisions are captured instead of
discarded. It is ~1 day of work and has no UI surface. The rest of Phase 6 still
waits for volume. This plan does not re-specify 6.0 — treat it as a parallel track.

---

## 2. Sub-phase breakdown

Sequenced so the shared component layer (7.1) lands first — onboarding, billing,
and demo mode all render on top of it.

| Order | Sub-phase | Effort (solo) | User-visible |
|-------|-----------|---------------|--------------|
| 1 | 7.1 UI polish — shadcn/ui + responsive | ~1 week | High |
| 2 | 7.2 Onboarding flow | ~3 days | High |
| 3 | 7.6 Public demo mode | ~2–3 days | High (acquisition) |
| 4 | 7.3 Stripe billing | ~4–5 days | High (revenue) |
| 5 | 7.5 README + architecture write-up | ~1 day | Low |
| 6 | 7.4 ECS Fargate worker | ~3–5 days | None — *deferrable* |

7.3 is backend-heavy and can run in parallel with 7.2/7.6 if desired.

---

### 7.1 — UI polish (shadcn/ui + mobile-responsive)

**Establish a component layer:**
- [ ] Install shadcn/ui (Radix UI + `class-variance-authority` + `tailwind-merge`
      + `clsx`; `components.json` config). Confirmed compatible with Next 15 +
      React 19 + Tailwind 3.4
- [ ] Add `apps/web/components/ui/` with the core primitives actually used:
      `button`, `input`, `card`, `badge`, `dialog`, `tabs`, `toast`/`sonner`,
      `skeleton`, `select`
- [ ] Define a small theme in `tailwind.config.ts` + `globals.css` (CSS
      variables for colour, radius) — one consistent visual language

**Refactor pages onto the layer — incrementally, one page per PR:**
- [ ] `/login` (+ signup) — forms onto `input`/`button`, inline validation
- [ ] `/chat` (624 lines — the largest) — extract the chat sidebar, message
      bubbles, pricing panel, image upload into components
- [ ] `/inbox` — draft cards, approve/edit/dismiss actions onto `card`/`dialog`
- [ ] `/settings` — autonomy radios, reprice history onto `card`/`select`
- [ ] Shared app shell: a single nav/sidebar component (the Settings link is
      currently duplicated across `/chat` and `/inbox`)

**Mobile-responsive:**
- [ ] Tailwind breakpoints on every page; the desktop-sidebar layouts collapse
      to a drawer/stacked layout on small screens
- [ ] Loading states via `skeleton`; replace blank waits on the poll-driven
      pricing/draft views
- [ ] Manual test golden paths in a mobile viewport before sign-off

**Note:** big-bang rewrites of working pages are risky — go page-by-page, keep
each page shippable. This is pure frontend; nothing is gated behind a flag.

---

### 7.2 — Onboarding flow

New sellers currently land straight on `/chat` with no eBay connection and no
guidance. Add a guided first-run path.

**Schema (migration — see § 4):**
- [ ] `sellers.onboarding_completed` bool default false — lets the walkthrough
      be dismissed and not re-shown

**Flow — `/onboarding` route, a 3-step stepper:**
- [ ] Step 1 — **Connect eBay**: triggers the existing eBay OAuth flow
      (`apps/api/routers/ebay.py`); "connected" is derived from a
      `platform_credentials` row
- [ ] Step 2 — **Notifications + autonomy**: opt into SNS notifications, pick an
      autonomy level (reuses the existing `/settings` PATCH endpoints; default
      stays `draft`)
- [ ] Step 3 — **List your first item**: hand off into `/chat` with a short
      coach-mark overlay on the first intake

**Routing:**
- [ ] After login, redirect to `/onboarding` while `onboarding_completed` is
      false and no `platform_credentials` row exists; otherwise `/chat`
- [ ] "Skip for now" sets `onboarding_completed = true`
- [ ] `GET /settings/seller` (or `/auth/me`) returns `onboarding_completed` so
      the frontend can route without guessing

**Tests:**
- [ ] New seller is routed to `/onboarding`; completing/skipping routes to `/chat`

---

### 7.3 — Stripe subscription billing (Free + Pro)

**Schema (migration — see § 4) — new `Seller` columns:**
- [ ] `stripe_customer_id` text nullable
- [ ] `plan` enum `plan_tier` (`free` | `pro`) default `free`
- [ ] `subscription_status` enum (`none` | `trialing` | `active` | `past_due`
      | `canceled`) default `none`
- [ ] `stripe_subscription_id` text nullable
- [ ] `current_period_end` timestamptz nullable

**Stripe setup (dashboard / one-time):**
- [ ] Create the Pro product + recurring price; record the price ID
- [ ] Note the webhook signing secret for the events below

**Backend — new router `apps/api/routers/billing.py`:**
- [ ] `POST /billing/checkout-session` — creates a Stripe Checkout session for
      the authenticated seller, returns the redirect URL (success/cancel URLs
      built from `frontend_base_url`)
- [ ] `POST /billing/portal-session` — creates a Stripe Customer Portal session
      so sellers manage/cancel their own subscription
- [ ] `GET /billing/status` — current plan + `subscription_status` +
      `current_period_end` for the UI
- [ ] `POST /billing/webhook` — Stripe events, **`Stripe-Signature` verified via
      `stripe.Webhook.construct_event`** (same defence pattern as the eBay
      webhooks). Handle: `checkout.session.completed`,
      `customer.subscription.updated`, `customer.subscription.deleted`,
      `invoice.payment_failed` — each updates the `Seller` billing columns
- [ ] Register the router in `apps/api/main.py`
- [ ] Add `stripe` to `pyproject.toml` dependencies

**Plan enforcement** — *needs a product decision before coding*:
- [ ] Decide what Free vs Pro actually gates. Candidates: a monthly listing cap,
      access to `full_auto` autonomy, access to automatic stale-reprice. Recommend
      keeping the gate **small and reversible** for launch
- [ ] A `require_pro` FastAPI dependency (or a usage check) on the gated routes;
      gracefully returns a 402/403 the UI turns into an upgrade prompt

**Frontend:**
- [ ] Billing section in `/settings` (or a `/billing` page): current plan,
      "Upgrade to Pro" → Checkout redirect, "Manage subscription" → Portal redirect
- [ ] Upgrade prompts where a gated feature is hit

**Config (`packages/config.py`):** `stripe_secret_key`, `stripe_publishable_key`,
`stripe_webhook_secret`, `stripe_price_id_pro`, `billing_enabled` (default
`false` so local/CI run without Stripe).

**Tests:**
- [ ] Webhook handler updates `Seller` correctly per event type; bad signature
      rejected
- [ ] `require_pro` dependency allows Pro, blocks Free
- [ ] Checkout/portal session creation with the Stripe SDK mocked

---

### 7.4 — ECS Fargate worker  *(lowest priority — defer if EC2 is fine)*

See § 1.1 — this is the biggest hidden-cost item and delivers no user-visible
value. Build it last, or defer past launch.

**CI/CD change (the real cost):**
- [ ] CodeBuild builds the image and **pushes to ECR** (new repository), tagged
      by commit SHA — the worker can no longer use a host-local image
- [ ] Keep the existing SSM → EC2 deploy for the API (or migrate the API to
      ECR too — out of scope here)

**ECS:**
- [ ] Fargate task definition: same image, entrypoint
      `uv run python -m workers.sqs_worker`; sized for the pre-baked HF/spaCy
      models (note the large image → slower cold start)
- [ ] ECS service in the existing VPC/subnets; IAM **task role** with SQS, S3,
      and RDS access; logs via the `awslogs` driver (a bonus — there is no
      CloudWatch agent on the EC2 host today)
- [ ] Application Auto Scaling — target tracking on the SQS
      `ApproximateNumberOfMessagesVisible` metric, ~1 task per 10 messages
      (v2 § 10), min 0–1 / sensible max
- [ ] Remove the `--profile worker` service from the EC2 deploy once the
      Fargate service is verified

**Tests / verification:**
- [ ] Enqueue a test task; confirm a Fargate task picks it up and scales down

---

### 7.5 — README + architecture write-up

- [ ] Rewrite `README.md` (currently minimal) as a public-facing doc: what it
      does, quickstart (`make up`), the `.env` it needs
- [ ] Architecture section — distil the v2 plan's diagrams; a clear
      4-agents + NLP-pipeline overview, the eBay-only-server-side constraint,
      the SQS/EventBridge topology
- [ ] Screenshots once 7.1 lands (chat, inbox, settings)
- [ ] Keep `CLAUDE.md` / `AGENTS.md` as the contributor docs; README is the
      front door

---

### 7.6 — Public demo mode (read-only seeded account)

**Schema (migration — see § 4):**
- [ ] `sellers.is_demo` bool default false

**Seed:**
- [ ] `scripts/seed_demo.py` — creates one demo seller pre-populated with items,
      published listings, buyer conversations, drafts, and a couple of sales, so
      every screen has realistic content
- [ ] Idempotent (safe to re-run)

**Read-only guard:**
- [ ] A FastAPI dependency that rejects all mutating requests
      (POST/PATCH/DELETE) for a demo seller with a friendly 403 — so the demo
      cannot be vandalised
- [ ] Exclude demo sellers from inbound eBay webhook processing (no real eBay
      account is attached)

**Access:**
- [ ] "Try the live demo" button on `/login` — issues a JWT for the demo seller
      and drops the visitor straight into `/chat`
- [ ] Demo state stays stable because writes are blocked — no periodic reset
      needed; re-running the seed is the reset if ever required

**Tests:**
- [ ] Demo seller: reads succeed, writes return 403

---

## 3. New routers / files summary

| Path | Change |
|------|--------|
| `alembic/versions/00XX_phase7_billing_onboarding.py` | new — `Seller` billing/onboarding/demo columns + 2 enums |
| `packages/db/models.py` | + `Seller` columns; `PlanTier`, `SubscriptionStatus` enums |
| `apps/api/routers/billing.py` | new — checkout, portal, status, Stripe webhook |
| `apps/api/main.py` | register `billing.router` |
| `apps/api/dependencies` (or where deps live) | `require_pro`, `block_demo_writes` |
| `packages/config.py` | + Stripe + `billing_enabled` settings (§ 7.3) |
| `scripts/seed_demo.py` | new — demo account seed |
| `apps/web/components/ui/*` | new — shadcn primitives |
| `apps/web/components/*` | new — app shell, chat/inbox/pricing components |
| `apps/web/app/onboarding/page.tsx` | new — 3-step stepper |
| `apps/web/app/{login,chat,inbox,settings}/page.tsx` | refactored onto components |
| `apps/web/app/settings/page.tsx` | + billing section |
| `buildspec.yml` | 7.4 only — build + push to ECR |
| `infrastructure/ecs-fargate-worker.md` | 7.4 only — Fargate runbook (matches `s3-images-setup.md`) |
| `README.md` | rewritten |

**Migration numbering:** Phase 6's plan also reserves `0013`. Whichever ships
first takes `0013`; the other becomes `0014`. Use a descriptive slug regardless.

---

## 4. Schema migration (Phase 7)

One migration, all additive — new nullable columns / columns with defaults on
`sellers`, plus two new enum types. Safe and non-blocking on deploy.

| Column | Type | Default | Sub-phase |
|--------|------|---------|-----------|
| `onboarding_completed` | bool | `false` | 7.2 |
| `is_demo` | bool | `false` | 7.6 |
| `stripe_customer_id` | text nullable | — | 7.3 |
| `plan` | enum `plan_tier` (`free`/`pro`) | `free` | 7.3 |
| `subscription_status` | enum (`none`/`trialing`/`active`/`past_due`/`canceled`) | `none` | 7.3 |
| `stripe_subscription_id` | text nullable | — | 7.3 |
| `current_period_end` | timestamptz nullable | — | 7.3 |

`sellers` already has RLS (`0002_rls_policies.py`); new columns inherit it — no
policy change needed.

---

## 5. Deployment & ops notes

- **Stripe webhook URL** must be registered in the Stripe dashboard pointing at
  `https://devopslearn.store/billing/webhook`; the signing secret goes in the
  prod `.env` as `STRIPE_WEBHOOK_SECRET`.
- **`billing_enabled=false`** in local/CI keeps everything runnable without
  Stripe keys — billing routes return a clean disabled response.
- **Frontend redirects** (Stripe success/cancel, eBay OAuth, onboarding) all key
  off `frontend_base_url` — confirm it is correct in prod.
- **Web deploy path:** the app is currently served via
  `apps/api/routers/pages.py`. Confirm whether 7.1's new build still deploys the
  same way before sign-off (the larger shadcn bundle should be fine, but verify).
- **7.4 only:** moving the worker to Fargate adds an ECR repo, a task role, and
  autoscaling — see § 1.1. Until then the EC2 `--profile worker` stays.

---

## 6. Risks

| Risk | Mitigation |
|------|-----------|
| shadcn refactor breaks working pages | One page per PR; manual golden-path test each; pages stay shippable |
| Stripe webhook spoofing | `Stripe-Signature` verified via `construct_event` — same rigour as the eBay webhooks |
| Stripe/DB drift (webhook missed) | Webhook is the source of truth; reconcile on `GET /billing/status` by re-reading Stripe if stale |
| Free/Pro gate undecided → blocks 7.3 | Settle the gate as a product decision *before* coding; keep it small and reversible |
| Demo account abused / mutated | Hard write-block dependency; demo sellers excluded from webhooks |
| 7.4 scope creep (ECR + VPC + IAM + autoscale) | Sequenced last and explicitly deferrable; EC2 worker is a fine fallback |
| Phase 7 ships, but no pricing data captured | Ship Phase 6.0 in parallel (§ 1.2) so launch-cohort data is not lost |

---

## 7. Suggested sequencing (solo)

| Step | Sub-phase | Notes |
|------|-----------|-------|
| 0 | Phase **6.0** (parallel track) | Prediction logging — ship early, ~1 day |
| 1 | 7.1 UI polish | Component layer first — everything else builds on it |
| 2 | 7.2 Onboarding | Needs the component layer |
| 3 | 7.6 Demo mode | Pairs with onboarding (the `/login` "try demo" button) |
| 4 | 7.3 Stripe billing | Backend-heavy; can overlap 7.2/7.6 |
| 5 | 7.5 README | Cheap; do once screenshots exist |
| 6 | 7.4 ECS Fargate | Last — or defer past launch (§ 1.1) |

Rough estimate: **~2.5–3.5 weeks solo**, the wide range driven by whether 7.4 is
done now or deferred. Excluding 7.4: **~2–2.5 weeks**.

---

*End of Phase 7 implementation plan.*
