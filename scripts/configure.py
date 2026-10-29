"""Create local configuration once. Does not overwrite existing secrets."""
from pathlib import Path
import argparse
import base64
import secrets

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--demo',action='store_true')
parser.add_argument('--production',action='store_true')
args=parser.parse_args()
target=root/'.env'
if target.exists():
    raise SystemExit('.env already exists; edit it directly. Existing configuration was not changed.')
if args.demo and args.production:
    raise SystemExit('Demo and production cannot be combined.')
text=(root/'.env.example').read_text()
text=text.replace('ENCRYPTION_KEY=\n','ENCRYPTION_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()+'\n')
text=text.replace('POSTGRES_PASSWORD=\n','POSTGRES_PASSWORD='+secrets.token_urlsafe(36)+'\n')
if args.demo:text=text.replace('DEMO_MODE=false','DEMO_MODE=true')
if args.production:text=text.replace('APP_ENV=development','APP_ENV=production')
target.write_text(text)
target.chmod(0o600)
print('Created .env. No secrets printed. Keep this file private and back it up securely.')
