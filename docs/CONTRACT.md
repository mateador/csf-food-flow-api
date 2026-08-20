# API Contract — `openapi.yaml`

This file (`docs/openapi.yaml`) is the **canonical source of truth** for the
API contract shared between this repo and `csf-food-flow-pwa`.

## Why a spec file instead of a real shared package

The project uses two separate repos in two different languages (Python/Sanic
here, TypeScript/Preact in the PWA) rather than a single pnpm-workspace
monorepo. That means there's no single `packages/shared` folder both sides
can literally import from. An OpenAPI spec is the practical equivalent for a
two-language, two-repo setup: one file, two independently-generated
consumers.

## How each side uses it

- **This repo (API)**: `src/` route handlers are hand-implemented against
  this spec using `msgspec` structs (Step 4). There is no automatic
  codegen from YAML to Python in this project — the discipline is manual:
  when a route's shape changes, update this file and the corresponding
  `msgspec` struct in the same commit.

- **PWA repo**: TypeScript types are **mechanically generated** from this
  exact file via `npm run generate:types` (using `openapi-typescript`),
  producing `src/types/api.ts`. This is real codegen, not hand-copied types
  — the PWA genuinely cannot drift from whatever this file says, as long as
  the generation step is re-run after a spec change.

## Sync process when the contract changes

1. Edit `docs/openapi.yaml` in **this** repo.
2. Copy the updated file to `csf-food-flow-pwa/docs/openapi.yaml`.
3. In the PWA repo, run `npm run generate:types` and commit the regenerated
   `src/types/api.ts` alongside the copied spec.
4. Update the corresponding `msgspec` struct(s) in this repo's route
   handlers to match.

There is no CI check enforcing this today — it's a documented discipline,
not an automated guarantee. Worth adding a CI step later that fails the
build if the two `openapi.yaml` copies diverge (a simple `diff` in GitHub
Actions would catch it) if this becomes a real pain point in practice.
