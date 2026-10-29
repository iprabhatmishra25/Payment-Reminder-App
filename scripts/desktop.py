"""Build the UI or native installers using the same command style on both OSes."""
import argparse
import platform
import subprocess
from platform_tools import ROOT, npm_command

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['build','package-windows','package-ubuntu'])
    args=parser.parse_args()
    expected={'package-windows':'Windows','package-ubuntu':'Linux'}.get(args.command)
    if expected and platform.system()!=expected:
        raise RuntimeError('Build this installer on its native operating system, or use the Windows and Ubuntu desktop workflow on GitHub.')
    script={'build':'build','package-windows':'package:win','package-ubuntu':'package:linux'}[args.command]
    raise SystemExit(subprocess.run(npm_command('run',script),cwd=ROOT/'apps/desktop').returncode)

if __name__=='__main__':
    try:main()
    except RuntimeError as exc:raise SystemExit(str(exc))
