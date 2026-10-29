"""Beginner launcher: migrations, optional demo data, API, Vite and Electron."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--demo',action='store_true',help='Use explicitly labeled sample records and preview email only')
parser.add_argument('--browser-only',action='store_true',help='Start the UI without launching Electron')
args=parser.parse_args()
if not (root/'.env').exists():
    subprocess.run([sys.executable,str(root/'scripts/configure.py')]+(['--demo'] if args.demo else []),check=True)
venv=root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
if not venv.exists():
    raise SystemExit('Create the project virtual environment and install requirements first; see README.md.')
npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
if not npm:raise SystemExit('Install a supported Node.js LTS release first.')
backend=root/'services/backend';desktop=root/'apps/desktop'
if not (desktop/'node_modules').exists():
    raise SystemExit('Run npm ci in apps/desktop first.')
env={**os.environ,'DUEWISE_DEV':'1'}
# Explicitly load the project root .env for all Python processes.
from_env='from dotenv import load_dotenv; load_dotenv('+repr(str(root/'.env'))+'); '
def python_module(module,args_list=[]):
    return [str(venv),'-c',from_env+'import runpy,sys;sys.argv=['+repr(module)+']+'+repr(args_list)+';runpy.run_module('+repr(module)+',run_name="__main__")']
subprocess.run(python_module('alembic',['upgrade','head']),cwd=backend,env=env,check=True)
if args.demo:subprocess.run(python_module('app.cli',['seed-demo']),cwd=backend,env=env,check=True)
processes=[]
def start(command,cwd):
    process=subprocess.Popen(command,cwd=cwd,env=env)
    processes.append(process)
    return process
def wait_for(url,process):
    deadline=time.time()+40
    while time.time()<deadline:
        if process.poll() is not None:raise RuntimeError('A required process stopped. Read the error above.')
        try:
            with urllib.request.urlopen(url,timeout=1) as response:
                if response.status==200:return
        except Exception:time.sleep(.25)
    raise RuntimeError('Timed out waiting for '+url)
try:
    api=start(python_module('uvicorn',['app.main:app','--host','127.0.0.1','--port','8000','--no-proxy-headers']),backend)
    wait_for('http://127.0.0.1:8000/health/live',api)
    # Preview worker deliberately avoids requiring Redis for the beginner demo.
    if args.demo:start(python_module('app.cli',['preview-worker']),backend)
    ui=start([npm,'run','dev'],desktop)
    wait_for('http://127.0.0.1:5173',ui)
    if args.browser_only:
        print('Open http://127.0.0.1:5173. Press Ctrl+C to stop.')
        while all(p.poll() is None for p in processes):time.sleep(.5)
    else:
        desktop_process=start([npm,'run','desktop'],desktop)
        while desktop_process.poll() is None and api.poll() is None and ui.poll() is None:time.sleep(.5)
except KeyboardInterrupt:pass
finally:
    for process in reversed(processes):
        if process.poll() is None:process.terminate()
    for process in processes:
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill()
