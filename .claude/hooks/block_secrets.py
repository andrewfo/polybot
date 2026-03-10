#!/usr/bin/env python3
"""
PreToolUse hook: Block dangerous operations involving secrets.
Prevents Claude from reading, printing, or logging .env files and private keys.
"""
import json
import sys

BLOCKED_PATTERNS = [
    "cat .env",
    "cat ./.env",
    "echo $PRIVATE_KEY",
    "echo $POLYMARKET_API_SECRET",
    "echo $OPENROUTER_API_KEY",
    "print(os.getenv",
    "PRIVATE_KEY",
]

BLOCKED_FILES = [".env", ".env.local", ".env.production"]

def main():
    data = json.load(sys.stdin)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    
    # Block bash commands that would expose secrets
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        for pattern in BLOCKED_PATTERNS:
            if pattern in command:
                print(f"🚫 BLOCKED: Command would expose secrets ({pattern})", file=sys.stderr)
                print("Use .env.example for reference. Never read .env directly.", file=sys.stderr)
                sys.exit(2)
    
    # Block reading .env files
    if tool_name in ("Read", "View"):
        file_path = tool_input.get("file_path", "") or tool_input.get("path", "")
        for blocked in BLOCKED_FILES:
            if file_path.endswith(blocked):
                print(f"🚫 BLOCKED: Cannot read {blocked} — contains secrets", file=sys.stderr)
                print("Use .env.example for reference instead.", file=sys.stderr)
                sys.exit(2)

if __name__ == "__main__":
    main()
