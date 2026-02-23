"""
Мастер первоначальной настройки (python run.py --setup).
Пошагово проводит пользователя через всю конфигурацию.
"""
import asyncio
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


BANNER = """
╔══════════════════════════════════════════════════════╗
║          TgAssistant — Первоначальная настройка       ║
╚══════════════════════════════════════════════════════╝
"""

DEJAVU_URL = (
    "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf"
)
DEJAVU_BOLD_URL = (
    "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"
)


def _print_step(n: int, total: int, text: str):
    print(f"\n{'─'*54}")
    print(f"  Шаг {n}/{total}: {text}")
    print(f"{'─'*54}")


def _check_ffmpeg() -> bool:
    result = subprocess.run(
        ["ffmpeg", "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode == 0


def _check_python_deps() -> list:
    missing = []
    for pkg, import_name in [
        ("telethon", "telethon"),
        ("faster_whisper", "faster_whisper"),
        ("anthropic", "anthropic"),
        ("fpdf", "fpdf"),
        ("dotenv", "dotenv"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    return missing


def _download_font(url: str, dest: str) -> bool:
    try:
        print(f"  Скачиваю {Path(dest).name}...", end=" ", flush=True)
        urllib.request.urlretrieve(url, dest)
        print("✓")
        return True
    except Exception as e:
        print(f"✗ ({e})")
        return False


def _write_env(data: dict, env_path: str = ".env") -> None:
    lines = []
    if Path(env_path).exists():
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()

    existing = {}
    for line in lines:
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip()

    existing.update(data)

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("# TgAssistant конфигурация (сгенерировано --setup)\n\n")
        for k, v in existing.items():
            f.write(f"{k}={v}\n")

    os.chmod(env_path, 0o600)
    print(f"  Сохранено в {env_path} (права: 600)")


def _secure_sessions_dir(sessions_dir: str = "./sessions"):
    Path(sessions_dir).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(sessions_dir, 0o700)
    except OSError:
        pass


def run_setup():
    print(BANNER)
    print("Отвечай на вопросы и нажимай Enter.")
    print("Для пропуска шага нажми Enter (если значение уже задано).\n")

    TOTAL_STEPS = 5

    # ── Шаг 1: Проверка зависимостей ─────────────────────────
    _print_step(1, TOTAL_STEPS, "Проверка зависимостей")

    missing_deps = _check_python_deps()
    if missing_deps:
        print(f"\n  ✗ Не установлены Python-пакеты: {', '.join(missing_deps)}")
        print("\n  Установи их командой:")
        print("  pip install -r requirements.txt\n")
        print("  Затем запусти --setup снова.")
        sys.exit(1)
    else:
        print("  ✓ Python-зависимости установлены.")

    if _check_ffmpeg():
        print("  ✓ ffmpeg найден.")
    else:
        print("""
  ✗ ffmpeg НЕ найден.

  Установи ffmpeg:
    1. Открой Терминал
    2. Установи Homebrew (если нет):
       /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    3. Установи ffmpeg:
       brew install ffmpeg
    4. Запусти --setup снова.
        """)
        sys.exit(1)

    # ── Шаг 2: Номер телефона ─────────────────────────────────
    _print_step(2, TOTAL_STEPS, "Номер телефона Telegram")

    env_data = {}

    current_env_phone = os.getenv("TG_PHONE", "")
    phone_input = input(
        f"  Phone number [{current_env_phone or 'e.g. +49123456789'}]: "
    ).strip()
    phone = phone_input or current_env_phone
    if not phone:
        print("  ✗ Phone number is required.")
        sys.exit(1)
    env_data["TG_PHONE"] = phone

    # ── Шаг 3: Anthropic API (optional) ─────────────────────
    _print_step(3, TOTAL_STEPS, "Anthropic API ключ (необязательно)")

    print("""
  Ключ нужен только если в будущем понадобится AI-summary.
  Для базовой транскрипции он НЕ нужен.
  Получить: https://console.anthropic.com/keys
  Нажми Enter чтобы пропустить.
""")

    current_key = os.getenv("ANTHROPIC_API_KEY", "")
    key_input = input(
        f"  ANTHROPIC_API_KEY [{'sk-ant-***' if current_key else 'пропустить'}]: "
    ).strip()
    anthropic_key = key_input or current_key
    if anthropic_key:
        env_data["ANTHROPIC_API_KEY"] = anthropic_key
        print("  ✓ Ключ сохранён.")
    else:
        print("  → Пропущено (не нужен для транскрипции).")

    # ── Шаг 4: Папка для PDF и шрифты ─────────────────────────
    _print_step(4, TOTAL_STEPS, "Папка для сохранения PDF")

    default_output = os.getenv(
        "OUTPUT_DIR",
        str(Path.home() / "Documents" / "TgAssistant")
    )
    output_input = input(f"  Папка для PDF [{default_output}]: ").strip()
    output_dir = output_input or default_output
    Path(output_dir).expanduser().mkdir(parents=True, exist_ok=True)
    env_data["OUTPUT_DIR"] = output_dir
    print(f"  ✓ PDF будут сохраняться в: {output_dir}")

    # Шрифты
    fonts_dir = Path("./fonts")
    fonts_dir.mkdir(exist_ok=True)

    regular_path = fonts_dir / "DejaVuSans.ttf"
    bold_path = fonts_dir / "DejaVuSans-Bold.ttf"

    if regular_path.exists():
        print("  ✓ DejaVuSans.ttf уже есть.")
    else:
        _download_font(DEJAVU_URL, str(regular_path))

    if bold_path.exists():
        print("  ✓ DejaVuSans-Bold.ttf уже есть.")
    else:
        _download_font(DEJAVU_BOLD_URL, str(bold_path))

    # ── Шаг 5: Авторизация Telegram ───────────────────────────
    _print_step(5, TOTAL_STEPS, "Авторизация в Telegram")

    print(f"""
  Сейчас Telegram отправит код подтверждения на номер {phone}.
  Это безопасно — код нужен только для входа, он нигде не сохраняется.
""")

    # Записываем .env до авторизации, чтобы клиент мог прочитать ключи
    _write_env(env_data)
    _secure_sessions_dir()

    # Авторизация
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        from app.config import load_config
        from app.auth.session_manager import interactive_login

        cfg = load_config()
        cfg.tg_phone = phone

        from app.utils.async_utils import run_sync, close_loop
        client = run_sync(interactive_login(cfg))
        run_sync(client.disconnect())
        close_loop()
        print("  ✓ Telegram сессия создана и сохранена.")

    except Exception as e:
        print(f"\n  ✗ Ошибка авторизации: {e}")
        print("  Проверь номер телефона и попробуй запустить --setup снова.")
        sys.exit(1)

    # ── Финал ─────────────────────────────────────────────────
    print(f"""
{'═'*54}
  ✓ Настройка завершена успешно!
{'═'*54}

  Теперь ты можешь обрабатывать Telegram-материалы:

  Одна ссылка:
    python run.py --link https://t.me/c/<chat_id>/<msg_id>

  Несколько ссылок подряд:
    python run.py --watch

  Проверка конфигурации:
    python run.py --check-config

  PDF будут сохраняться в:
    {output_dir}
""")
