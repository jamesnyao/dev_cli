---
name: temp-files
description: >-
  Temporary and one-off file placement rules. Use when creating helper scripts,
  test files, scratch code, or any throwaway artifacts.
---

# Temporary & One-Off Files

All temporary files, helper scripts, one-off test files, and scratch artifacts
go in the workspace `temp/` directory — never in the workspace root or repo
directories. `dev repo root` prints the workspace root.

## Location

```text
<workspace>/temp/
```

## What Goes in temp/

- One-off helper scripts (e.g., `fix_paths.py`, `check_config.py`)
- Test input/output files
- Scratch code and prototypes
- Downloaded archives or debug artifacts
- Any file you wouldn't commit to a repo

## Rules

- **ALWAYS** create throwaway files under `temp/`, not the workspace root
- **NEVER** leave one-off scripts in repo directories or workspace root
- Use descriptive names (e.g., `temp/sample_build.log`, not `temp/test.txt`)
- Clean up when done if the file is no longer needed
- Docs you want to **keep** (notes, analyses, plans) do not belong in `temp/`;
  follow the user's documentation conventions instead
