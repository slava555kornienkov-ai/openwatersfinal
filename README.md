# Open Waters - Telegram Verification Backend

Backend для верификации номеров телефона через официальный Telegram API (Telethon).

## Как это работает

1. Пользователь вводит номер телефона в приложении
2. Frontend вызывает `POST /api/send-code` с номером
3. Backend через Telegram API отправляет код (пользователь получает SMS от Telegram или уведомление в самом Telegram)
4. Пользователь вводит код
5. Frontend вызывает `POST /api/verify-code` с кодом
6. Backend подтверждает номер

## Настройка

### 1. Получить API ID и API Hash

1. Зайди на https://my.telegram.org
2. Войди по номеру телефона
3. Перейди в **"API development tools"**
4. Создай приложение:
   - App title: `Open Waters`
   - Short name: `openwaters`
   - Platform: `Desktop`
5. Скопируй **api_id** и **api_hash**

### 2. Запуск через Docker (рекомендуется)

```bash
# Скопируй env файл
cp .env.example .env

# Отредактируй .env - вставь свои api_id и api_hash
nano .env

# Запуск
docker-compose up -d

# Проверка
curl http://localhost:8000/api/health
```

### 3. Запуск без Docker

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск
API_ID=12345678 API_HASH=abcdef123456 uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. Первая авторизация (обязательно!)

При первом запуске Telegram запросит код подтверждения:

```bash
docker-compose logs -f
```

В логах появится: **"Please enter the code you received: "**
- Введи код, который придёт в Telegram от Telegram
- Это одноразовая процедура — сессия сохранится в файл

### 5. Деплой (Railway / Render / VPS)

**Railway (бесплатно):**
1. Создай проект на railway.app
2. Подключи GitHub репозиторий
3. Добавь переменные окружения: `API_ID`, `API_HASH`
4. Railway сам соберёт Dockerfile

**Render (бесплатно):**
1. Создай Web Service на render.com
2. Выбери Docker
3. Добавь env переменные

**VPS:**
```bash
git clone <repo>
cd backend
cp .env.example .env
# редактируй .env
docker-compose up -d
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/send-code` | Отправить код на номер |
| POST | `/api/verify-code` | Проверить код |
| GET | `/api/health` | Проверка работы |

### POST /api/send-code

**Request:**
```json
{
  "phone": "79160179220"
}
```

**Response:**
```json
{
  "success": true,
  "phone_code_hash": "abc123...",
  "message": "Code sent. Check your Telegram or SMS from Telegram."
}
```

### POST /api/verify-code

**Request:**
```json
{
  "phone": "79160179220",
  "code": "123456",
  "phone_code_hash": "abc123..."
}
```

**Response:**
```json
{
  "success": true,
  "verified": true,
  "message": "Phone number verified successfully."
}
```

## Ошибки

| Код | Значение |
|-----|----------|
| 400 | Неверный номер / код |
| 429 | Слишком много попыток (rate limit) |
| 500 | Ошибка сервера |
