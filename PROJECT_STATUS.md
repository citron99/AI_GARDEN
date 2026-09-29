# AI Garden — итоговый чек-лист проекта

Статус на 2026-09-24. Проект развёрнут локально в Docker, все проверки пройдены.

## 1. Стек и архитектура

- **Backend:** FastAPI (Python 3.12), монолит `app/main.py` + сервисы в `app/services/`
- **БД:** SQLite в Docker (`/app/data/gardener.db`), в проде — PostgreSQL + pgvector (миграции Alembic, downgrade проверяется в CI)
- **Фронтенд:** серверный ванильный JS (`web/app.js`, i18n ru/lv/en через `app/i18n.py` и `web/i18n.js`)
- **Очереди:** собственный task queue (`app/task_queue.py`) с lease-механизмом (защита от двойных платных вызовов AI)
- **Интеграции:** Telegram, биллинг/подписки, погода, партнёрская атрибуция, метрики

## 2. AI-провайдеры ✅

| Провайдер | Роль | Файл |
|---|---|---|
| OpenAI (мультимодальный) | основной, анализ с фото | `app/ai/providers/openai_multimodal.py` |
| Gemini | альтернативный основной | `app/ai/providers/gemini.py` |
| DeepSeek | резервный текстовый | `app/ai/providers/deepseek.py` |
| Mock | для CI/тестов | `app/ai/providers/mock.py` |

- **FallbackAIGateway** (`app/ai/providers/fallback.py`): при ошибках `provider_unavailable / provider_timeout / provider_rate_limit / provider_error` от основного провайдера автоматически переключается на резервный; реально ответившая модель возвращается в `AIAnalysis.responder_model` (не на изменяемом состоянии шлюза — он синглтон при тредпуле FastAPI).
- **Деградация:** ответ резервного провайдера помечается `ai_degraded=true`, фронт показывает баннер с `degraded_notice` — **локализован по языку профиля пользователя** (ru/lv/en). Текущая реальность: кредиты Gemini исчерпаны (402) → рабочий режим — фолбэк на DeepSeek, баннер показывается корректно.
- Результат от DeepSeek без фото — `cannot_analyze` by design (недостаточно данных, честное поведение).

## 3. Демо-вход ✅

- Эндпоинт `POST /api/v1/auth/demo-login` (включён вне production), пользователь `demo@ai-garden.local`.
- Кнопка входа в демо-режиме в правом верхнем углу UI; демо-сад с фикусом и геранью для проверки полного цикла.

## 4. Docker ✅

- `compose.yaml`: сервис `api`, тома `gardener_data` (БД) и `gardener_uploads` (фото), порт 8000, `restart: unless-stopped`, health-чеки `/health`, `/health/ready`.
- Пересборка: `docker-compose -f compose.yaml up -d --build` (образ `ai_garden-api` — с подчёркиванием).
- Секреты (OPENAI_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY, JWT_SECRET и т.д.) — через `.env`, в репозиторий не коммитятся.

## 5. CI (`.github/workflows/ci.yml`) ✅

| Job | Что делает |
|---|---|
| `test` | `pip-audit`, `ruff check`, `pytest -q -o timeout_method=signal` (signal-метод — без оверхеда thread-метода на Linux) |
| `browser-e2e` | Playwright (chromium), артефакт отчёта при падении |
| `docker-smoke` | сборка образа, подъём с `AI_PROVIDER=mock`, проверка `/health` |
| `postgres-migrations` | реальный pgvector: `alembic upgrade head → check → downgrade base` + тесты конкуренции lease |

## 6. Тесты ✅

- 24 тестовых файла в `tests/` (API, провайдеры, уведомления, биллинг, миграции, безопасность, хранилище и др.); 58 тестов в `test_api.py` + 36 провайдер/нотификации — все зелёные.
- Целевые тесты фолбэка: `test_diagnosis_marks_degraded_response_from_fallback_provider` (включая проверку через `/plants/{id}/history`), `test_degraded_notice_follows_user_language`.

## 7. Исправления этого цикла

1. **Баг истории:** `GET /api/v1/plants/{id}/history` отдавал сырые ORM-объекты → флаги `ai_degraded`/`degraded_notice` терялись, баннер не показывался при открытии результата из истории. Исправлено: сериализация через `_diagnosis_read(d, user.language)`.
2. **Локализация баннера:** словарь `_DEGRADED_NOTICES` (ru/lv/en), язык из профиля пользователя; проверено на живом API для ru и en.
3. CI: `pytest -q -o timeout_method=signal`.

## 8. Как запустить

```bash
docker-compose -f compose.yaml up -d --build
# UI: http://127.0.0.1:8000  (демо-кнопка входа в правом верхнем углу)
# API health: http://127.0.0.1:8000/health
```

Локальная разработка: `.venv\Scripts\python.exe`, `pytest -q`, `ruff check app tests migrations evals`.

## 9. Известные ограничения

- Кредиты Gemini API исчерпаны — штатно работает фолбэк на DeepSeek (это и есть цель фолбэка).
- Антивирус Kaspersky инжектит скрипт в браузер и блокирует fetch-запросы страницы — мешает только браузерной автоматизации, на работу приложения не влияет.
- Диагноз от резервного текстового провайдера без фото — всегда `cannot_analyze` (намеренно, честность перед пользователем).

## Артефакты проверки

- `banner-ru.png` — баннер деградации в русском UI (путь: демо-вход → Демо-сад → Фикус → история → «Открыть результат»)
- `banner-check.png` — ранняя проверка баннера
