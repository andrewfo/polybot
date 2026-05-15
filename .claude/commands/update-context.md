Update the project context by modifying existing files. Do NOT create new files.

## Steps

1. **Gather state** (run all in parallel):
   - `git log --oneline -20`
   - `git status` and `git diff --stat`
   - `git diff --name-only HEAD~5`
   - `python3 -m pytest tests/ -v --tb=short 2>&1 | tail -30`
   - Grep for `TODO|FIXME|HACK|STUB|XXX` across all `.py` files

2. **Read current context**:
   - Read the "Build Sequence & Progress" section of `CLAUDE.md`
   - Read `POLYMARKET_BOT_PLAN (1).md` to see which sections are marked COMPLETE vs still have task descriptions

3. **Update CLAUDE.md** — Edit the "Build Sequence & Progress" section:
   - Update which sections are COMPLETE vs IN PROGRESS based on what files exist with real implementations
   - Update the "Next up" line to reflect actual current state
   - Keep it concise — 3-4 lines max for the progress block

4. **Update `POLYMARKET_BOT_PLAN (1).md`** — For each section:
   - If a section's acceptance criteria are met (files exist, tests pass, features work), change its heading to include `— COMPLETE` and collapse its Tasks/Acceptance Criteria into a 2-3 line summary of what was built (similar to how Sections 0-6 are already formatted)
   - If a section is partially done, add a `**Status:**` line under the heading noting what's done and what remains
   - Do NOT remove acceptance criteria for incomplete sections
   - Do NOT change task descriptions for sections that haven't been started yet

5. **Report** — Print a short summary to the user:
   - Test health (pass/fail count)
   - What sections changed status
   - Any TODOs/FIXMEs found
   - What uncommitted work exists
   - 2-3 suggested next steps
