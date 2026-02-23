# Security Policy

## Sensitive Data

| File / Variable | Contents | Storage |
|---|---|---|
| `TG_API_ID` / `TG_API_HASH` | Telegram API credentials | `.env` |
| `TG_PHONE` | Phone number linked to Telegram | `.env` |
| `ANTHROPIC_API_KEY` | Claude API key | `.env` |
| `sessions/*.session` | Authorized Telegram session | `sessions/` |

## Rules

### Never commit to git:
```
.env
config.yaml  (if it contains secrets)
sessions/
*.session
```

The `.gitignore` already excludes these — **do not remove those entries**.

### File permissions (set automatically by `--setup`):
```bash
chmod 600 .env               # owner-only
chmod 700 sessions/          # owner-only
chmod 600 sessions/*.session # owner-only
```

### Log masking:
All logs automatically redact:
- `api_hash` → `api_hash=***`
- `ANTHROPIC_API_KEY` → `sk-ant-***`
- Phone number → `+49***789`
- Session string → `[SESSION_REDACTED]`

## If a session is compromised

1. Open Telegram → Settings → Devices
2. Find the "TgAssistant" session and terminate it
3. Delete the file: `rm sessions/tgassistant.session`
4. Re-run: `python run.py --setup`

## If ANTHROPIC_API_KEY is leaked

1. Go to https://console.anthropic.com/keys
2. Click "Revoke" next to the key
3. Create a new key
4. Update `.env`: `ANTHROPIC_API_KEY=new_key`

## If TG_API_HASH is leaked

1. Go to https://my.telegram.org → API development tools
2. Recreate the application or change the hash
3. Update `.env`

## Reporting a Vulnerability

If you find a security vulnerability, please report it responsibly:
- Open a **private security advisory** on GitHub (Security tab → Report a vulnerability)
- Or email: [INSERT CONTACT EMAIL]

Please do not open public issues for security vulnerabilities.
