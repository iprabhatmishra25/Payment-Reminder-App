"""Portable process helpers shared by setup, development, and administration."""
from __future__ import annotations
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def venv_python(root: Path = ROOT, system: str | None = None) -> Path:
    return root / '.venv' / ('Scripts/python.exe' if (system or platform.system()) == 'Windows' else 'bin/python')

def npm_command(*arguments: str) -> list[str]:
    """Run npm's JavaScript CLI through Node; never execute npm.cmd directly."""
    node = shutil.which('node')
    npm = shutil.which('npm.cmd' if platform.system() == 'Windows' else 'npm')
    if not node or not npm:
        raise RuntimeError('Install Node.js 24 LTS with npm, then reopen the terminal.')
    npm_path, node_path = Path(npm), Path(node)
    resolved = npm_path.resolve()
    candidates = [resolved] if resolved.suffix == '.js' else []
    candidates += [npm_path.parent / 'node_modules/npm/bin/npm-cli.js', node_path.parent / 'node_modules/npm/bin/npm-cli.js', npm_path.parent.parent / 'lib/node_modules/npm/bin/npm-cli.js']
    for candidate in candidates:
        if candidate.is_file():
            return [node, str(candidate), *arguments]
    raise RuntimeError('Cannot locate npm-cli.js. Install the standard Node.js 24 LTS distribution, including npm.')

def python_module(module: str, arguments: list[str] | None = None, root: Path = ROOT) -> list[str]:
    executable = venv_python(root)
    if not executable.is_file():
        raise RuntimeError('Run python scripts/setup.py first to create the project virtual environment.')
    code = ('from dotenv import load_dotenv; ' + f'load_dotenv({str(root / ".env")!r}); ' + 'import runpy,sys; ' + f'sys.argv={[module, *(arguments or [])]!r}; ' + f'runpy.run_module({module!r},run_name="__main__")')
    return [str(executable), '-c', code]

def spawn(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if platform.system() == 'Windows' else {'start_new_session': True}
    return subprocess.Popen(command, cwd=cwd, env=env, **options)

def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if platform.system() == 'Windows':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if platform.system() != 'Windows':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        else:
            process.kill()
        process.wait(timeout=5)

def check_python() -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError('Python 3.12 or newer is required. Python 3.12 is used by the Windows and Ubuntu checks.')
