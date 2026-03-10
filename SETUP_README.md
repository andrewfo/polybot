# Claude Code Setup for Polymarket Bot

## Quick Start

1. **Copy CLAUDE.md** to your project root:
   ```bash
   cp CLAUDE.md ~/polymarket-bot/CLAUDE.md
   ```

2. **Copy the .claude directory** to your project root:
   ```bash
   cp -r claude-code-setup/.claude ~/polymarket-bot/.claude
   ```

3. **Copy your build plan** into the project:
   ```bash
   cp POLYMARKET_BOT_PLAN.md ~/polymarket-bot/
   ```

4. **Make hooks executable**:
   ```bash
   chmod +x ~/polymarket-bot/.claude/hooks/*.py
   ```

## What's Included

### CLAUDE.md (Project Memory)
Loaded automatically every session. Contains:
- Architecture map so Claude knows where everything lives
- Critical design rules (LLM routing, async patterns, error handling)
- Build sequence and key commands
- Code standards (type hints, dataclasses, no stubs)

### Custom Slash Commands

| Command | Usage | What It Does |
|---------|-------|--------------|
| `/build-section` | `/build-section 3` | Builds Section 3 from the plan with full implementation |
| `/verify-section` | `/verify-section 3` | Checks all acceptance criteria for Section 3 |
| `/audit-llm` | `/audit-llm` | Scans codebase for LLM routing violations |

### Hooks

| Hook | Event | What It Does |
|------|-------|--------------|
| `block_secrets.py` | PreToolUse | Prevents Claude from reading .env or echoing secrets |
| `post_edit_test.py` | PostToolUse | Auto-runs pytest after Python file edits |

## Recommended Workflow

```
# Start a new session for each section
claude

# Build a section
> /build-section 0

# After it finishes, verify
> /verify-section 0

# Commit before moving on
> git add -A && git commit -m "Section 0: scaffolding"

# Compact or clear context, then next section
> /clear
> /build-section 1
```

## Tips for This Project

- **Use Plan Mode (Shift+Tab twice)** before each section to have Claude read the plan and outline its approach before writing code
- **Use `/compact` at ~50% context** — don't let it auto-compact, do it manually
- **Use `/clear` between sections** to reset context completely
- **Enable thinking mode** in `/config` for better reasoning on complex sections (especially 4 and 5)
- **Use `ultrathink` keyword** in prompts for Sections 4-5 (signal engine and Kelly) where reasoning quality matters most
- **Git commit after every section** — gives you rollback points
- **If Claude goes off track**: `Esc Esc` to undo, or `/rewind` to go back
