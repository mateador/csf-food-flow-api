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

- **This repo (API)**: route handlers in `src/modules/` are written by
  hand against this spec, and validate request bodies themselves. There is
  no codegen from YAML to Python and no schema library in between, so the
  discipline is manual: when a route's shape changes, update this file and
  the handler in the same commit.

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
4. Update the corresponding route handler(s) in this repo to match, in the
   same commit as step 1.

## What CI checks

- **PWA repo:** CI fails if `src/types/api.ts` doesn't match the PWA's own
  copy of `docs/openapi.yaml` (it regenerates the types and diffs them).
- **This repo:** CI compares this file with the PWA's copy on `main` and
  **warns** if they differ. It's a warning rather than a failure because
  one repo is always updated before the other, and that must not block a
  deploy.

Neither check proves the API actually implements what this file
describes. That's still the manual discipline in step 4 above. (At the
time of writing, three entry endpoints are described here but not
implemented; see the API README, A6.)
