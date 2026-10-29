"""Portable backend commands for Windows and Ubuntu."""
import argparse
import subprocess
from platform_tools import ROOT, python_module

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['migrate', 'test', 'create-admin', 'reset-password', 'disable-mfa', 'preview-worker', 'drain-preview'])
    args, extra = parser.parse_known_args()
    if args.command == 'migrate':
        module, arguments = 'alembic', ['upgrade', 'head', *extra]
    elif args.command == 'test':
        module, arguments = 'pytest', ['-q', *extra]
    else:
        module, arguments = 'app.cli', [args.command, *extra]
    result = subprocess.run(python_module(module, arguments), cwd=ROOT / 'services/backend')
    raise SystemExit(result.returncode)

if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
