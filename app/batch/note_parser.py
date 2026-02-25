"""
Парсинг заметок с URL-ами.

Формат заметки:
  Тема (первая строка без URL)
  ➡️ описание https://url1.com
  👉 https://url2.com - описание
  текст без URL → пропускается

Результат: ParsedNote с темой, группами и плоским списком записей.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from app.utils.url_parser import parse_url, ParsedLink

# URL в тексте
_URL_RE = re.compile(r"https?://\S+")

# Известные emoji-префиксы групп (расширяемый список)
_EMOJI_PREFIXES = frozenset({
    "\u27a1\ufe0f",  # ➡️
    "\u27a1",        # ➡ (без variation selector)
    "\U0001f449",    # 👉
    "\u2764\ufe0f",  # ❤️
    "\u2764",        # ❤
    "\U0001f525",    # 🔥
    "\u2b50",        # ⭐
    "\U0001f4cc",    # 📌
    "\U0001f4a1",    # 💡
    "\U0001f3af",    # 🎯
    "\u2705",        # ✅
    "\U0001f4ce",    # 📎
    "\U0001f4e2",    # 📢
    "\U0001f680",    # 🚀
    "\u26a1",        # ⚡
})

# Текстовые стрелки как групповые префиксы
_ARROW_PREFIXES = ("->", "=>", "-->")


@dataclass
class NoteEntry:
    url: str                        # сырой URL
    label: str                      # описание
    group: str                      # ключ группы ("" = без группы)
    line_number: int                # для отчёта об ошибках
    link: Optional[ParsedLink] = None  # результат parse_url(), None если невалидный


@dataclass
class NoteGroup:
    prefix: str                     # отображаемый префикс
    entries: list[NoteEntry] = field(default_factory=list)


@dataclass
class ParsedNote:
    topic: str
    groups: list[NoteGroup] = field(default_factory=list)
    entries: list[NoteEntry] = field(default_factory=list)  # плоский список
    skipped_lines: list[tuple[int, str]] = field(default_factory=list)
    errors: list[tuple[int, str, str]] = field(default_factory=list)  # (line, url, error_msg)

    @property
    def valid_count(self) -> int:
        """Количество записей с успешно распознанным URL."""
        return sum(1 for e in self.entries if e.link is not None)

    @property
    def total_count(self) -> int:
        return len(self.entries)


def slugify(text: str, max_length: int = 50) -> str:
    """
    Превращает текст в slug: lowercase, не-alnum → _, trim.
    Поддерживает кириллицу (транслитерация не делается — просто lowercase).
    """
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)
    # Lowercase
    text = text.lower()
    # Replace non-alphanumeric (including unicode letters) with _
    text = re.sub(r"[^\w]", "_", text, flags=re.UNICODE)
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text)
    # Strip leading/trailing underscores
    text = text.strip("_")
    # Truncate
    if len(text) > max_length:
        text = text[:max_length].rstrip("_")
    return text or "untitled"


_EMOJI_PREFIXES_SORTED = sorted(_EMOJI_PREFIXES, key=len, reverse=True)


def _detect_group_prefix(text: str) -> Optional[str]:
    """
    Определяет групповой префикс из текста перед URL или отдельной строки.
    Возвращает префикс или None.
    """
    text = text.strip()
    if not text:
        return None

    # Проверяем emoji-префиксы (длинные первыми, чтобы ➡️ совпало раньше ➡)
    for emoji in _EMOJI_PREFIXES_SORTED:
        if text.startswith(emoji):
            return emoji

    # Проверяем текстовые стрелки
    for arrow in _ARROW_PREFIXES:
        if text.startswith(arrow):
            return arrow

    return None


def _extract_label(text: str, url: str) -> str:
    """
    Извлекает описание из строки, убирая URL и разделители.
    Поддерживает: 'desc - url', 'url - desc', 'desc url', 'url desc'.
    """
    # Убираем URL
    remaining = text.replace(url, "", 1).strip()

    # Убираем групповой префикс если есть
    prefix = _detect_group_prefix(remaining)
    if prefix:
        remaining = remaining[len(prefix):].strip()

    # Убираем разделители с краёв
    for sep in (" - ", " | ", " — ", " – "):
        remaining = remaining.strip()
        if remaining.startswith(sep.strip()):
            remaining = remaining[len(sep.strip()):].strip()
        if remaining.endswith(sep.strip()):
            remaining = remaining[:-(len(sep.strip()))].strip()

    return remaining.strip()


def parse_note(text: str) -> ParsedNote:
    """
    Парсит заметку → ParsedNote.

    1. Тема: первая непустая строка без URL (или "Untitled").
    2. Записи: строки с URL, с группами по emoji/стрелкам.
    3. Строки без URL → skipped_lines.
    """
    lines = text.split("\n")

    topic = ""
    entries: list[NoteEntry] = []
    skipped: list[tuple[int, str]] = []
    errors: list[tuple[int, str, str]] = []
    current_group = ""
    topic_found = False

    # Словарь для группировки (сохраняет порядок)
    groups_dict: dict[str, NoteGroup] = {}

    for i, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        url_match = _URL_RE.search(line)

        # Определяем тему из первой содержательной строки без URL
        if not topic_found:
            if not url_match:
                # Первая строка без URL — тема
                topic = line.lstrip("#").strip()
                topic_found = True
                continue
            else:
                # Первая строка содержит URL — тема = Untitled
                topic = "Untitled"
                topic_found = True
                # Продолжаем обработку этой строки как записи

        if not url_match:
            # Строка без URL — может быть заголовок группы
            prefix = _detect_group_prefix(line)
            if prefix:
                current_group = prefix
                # Создаём группу если не существует
                if current_group not in groups_dict:
                    groups_dict[current_group] = NoteGroup(prefix=current_group)
                continue
            # Обычный текст без URL — пропускаем
            skipped.append((i, line))
            continue

        url = url_match.group(0)
        # Чистим URL от trailing пунктуации
        while url and url[-1] in (")", "]", ",", ";", ".", "!"):
            url = url[:-1]

        # Определяем группу для этой строки
        text_before_url = line[:url_match.start()]
        line_prefix = _detect_group_prefix(text_before_url)
        if line_prefix:
            current_group = line_prefix

        # Извлекаем описание
        label = _extract_label(line, url_match.group(0))

        # Валидация URL через parse_url
        link: Optional[ParsedLink] = None
        try:
            link = parse_url(url)
        except ValueError as e:
            errors.append((i, url, str(e)))

        entry = NoteEntry(
            url=url,
            label=label,
            group=current_group,
            line_number=i,
            link=link,
        )
        entries.append(entry)

        # Добавляем в группу
        group_key = current_group
        if group_key not in groups_dict:
            groups_dict[group_key] = NoteGroup(prefix=group_key or "BASE")
        groups_dict[group_key].entries.append(entry)

    # Если текст пустой или содержит только пустые строки
    if not topic:
        topic = "Untitled"

    groups = list(groups_dict.values())

    return ParsedNote(
        topic=topic,
        groups=groups,
        entries=entries,
        skipped_lines=skipped,
        errors=errors,
    )
