# WAF-кандидат — ещё не включён в production

Сборка Coraza+Caddy и CRS зафиксирована в go.mod/go.sum; базовые образы по digest.
Артефакты подготовлены 2 октября 2026. Бинарник собран локально, но end-to-end
тесты не завершены из-за запрета на локальные сетевые сокеты. Нельзя объявлять
защиту включённой по наличию этих файлов или успешной компиляции.

## Проверки перед применением

1. Собрать Dockerfile.caddy на резерве; production пока оставить на штатном Caddy.
2. Выполнить `CADDY_WAF_BIN=/path/to/caddy python3 -m unittest discover -s tests -p 'test_waf_integration.py' -v`.
3. Проверить нормальную авторизацию Telegram, JSON с редакторским HTML, загрузку
   PNG до 4 МиБ, большие ответы; SQLi/XSS, повреждённый и глубокий JSON, превышение
   размера должны отклоняться до backend. Проверить реальные запросы Mini App.
4. Убедиться, что ни access log, ни runtime/error log не содержат initData,
   query-string, текст постов или тела запросов. Отдельный logging.caddy обязателен.
5. Измерить память, задержку и ложные блокировки на резерве; лимит памяти 256 МиБ
   — стартовый бюджет, а не подтверждённая достаточность. Провести ограниченную
   нагрузочную проверку локального стенда, без нагрузки на production.
6. После проверки сохранить старый образ и конфиг, применить compose.waf.yml,
   проверить HTTPS и реальные пользовательские действия; иметь быстрый откат.

## Дизайн

PL1, threshold 5. Общий body cap 64 КиБ до парсинга, исключение только POST
/api/channels/<число>/logo с image/png до 4 МиБ. Response body inspection выключен.
Полные audit/debug тела выключены; runtime WAF messages обезличены.

Нельзя просто отключить WAF для всего /api при ложных блокировках. Нужны точечные
исключения правила/аргумента с регрессионным тестом. SecRequestBodyNoFilesLimit
не используется: Coraza документирует отсутствие реализации этой директивы.

Локальный WAF не заменяет upstream L3/L4 Anti-DDoS и не скрывает IP сервера.
Cloudflare Free требует отдельного аккаунта и DNS-переключения, а доступность
из российских сетей необходимо проверить до выбора этого варианта.

Источники:
- https://github.com/corazawaf/coraza-caddy
- https://coraza.io/docs/seclang/directives/
- https://coreruleset.org/docs/2-how-crs-works/2-3-false-positives-and-tuning/
- https://blog.cloudflare.com/russian-internet-users-are-unable-to-access-the-open-internet/
