"""
Генерация подробного конспекта через Anthropic Claude API.
Поддерживает чанкинг для длинных транскриптов.
"""
import logging
import time
from typing import List, Tuple

from app.config import Config
from app.pipeline.transcriber import TranscriptResult

logger = logging.getLogger("tgassistant.summarizer")

# Максимум символов на один запрос (≈150k токенов с запасом для кириллицы)
MAX_CHUNK_CHARS = 120_000

SYSTEM_PROMPT = """Ты — профессиональный редактор образовательных конспектов. \
Тебе дана транскрипция видео-лекции или обучающего материала на русском языке.

Создай ПОДРОБНЫЙ структурированный конспект строго по следующей схеме:

## Обзор материала
[2–4 предложения: о чём этот материал, главная тема]

## Основные разделы и темы

### [Название темы 1]
[Подробное изложение. Сохраняй ВСЕ важные детали, факты, цифры, примеры из оригинала. \
Не сжимай — конспект должен быть подробным, а не краткой выжимкой.]

### [Название темы 2]
[...]

## Ключевые понятия и определения

| Термин | Определение / пояснение |
|--------|------------------------|
| ...    | ...                    |

## Практические выводы
[Что важно запомнить и применить на практике]

## Вопросы для самопроверки
1. ...
2. ...
[5–10 вопросов по материалу]

ТРЕБОВАНИЯ:
- Конспект должен быть ПОДРОБНЫМ — не краткой выжимкой.
- Сохраняй все важные детали, примеры, числа, термины из оригинала.
- Если в материале есть специальные термины — объясняй их.
- Структурируй логично по темам.
- Не выдумывай информацию, которой нет в транскрипте."""


def _build_user_prompt(text: str, target_language: str, chunk_info: str = "") -> str:
    lang_instruction = ""
    if target_language and target_language.lower() not in ("", "original", "auto"):
        lang_map = {
            "ru": "русском",
            "de": "немецком",
            "en": "английском",
            "uk": "украинском",
        }
        lang_name = lang_map.get(target_language, target_language)
        lang_instruction = f"\n\nВажно: напиши конспект на {lang_name} языке, независимо от языка оригинала."

    chunk_note = f"\n\n[Это часть {chunk_info} общего транскрипта. Суммаризируй только эту часть.]" if chunk_info else ""

    return f"Транскрипт:{chunk_note}{lang_instruction}\n\n{text}"


def _chunk_text(text: str, max_chars: int) -> List[str]:
    """Делит текст на части без перекрытия, разрезая по переносу строки."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + max_chars

        if end >= len(text):
            # Последний кусок
            chunks.append(text[start:])
            break

        # Ищем перенос строки во второй половине чанка (безопаснее чем в первой)
        newline_pos = text.rfind("\n", start + max_chars // 2, end)
        if newline_pos > start:
            end = newline_pos + 1  # включаем сам \n

        chunks.append(text[start:end])
        # Следующий чанк начинается строго после текущего — гарантируем прогресс
        start = end

    return chunks


class Summarizer:
    def __init__(self, cfg: Config):
        import anthropic as _anthropic
        self.cfg = cfg
        self._anthropic = _anthropic
        self._client = _anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    def _call_api(self, user_text: str) -> Tuple[str, int, int]:
        """Вызывает Claude API. Возвращает (content, prompt_tokens, completion_tokens)."""
        max_attempts = self.cfg.max_retries
        backoff = self.cfg.retry_backoff_sec

        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.messages.create(
                    model=self.cfg.llm_model,
                    max_tokens=self.cfg.llm_max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_text}],
                )
                content = response.content[0].text
                pt = response.usage.input_tokens
                ct = response.usage.output_tokens
                return content, pt, ct

            except self._anthropic.RateLimitError as e:
                last_exc = e
                wait = 60.0 * attempt
                logger.warning(
                    "Anthropic rate limit (попытка %d/%d). Ждём %.0f сек...",
                    attempt, max_attempts, wait,
                )
                time.sleep(wait)

            except self._anthropic.APIStatusError as e:
                last_exc = e
                if e.status_code >= 500:
                    wait = backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "Anthropic server error %d (попытка %d/%d). Повтор через %.0f сек...",
                        e.status_code, attempt, max_attempts, wait,
                    )
                    time.sleep(wait)
                else:
                    raise  # 4xx — не retryable

            except self._anthropic.AuthenticationError:
                raise RuntimeError(
                    "Неверный ANTHROPIC_API_KEY.\n"
                    "Проверь ключ на https://console.anthropic.com/keys\n"
                    "и обнови значение в .env"
                )

        raise RuntimeError(
            f"Claude API недоступен после {max_attempts} попыток: {last_exc}"
        )

    def summarize(self, transcript: TranscriptResult) -> Tuple[str, int, int, int]:
        """
        Генерирует конспект по транскрипту.

        Returns:
            (summary_text, total_prompt_tokens, total_completion_tokens, chunks_count)
        """
        full_text = transcript.full_text
        chunks = _chunk_text(full_text, MAX_CHUNK_CHARS)
        chunks_count = len(chunks)

        logger.info(
            "Генерирую конспект через Claude (%s)... [%d часть(ей)]",
            self.cfg.llm_model, chunks_count,
        )

        total_pt = 0
        total_ct = 0

        if chunks_count == 1:
            user_text = _build_user_prompt(chunks[0], self.cfg.summary_language)
            content, pt, ct = self._call_api(user_text)
            total_pt += pt
            total_ct += ct
            logger.info(
                "Конспект готов. Токены: вход=%d, выход=%d",
                total_pt, total_ct,
            )
            return content, total_pt, total_ct, 1

        # Несколько чанков: суммаризируем каждый, потом сшиваем
        partial_summaries = []
        for i, chunk in enumerate(chunks, 1):
            logger.info("Обрабатываю часть %d/%d...", i, chunks_count)
            chunk_info = f"{i} из {chunks_count}"
            user_text = _build_user_prompt(chunk, self.cfg.summary_language, chunk_info)
            content, pt, ct = self._call_api(user_text)
            partial_summaries.append(content)
            total_pt += pt
            total_ct += ct

        # Финальное объединение
        logger.info("Объединяю %d частей конспекта...", chunks_count)
        merged_text = "\n\n---\n\n".join(partial_summaries)
        merge_prompt = (
            f"Ниже представлены конспекты {chunks_count} частей одной лекции. "
            "Объедини их в единый связный конспект, убери дублирование, "
            "сохрани все важные детали.\n\n"
            f"{merged_text}"
        )
        final_content, pt, ct = self._call_api(merge_prompt)
        total_pt += pt
        total_ct += ct

        logger.info(
            "Конспект готов. Всего токенов: вход=%d, выход=%d",
            total_pt, total_ct,
        )
        return final_content, total_pt, total_ct, chunks_count
