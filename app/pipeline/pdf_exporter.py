"""
Генерация PDF через fpdf2 с поддержкой кириллицы (DejaVu шрифт).
Формат имён файлов: <date>_<chat_id>_<msg_id>_transcript.pdf
"""
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from fpdf import FPDF

from app.config import Config
from app.pipeline.transcriber import TranscriptResult, Segment
from app.utils.url_parser import TelegramLink

logger = logging.getLogger("tgassistant.pdf")

# Отступы и размеры
MARGIN = 20
LINE_HEIGHT = 6
HEADING1_SIZE = 18
HEADING2_SIZE = 14
HEADING3_SIZE = 12
BODY_SIZE = 11
TIMESTAMP_SIZE = 9
FOOTER_SIZE = 8


def _make_filename(prefix: str, link: TelegramLink) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"{date}_{link.chat_id}_{link.msg_id}_{prefix}.pdf"


def _ensure_output_dir(output_dir: str) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)


class TgPDF(FPDF):
    """PDF с заголовком и нумерацией страниц."""

    def __init__(self, title: str, font_path: str, bold_font_path: Optional[str], page_size: str):
        super().__init__(orientation="P", unit="mm", format=page_size)
        self.doc_title = title
        self.font_path = font_path
        self.bold_font_path = bold_font_path
        self._fonts_added = False
        self._has_bold = False  # инициализируем явно — защита от AttributeError
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(MARGIN, MARGIN, MARGIN)

    def _add_fonts(self):
        if self._fonts_added:
            return
        self.add_font("DejaVu", "", self.font_path)
        if self.bold_font_path and Path(self.bold_font_path).exists():
            self.add_font("DejaVu", "B", self.bold_font_path)
            self._has_bold = True
        else:
            self._has_bold = False
        self._fonts_added = True

    def setup(self):
        self._add_fonts()
        self.add_page()

    def header(self):
        if not self._fonts_added:
            return
        self.set_font("DejaVu", size=FOOTER_SIZE)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, self.doc_title, align="L")
        self.ln(2)
        self.set_draw_color(200, 200, 200)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.ln(4)
        self.set_text_color(0, 0, 0)

    def footer(self):
        if not self._fonts_added:
            return
        self.set_y(-13)
        self.set_font("DejaVu", size=FOOTER_SIZE)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, f"Стр. {self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)

    def write_h1(self, text: str):
        self.set_font("DejaVu", size=HEADING1_SIZE)
        self.multi_cell(0, 9, text)
        self.ln(4)

    def write_h2(self, text: str):
        self.ln(3)
        font_style = "B" if self._has_bold else ""
        self.set_font("DejaVu", style=font_style, size=HEADING2_SIZE)
        self.multi_cell(0, 8, text)
        self.ln(2)

    def write_h3(self, text: str):
        self.ln(2)
        font_style = "B" if self._has_bold else ""
        self.set_font("DejaVu", style=font_style, size=HEADING3_SIZE)
        self.multi_cell(0, 7, text)
        self.ln(1)

    def write_body(self, text: str):
        self.set_font("DejaVu", size=BODY_SIZE)
        self.multi_cell(0, LINE_HEIGHT, text)
        self.ln(1)

    def write_timestamp(self, text: str):
        """Таймкод [ЧЧ:ММ:СС] — серый мелкий текст."""
        self.set_font("DejaVu", size=TIMESTAMP_SIZE)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, text)
        self.ln(1)
        self.set_text_color(0, 0, 0)

    def write_divider(self):
        self.ln(3)
        self.set_draw_color(220, 220, 220)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.ln(4)

    def write_table_row(self, col1: str, col2: str, is_header: bool = False):
        col1_w = 60
        col2_w = self.w - 2 * MARGIN - col1_w
        font_style = "B" if (is_header and self._has_bold) else ""
        self.set_font("DejaVu", style=font_style, size=BODY_SIZE)
        self.cell(col1_w, LINE_HEIGHT + 1, col1, border=1)
        self.multi_cell(col2_w, LINE_HEIGHT + 1, col2, border=1)


def _render_markdown(pdf: TgPDF, text: str) -> None:
    """
    Простой Markdown рендерер для summary PDF.
    Поддерживает: ## ### обычный текст | таблицы | - списки
    """
    lines = text.split("\n")
    in_table = False
    table_headers_done = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Пустая строка
        if not stripped:
            if in_table:
                in_table = False
                table_headers_done = False
            pdf.ln(2)
            i += 1
            continue

        # ## Заголовок 2
        if stripped.startswith("## "):
            in_table = False
            pdf.write_h2(stripped[3:])
            i += 1
            continue

        # ### Заголовок 3
        if stripped.startswith("### "):
            in_table = False
            pdf.write_h3(stripped[4:])
            i += 1
            continue

        # # Заголовок 1 (на случай если Claude добавит)
        if stripped.startswith("# ") and not stripped.startswith("## "):
            in_table = False
            pdf.write_h1(stripped[2:])
            i += 1
            continue

        # Строки таблицы | col1 | col2 |
        if stripped.startswith("|") and stripped.endswith("|"):
            cols = [c.strip() for c in stripped.strip("|").split("|")]

            # Строка-разделитель |---|---|
            if all(re.match(r"^[-:]+$", c) for c in cols if c):
                i += 1
                continue

            if not in_table:
                in_table = True
                table_headers_done = False

            if not table_headers_done and len(cols) >= 2:
                pdf.write_table_row(cols[0], " | ".join(cols[1:]), is_header=True)
                table_headers_done = True
            elif len(cols) >= 2:
                pdf.write_table_row(cols[0], " | ".join(cols[1:]))
            i += 1
            continue

        in_table = False

        # - Маркированный список
        if stripped.startswith("- ") or stripped.startswith("• "):
            content = stripped[2:].lstrip()
            pdf.set_font("DejaVu", size=BODY_SIZE)
            pdf.multi_cell(0, LINE_HEIGHT, f"  • {content}")
            i += 1
            continue

        # Нумерованный список 1. 2.
        num_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if num_match:
            num = num_match.group(1)
            content = num_match.group(2)
            pdf.set_font("DejaVu", size=BODY_SIZE)
            pdf.multi_cell(0, LINE_HEIGHT, f"  {num}. {content}")
            i += 1
            continue

        # Обычный текст
        # Убираем **bold** разметку (просто убираем звёздочки, fpdf2 не поддерживает inline bold без RTL)
        clean = re.sub(r"\*\*(.*?)\*\*", r"\1", stripped)
        clean = re.sub(r"\*(.*?)\*", r"\1", clean)
        pdf.write_body(clean)
        i += 1


def _render_transcript(pdf: TgPDF, transcript: TranscriptResult) -> None:
    """Рендерит транскрипт с таймкодами."""
    formatted = transcript.format_with_timestamps()
    paragraphs = formatted.split("\n\n")

    for para in paragraphs:
        if not para.strip():
            continue
        lines = para.strip().split("\n")
        if lines and lines[0].startswith("[") and lines[0].endswith("]"):
            pdf.write_timestamp(lines[0])
            body = " ".join(lines[1:])
            pdf.write_body(body)
        else:
            pdf.write_body(para.strip())
        pdf.ln(1)


class PDFExporter:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _make_pdf(self, title: str) -> TgPDF:
        pdf = TgPDF(
            title=title,
            font_path=self.cfg.pdf_font_path,
            bold_font_path=self.cfg.pdf_bold_font_path,
            page_size=self.cfg.pdf_page_size,
        )
        pdf.setup()
        return pdf

    def export_transcript(
        self, transcript: TranscriptResult, link: TelegramLink
    ) -> str:
        """Создаёт transcript PDF. Возвращает путь к файлу."""
        _ensure_output_dir(self.cfg.output_dir)
        filename = _make_filename("transcript", link)
        out_path = os.path.join(self.cfg.output_dir, filename)

        title = f"Транскрипт | {link.chat_id}/{link.msg_id}"
        pdf = self._make_pdf(title)

        # Заголовок документа
        pdf.write_h1("Транскрипт материала")
        pdf.set_font("DejaVu", size=BODY_SIZE)
        pdf.multi_cell(
            0, LINE_HEIGHT,
            f"Канал: {link.chat_id}  |  Сообщение: {link.msg_id}\n"
            f"Язык: {transcript.language}  |  Модель: {transcript.model_used}\n"
            f"Слов: {transcript.word_count}  |  Неразборчиво: {transcript.unrecognized_count}"
        )
        pdf.write_divider()

        _render_transcript(pdf, transcript)

        pdf.output(out_path)
        size = Path(out_path).stat().st_size
        logger.info("Transcript PDF: %s (%.1f КБ)", out_path, size / 1024)
        return out_path

    def export_summary(
        self, summary_text: str, link: TelegramLink, model_used: str = ""
    ) -> str:
        """Создаёт summary PDF. Возвращает путь к файлу."""
        _ensure_output_dir(self.cfg.output_dir)
        filename = _make_filename("summary", link)
        out_path = os.path.join(self.cfg.output_dir, filename)

        title = f"Конспект | {link.chat_id}/{link.msg_id}"
        pdf = self._make_pdf(title)

        pdf.write_h1("Конспект материала")
        pdf.set_font("DejaVu", size=BODY_SIZE)
        pdf.multi_cell(
            0, LINE_HEIGHT,
            f"Канал: {link.chat_id}  |  Сообщение: {link.msg_id}"
            + (f"\nМодель: {model_used}" if model_used else "")
        )
        pdf.write_divider()

        _render_markdown(pdf, summary_text)

        pdf.output(out_path)
        size = Path(out_path).stat().st_size
        logger.info("Summary PDF: %s (%.1f КБ)", out_path, size / 1024)
        return out_path

    def get_page_count(self, pdf: TgPDF) -> Optional[int]:
        """Возвращает реальное количество страниц из PDF-объекта."""
        try:
            return pdf.page
        except Exception:
            return None
