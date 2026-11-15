"""Dev helper: compile a throwaway Frida script and call one of its rpc exports in MoneyWiz.

The script must live under agent/ (the compiler's project root), e.g. agent/probe/x.js;
agent/probe/ is git-ignored for that purpose. Keep probes read-only.

Usage: .venv/bin/python scripts/devrun.py agent/probe/x.js <export> ['<json args>']
"""
import json, sys
from pathlib import Path
import frida

ROOT = Path(__file__).resolve().parents[1]

def main():
    entry, export = sys.argv[1], sys.argv[2]
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
    compiler = frida.Compiler()
    compiler.on("diagnostics", lambda d: print("diag", d, file=sys.stderr))
    bundle = compiler.build(str(Path(entry).resolve()), project_root=str(ROOT / "agent"))
    session = frida.attach("MoneyWiz")
    script = session.create_script(bundle)
    script.on("message", lambda m, d: print("msg", m, file=sys.stderr))
    script.load()
    try:
        result = getattr(script.exports_sync, export)(*args)
        print(json.dumps(result, ensure_ascii=False, indent=1))
    finally:
        script.unload(); session.detach()

main()
