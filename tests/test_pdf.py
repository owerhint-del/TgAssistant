"""
Тесты генерации PDF с кириллицей.
"""
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


FONT_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf"


@pytest.fixture(scope="module")
def font_path(tmp_path_factory):
    """Скачивает шрифт один раз для всех тестов."""
    fonts_dir = tmp_path_factory.mktemp("fonts")
    font = fonts_dir / "DejaVuSans.ttf"
    if not font.exists():
        try:
            urllib.request.urlretrieve(FONT_URL, str(font))
        except Exception:
            pytest.skip("Нет интернета для скачивания шрифта")
    return str(font)


@pytest.fixture(scope="module")
def cfg(font_path, tmp_path_factory):
    from app.config import Config
    out_dir = str(tmp_path_factory.mktemp("output"))
    return Config(
        pdf_font_path=font_path,
        pdf_bold_font_path="",
        pdf_page_size="A4",
        output_dir=out_dir,
    )


@pytest.fixture
def link():
    from app.utils.url_parser import TelegramLink
    return TelegramLink(chat_id=1775135187, msg_id=1197, raw_url="https://t.me/c/1775135187/1197")


def test_transcript_pdf_created(cfg, link):
    """PDF транскрипта создаётся и не пустой."""
    from app.pipeline.pdf_exporter import PDFExporter
    from app.pipeline.transcriber import TranscriptResult, Segment

    transcript = TranscriptResult(
        segments=[
            Segment(start=0.0, end=3.5, text="Здравствуйте, сегодня мы поговорим о питании кошек."),
            Segment(start=3.5, end=7.0, text="Кошки — облигатные хищники."),
            Segment(start=10.0, end=14.0, text="Белок должен составлять не менее 30% рациона."),
        ],
        language="ru",
        model_used="large-v3",
        duration_sec=14.0,
    )

    exporter = PDFExporter(cfg)
    path = exporter.export_transcript(transcript, link)

    assert Path(path).exists()
    assert Path(path).stat().st_size > 1000  # минимум 1 КБ
    assert path.endswith("_transcript.pdf")


def test_summary_pdf_created(cfg, link):
    """PDF конспекта создаётся корректно."""
    from app.pipeline.pdf_exporter import PDFExporter

    summary_text = """## Обзор материала

Лекция посвящена правильному питанию кошек.

## Основные разделы и темы

### Белки

Кошки — облигатные хищники. Белок должен составлять 30–40% рациона.

### Жиры

Жиры обеспечивают энергию и поддерживают здоровье кожи и шерсти.

## Ключевые понятия и определения

| Термин | Определение |
|--------|-------------|
| Таурин | Аминокислота, необходимая для зрения и сердца кошек |
| Облигатный хищник | Животное, которое не может жить без мяса |

## Практические выводы

- Кормить натуральным мясом или качественным сухим кормом
- Следить за количеством таурина в составе

## Вопросы для самопроверки

1. Почему кошки не могут быть вегетарианцами?
2. Что такое таурин и зачем он нужен?
"""

    exporter = PDFExporter(cfg)
    path = exporter.export_summary(summary_text, link, model_used="claude-sonnet-4-6")

    assert Path(path).exists()
    assert Path(path).stat().st_size > 1000
    assert path.endswith("_summary.pdf")


def test_pdf_filename_format(cfg, link):
    """Имя файла соответствует формату <date>_<chat_id>_<msg_id>_<type>.pdf"""
    from app.pipeline.pdf_exporter import PDFExporter
    from app.pipeline.transcriber import TranscriptResult, Segment

    transcript = TranscriptResult(
        segments=[Segment(start=0.0, end=1.0, text="Тест кириллицы: абвгдеёжзийклмнопрстуфхцчшщъыьэюя")],
        language="ru",
        model_used="test",
        duration_sec=1.0,
    )

    exporter = PDFExporter(cfg)
    path = exporter.export_transcript(transcript, link)
    filename = Path(path).name

    # Формат: 2026-02-22_1775135187_1197_transcript.pdf
    parts = filename.replace(".pdf", "").split("_")
    assert len(parts) >= 4
    assert parts[-1] == "transcript"
    assert parts[-2] == str(link.msg_id)
    assert parts[-3] == str(link.chat_id)


def test_cyrillic_in_pdf(cfg, link):
    """PDF с кириллицей не вызывает ошибок."""
    from app.pipeline.pdf_exporter import PDFExporter

    cyrillic_text = """## Состав крови

Кровь состоит из плазмы и форменных элементов.

### Эритроциты (Erythrozyten)

Красные кровяные тельца. Содержат гемоглобин (Hämoglobin).

### Лейкоциты (Leukozyten)

Белые кровяные тельца. Защищают от инфекций.
"""

    exporter = PDFExporter(cfg)
    # Не должно выбросить исключение
    path = exporter.export_summary(cyrillic_text, link)
    assert Path(path).exists()
