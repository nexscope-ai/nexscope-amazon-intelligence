# NexScope Amazon Intelligence

An OpenAI Codex plugin containing 40 Amazon ecommerce skills for product, market, keyword, competitor, review, sales, policy, category, and advertising workflows across supported Amazon marketplaces.

## Requirements

- A paid NexScope subscription
- OpenAI Codex CLI
- Windows x64, macOS x64, or macOS arm64
- Python 3.11 or later for the commercial installer

## Install

Use the commercial release archive for your platform. It contains the installer, encrypted plugin payload, reviewed offline dependencies, and [installation instructions](OFFLINE_INSTALL.md).

- macOS: open `install.command` or run `./install.command`.
- Windows: open `install.bat`.

Choose **Install or connect account**. The installer opens the NexScope authorization page, verifies the subscription, installs the licensed payload, and registers the plugin with Codex. Restart Codex after installation.

Example prompts:

- `Find promising Amazon US niches with strong demand and moderate competition.`
- `Analyze the keywords and traffic sources for this Amazon ASIN.`
- `Compare recurring customer complaints for these Amazon products.`

## Contents

- `.codex-plugin/plugin.json` — plugin manifest and public listing metadata
- `skills/` — 40 independently loadable Amazon skills
- `assets/` — plugin icon and light/dark logos
- `SUBMISSION.md` — public listing copy, test cases, and publisher-side release requirements

The skills are synchronized from [`nexscope-ai/nexscope-ecommerce-skills`](https://github.com/nexscope-ai/nexscope-ecommerce-skills) at commit `e380b29273c2d1cd77e3335c2979c6310f484db4`. The two upstream skills absent from the earlier local snapshot, `amazon-ads-manager` and `amazon-category-lookup`, are included.

## Authentication

Paid NexScope subscribers automatically receive plugin access. The browser device flow verifies the subscription, binds the installation to the subscriber's existing API key, and stores only a revocable installation token in macOS Keychain or Windows Credential Manager:

```sh
python3 runtime/installer.py connect
python3 runtime/installer.py update
```

All skill scripts load the connected device credential first. Developers can explicitly retain the original environment-variable flow:

```sh
export NEXSCOPE_AUTH_MODE="environment"
export NEXSCOPE_PROXY_BASE="https://api.nexscope.ai/"
export NEXSCOPE_API_KEY="<your_api_key>"
```

Never commit API keys or include them in prompts, logs, fixtures, or bug reports. There is no plaintext fallback when the system credential store is unavailable.

If the account has no active subscription, the authorization page does not activate the device and directs the user to subscribe. The plugin does not create or display a separate API key.

Other local commands are `status`, `doctor`, `disconnect`, and `uninstall`. Use `disconnect --local-only` only when the service is unreachable; the remote device seat then remains occupied until it is removed on the account page.

## Security

- Installation credentials are stored in macOS Keychain or Windows Credential Manager; plaintext credential stores are rejected.
- Authenticated redirects cannot leave the original API host.
- Release archives and encrypted payloads are verified before installation.
- Production signing keys and payload keys are not stored in this repository.

Run `python3 scripts/self_check.py` before committing or building a release. Keep `.env` files, private keys, API keys, generated `payload-key.private.json` files, and release output out of Git.

## Commercial release build

Prepare reviewed offline wheels for both supported platforms, then run:

```sh
python3 scripts/build_release.py 1.1.1 \
  --wheel-dir /path/to/reviewed-wheels \
  --trusted-keys /path/to/reviewed-trusted-keys.json \
  --artifact-base-url https://github.com/nexscope-ai/nexscope-amazon-intelligence/releases/download/v1.1.1
python3 scripts/self_check.py
```

The build emits the three frozen platform/architecture archives, an exact ManifestV1 metadata file, and a mode-`0600` private payload-key file. It injects the reviewed public key set without storing production trust material in Git. Import the private file into the NexScope release service, then securely delete it.

## Install locally

The commercial archive contains a thin installer, encrypted payload, reviewed offline dependencies, and installation instructions. Plaintext skills are installed only after the service grants a licensed payload key.

For troubleshooting, run `.runtime-venv/bin/python runtime/installer.py doctor` on macOS or `.runtime-venv\Scripts\python.exe runtime\installer.py doctor` on Windows. Diagnostic output does not include credentials.

## Publisher

ECOCREATE TECHNOLOGY PTE. LTD.  
Support: [service@nexscope.ai](mailto:service@nexscope.ai)  
[Website](https://www.nexscope.ai/) · [Privacy](https://www.nexscope.ai/privacy) · [Terms](https://www.nexscope.ai/terms)
