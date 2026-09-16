# Runtime Configuration and Credentials

Managed installations read the device credential from the system credential store. Only developers using `NEXSCOPE_AUTH_MODE=environment` set the variables below. Never place credentials in CLI arguments, saved payloads, logs, or generated reports.

| Variable | Classification |
|---|---|
| `APP_NAME` | runtime configuration |
| `MESSAGE_ID` | runtime configuration |
| `MODE_ID` | runtime configuration |
| `NEXSCOPE_API_KEY` | secret credential |
| `NEXSCOPE_PROXY_BASE` | runtime configuration |
| `NEXSCOPE_WORKSPACES` | runtime configuration |
| `SESSION_ID` | runtime configuration |

Business and tool-service clients use `NEXSCOPE_PROXY_BASE` with `NEXSCOPE_API_KEY`. Third-party connectors use their named provider credentials and endpoints. Missing configuration must fail closed without printing secret values.
