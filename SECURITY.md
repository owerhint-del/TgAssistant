# SECURITY — Политика безопасности TgAssistant

## Что является секретом

| Файл/переменная | Что содержит | Где хранится |
|---|---|---|
| `TG_API_ID` / `TG_API_HASH` | Ключи доступа к Telegram API | `.env` |
| `TG_PHONE` | Номер телефона аккаунта | `.env` |
| `ANTHROPIC_API_KEY` | Ключ API Claude | `.env` |
| `sessions/*.session` | Авторизованная сессия Telegram | `sessions/` |

## Правила

### Никогда не публиковать в git:
```
.env
config.yaml  (если содержит секреты)
sessions/
*.session
```

Файл `.gitignore` уже содержит эти исключения — **не удаляй их**.

### Права доступа к файлам (устанавливаются автоматически при `--setup`):
```bash
chmod 600 .env               # только ты
chmod 700 sessions/          # только ты
chmod 600 sessions/*.session # только ты
```

### Маскирование в логах:
Все логи автоматически скрывают:
- `api_hash` → `api_hash=***`
- `ANTHROPIC_API_KEY` → `sk-ant-***`
- Номер телефона → `+49***789`
- Session string → `[SESSION_REDACTED]`

## Если сессия скомпрометирована

1. Открой Telegram → Настройки → Устройства
2. Найди сессию "TgAssistant" и завершй её
3. Удали файл: `rm sessions/tgassistant.session`
4. Запусти заново: `python run.py --setup`

## Если утёк ANTHROPIC_API_KEY

1. Открой https://console.anthropic.com/keys
2. Нажми "Revoke" рядом с ключом
3. Создай новый ключ
4. Обнови `.env`: `ANTHROPIC_API_KEY=новый_ключ`

## Если утёк TG_API_HASH

1. Открой https://my.telegram.org → API development tools
2. Пересоздай приложение или смени hash
3. Обнови `.env`

## Отчёт об уязвимости

Если ты нашла уязвимость в коде — создай приватный issue или напиши напрямую.
