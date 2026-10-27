---
name: making-code-changes
description: >-
  Guidelines for making code changes. Prefer the simplest complete solution.
  Use when making code changes, fixing bugs, or refactoring.
---

# Making Code Changes

Choose the simplest design that solves the actual problem. Keep the diff focused,
but do not preserve unnecessary machinery just to change fewer lines.

## Branching

Create a feature branch off the repo's default branch before making changes,
following the repository's (or a loaded `branching` skill's) naming conventions.
If working on an existing feature branch, rebase it onto the default branch
before adding new commits.

## Design before editing

- **Protect the invariant, not the suggested mechanism.** Identify the behavior
  a review comment protects before implementing it. When later feedback proposes
  removing that fix, check whether the new design preserves the same invariant.
  An earlier request alone is not a reason to retain an implementation.
- **Verify assumptions that justify complexity.** Inspect actual callers and the
  pinned dependency's behavior before adding synchronization, caching, retries,
  context propagation, or compatibility paths. Use a targeted probe when needed;
  generic framework expectations and hypothetical consumers are not evidence.
- **Find the existing source of truth.** Check framework facilities, existing
  state, and derivable information before adding counters, flags, events, or
  caches. If duplication is necessary, identify why deriving the information
  cannot meet the requirement.
- **Make state ownership and lifetime explicit.** Identify who creates and
  changes new state, when it expires, and what happens on failure. Scope it to
  its actual owner; do not automatically choose globals or per-request contexts.
  Prefer removing competing paths over coordinating duplicated state.
- **Evaluate the complete producer-to-consumer path.** Read applicable ownership
  guidance and compare solutions across the owned system, not just the current
  file. Prefer fixing a contract at its source over consumer workarounds. Account
  for real deployment ordering and keep changes within the authorized scope.
- **Tie checks to concrete guarantees.** Distinguish untrusted-input validation,
  privacy filtering, transport constraints, and internal invariants. Check at
  the responsible boundary instead of repeatedly revalidating trusted values.
  Keep internal invariant failures visible; do not turn them into success.
- **Test behavior, not machinery.** Define when an operation is accepted,
  completed, persisted, or failed, and test the relevant boundaries. A regression
  test should preserve the required outcome when a lock, callback, or wrapper is
  replaced by a simpler correct design.

Keep this reasoning proportional to the change. A small fix needs a short
explanation, not a design document; uncertain or risky assumptions need evidence.

## Principles

- **Change only what's necessary.** Don't reformat, rename, or restructure code outside the scope
  of the fix.
- **Keep changes focused.** Include producer and consumer changes when necessary
  for a simpler complete solution, rather than hiding complexity in an adapter.
- **Don't duplicate code.** If you find yourself copying and pasting, consider refactoring to share logic instead.
- **Avoid drive-by cleanups.** If you spot unrelated issues, note them; don't bundle them into the same change.
- **Preserve existing style.** Match the surrounding code's formatting, naming, and patterns
  even if you'd prefer something different.
- **One concern per change.** If a task involves both a bug fix and a refactor, split them into separate commits or PRs.
- **Minimize moved lines.** Reordering functions, imports, or blocks inflates the diff without adding value.
- **Don't add speculative code.** Only add what's needed now, not "just in case" abstractions or unused parameters.
- **Always use multi-line braces.** Never put braces on the same line as the statement body for code blocks.
  Use newlines after `{` and before `}`.

  ```powershell
  # Bad
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  # Good
  if ($LASTEXITCODE -ne 0) {
      exit $LASTEXITCODE
  }
  ```

## Code cleanup (apply to new/modified code)

Run the `code-cleanup` skill on the code you are adding or modifying before committing.

## When reviewing your own diff

Before committing, review the diff and ask:

1. Does this solve the original problem, rather than only satisfy the wording of a comment?
2. Can any new state, branch, fallback, lock, or abstraction be removed?
3. Are we compensating in a consumer for a contract we can simplify at its source?
4. Have we preserved required behavior, privacy, and failure handling?
5. Did I accidentally include unrelated changes, duplication, or formatting churn?

If so, consider revising your changes.

## Committing and pushing

Follow the repository's commit conventions, or a loaded `committing-code` skill,
for commit messages, pushing, and pull requests.
