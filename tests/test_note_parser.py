"""
Tests for batch note parser: topic extraction, URL parsing, groups, slugify.
"""
import unittest

from app.batch.note_parser import parse_note, slugify, NoteEntry


class TestTopicExtraction(unittest.TestCase):
    """Извлечение темы из первой строки."""

    def test_simple_topic(self):
        note = parse_note("Маркетинг\nhttps://youtube.com/watch?v=abc12345678")
        self.assertEqual(note.topic, "Маркетинг")

    def test_topic_with_hash(self):
        note = parse_note("# SEO ссылки\nhttps://youtube.com/watch?v=abc12345678")
        self.assertEqual(note.topic, "SEO ссылки")

    def test_topic_with_multiple_hashes(self):
        note = parse_note("## Важные видео\nhttps://youtube.com/watch?v=abc12345678")
        self.assertEqual(note.topic, "Важные видео")  # lstrip strips all leading #

    def test_first_line_is_url(self):
        """Если первая строка — URL, тема = Untitled."""
        note = parse_note("https://youtube.com/watch?v=abc12345678")
        self.assertEqual(note.topic, "Untitled")
        self.assertEqual(len(note.entries), 1)

    def test_empty_note(self):
        note = parse_note("")
        self.assertEqual(note.topic, "Untitled")
        self.assertEqual(len(note.entries), 0)

    def test_topic_only_note(self):
        note = parse_note("Мои заметки")
        self.assertEqual(note.topic, "Мои заметки")
        self.assertEqual(len(note.entries), 0)

    def test_blank_lines_before_topic(self):
        note = parse_note("\n\n  Тема  \nhttps://example.com/page")
        self.assertEqual(note.topic, "Тема")


class TestURLParsing(unittest.TestCase):
    """Парсинг URL и описаний."""

    def test_url_with_description_after(self):
        note = parse_note("Тема\nhttps://youtube.com/watch?v=abc12345678 - Хорошее видео")
        self.assertEqual(len(note.entries), 1)
        self.assertEqual(note.entries[0].label, "Хорошее видео")

    def test_url_with_description_before(self):
        note = parse_note("Тема\nХорошее видео - https://youtube.com/watch?v=abc12345678")
        self.assertEqual(len(note.entries), 1)
        self.assertEqual(note.entries[0].label, "Хорошее видео")

    def test_url_only(self):
        note = parse_note("Тема\nhttps://youtube.com/watch?v=abc12345678")
        self.assertEqual(len(note.entries), 1)
        self.assertEqual(note.entries[0].label, "")

    def test_url_with_pipe_separator(self):
        note = parse_note("Тема\nhttps://example.com/page | Описание")
        self.assertEqual(note.entries[0].label, "Описание")

    def test_multiple_urls(self):
        text = "Тема\nhttps://youtube.com/watch?v=abc12345678\nhttps://example.com/page"
        note = parse_note(text)
        self.assertEqual(len(note.entries), 2)

    def test_invalid_url_recorded_in_errors(self):
        """Невалидный URL записывается в errors, entry создаётся с link=None."""
        text = "Тема\nhttps://t.me/c/0/123"  # chat_id=0 -> ValueError
        note = parse_note(text)
        self.assertEqual(len(note.entries), 1)
        self.assertIsNone(note.entries[0].link)
        self.assertEqual(len(note.errors), 1)
        self.assertEqual(note.errors[0][1], "https://t.me/c/0/123")

    def test_telegram_link(self):
        note = parse_note("Тема\nhttps://t.me/channel_name/42")
        self.assertEqual(len(note.entries), 1)
        self.assertIsNotNone(note.entries[0].link)

    def test_external_link(self):
        note = parse_note("Тема\nhttps://youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(len(note.entries), 1)
        self.assertIsNotNone(note.entries[0].link)

    def test_url_trailing_punctuation_stripped(self):
        note = parse_note("Тема\nhttps://example.com/page.")
        self.assertEqual(note.entries[0].url, "https://example.com/page")

    def test_line_numbers_tracked(self):
        text = "Тема\n\nhttps://example.com/page1\n\nhttps://example.com/page2"
        note = parse_note(text)
        self.assertEqual(note.entries[0].line_number, 3)
        self.assertEqual(note.entries[1].line_number, 5)


class TestEmojiGroups(unittest.TestCase):
    """Группировка по emoji-префиксам."""

    def test_inline_emoji_prefix(self):
        text = "Тема\n➡️ https://example.com/1 - первый\n➡️ https://example.com/2 - второй"
        note = parse_note(text)
        self.assertEqual(len(note.entries), 2)
        self.assertEqual(note.entries[0].group, "➡️")
        self.assertEqual(note.entries[1].group, "➡️")
        # Все в одной группе
        self.assertEqual(len(note.groups), 1)
        self.assertEqual(note.groups[0].prefix, "➡️")

    def test_header_style_group(self):
        """Emoji на отдельной строке устанавливает группу для последующих строк."""
        text = "Тема\n👉\nhttps://example.com/1\nhttps://example.com/2"
        note = parse_note(text)
        self.assertEqual(len(note.entries), 2)
        self.assertEqual(note.entries[0].group, "\U0001f449")
        self.assertEqual(note.entries[1].group, "\U0001f449")

    def test_multiple_groups(self):
        text = (
            "Тема\n"
            "➡️ https://example.com/1\n"
            "🔥 https://example.com/2\n"
            "➡️ https://example.com/3"
        )
        note = parse_note(text)
        self.assertEqual(note.entries[0].group, "➡️")
        self.assertEqual(note.entries[1].group, "\U0001f525")
        self.assertEqual(note.entries[2].group, "➡️")

    def test_mixed_groups_and_ungrouped(self):
        text = (
            "Тема\n"
            "https://example.com/1\n"
            "➡️ https://example.com/2\n"
            "https://example.com/3"
        )
        note = parse_note(text)
        self.assertEqual(note.entries[0].group, "")
        self.assertEqual(note.entries[1].group, "➡️")
        # Третья запись наследует группу ➡️ (текущая группа сохраняется)
        self.assertEqual(note.entries[2].group, "➡️")

    def test_fire_emoji(self):
        text = "Тема\n🔥 https://youtube.com/watch?v=abc12345678 - hot"
        note = parse_note(text)
        self.assertEqual(note.entries[0].group, "\U0001f525")

    def test_star_emoji(self):
        text = "Тема\n⭐ https://example.com/fav"
        note = parse_note(text)
        self.assertEqual(note.entries[0].group, "\u2b50")


class TestArrowPrefixes(unittest.TestCase):
    """Группировка по текстовым стрелкам."""

    def test_arrow_dash(self):
        text = "Тема\n-> https://example.com/1 - desc"
        note = parse_note(text)
        self.assertEqual(note.entries[0].group, "->")

    def test_fat_arrow(self):
        text = "Тема\n=> https://example.com/1"
        note = parse_note(text)
        self.assertEqual(note.entries[0].group, "=>")

    def test_long_arrow(self):
        text = "Тема\n--> https://example.com/1"
        note = parse_note(text)
        self.assertEqual(note.entries[0].group, "-->")


class TestSkippedLines(unittest.TestCase):
    """Строки без URL → skipped_lines."""

    def test_random_text_skipped(self):
        text = "Тема\nкакой-то текст без ссылок\nhttps://example.com/1"
        note = parse_note(text)
        self.assertEqual(len(note.entries), 1)
        self.assertEqual(len(note.skipped_lines), 1)
        self.assertEqual(note.skipped_lines[0][1], "какой-то текст без ссылок")

    def test_numbers_skipped(self):
        text = "Тема\n12345\nhttps://example.com/1"
        note = parse_note(text)
        self.assertEqual(len(note.skipped_lines), 1)

    def test_empty_lines_not_in_skipped(self):
        text = "Тема\n\n\nhttps://example.com/1"
        note = parse_note(text)
        self.assertEqual(len(note.skipped_lines), 0)


class TestValidCount(unittest.TestCase):
    """Проверка valid_count и total_count."""

    def test_all_valid(self):
        text = "Тема\nhttps://example.com/1\nhttps://youtube.com/watch?v=abc12345678"
        note = parse_note(text)
        self.assertEqual(note.valid_count, 2)
        self.assertEqual(note.total_count, 2)

    def test_mixed_valid_invalid(self):
        text = "Тема\nhttps://example.com/1\nhttps://t.me/c/0/123"
        note = parse_note(text)
        self.assertEqual(note.total_count, 2)
        self.assertEqual(note.valid_count, 1)

    def test_only_urls_note(self):
        text = "https://example.com/1\nhttps://example.com/2"
        note = parse_note(text)
        self.assertEqual(note.topic, "Untitled")
        self.assertEqual(note.total_count, 2)


class TestSlugify(unittest.TestCase):
    """Тест slugify()."""

    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello_world")

    def test_cyrillic(self):
        result = slugify("Маркетинг 2024")
        self.assertEqual(result, "маркетинг_2024")

    def test_special_chars(self):
        result = slugify("SEO & SEM: Best Practices!!!")
        self.assertEqual(result, "seo_sem_best_practices")

    def test_long_string_truncated(self):
        result = slugify("a" * 100, max_length=50)
        self.assertLessEqual(len(result), 50)

    def test_empty_string(self):
        self.assertEqual(slugify(""), "untitled")

    def test_only_special_chars(self):
        self.assertEqual(slugify("!@#$%"), "untitled")

    def test_collapse_underscores(self):
        result = slugify("one   two   three")
        self.assertEqual(result, "one_two_three")

    def test_no_leading_trailing_underscores(self):
        result = slugify("  hello  ")
        self.assertEqual(result, "hello")

    def test_max_length_cuts_at_boundary(self):
        result = slugify("hello_world_test", max_length=11)
        self.assertLessEqual(len(result), 11)


class TestComplexNotes(unittest.TestCase):
    """Комплексные сценарии."""

    def test_realistic_note(self):
        text = """SEO ссылки на февраль

➡️ https://youtube.com/watch?v=abc12345678 - Гайд по SEO
➡️ https://youtube.com/watch?v=def12345678 - Продвижение сайта

🔥 https://t.me/seo_channel/42 - Пост про ключи
🔥 https://t.me/seo_channel/43 - Анализ конкурентов

Заметка: проверить потом
https://example.com/report - Отчёт"""

        note = parse_note(text)
        self.assertEqual(note.topic, "SEO ссылки на февраль")
        self.assertEqual(note.total_count, 5)
        self.assertEqual(len(note.skipped_lines), 1)  # "Заметка: проверить потом"
        # Проверяем группы
        # last url inherits 🔥 group because current_group persists
        groups = {g.prefix: len(g.entries) for g in note.groups}
        self.assertEqual(groups.get("➡️", 0), 2)
        self.assertEqual(groups.get("\U0001f525", 0), 3)  # 2 TG + 1 inherited

    def test_note_with_only_invalid_urls(self):
        text = "Тема\nhttps://t.me/c/0/1\nhttps://t.me/c/0/2"
        note = parse_note(text)
        self.assertEqual(note.total_count, 2)
        self.assertEqual(note.valid_count, 0)
        self.assertEqual(len(note.errors), 2)

    def test_note_groups_preserve_order(self):
        text = "Тема\n🔥 https://example.com/1\n➡️ https://example.com/2\n🔥 https://example.com/3"
        note = parse_note(text)
        # Порядок групп: 🔥, ➡️ (в порядке появления)
        self.assertEqual(note.groups[0].prefix, "\U0001f525")
        self.assertEqual(note.groups[1].prefix, "➡️")


if __name__ == "__main__":
    unittest.main()
