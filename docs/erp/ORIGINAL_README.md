> Архив исходной документации ERP_to_DWH. Для объединённого портала используйте docs/ETL_DEV.md.

# Django ETL Control Center

Веб-платформа управления Full Load из физической базы 1С / SQL Server в отдельную аналитическую SQL Server. Python 3.12+, Django 5.2 LTS, pyodbc, Bootstrap 5, HTMX, Plotly. Redis не требуется.

## Архитектура и гарантии

- `config/` — настройки Django, URL, ASGI/WSGI.
- `apps/control/` — подключения, задания, история, аудит, формы, Dashboard, очередь и management commands. Модули объединены в одном Django app; бизнес-движок вынесен отдельно.
- `etl_engine/` — подключения, валидация SQL/идентификаторов, метаданные SQL Server, потоковый extractor, сохранение нативных типов, staging loader и runner. Runner не зависит от HTTP или очереди; его можно вызвать из будущей Celery task.
- Служебная БД — локальная SQLite, отдельно от обеих SQL Server. Частичный уникальный индекс запрещает более одного PENDING/RUNNING для задания. Захват очереди — условный атомарный UPDATE. Локальный worker один на служебную БД, блокировка ОС автоматически освобождается при смерти процесса. SQLite и lock-файл должны находиться на локальном диске одного сервера, не SMB/NFS.
- Каждый перенос создаёт уникальную обычную `_etl_<uuid>` таблицу в целевой схеме. Пакеты `fetchmany` → `fast_executemany` фиксируются только в staging. Число строк проверяется перед публикацией. Существующая таблица меняется через транзакцию `DELETE + INSERT`, новая создаётся через транзакционный `SELECT INTO`. При ошибке выполняется rollback. `TRUNCATE` не используется.
- Session `sp_getapplock` сериализует публикации в одну целевую таблицу, включая разные задания. Существующая схема должна точно совпадать: порядок/имена/типы/nullability. Identity/computed/generated колонки отклоняются. Индексы и ограничения существующей таблицы сохраняются; ошибка ограничения откатывает DELETE.
- Decimal/NULL/bytes сохраняются без преобразования в float/строки. Поддерживаются INT, BIGINT, DECIMAL/NUMERIC, NVARCHAR, DATETIME2, BIT, UNIQUEIDENTIFIER, VARBINARY и ряд простых SQL-типов. XML, spatial, CLR, rowversion, пользовательские alias types отклоняются; применяйте явный CAST в административном SELECT. Имена колонок должны быть уникальными и соответствовать допустимым SQL-идентификаторам приложения.
- Поля `rows_loaded` во время работы показывают записи в staging, а не обещают публикацию в назначение. Процент не выдумывается. Dashboard суммирует только SUCCESS.
- Heartbeat выполнения обновляется каждые 10 секунд и при завершении пакетов. Старый heartbeat требует проверки worker. Новый worker после получения lock помечает оставшиеся RUNNING как FAILED, не повторяет автоматически: после сбоя в момент commit данные могли уже опубликоваться. Проверьте назначение, staging и журнал SQL Server перед ручным повтором. Остатки `_etl_` после аварии удаляет администратор после такой проверки.
- Инкрементальный режим отображается «В разработке», не ставится в очередь и не выполняется. Следующий этап: watermark/checkpoint, ключи, upsert и транзакционная фиксация watermark.

## Установка на Windows Server

1. Установите **64-bit Python 3.12+** и **Microsoft ODBC Driver 18 for SQL Server** с официального сайта: https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server . При необходимости установите Microsoft Visual C++ Redistributable, указанный Microsoft. Разрядности Python и ODBC должны совпадать.
2. Откройте PowerShell в корне этого репозитория:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

Запишите случайный ключ в `DJANGO_SECRET_KEY` файла `.env`. Не публикуйте его. Для локальной разработки `DJANGO_DEBUG=true`; production требует `false`, HTTPS и реальные `DJANGO_ALLOWED_HOSTS`.

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8001
```

Сервер слушает все сетевые интерфейсы на порту **8001**. На самом сервере открывайте `http://127.0.0.1:8001`, с другого компьютера — `http://<IP-или-DNS-сервера>:8001`. Добавьте этот IP или DNS в `DJANGO_ALLOWED_HOSTS` файла `.env` через запятую, без схемы и порта. `0.0.0.0` обозначает адрес привязки, а не адрес для браузера. Для доступа из локальной сети разрешите входящий TCP 8001 в Windows Firewall для нужной сети.

В отдельном PowerShell из того же каталога:

```powershell
.\.venv\Scripts\python.exe manage.py etl_worker
```

`etl_worker --once` обрабатывает максимум один ожидающий запуск и завершает работу; `--poll 2` меняет интервал опроса. Worker должен работать постоянно как управляемый процесс/служба под отдельной Windows service account. Настройте перезапуск при аварии и запрет второго экземпляра. Закрытие консоли остановит обычный процесс.

## После запуска сервера

1. Оставьте окно веб-сервера открытым и откройте в браузере адрес вашего сервера с портом `8001`.
2. Войдите под пользователем, созданным командой `manage.py createsuperuser`. Если ещё не создали его, выполните эту команду в другом PowerShell из корня проекта.
3. В отдельном PowerShell запустите `.\.venv\Scripts\python.exe manage.py etl_worker`. Без worker задания остаются PENDING.
4. Создайте два подключения: Source к базе 1С и Target к отдельной аналитической базе. Проверьте оба кнопкой «Проверить подключение».
5. Создайте Full Load задание, укажите таблицу или SELECT, существующую целевую схему и имя таблицы назначения.
6. Нажмите «Запустить сейчас», наблюдайте PENDING → RUNNING → SUCCESS. При FAILED откройте сообщение ошибки и этап выполнения; проверьте конфигурацию перед повтором.
7. После SUCCESS проверьте количество и содержимое строк в целевой базе SQL Server и запись в разделе «История».
8. Для автоматизации включите расписание и настройте Windows Task Scheduler, как описано ниже. Web, worker и scheduler — отдельные процессы.

## Подключения и первый перенос

1. Войдите под superuser. Создайте Source и Target на странице «Подключения». Сервер указывается без порта (порт — отдельное поле), имя базы отдельно. Целевая схема должна уже существовать.
2. Windows Authentication использует Windows identity **того процесса**, который проверяет/выполняет подключение. Веб-процесс и worker должны иметь нужные права. В Linux Windows Authentication требует отдельно настроенного Kerberos; MVP не настраивает домен автоматически.
3. Для SQL Authentication укажите username и `credential_reference`, например `ERP_SQL_PASSWORD`. Сам пароль задайте переменной окружения процесса или в защищённом локальном `.env`. Приложение не хранит и не принимает пароль в форме. `.env` не включается в Git; ограничьте NTFS ACL доступом service account и администратора. Перезапустите web/worker после изменения окружения.
4. Учётная запись Source должна иметь только необходимые SELECT-права на таблицы/представления и доступ к их метаданным; **никаких прав изменения/DDL/EXEC произвольных процедур**. SQL-фильтр приложения — дополнительная проверка, не граница безопасности. Произвольный SELECT доступен только администратору. Разрешён один SELECT/CTE без INTO/DDL/DML/OPENQUERY и т.п.
5. Target: отдельная учётная запись, права SELECT/INSERT/DELETE на назначение, CREATE TABLE в базе и ALTER на **выделенной ETL-схеме** для staging, доступ к метаданным и `sp_getapplock`. Схемные DDL-права широкие: выделите отдельную базу/схему, не используйте db_owner. Проверьте права с DBA на своей версии SQL Server.
6. TLS обязателен: `Encrypt=yes;TrustServerCertificate=no`. Сертификат сервера должен быть доверенным и соответствовать имени. Исправьте сертификаты/доверие вместо отключения проверки.
7. Нажмите «Проверить подключение». Выполняется SELECT 1: login timeout до 30 с, query timeout проверки до 10 с. Это не проверка всех прав переноса.
8. Создайте задание TABLE (`dbo._Reference123`) или SQL_QUERY (`SELECT ...` с уникальными aliases). Укажите target schema/table, batch size, timeout. Не выбирайте исходную базу как назначение.
9. «Запустить сейчас» создаёт PENDING и открывает мониторинг. Worker выполняет перенос вне HTTP. HTMX обновляет состояние каждые 3 с до завершения. История сохраняется между запусками. Ожидающий запуск можно отменить; прерывание активного переноса требует остановки worker и проверки результата.
10. Все вошедшие обычные пользователи — наблюдатели: могут читать Dashboard/историю/конфигурацию, но не менять подключения/SQL/задания и не запускать переносы. Superuser — администратор. Создавайте пользователей в Django Admin, не выдавайте наблюдателям staff/superuser. Изменения заданий и подключений через формы/Admin записываются в аудит без копий SQL и секретов.

Не направляйте загрузку на таблицы с бизнес-триггерами/temporal/репликацией или внешними побочными эффектами без отдельного анализа DBA. Новая таблица получает колонки, но не копирует индексы, PK/FK/defaults/collation источника. Сравнение схем консервативное. Согласованный snapshot изменяющегося источника не гарантируется: согласуйте isolation/snapshot с DBA. DELETE + INSERT больших таблиц требует места в transaction log и может блокировать читателей. Планируйте окно загрузки и резервирование журнала.

## Ежедневный планировщик Windows Task Scheduler

Включите расписание задания и укажите время (часовой пояс `TIME_ZONE`, по умолчанию Europe/Moscow).

Создайте задачу Windows Task Scheduler под service account:

- Trigger: ежедневно, повтор каждые **1 минуту**, бессрочно; запускать пропущенный запуск при первой возможности.
- Program: `C:\path\ERP_to_DWH\.venv\Scripts\python.exe`
- Arguments: `manage.py enqueue_scheduled_jobs`
- Start in: `C:\path\ERP_to_DWH`
- Run whether user is logged on or not; запрет нового экземпляра, если предыдущий ещё работает.

Команда добавляет наступившие за **текущий локальный день** задания. Уникальный `job_id:YYYY-MM-DD` исключает повторный плановый запуск даже после завершения. Активное ручное выполнение откладывает постановку до следующего вызова. Пропущенные прошлые дни автоматически не воспроизводятся; при необходимости запускайте вручную. Изменение расписания не создаёт второй запуск в тот же день. Worker запускается отдельно, scheduler только ставит задания в очередь.

## Production Windows

`runserver` предназначен только для разработки. Установленный Waitress запускайте за IIS/ARR или другим HTTPS reverse proxy:

```powershell
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\waitress-serve.exe --listen=0.0.0.0:8001 config.wsgi:application
```

Reverse proxy завершает HTTPS, передаёт Host, обслуживает `/static/` из `staticfiles/`, проксирует остальные пути на `127.0.0.1:8001`. Waitress слушает `0.0.0.0:8001`; в production ограничьте доступ к backend-порту правилами firewall, разрешив его только reverse proxy. `DJANGO_DEBUG=false`, уникальный secret key, корректный allowed host; secure cookies включены автоматически. Настройте HSTS/HTTPS redirect на reverse proxy. Не доверяйте произвольным заголовкам X-Forwarded-* из Интернета. Для нескольких серверов/worker нужна другая служебная БД и распределённая очередь: этот MVP рассчитан на один Windows Server.

JS/CSS поставляются локально в `static/vendor/`: Bootstrap 5.3.8 (MIT), HTMX 2.0.8 (BSD-2-Clause), Plotly 3.3.0 (MIT), CodeMirror 5.65.20 (MIT); лицензии сохранены в заголовках upstream. Внешние CDN не нужны при работе приложения.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Unit/DB/web тесты используют mock ODBC. Проверяются валидация, роли и CSRF, очередь/переходы статусов/ограничения, пакетная обработка, схема/типы, ошибки SQL/записи/соединения, rollback, восстановление worker и идемпотентность расписания. Это не заменяет SQL Server integration.

Отдельный интеграционный тест создаёт/удаляет только случайные `dbo.test_etl_*` и staging таблицы в **отдельной тестовой базе**, проверяет точный Decimal/NULL/bytes/unicode, новую публикацию и rollback существующей таблицы при CHECK violation после DELETE:

```powershell
$env:ETL_RUN_MSSQL_TESTS='1'
$env:MSSQL_TEST_SERVER='your-test-server'
$env:MSSQL_TEST_DATABASE='your-disposable-test-database'
$env:MSSQL_TEST_USERNAME='your-test-user'
# MSSQL_TEST_PASSWORD задайте безопасно в окружении; не в Git и не в истории команд.
.\.venv\Scripts\python.exe -m pytest -m mssql -q
```

Без `ETL_RUN_MSSQL_TESTS=1` эта группа явно SKIPPED. Реальные адреса/реквизиты не включены в проект. Текст сырых ODBC исключений намеренно не сохраняется: он может содержать пароль/SQL. В истории фиксируется безопасная категория ошибки и этап; для углублённой диагностики используйте защищённые журналы SQL Server/ODBC под контролем DBA (не включайте глобальную трассировку с секретами).

## Облачная среда Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# Для реальных подключений, x86_64 Debian 13; принимает Microsoft EULA:
.venv/bin/python tools/install_odbc_linux.py
export ODBCSYSINI=/workspace/.etl-odbc
# Подготовьте .env по .env.example
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 0.0.0.0:8001
# отдельный процесс
.venv/bin/python manage.py etl_worker
```

Linux helper проверяет подпись Microsoft Release.gpg и SHA256 индекса и пакета, извлекает драйвер без root. Нужны HTTPS к packages.microsoft.com, gpg, dpkg-deb, unixODBC и системные зависимости драйвера. Linux helper не нужен на Windows.

## Следующий этап

1. Прогнать integration на ваших SQL Server и приёмочные тесты на Windows Server (TLS, Windows Auth/service identity, реальные типы 1С и объёмы).
2. Реализовать Incremental: бизнес-ключ, watermark с tie-breaker, upsert/delete policy, checkpoint только после commit, повторное выполнение и тесты отказов.
3. Добавить версии конфигураций запуска, расширенный аудит и политику хранения логов, уведомления и управляемую отмену активного запроса.
4. При росте нагрузки заменить SQLite/локальную очередь на серверную БД и Celery/Redis, сохранив `etl_engine.runner` и контракт прогресса; добавить распределённую блокировку/leases с fencing.
