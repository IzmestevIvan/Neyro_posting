# Поддержка NeuroPost

Бот: @Neyro_SupportBot. Команды /start и /help объясняют порядок обращения.
Любой пользователь может написать в личный чат, даже без активной подписки.
Обращение копируется существующим ADMIN_IDS. Чтобы получать обращения,
каждому администратору нужно открыть нового бота и нажать /start.
Ответить клиенту можно ответом на заголовок обращения или на его вложение.
Бот проверяет соответствие admin_id и message_id, не позволяет вручную указать
произвольного получателя. Поддерживаются текст и вложения, которые Telegram
разрешает копировать. При ошибке отправки успех не подтверждается.

Это отдельный процесс Docker Compose support с ограниченным журналом.
SUPPORT_BOT_TOKEN хранится в .support.env на сервере, файл не входит ни в Git,
ни в образ. После раскрытия токена его нужно перевыпустить и заменить здесь.
Назначение, имя и аватар настроены scripts/configure_support_bot.py.
Скрипт проверяет ID бота и отказывается заменять существующий webhook.

Таблица support_routes хранит только маршруты ответов (ID администратора,
сообщения и клиента), не тела сообщений. Маршруты удаляются через 30 дней.
Удаление маршрута не удаляет сообщения в Telegram. Лимит 15 сообщений в минуту
на пользователя; до 8 параллельных обработчиков. Ссылка на поддержку появляется
в мини-приложении после успешного запуска support.

Обычные предупреждения основного приложения читаются в админке из
data/logs/events.jsonl (3 файла по 1 МиБ, последние 60 событий). Полный errors.log
сохраняется отдельно с прежней ротацией. Наличие записи о восстановлении ИИ не
означает публикацию: редакционные проверки и расписание остаются обязательными.

## Аватар

app/assets/support-avatar.png — оригинал, support-avatar.jpg — копия для Telegram.
Встроенная генерация изображений, запрос:
«Use case: stylized-concept. Asset type: Telegram avatar for NeuroPost customer support bot.
Create a polished square raster illustration: a friendly compact mint-green robot wearing
a customer-support headset, a subtle lightning-bolt emblem on its chest, centered on a
deep forest-green background with soft mint glow. Premium restrained 3D clay/render aesthetic,
clear welcoming eyes, clean simple silhouette, readable at 48 pixels. Palette echoes NeuroPost
dark green and pale mint. Circular crop safe with all important details inside central 70 percent.
No text, no letters, no watermark, no extra objects.»
