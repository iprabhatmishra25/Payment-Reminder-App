"""Launch the local development/demo workspace on Windows or Ubuntu."""
import argparse
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from platform_tools import ROOT, python_module, spawn, stop_process_tree, venv_python

def wait_for(url, process):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('A required process stopped. Read the error above.')
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(.25)
    raise RuntimeError('Timed out waiting for ' + url)

def require_free_port(port):
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'Port {port} is already in use. Stop the other local server before starting Duewise; no existing process was stopped.') from exc

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Use labeled sample records and preview-only email')
    parser.add_argument('--browser-only', action='store_true', help='Run the development UI without Electron')
    parser.add_argument('--backend-only', action='store_true', help='Run the local API/preview worker for an installed desktop client')
    args = parser.parse_args()
    if args.browser_only and args.backend_only:
        raise RuntimeError('Choose either --browser-only or --backend-only.')
    if not venv_python().exists():
        raise RuntimeError('Run python scripts/setup.py --demo first; see docs/WINDOWS_UBUNTU.md.')
    if not (ROOT / '.env').exists():
        subprocess.run([str(venv_python()), str(ROOT / 'scripts/configure.py'), *(['--demo'] if args.demo else [])], check=True)
    require_free_port(8000)
    if not args.backend_only:
        require_free_port(5173)
        if not (ROOT / 'apps/desktop/node_modules/vite/bin/vite.js').exists():
            raise RuntimeError('Desktop dependencies are missing. Run python scripts/setup.py first.')
    node = shutil.which('node')
    if not args.backend_only and not node:
        raise RuntimeError('Install Node.js 24 LTS and reopen the terminal.')
    backend, desktop = ROOT / 'services/backend', ROOT / 'apps/desktop'
    env = {**os.environ, 'DUEWISE_DEV': '1', 'DUEWISE_API_URL': 'http://127.0.0.1:8000'}
    subprocess.run(python_module('alembic', ['upgrade', 'head']), cwd=backend, env=env, check=True)
    if args.demo:
        subprocess.run(python_module('app.cli', ['seed-demo']), cwd=backend, env=env, check=True)
    processes = []
    def start(command, cwd):
        process = spawn(command, cwd, env)
        processes.append(process)
        return process
    try:
        api = start(python_module('uvicorn', ['app.main:app', '--host', '127.0.0.1', '--port', '8000', '--no-proxy-headers']), backend)
        wait_for('http://127.0.0.1:8000/health/live', api)
        if args.demo:
            start(python_module('app.cli', ['preview-worker']), backend)
        if args.backend_only:
            print('API ready at http://127.0.0.1:8000. Open the installed Duewise app. Ctrl+C stops this local backend.', flush=True)
        else:
            ui = start([node, str(desktop / 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1'], desktop)
            wait_for('http://127.0.0.1:5173', ui)
            if args.browser_only:
                print('Open http://127.0.0.1:5173. Ctrl+C stops this local workspace.', flush=True)
            else:
                start([node, str(desktop / 'node_modules/electron/cli.js'), '.'], desktop)
        while all(process.poll() is None for process in processes):
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            stop_process_tree(process)

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
