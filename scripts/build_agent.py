"""Compile agent/src/agent.js (+ frida-objc-bridge) into src/moneywiz_mcp/agent.js.

Run after editing the agent:  .venv/bin/python scripts/build_agent.py
Requires `npm install` in agent/ once.
"""
from pathlib import Path
import sys
import frida

ROOT = Path(__file__).resolve().parents[1]

def build() -> Path:
    errors = []
    compiler = frida.Compiler()
    compiler.on("diagnostics", lambda diags: errors.extend(diags))
    bundle = compiler.build(str(ROOT / "agent/src/agent.js"), project_root=str(ROOT / "agent"))
    if errors:
        sys.exit(f"agent build diagnostics: {errors}")
    out = ROOT / "src/moneywiz_mcp/agent.js"
    out.write_text(bundle)
    return out

if __name__ == "__main__":
    print(build())
