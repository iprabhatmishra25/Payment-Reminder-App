# Run Duewise on Windows and Ubuntu

Version 0.1.1 provides native x64 desktop packages for Windows and Ubuntu. Both connect to the same FastAPI backend and share the same company, payment, and reminder records. The installer does not contain production data or private email/database credentials.

## Choose how to run it

| Use | Windows | Ubuntu |
|---|---|---|
| Installed desktop client | `Duewise-0.1.1-Windows-x64-Setup.exe` | `Duewise-0.1.1-Ubuntu-x64.deb` (recommended), or `.AppImage` |
| Source/demo | Python 3.12 and Node.js 24 LTS | Python 3.12, venv support, and Node.js 24 LTS |
| Shared production server | Linux containers in Docker Desktop/WSL2, or a separate Ubuntu server | Docker Engine and Compose |

The automated native jobs use `windows-latest` and Ubuntu 24.04 x64 runners. They build the installers, install the Windows/Ubuntu packages, and exercise the actual desktop against a temporary local API. ARM64 and macOS are not part of this release's platform matrix.

## A. Install the desktop app

Get the packages from the latest successful [Windows and Ubuntu desktop workflow](https://github.com/iprabhatmishra25/Payment-Reminder-App/actions/workflows/desktop-release.yml). Open its run, then download `Duewise-Windows-x64` or `Duewise-Ubuntu-x64` under Artifacts. Extract the artifact ZIP before installing. GitHub may require you to be signed in to download artifacts.

### Windows

1. Open the `...Windows-x64-Setup.exe` installer.
2. Choose your installation folder. The app installs for the current user and creates Desktop and Start-menu shortcuts.
3. Open Duewise from the shortcut.
4. On the login screen, choose **Server** and enter the HTTPS origin provided by the person hosting your backend.
5. Sign in with a real staff account. Production accounts must complete MFA when required.

The backend must already be running. Installed desktop users do not need Node.js, Python, PostgreSQL, Redis, or Docker on their own machines when connecting to a shared server.

The CI build is unsigned unless signing secrets are configured. Windows can display a publisher/SmartScreen notice. For distribution to an organization, configure an approved signing certificate; do not disable Windows security settings.

### Ubuntu

Install the recommended `.deb` package from a terminal in the download folder:

```bash
sudo apt install ./Duewise-0.1.1-Ubuntu-x64.deb
```

Open Duewise from the Applications menu and set the backend server on the login screen.

Alternatively, use the AppImage:

```bash
chmod +x Duewise-0.1.1-Ubuntu-x64.AppImage
./Duewise-0.1.1-Ubuntu-x64.AppImage
```

If the AppImage reports a missing FUSE component or sandbox setup problem on your Ubuntu installation, use the `.deb` package, which installs the native integration and sandbox helper. Do not run the application as root or add `--no-sandbox` as a normal workaround.

## B. Run the complete demo from source

This is a local learning/demo workspace. Preview reminders are recorded but no real email is sent. Use a graphical session as a normal user.

### Windows PowerShell or Command Prompt

Install Python 3.12, Node.js 24 LTS (including npm), and Git. Reopen the terminal after installation.

```powershell
git clone https://github.com/iprabhatmishra25/Payment-Reminder-App.git
cd Payment-Reminder-App
py -3.12 scripts/setup.py --demo
py -3.12 scripts/run_local.py --demo
```

You do not need to activate `.venv` or change PowerShell's execution policy. Alternatively, double-click `Start-Demo-Windows.cmd` in the project folder; it installs dependencies on first use, then launches the demo.

### Ubuntu terminal

Install Python 3.12 with venv support, Node.js 24 LTS, npm, and Git. For Ubuntu 24.04, venv support can be installed with:

```bash
sudo apt install python3-venv
```

Then:

```bash
git clone https://github.com/iprabhatmishra25/Payment-Reminder-App.git
cd Payment-Reminder-App
python3 scripts/setup.py --demo
python3 scripts/run_local.py --demo
```

Alternatively run `bash start-demo-ubuntu.sh`. Setup never overwrites an existing `.env`. If an existing configuration is not a demo configuration, do not force sample records into it; use a separate checkout for the demo.

Demo login:

```text
demo@duewise.example.com
DemoOnly!2026
```

The launcher starts the API, preview worker, development UI, and Electron directly. It handles `.venv/Scripts/python.exe` versus `.venv/bin/python` internally, runs npm through Node rather than `npm.cmd`, and stops only the process trees it created.

## C. Try an installer with the local demo backend

After source setup, keep this command running in a terminal:

Windows:

```powershell
py -3.12 scripts/run_local.py --demo --backend-only
```

Ubuntu:

```bash
python3 scripts/run_local.py --demo --backend-only
```

Open the installed app, set its server to `http://127.0.0.1:8000`, and use the demo login. Press Ctrl+C in the terminal to stop that local backend.

## D. Start an empty workspace and manage the backend

Use `scripts/setup.py` without `--demo` for an empty local workspace. Common commands are the same on both OSes; use `py -3.12` on Windows or `python3` on Ubuntu:

| Task | Command after the Python executable |
|---|---|
| Create/upgrade tables | `scripts/backend.py migrate` |
| Create initial admin | `scripts/backend.py create-admin --email admin@your-company.com --name "Your Name"` |
| Run backend tests | `scripts/backend.py test` |
| Reset an account password interactively | `scripts/backend.py reset-password --email admin@your-company.com` |
| Run local preview worker | `scripts/backend.py preview-worker` |
| Build Windows installer on Windows | `scripts/desktop.py package-windows` |
| Build Ubuntu installers on Ubuntu | `scripts/desktop.py package-ubuntu` |

Passwords are entered interactively. Never put real credentials in source files or GitHub commits.

## E. Monthly reminders with either desktop closed

Keep the production API, PostgreSQL, Redis, Celery worker, and one scheduler on an always-on server. Windows and Ubuntu clients can close without stopping that server.

Celery workers are not supported natively on Windows. Host them on Ubuntu, or run the supplied Linux containers using Docker Desktop's WSL2 backend. The local Windows demo uses a preview-only Python worker instead of pretending that native Windows Celery is a supported production service.

Configure server credentials and start the shared services using the README's Docker Compose instructions. This does not require Docker on every desktop client.

## Troubleshooting

- **Port 8000/5173 already in use:** stop the specific existing development server. The launcher refuses to terminate unrelated applications.
- **No `py -3.12` on Windows:** install Python 3.12 with the Python launcher, or use a working Python 3.12+ executable explicitly.
- **Node/npm not found:** install the standard Node.js 24 LTS distribution and reopen the terminal.
- **Unable to connect:** check the backend is running and the desktop Server origin is correct. Remote servers require HTTPS; loopback development may use HTTP.
- **No email arrives in demo:** expected. Preview mode never sends messages or counts them as successful reminders.
- **Updates:** pull new source and rerun setup for development. Installed clients require installing the newer package; automatic self-updates are not included.
