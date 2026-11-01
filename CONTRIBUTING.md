# Как вкладываться в проект

Установка и запуск — в [README.md](README.md#запуск). Здесь только правила, которые
проверяются до коммита.

## Обязательные проверки

```powershell
.venv\Scripts\ruff.exe check app tests migrations evals scripts
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest -q
```

`npm ci && npx playwright install chromium && npm run test:e2e` — если затронут `web/`,
сценарий пользователя или переводы.

CI дополнительно прогоняет `pip-audit` по обоим логам зависимостей и `docker-smoke`.
В CI pytest идёт с `-o timeout_method=signal` (на Linux этот метод дешевле);
локально на Windows используется `thread` из `pyproject.toml` — не менять на «удобный».

## Зависимости

Списки — `requirements.txt` (продакшен) и `requirements-dev.txt` (инструменты),
к ним хэш-логи `requirements.lock` / `requirements-dev.lock`. Логи генерируются, а не
редактируются вручную:

```powershell
uv pip compile requirements.txt --python-version 3.12 --generate-hashes --output-file requirements.lock
uv pip compile requirements-dev.txt --python-version 3.12 --generate-hashes `
  --constraint requirements-dev.lock --output-file requirements-dev.lock
```

`--constraint` на сам лок замораживает транзитивные версии: без него перегенерация
поднимает ~45 косвенных пакетов. Флаг `--no-strip-extras` не использовать — с ним в локе
остаётся `uvicorn[standard]`, а `pip install --require-hashes` на Linux тогда требует все
зависимости экстры; `uvloop` (`sys_platform != 'win'`) при сборке лока на Windows не
резолвится и в лок не попадает, из-за чего CI падает. Зависимости экстры при этом остаются
в локе отдельными строками (`httptools`, `watchfiles`, `websockets`, `python-dotenv`).

Паритет с Linux-CI проверяется без Docker:

```powershell
uv pip install --dry-run --require-hashes --python-platform x86_64-manylinux_2_28 -r requirements-dev.lock
```

Если нужно добавить пакет в CI, добавьте его в `requirements-dev.txt` и перегенерируйте
лог. Ручная правка `*.lock` ломает `pip install --require-hashes`.

## Структура кода

- `app/routers/*.py` — только HTTP-слой: зависимости, проверка прав, вызов сервисов.
  Общие префиксы вида `/api/v1/<домен>`; новый домен — отдельный модуль и один
  `include_router` в `app/main.py`.
- `app/services/*.py` — бизнес-логика без `Request`, `Response` и `HTTPException`
  в возвращаемых значениях.
- `app/models.py` — ORM, `app/schemas.py` — pydantic-модели. Наружу ответы уходят через
  `response_model`, а не сырым словарём.
- `app/ai/providers/*` — провайдеры, результат строго `DiagnosisResult`.
- `web/` — vanilla JS без сборки.

Композиция маршрутов важна: `include_router` подключается по порядку, и при совпадении
пути срабатывает тот, что объявлен раньше. Сейчас пересечений нет (проверка: нормализовать
`{param}` в `*` и убедиться, что пар `(method, форма пути)` не повторяется), поэтому
порядок роутеров можно менять только вместе с такой проверкой.

Списки не должны обращаться к relationship по строке: это N+1, а `KnowledgeSourceRecord.chunks`
дополнительно тянет векторы эмбеддингов. Нужен `selectinload` или одна агрегатная выборка с
`group_by`. Инвариант «число запросов не растёт с числом строк» закрепляет
`test_knowledge_source_list_makes_constant_number_of_queries`.

## Переводы

Ключом в `web/i18n.js` служит русский текст; для каждого нового ключа нужны варианты
`en` и `lv`, иначе `tests/test_i18n.py` падает. Не оборачивайте в `t()` строки, которые
потом должны перерисовываться при смене языка: `app.js` применяет `t()` в момент вставки,
и узел теряет оригинал для повторного перевода.

## Миграции

Alembic, имя файла — следующий номер `NNNN_короткое_имя.py`. Обязаны работать и
`upgrade head`, и `downgrade base`, на SQLite и на PostgreSQL. Новая таблица или колонка
добавляется в проверку `tests/test_migrations.py`. `alembic check` на реальном pgvector
выполняется отдельным job `postgres-migrations` — дрейф модели и схемы красит CI.

## Ошибки и логи

Прикладные сбои поднимаются доменными исключениями с фиксированным русским сообщением
(`BillingProviderError`, `WeatherServiceError`, `InvoiceDocumentError`, `StorageError`) и
преобразуются в `HTTPException`; общий обработчик в `app/main.py` локализует `detail`
по `Accept-Language`. В ответ клиенту не уходит `str()` чужого исключения — только
стабильная формулировка, детали остаются в логе через `logger.exception`.

`except Exception` без повторного `raise` требует `# noqa: BLE001` с указанием, кто
считает сбой: lint-правило `BLE` включено.

## Тесты

`monkeypatch.setattr` заменяет имя в том модуле, который его читает: `from app.x import Y`
создаёт локальную связь, и патч `app.x` не отразится на импортировавший его модуль. Пример:
`monkeypatch.setattr("app.services.invoice_delivery_service.send_invoice_email", ...)` —
`process_invoice_delivery` вызывает это имя из своего пространства имён, а не из
`app.services.invoice_service`.

## Рефакторинг без смены поведения

Перед слиянием доказать, что API не изменился: снять `app.openapi()` до и после и
сравнить построчно, сверить плоскую таблицу маршрутов `(method, path)`, списки
middleware и exception handlers. Изменение байт в байт спецификации означает те же пути,
методы, параметры, тела и коды ответов.

## Коммиты

Одна строка по-русски: что изменено и зачем, как в `git log`. Не коммитить `.env`,
`*.db`, `uploads/`, реальные ключи и архивы; секреты CI попадают в Actions secrets,
а не в workflow. Не мержить PR с красными проверками.
