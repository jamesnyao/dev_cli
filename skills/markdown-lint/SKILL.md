---
name: markdown-lint
description: >-
  Markdown linting rules (markdownlint). Use when writing, editing, or reviewing
  markdown files to ensure clean, consistent formatting.
---

# Markdown Lint Rules

When writing or editing markdown files, follow these markdownlint rules. Fix violations in any markdown you create or modify.

## Headings

- **MD001**: Heading levels increment by one — don't skip (e.g., `##` → `####`).
- **MD003**: Use ATX-style headings (`# Heading`) consistently. Don't mix with setext.
- **MD022**: Surround headings with blank lines (one before, one after).
- **MD023**: Headings must start at the beginning of the line.
- **MD024**: No duplicate heading text at the same level.
- **MD025**: Only one top-level `#` heading per document.
- **MD026**: No trailing punctuation in headings (`.`, `:`, `,`, `;`, `!`). Question marks are OK.
- **MD041**: First line should be a top-level heading.

## Lists

- **MD004**: Use `-` for unordered lists by default. Mixing markers
  (e.g., `*` and `-`) is acceptable within the same list to distinguish
  item types (task items, categories).
- **MD005**: Consistent indentation for list items at the same level.
- **MD007**: Indent nested unordered lists by 2 spaces.
- **MD029**: Ordered list prefixes should be sequential (`1.`, `2.`, `3.`).
- **MD030**: Single space after list markers.
- **MD032**: Surround lists with blank lines (one before, one after).

## Whitespace and Line Length

- **MD009**: No trailing spaces at end of lines.
- **MD010**: No hard tabs — use spaces.
- **MD012**: No multiple consecutive blank lines.
- **MD013**: Keep lines under 120 characters. Break long lines at natural points in
  prose paragraphs and list items. Exceptions: URLs, code spans, and headings that
  cannot be wrapped. Configure via `.markdownlint.json`:
  `{ "MD013": { "line_length": 120 } }`
- **MD047**: Files should end with a single newline.

## Code Blocks

- **MD014**: Don't prefix shell commands with `$` unless showing output.
- **MD031**: Surround fenced code blocks with blank lines.
- **MD038**: No spaces inside code spans (`` `code` `` not `` ` code ` ``).
- **MD040**: Fenced code blocks must specify a language
  (` ```python `, ` ```log `, ` ```text `). Use `text` when no specific
  language applies.
- **MD046**: Use fenced (` ``` `) style consistently, not indented.

## Links and Images

- **MD011**: Don't reverse link syntax — use `[text](url)` not `(text)[url]`.
- **MD034**: Don't use bare URLs — wrap in `<url>` or `[text](url)`.
- **MD039**: No spaces inside link text brackets.
- **MD042**: No empty links — `[text]()` is not allowed.
- **MD045**: Images must have alt text — `![alt](image.png)`.
- **MD059**: Link text must be descriptive — don't use generic text like `[link]`,
  `[here]`, or `[click here]`. Use the resource name as the link text instead.

## Blockquotes

- **MD027**: Single space after `>` in blockquotes.
- **MD028**: No blank lines inside blockquotes.

## Emphasis and HTML

- **MD033**: Avoid inline HTML in markdown.
- **MD036**: Don't use emphasis (`**bold**`) as a substitute for headings.
- **MD037**: No spaces inside emphasis markers.

## Tables

- **MD060**: Table column style must be consistent. Use padded style with spaces
  around pipes: `| cell |` not `|cell|`. Don't add extra padding beyond one space.
- Keep column widths reasonable — break long cell content or use shorter descriptions.
- Align separator row dashes with header content for readability.
- Surround tables with blank lines.

## Applying Fixes

When editing markdown, fix all violations in the lines you touch. For new files,
the entire file must be clean. Prioritize:

1. **Structure** — blank lines around headings, lists, code blocks (MD022, MD031, MD032)
2. **Consistency** — list markers, heading style, table style (MD003, MD004, MD060)
3. **Code blocks** — always specify language (MD040)
4. **Links** — descriptive text, no bare URLs (MD059, MD034)
5. **Line length** — break long lines at natural points (MD013)
6. **Cleanup** — trailing spaces, tabs (MD009, MD010)

## Style

- **Emojis** — use sparingly. One or two for status indicators in tables (e.g.,
  ✅/❌) is fine. Don't scatter emojis through prose or headings.
- **Headings** — only add section headings when they genuinely help navigation.
  A short document with 3 paragraphs doesn't need 3 `##` headings. Prefer flat
  prose or a single list over deeply nested heading hierarchies.

## Configuration

If the repository or workspace has a markdownlint config, pass it explicitly
when running lint checks:

```shell
npx markdownlint-cli -c <path-to>/.markdownlint.json <file-or-glob>
```

When wrapping long lines with URLs, put the descriptive link text inline and let
the URL be the only long element. Never shorten link text to generic words like
"link" or "here" — use the resource name instead.
