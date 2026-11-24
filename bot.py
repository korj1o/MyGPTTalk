import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_env():
    path = Path(__file__).with_name('.env')
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip().strip('"\'')


def post(url, data, headers=None, timeout=60):
    request = urllib.request.Request(
        url, data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json', **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main():
    load_env()
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    allowed = os.getenv('TELEGRAM_ALLOWED_USER_ID', '').strip()
    if not token or not api_key:
        raise SystemExit('Укажите TELEGRAM_BOT_TOKEN и OPENAI_API_KEY в файле .env')
    if allowed and not allowed.isdigit():
        raise SystemExit('TELEGRAM_ALLOWED_USER_ID должен быть числовым ID пользователя')
    model = os.getenv('OPENAI_MODEL', 'gpt-6-luna').strip()
    api_base = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').strip().rstrip('/')
    base = f'https://api.telegram.org/bot{token}/'
    history = {}

    def telegram(method, payload, timeout=60):
        result = post(base + method, payload, timeout=timeout)
        if not result.get('ok'):
            raise RuntimeError('Ошибка Telegram API')
        return result.get('result')

    def send(chat_id, message):
        while message:
            part, message = message[:4000], message[4000:]
            telegram('sendMessage', {'chat_id': chat_id, 'text': part})

    logging.info('Бот запущен. Для остановки нажмите Ctrl+C.')
    if not allowed:
        logging.warning('Режим настройки: отправьте боту /start, затем укажите свой ID в .env и перезапустите бота.')
    offset = None
    while True:
        try:
            updates = telegram('getUpdates', {'offset': offset, 'timeout': 30, 'allowed_updates': ['message']}, timeout=40)
            for update in updates:
                offset = update['update_id'] + 1
                msg = update.get('message', {})
                chat = msg.get('chat', {})
                user = msg.get('from', {})
                text = (msg.get('text') or msg.get('caption') or '').strip()
                photos = msg.get('photo') or []
                document = msg.get('document') or {}
                image_file = photos[-1] if photos else (document if document.get('mime_type') in ('image/jpeg', 'image/png', 'image/webp', 'image/gif') else None)
                if chat.get('type') != 'private' or not user.get('id') or not (text or image_file):
                    continue
                chat_id, user_id = chat['id'], str(user['id'])
                if not allowed:
                    if text and text.split()[0].split('@')[0] == '/start':
                        send(chat_id, f'Ваш Telegram user ID: {user_id}. Укажите его в TELEGRAM_ALLOWED_USER_ID и перезапустите бота.')
                    continue
                if user_id != allowed:
                    continue
                command = text.split()[0].split('@')[0] if text and not image_file else ''
                if command == '/start':
                    send(chat_id, 'Привет! Напишите сообщение или отправьте картинку с вопросом. Команда /reset начинает новый диалог.')
                    continue
                if command == '/reset':
                    history.pop(user_id, None)
                    send(chat_id, 'История диалога очищена.')
                    continue
                if text.startswith('/'):
                    send(chat_id, 'Доступные команды: /start и /reset.')
                    continue
                try:
                    telegram('sendChatAction', {'chat_id': chat_id, 'action': 'typing'})
                    content = text
                    if image_file:
                        limit = 10 * 1024 * 1024
                        if image_file.get('file_size', 0) > limit:
                            send(chat_id, 'Картинка слишком большая. Отправьте изображение до 10 МБ.')
                            continue
                        file_info = telegram('getFile', {'file_id': image_file['file_id']})
                        if file_info.get('file_size', 0) > limit:
                            send(chat_id, 'Картинка слишком большая. Отправьте изображение до 10 МБ.')
                            continue
                        with urllib.request.urlopen(f'https://api.telegram.org/file/bot{token}/' + file_info['file_path'], timeout=60) as img:
                            data = img.read(limit + 1)
                        if len(data) > limit:
                            send(chat_id, 'Картинка слишком большая. Отправьте изображение до 10 МБ.')
                            continue
                        mime = 'image/jpeg' if photos else document['mime_type']
                        encoded = base64.b64encode(data).decode('ascii')
                        content = [{'type': 'text', 'text': text or 'Что изображено на этой картинке?'},
                                   {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}}]
                    messages = history.get(user_id, []) + [{'role': 'user', 'content': content}]
                    response = post(api_base + '/chat/completions', {
                        'model': model, 'messages': messages,
                    }, {'Authorization': f'Bearer {api_key}'}, timeout=120)
                    answer = response['choices'][0]['message'].get('content') or ''
                    if not answer:
                        raise ValueError('Пустой ответ OpenAI')
                    send(chat_id, answer)
                    history[user_id] = (messages + [{'role': 'assistant', 'content': answer}])[-20:]
                except Exception as exc:
                    logging.error('Ошибка обработки сообщения: %s', type(exc).__name__)
                    send(chat_id, 'Не удалось получить ответ. Проверьте ключ OpenAI, доступ к модели и баланс API.')
        except KeyboardInterrupt:
            break
        except Exception as exc:
            logging.error('Ошибка соединения с Telegram: %s. Повтор через 5 секунд.', type(exc).__name__)
            time.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    main()
