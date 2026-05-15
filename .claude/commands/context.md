Build an updating context snapshot based on recent changes to the codebase. This provides a "what's been happening" briefing for resuming work.

## Steps

1. **Recent commits**: Run `git log --oneline -20` and group commits by theme (signal tuning, infra, new features, bug fixes, etc.)

2. **Uncommitted work**: Run `git status` and `git diff --stat` to identify what's in progress right now — modified files, untracked files, staged changes

3. **What changed recently**: For each file modified in the last 5 commits, read the file and summarize what it does and what changed (use `git diff HEAD~5 -- <file>` for each)

4. **Current build progress**: Cross-reference the architecture map in CLAUDE.md against what files actually exist. Identify:
   - Which build plan sections are complete (key files exist with real implementations)
   - Which sections are in progress (files exist but partially implemented or have TODOs)
   - What's next according to CLAUDE.md

5. **Broken or risky state**: Run `pytest tests/ -v --tb=short 2>&1 | tail -30` to check test health. Flag failures.

6. **Open threads**: Grep for TODO, FIXME, HACK, STUB, XXX across all Python files — these are unfinished threads of work

7. **Config drift**: Check if `config/settings.py` has any new constants added in recent commits that aren't yet referenced anywhere else (orphaned config)

## Output Format

Structure the report as:

### Recent Activity (last N commits)
- Bullet summary grouped by theme, most recent first
- Note any patterns (e.g., "heavy signal tuning phase", "new module buildout")

### Work In Progress
- Modified/untracked files and what they appear to contain
- Staged changes ready to commit

### What Changed (detail)
- Per-file summary of recent changes with key decisions or trade-offs visible in the diffs

### Build Status
- Section-by-section progress vs the build plan
- What's next

### Health
- Test results (pass/fail count)
- Any broken imports, missing dependencies, or failing tests

### Open Threads
- TODOs/FIXMEs with file:line references, grouped by module

### Suggested Next Steps
- Based on all the above, suggest 2-3 concrete next actions ranked by priority
