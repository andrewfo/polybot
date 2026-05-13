Give a concise project status report:

1. **Build progress**: Check which sections from `POLYMARKET_BOT_PLAN (1).md` are complete by verifying the key files exist and have real implementations (not stubs)
2. **Test health**: Run `pytest tests/ -v --tb=short` and report pass/fail counts
3. **Code coverage**: List any modules in core/, signals/, strategy/, monitoring/, web/ that lack corresponding test files
4. **Open issues**: Check for any TODO, FIXME, HACK, or stub comments in Python files
5. **Dependencies**: Check if requirements.txt has any unused packages (imported nowhere) or missing packages (imported but not listed)

Report as a compact checklist. Flag anything that needs attention.
