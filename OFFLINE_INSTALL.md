# NexScope Amazon Intelligence Installation

The installer requires Windows x64, macOS x64, or macOS arm64 and Python 3.11 or later. It does not modify the system `PATH`.

- macOS: open `install.command` or run `./install.command` in Terminal.
- Windows: open `install.bat`.

The installer loads pinned dependencies only from the bundled `wheels/` directory, then displays options to install, update, inspect status, disconnect, or uninstall. Connecting an account opens the NexScope authorization page. Credentials are stored in macOS Keychain or Windows Credential Manager, never in a configuration file. If the browser does not open, use the URL and short code shown in the terminal.

For diagnostics, run `.runtime-venv/bin/python runtime/installer.py doctor` on macOS or `.runtime-venv\Scripts\python.exe runtime\installer.py doctor` on Windows. Diagnostic output does not include credentials.
