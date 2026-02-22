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

    TOTAL_STEPS = 6

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

    # ── Шаг 2: Telegram API ────────────────────────────────────
    _print_step(2, TOTAL_STEPS, "Telegram API ключи")

    print("""
  Для работы нужны api_id и api_hash твоего приложения Telegram.
  Получить их бесплатно (занимает 2 минуты):

  1. Открой в браузере: https://my.telegram.org
  2. Войди своим номером телефона (тем же, что используешь в Telegram)
  3. Нажми: "API development tools"
  4. Заполни форму (название приложения — любое, например "MyApp")
  5. Нажми "Create application"
  6. Скопируй App api_id (число) и App api_hash (длинная строка)
""")

    env_data = {}
    current_env_api_id = os.getenv("TG_API_ID", "")
    current_env_api_hash = os.getenv("TG_API_HASH", "")
    current_env_phone = os.getenv("TG_PHONE", "")

    api_id_input = input(
        f"  App api_id [{current_env_api_id or 'введи число'}]: "
    ).strip()
    api_id = api_id_input or current_env_api_id
    if not api_id:
        print("  ✗ api_id обязателен.")
        sys.exit(1)
    try:
        int(api_id)
    except ValueError:
        print("  ✗ api_id должен быть числом.")
        sys.exit(1)
    env_data["TG_API_ID"] = api_id

    api_hash_input = input(
        f"  App api_hash [{'***' if current_env_api_hash else 'введи строку'}]: "
    ).strip()
    api_hash = api_hash_input or current_env_api_hash
    if not api_hash:
        print("  ✗ api_hash обязателен.")
        sys.exit(1)
    env_data["TG_API_HASH"] = api_hash

    phone_input = input(
        f"  Номер телефона [{current_env_phone or 'например +49123456789'}]: "
    ).strip()
    phone = phone_input or current_env_phone
    if not phone:
        print("  ✗ Номер телефона обязателен.")
        sys.exit(1)
    env_data["TG_PHONE"] = phone

    # ── Шаг 3: Anthropic API ──────────────────────────────────
    _print_step(3, TOTAL_STEPS, "Anthropic API ключ")

    print("""
  Ключ нужен для генерации конспекта через Claude.
  Получить:
  1. Открой: https://console.anthropic.com/keys
  2. Нажми "Create key"
  3. Скопируй ключ (начинается с sk-ant-)
""")

    current_key = os.getenv("ANTHROPIC_API_KEY", "")
    key_input = input(
        f"  ANTHROPIC_API_KEY [{'sk-ant-***' if current_key else 'введи ключ'}]: "
    ).strip()
    anthropic_key = key_input or current_key
    if not anthropic_key:
        print("  ✗ API ключ обязателен.")
        sys.exit(1)
    env_data["ANTHROPIC_API_KEY"] = anthropic_key

    # ── Шаг 4: Папка для PDF ──────────────────────────────────
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

    # ── Шаг 5: Шрифты ─────────────────────────────────────────
    _print_step(5, TOTAL_STEPS, "Скачивание шрифтов для PDF")

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

    # ── Шаг 6: Авторизация Telegram ───────────────────────────
    _print_step(6, TOTAL_STEPS, "Авторизация в Telegram")

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
        cfg.tg_api_id = int(api_id)
        cfg.tg_api_hash = api_hash
        cfg.tg_phone = phone

        from app.utils.async_utils import run_sync, close_loop
        client = run_sync(interactive_login(cfg))
        run_sync(client.disconnect())
        close_loop()
        print("  ✓ Telegram сессия создана и сохранена.")

    except Exception as e:
        print(f"\n  ✗ Ошибка авторизации: {e}")
        print("  Проверь api_id, api_hash и номер телефона, затем запусти --setup снова.")
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
