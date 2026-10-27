# Code Review: Duplication & Code Reuse Workflow (`review-duplication`)

The `review-duplication` workflow is a targeted code review workflow designed to audit commits to remove duplicate code and maximize code reuse.

It is part of the modular code review workflow family in `emu-dev-cli`, where individual review workflows focus deeply on specific aspects of code quality (e.g. duplication, security, structure, readability).

---

## 🎯 Focus Areas

1. **Repeated Logic & Redundant Implementations:**
   Identifies identical or near-identical statements, logic branches, copy-pasted blocks, or redundant error handling introduced in the commit.
2. **Code Reuse:**
   Scans the codebase for existing helper functions, standard library utilities, or shared modules that should be reused instead of adding new duplicate code.
3. **Helper Extraction & Consolidation:**
   Refactors duplicate patterns into shared, reusable functions with proper parameters and documentation.
4. **Clean Commit Amending:**
   Ensures any refactoring made during the review is tested and amended cleanly into the commit, preserving its Gerrit `Change-Id`.

---

## 🚀 Usage

### 1. Initialize Workflow Session for HEAD
```bash
emu-dev-cli workflow review-duplication init
```

### 2. Review a Specific Commit or Repository
```bash
emu-dev-cli workflow review-duplication init --commit <sha_or_ref> --repo <repo_path>
```
