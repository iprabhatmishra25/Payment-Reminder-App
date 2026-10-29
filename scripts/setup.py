"""One setup command for Windows and Ubuntu. Never replaces an existing .env."""
import argparse
import json
import subprocess
import venv
from platform_tools import ROOT, check_python, npm_command, venv_python

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Create preview-only demo configuration if no .env exists')
    parser.add_argument('--no-config', action='store_true', help='Install dependencies only (for CI)')
    args = parser.parse_args()
    check_python()
    node = npm_command()[0]
    major = int(json.loads(subprocess.check_output([node, '-p', 'JSON.stringify(Number(process.versions.node.split(".")[0]))'], text=True)))
    if major < 24:
        raise RuntimeError('Install Node.js 24 LTS or newer, then reopen the terminal.')
    executable = venv_python()
    if not executable.exists():
        print('Creating the project Python virtual environment...', flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    subprocess.run([str(executable), '-m', 'pip', 'install', '-r', str(ROOT / 'services/backend/requirements.lock.txt')], cwd=ROOT, check=True)
    subprocess.run(npm_command('ci'), cwd=ROOT / 'apps/desktop', check=True)
    if not args.no_config and not (ROOT / '.env').exists():
        subprocess.run([str(executable), str(ROOT / 'scripts/configure.py'), *(['--demo'] if args.demo else [])], cwd=ROOT, check=True)
    print('\nSetup complete. No activation command is needed.', flush=True)
    print('Start with: python scripts/run_local.py' + (' --demo' if args.demo else ''))

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
