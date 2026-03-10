#!/usr/bin/env python3
"""
PostToolUse hook: Run pytest after Python file edits.
Only runs if the edited file is in the project (not config/scripts).
Warns Claude if tests fail so it can fix them immediately.
"""
import json
import sys
import subprocess

def main():
    data = json.load(sys.stdin)
    tool_name = data.get("tool_name", "")
    
    if tool_name not in ("Edit", "Write", "Create"):
        return
    
    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path.endswith(".py"):
        return
    
    # Skip non-testable files
    skip_dirs = ["scripts/", "config/", ".claude/"]
    if any(file_path.startswith(d) or f"/{d}" in file_path for d in skip_dirs):
        return
    
    # Run pytest quietly
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-x", "-q", "--tb=short"],
        capture_output=True, text=True, timeout=30
    )
    
    if result.returncode != 0:
        print(f"⚠️ Tests failing after edit to {file_path}:", file=sys.stderr)
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout, file=sys.stderr)
        print(result.stderr[-300:] if len(result.stderr) > 300 else result.stderr, file=sys.stderr)

if __name__ == "__main__":
    main()
