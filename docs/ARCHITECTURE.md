# Architecture

```
intent ──► Builder (plan → approve → scaffold → implement on a branch)
                │
                ▼
     FastAPI app = redblue.platform.create_app()
     ┌───────────────────────────────────────────────────────────────┐
     │ core: settings · DB · CSP/CSRF/headers · auth · storage · jobs │
     │       email · AI client (BYOK, schemas, caching, spend limits) │
     │ observe: request metrics · error groups · uptime · ops agent   │
     │ cms:  blocks · collections · workflow · revisions · API        │
     │ growth: sitemaps · robots · llms.txt · analytics · A/B · forms │
     │ admin: HTMX UI over all of the above                            │
     │ templates/site: page types → server-rendered HTML + JSON-LD    │
     └───────────────────────────────────────────────────────────────┘
                │ every PR
                ▼
     gate: Red (plan scans from diff + OpenAPI → SAST, deps, DAST on a
           self-launched loopback preview → triage) → Blue (fix + failing-
           then-passing test → PR) → release gate (severity threshold)
```

## Request path

1. `HeadAsGetMiddleware` → `SecurityHeadersMiddleware` (nonce, CSP, HSTS) →
   `CSRFMiddleware` (double-submit token, replayed body) → `ObservabilityMiddleware`
   (request id, route template timing, error capture).
2. Routes, in order: CMS API (`/api/cms`), growth (`/sitemap.xml`, `/robots.txt`,
   `/llms.txt`, `/feed.xml`, `/_rb/beacon`, `/_rb/forms/*`, newsletter), admin (`/admin`),
   then the public site's catch-all, which resolves pages, collection listings and entries.
3. Unknown paths check the redirect table (automatic 301s on slug changes) before the 404
   page.

## Content model

`Entry` has working-copy fields plus a `live` JSON snapshot. The public site only renders
`live`, so editing a published page never changes the site until the edit is reviewed and
re-published. Every save writes a `Revision`; rollback restores one as a new draft.

Publishing requires the `publisher` role. Registered publish checks (the growth quality
threshold) can block it. Agents can only create drafts, and their revisions are tagged with
the agent name.

## Agents

| Agent | Where | Output |
|---|---|---|
| Builder | `redblue.builder` | plan (approved by a human), scaffolded project, feature branches |
| Content | `redblue.cms.agent` | CMS drafts and refresh drafts |
| Growth / Repurposing | `redblue.growth.agent` | weekly report, recommendations, social/email drafts |
| Ops | `redblue.observe.ops` | anomaly explanations, builder tasks |
| Red / Blue | `redblue.gate` | findings + triage; fix proposals with regression tests, PRs |
| Video | `redblue.video` | scored trends, cited briefs and scripts, renders awaiting human review |

All model calls go through `AIClient.structured()` (or the gate's standalone `LLM`), which
validates against Pydantic models, fences untrusted input, applies prompt caching, enforces
per-agent spend limits and logs cost. Every agent has a deterministic no-key fallback.

## Package dependencies

`core ← cms ← templates, growth ← admin ← platform ← cli`; `observe` depends on `core`;
`gate` depends on nothing in RedBlue, so the GitHub Action installs it alone.

## Known gaps (v0.1)

- Migrations (Alembic) use one linear history in `redblue-core` for all packages' tables.
  CI runs them on SQLite and Postgres 16. On Postgres, upgrades take an advisory lock so
  several workers can start at once, and a failed upgrade rolls back completely. Other
  databases (MySQL etc.) are untested.
- Temporal-backed workflows, Search Console API sync (CSV import works), and Tailwind
  integration are not implemented yet; the base CSS is hand-written on design tokens.
- Passkeys (WebAuthn, via py_webauthn) sign people in to the admin without a password. They
  must be discoverable and user-verified; challenges are stored server-side and work once.
  The relying party is the host of `RB_BASE_URL`, so a site must be served from that origin
  (and over https, except on localhost). Passwords and OAuth keep working alongside them.
  Adding a passkey needs a sign-in from the last 10 minutes (or the password again), so a
  stolen session can't add one; admins can remove anyone's passkeys on the Users page.
- The block editor is visual: one card per block, with fields made from the Pydantic block
  models (`redblue.cms.editor`), so new block types appear without JavaScript changes. It
  writes the same JSON the API takes, the server validates it as before, and "Edit as JSON"
  shows the raw form. Blocks reorder by dragging a card's handle, or with its ↑/↓ buttons
  from the keyboard. Without JavaScript the JSON textarea is used directly. A live preview
  under the blocks shows them rendered with the public site's templates
  (`POST /admin/content/preview-blocks` validates like a save and stores nothing).
- Built-in DAST runs passive checks plus inert canaries. ZAP (baseline) and Nuclei run too
  when installed; the gate Action installs pinned versions with `dast_tools: "true"`, which
  RedBlue's own gate uses.
