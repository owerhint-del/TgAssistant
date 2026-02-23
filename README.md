# TgAssistant

Transcribe and summarize video/audio from private Telegram channels -- automatically.

TgAssistant downloads media through your personal Telegram account, transcribes speech to timestamped text using Whisper, generates structured summaries via Claude AI, and exports everything as PDF files.

---

## Features

- **Download** media from private Telegram channels using your own account
- **Transcribe** speech to near-verbatim text with timestamps (powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper))
- **Summarize** transcripts into structured notes (powered by [Claude AI](https://www.anthropic.com/claude))
- **Export** transcript and summary as separate PDF files
- **Web UI** for submitting links, tracking progress, and downloading results
- **CLI** with single-link, batch, and watch modes
- **Job tracking** with status history, filtering, and retry support
- **Idempotent** -- re-submitting the same link skips already-completed work

---

## Requirements

- **Python 3.11+** -- check with `python3 --version`
- **ffmpeg** -- required for audio extraction
- **Telegram account** -- you must be a member of the channel you want to transcribe
- **Telegram API credentials** -- obtained from [my.telegram.org](https://my.telegram.org) (see [Configuration](#configuration))
- **Anthropic API key** -- obtained from [console.anthropic.com](https://console.anthropic.com/keys)

### Installing ffmpeg

**macOS (Homebrew):**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt update && sudo apt install ffmpeg
```

**Windows:**
Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to your PATH.

---

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/TgAssistant.git
cd TgAssistant

# Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

TgAssistant uses a setup wizard that walks you through all required settings.

### Step 1 -- Get Telegram API Credentials

You need your own Telegram API credentials. They are **not** included with the project.

1. Go to [https://my.telegram.org](https://my.telegram.org) and sign in with your phone number
2. Navigate to **"API development tools"**
3. Create a new application (the name and description can be anything)
4. Note down your **API ID** (a number) and **API Hash** (a hex string)

These credentials identify your application to Telegram. Keep them private.

### Step 2 -- Get an Anthropic API Key

1. Go to [https://console.anthropic.com/keys](https://console.anthropic.com/keys)
2. Click **"Create key"**
3. Copy the key (it starts with `sk-ant-`)

### Step 3 -- Run the Setup Wizard

```bash
source .venv/bin/activate
python run.py --setup
```

The wizard will prompt you for:
- Telegram API ID and API Hash
- Phone number linked to your Telegram account
- Anthropic API key
- Output directory for PDF files

After entering your phone number, Telegram will send a verification code -- enter it in the terminal. This is a one-time step; the session is saved locally for future use.

### Optional: YAML Configuration

For advanced use, copy and edit the example config:

```bash
cp config.example.yaml config.yaml
```

Configuration priority: CLI arguments > `.env` > `config.yaml` > defaults.

> **Warning:** If `config.yaml` contains secrets, make sure it stays in `.gitignore`.

---

## Usage

> Always activate the virtual environment before running:
> ```bash
> source .venv/bin/activate
> ```

### CLI Modes

#### Process a single link

```bash
python run.py --link https://t.me/c/1234567890/42
```

You can pass multiple links at once:

```bash
python run.py --link https://t.me/c/1234567890/42 https://t.me/c/1234567890/43
```

#### Watch mode (continuous input)

```bash
python run.py --watch
```

Paste links one at a time and press Enter. Exit with `Ctrl+C`.

#### Web interface

```bash
python run.py --web
```

Opens a web UI at [http://localhost:8000](http://localhost:8000) where you can submit links, monitor progress in real time, and download finished PDFs.

**Custom host and port:**

```bash
python run.py --web --host 0.0.0.0 --port 9000
```

> **Security note:** Using `--host 0.0.0.0` exposes the web interface to your entire local network (and potentially beyond, depending on your firewall). Only use this on trusted networks. For remote access, consider placing TgAssistant behind a reverse proxy with authentication.

### Utility Commands

#### Check configuration

```bash
python run.py --check-config
```

Validates all credentials, checks for ffmpeg, and verifies the output directory.

#### View job history

```bash
python run.py --status
python run.py --status --filter done
python run.py --status --filter error
python run.py --status --filter pending
python run.py --status --filter in_progress
```

#### Retry a failed job

```bash
python run.py --retry <job_id>
```

The job ID is shown in `--status` output (the first 8 characters are enough).

To restart processing from scratch instead of resuming:

```bash
python run.py --retry <job_id> --from-start
```

### Additional Flags

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to a custom `config.yaml` |
| `--output-dir DIR` | Override the PDF output directory |
| `--log-level LEVEL` | Set log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--no-cleanup` | Keep temporary files after processing |

---

## Output

PDF files are saved to the output directory specified during setup.

File naming convention:
```
YYYY-MM-DD_<channel_id>_<message_id>_transcript.pdf   # full transcription
YYYY-MM-DD_<channel_id>_<message_id>_summary.pdf      # structured summary
```

---

## Processing Times

| Media Length | Transcription | Summary | Total |
|-------------|---------------|---------|-------|
| 30 min | ~8--12 min | ~1 min | ~10--15 min |
| 1 hour | ~15--25 min | ~2 min | ~20--30 min |
| 2 hours | ~30--50 min | ~3 min | ~35--55 min |

> On the first run, Whisper downloads the language model (~3 GB). This adds 5--15 minutes depending on your connection speed.

Times measured on CPU (`int8` compute type). GPU acceleration significantly reduces transcription time.

---

## Project Structure

```
TgAssistant/
├── run.py                    # Entry point (CLI + web)
├── requirements.txt          # Python dependencies
├── config.example.yaml       # Example configuration
├── SECURITY.md               # Security policy
├── app/
│   ├── auth/
│   │   └── session_manager.py    # Telegram session handling
│   ├── db/
│   │   └── models.py             # Job database models
│   ├── pipeline/
│   │   ├── downloader.py         # Media download from Telegram
│   │   ├── transcriber.py        # Speech-to-text (Whisper)
│   │   ├── summarizer.py         # Text summarization (Claude)
│   │   └── pdf_exporter.py       # PDF generation
│   ├── queue/
│   │   └── scheduler.py          # Job scheduling and watch mode
│   ├── utils/
│   │   ├── url_parser.py         # Telegram URL parsing
│   │   ├── cleanup.py            # Temp file cleanup
│   │   └── retry.py              # Retry logic
│   └── logger.py                 # Logging with secret masking
├── tests/                    # Test suite
├── fonts/                    # PDF fonts (downloaded during setup)
├── sessions/                 # Telegram sessions (gitignored)
├── data/                     # SQLite database (gitignored)
└── logs/                     # Application logs (gitignored)
```

---

## Troubleshooting

### "Telegram session not found"

Run the setup wizard again to re-authenticate:

```bash
python run.py --setup
```

### "ffmpeg not found"

Install ffmpeg using your system's package manager (see [Requirements](#requirements)).

### "Invalid ANTHROPIC_API_KEY"

1. Go to [console.anthropic.com/keys](https://console.anthropic.com/keys)
2. Create a new key
3. Update the `ANTHROPIC_API_KEY` value in your `.env` file

### "No access to channel"

Your Telegram account must be a member of the channel. Verify you can access the content in the Telegram app before trying to process it.

### "Invalid or expired Telegram API credentials"

1. Go to [my.telegram.org](https://my.telegram.org) and verify your API ID and Hash
2. Update the values in your `.env` file
3. Run `python run.py --setup` to re-authenticate

### Other errors

Check the detailed log file at `logs/tgassistant.log` for full stack traces and diagnostic information.

---

## Security

- **`.env`** contains your API keys and phone number -- never share or commit this file
- **`sessions/`** contains an authorized Telegram session -- treat it like a password
- All secrets are automatically masked in log output
- File permissions are set to owner-only (`600`/`700`) during setup

For the full security policy, incident response steps, and credential rotation procedures, see [SECURITY.md](SECURITY.md).

---

## License

This project is licensed under the [MIT License](LICENSE).
