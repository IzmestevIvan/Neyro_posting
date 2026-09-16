# Меню чата и административные рассылки

`/start`: приветственная картинка и кнопки приложения, описания продукта,
инструкции и поддержки. Поддержка берётся из сохранённого `support_username`.
Описание и инструкция доступны без активного промокода. `/start add_source`
сохраняет прежний сценарий добавления источника. `/status` показывает состояние
канала; `/help` объясняет действия в чате.

Администратор видит кнопку «Рассылка клиентам». Команды работают только в личном
чате и повторно проверяют административные права на каждом действии.

1. `/broadcast Текст сообщения` — до 3500 символов, простой текст без HTML.
2. Бот показывает точный текст и количество получателей; снимок аудитории сохранён.
3. Нажать «Отправить» в течение 30 минут или «Отмена».
4. `/broadcast_status` показывает последние пять рассылок и результаты.

Получатели — все пользователи в базе без административной блокировки, в том числе
с истёкшим доступом. Пользователи, заблокировавшие бота, попадут в недоступные.
Рассылки не отправляют ничего в каналы. Запускается максимум одна рассылка,
по одному получателю с секундной паузой, отдельно от планировщика постов.
Текст никогда не интерпретируется как HTML. Повторное нажатие не повторяет отправку.
При неопределённом ответе Telegram или перезапуске во время отправки получатель
помечается «требует сверки» и автоматически не получает повтор. При явном 429
соблюдается указанная Telegram задержка. Завершённые записи хранятся 30 дней.
Нельзя гарантировать exactly-once доставку через Telegram: неизвестный результат
означает возможную доставку, а не подтверждённый отказ.

Реальная массовая отправка при разработке и проверках не выполнялась.

## Приветственная картинка

Файл: `app/bot/assets/welcome.png`. Создан встроенным image_gen, не через CLI.
Аватарка основного бота не изменяется до выбора пользователя.

Промпт генерации:

Use case: ads-marketing. Asset: welcome illustration for NeuroPost Telegram
publishing assistant, landscape 1536x1024. A refined editorial 3D still life:
a flowing sequence of three floating paper post cards, a subtle lightning emblem
and one luminous mint orb, conveying drafts becoming organised posts. Quiet
premium composition, generous negative space, deep forest green background
#101615, sage and mint #b8edb1 #92d9c2, soft studio lighting, tactile paper and
frosted glass, restrained detail, excellent legibility at mobile size. No text,
no letters, no UI buttons, no logos of other brands, no watermark. This is the
welcome image, not an app screenshot.
