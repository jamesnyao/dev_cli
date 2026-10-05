---
name: code-cleanup
description: >-
  Code cleanup guidelines — simplify documentation, remove inline comments,
  clean unused imports, remove unnecessary namespace prefixes, remove narrative
  comments. Use when asked to do code cleanup or review code for cleanliness.
---

# Code Cleanup Guidelines

When performing code cleanup, apply these rules:

1. **Simplify function documentation** to about a single sentence unless the function REALLY needs a more complex description
2. **Remove all inline comments** unless they explain convoluted code
3. **Clean up unused usings/includes/imports**
4. **Clean up unnecessary namespace prefixes** (e.g., `System.IO.File` → `File`) if not required
5. **Clean up narrative comments** that explain what the code is doing — the code should be clear enough
   to not require comments in most cases
