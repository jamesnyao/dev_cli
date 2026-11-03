---
name: code-cleanup
description: >-
  Review changed code for unnecessary complexity, duplication, comments, imports,
  and namespace prefixes. Use when asked to clean up code or before committing.
---

# Code Cleanup Guidelines

Review only the requested scope or current changes. Cleanup should reduce the
number of concepts a maintainer must understand, not just shorten the code.

1. **Remove unnecessary machinery.** Require an actual caller or verified runtime
   behavior for added locks, fallbacks, wrappers, and compatibility paths. Look for
   framework facilities or existing data that make new state unnecessary.
2. **Check responsibility and lifetime boundaries.** State should have a clear
   owner and cleanup path, including failures. Each check should protect a named
   trust boundary, transport constraint, or invariant; avoid repeating internal
   shape checks across layers.
3. **Review both sides when ownership allows.** Do not keep consumer workarounds
   solely because the producer is in another repository we also own. Propose the
   smaller coordinated change when it falls outside the authorized scope.
4. **Keep necessary explanations.** Use short docstrings and comments for
   non-obvious constraints or reasons. Remove comments that merely narrate code.
5. **Remove unused imports and unnecessary namespace prefixes.**
6. **Keep invariant failures visible.** Reject unsupported cases and stop invalid
   execution when an internal invariant fails, with actionable diagnostics and
   the repository's appropriate failure scope. Use checks that work in production;
   logging or debug-only assertions alone do not enforce a contract, and assertion
   expressions must not have required side effects.
7. **Handle expected failures at the boundary.** Validate external input, stale or
   corrupt data, and operational failures through ordinary error handling, not
   process-fatal assertions. Avoid catch-all recovery and success-shaped defaults.
   Recover narrowly only when recovery restores or preserves valid state; bound
   retries to transient errors.

Do not remove a safeguard just because it looks defensive. Identify the condition
it protects, and verify that the simpler design still handles that condition.
Test violated internal contracts and expected failures without weakening invariants.
Keep regression tests focused on behavior, not the implementation being removed.
Preserve real privacy protections and visible failure handling.

For context, see Chromium's [defensive versus offensive coding guidance]. Apply
the repository's own error-handling conventions and language-specific check semantics.

[defensive versus offensive coding guidance]: https://www.chromium.org/chromium-os/developer-library/reference/cpp/cpp-invariant-checks/#defensive-vs-offensive-coding
