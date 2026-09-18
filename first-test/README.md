# first-test — проверка связки бот ↔ MAX ↔ мини-приложение

## Бот (`bot/`)

```bash
cd first-test/bot
pip install -r requirements.txt
python main.py
```

Токен: переменная окружения `MAX_BOT_TOKEN`, либо файл `THEORY/token.txt` (папка в `.gitignore`).

Что проверяем в MAX у `@t419_hakaton_max_bot`:
- `/start` — приветствие с кнопками;
- `Ping` — callback;
- `Контакт`, `Гео` — вложения приходят боту;
- `/id` — chat_id / user_id;
- любой текст — эхо;
- `Открыть мини-приложение` — сработает только после того, как организаторы привяжут URL из формы;
- `Открыть как ссылку` — откроет страницу во встроенном браузере без контекста Bridge.

## Мини-приложение (`miniapp/`)

Статическая страница, деплоится на GitHub Pages workflow'ом `.github/workflows/pages.yml`
по адресу `https://ilyakonfetka.github.io/max_hackaton/`.

Показывает контекст запуска (`platform`, `version`, `initDataUnsafe`, список методов Bridge)
и даёт кнопки: QR-сканер, запрос контакта, вибрация, `getLaunchContext`, фото с камеры.

Открытая напрямую в браузере страница покажет «Bridge не найден» — это нормально,
полноценно она работает только внутри MAX после привязки к боту.
