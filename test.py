import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import base64
import time
import io
import os
import requests
import urllib3
import threading
from PIL import Image
from gigachat import GigaChat
from transformers import BlipProcessor, BlipForConditionalGeneration
import torch
import sqlite3
import json
import re
import hashlib
import logging
import signal
import sys
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 0️⃣ ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================================

vk_session = None
vk = None
supabase = None

# ============================================================
# 1️⃣ НАСТРОЙКА FLASK
# ============================================================

app = Flask(__name__)


@app.route('/send_broadcast', methods=['POST'])
def handle_broadcast():
    """Принимает рассылку с сайта и отправляет всем пользователям"""
    global vk, supabase

    try:
        print("📨 Получен POST-запрос на /send_broadcast")

        data = request.get_json()
        print(f"📨 Данные: {data}")

        if not data:
            print("❌ Нет данных в запросе")
            return jsonify({'status': 'error', 'error': 'No data provided'}), 400

        if 'message' not in data:
            print("❌ Нет поля message")
            return jsonify({'status': 'error', 'error': 'No message provided'}), 400

        message = data['message']
        print(f"📨 Получена рассылка с сайта: {message[:50]}...")

        users = get_all_bot_users()
        print(f"📊 Найдено {len(users)} пользователей для рассылки")

        if not users:
            return jsonify({'status': 'error', 'error': 'No users found'}), 404

        sent = 0
        for user_id in users:
            try:
                send_message(user_id, message)
                sent += 1
                time.sleep(0.1)
                if sent % 10 == 0:
                    print(f"📤 Отправлено: {sent}/{len(users)}")
            except Exception as e:
                print(f"❌ Ошибка отправки пользователю {user_id}: {e}")

        print(f"✅ Рассылка выполнена! Отправлено: {sent}")
        return jsonify({'status': 'success', 'sent': sent, 'total': len(users)}), 200

    except Exception as e:
        print(f"❌ Ошибка при обработке рассылки: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/get_users', methods=['GET'])
def get_users():
    """Возвращает список всех пользователей бота"""
    try:
        users = get_all_bot_users()
        return jsonify({'status': 'success', 'users': users, 'count': len(users)}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/stats', methods=['GET'])
def get_stats():
    """Возвращает статистику бота"""
    try:
        users = get_all_bot_users()
        return jsonify({
            'status': 'success',
            'total_users': len(users),
            'users': users
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Проверка работоспособности бота"""
    return jsonify({'status': 'ok', 'message': 'Bot is running'}), 200


def run_flask():
    """Запускает Flask сервер в отдельном потоке"""
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


# ============================================================
# 2️⃣ НАСТРОЙКИ VK
# ============================================================

VK_TOKEN = "vk1.a.zDmGVDdiQH-j2MHwzh0rRoNDPfzDFNrpoje5sC7NtZKsSrElAi3rUeUfEEi0sqgNDuxwYRkeSMpMoABD8tlugCc_pYTGG93SavFBtyiaLiphwjQQ-AjKEFqJpsFBewUnqbIM262W96Tn08BXMHGs_RpFIS64bu6cXEuIWb6QKvd6hSd0OG8bYF7iIWM95EoGz2DkdVLISrwqh25Yg001mg"
GROUP_ID = 240220666
ADMIN_ID = 835355641

CLIENT_ID = "019f5b19-1f71-783b-b651-f1417004dde3"
CLIENT_SECRET = "f152afa8-9ce3-4588-bf11-91114b981b06"

raw_credits = f"{CLIENT_ID}:{CLIENT_SECRET}"
GIGACHAT_CREDENTIALS = base64.b64encode(raw_credits.encode()).decode()

giga_client = GigaChat(credentials=GIGACHAT_CREDENTIALS,
                       scope="GIGACHAT_API_PERS", verify_ssl_certs=False)

print("🔄 Загрузка BLIP...")
processor = BlipProcessor.from_pretrained(
    "Salesforce/blip-image-captioning-base")
vision_model = BlipForConditionalGeneration.from_pretrained(
    "Salesforce/blip-image-captioning-base")
print("✅ BLIP загружен!")

# ============================================================
# 3️⃣ НАСТРОЙКИ SUPABASE
# ============================================================

SUPABASE_URL = "https://fmijtyjmliklxciqryap.supabase.co"
SUPABASE_KEY = "sb_secret_cRKj_FURc95dFCYSrxNDXw_oT7W7yiU"
SUPABASE_BUCKET = "images"

try:
    from supabase import create_client, Client

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase клиент инициализирован")
except ImportError:
    print("⚠️ Библиотека supabase не установлена. Установи: pip install supabase")
    supabase = None
except Exception as e:
    print(f"⚠️ Ошибка инициализации Supabase: {e}")
    supabase = None


# ============================================================
# 4️⃣ ФУНКЦИИ ДЛЯ РАБОТЫ С SUPABASE
# ============================================================

def create_bot_users_table():
    """Создаёт таблицу bot_users в Supabase"""
    if not supabase:
        print("⚠️ Supabase не инициализирован, таблица не создана")
        return

    try:
        supabase.table("bot_users").select("*").limit(1).execute()
        print("✅ Таблица bot_users уже существует")
    except Exception as e:
        if "does not exist" in str(e).lower():
            print("⚠️ Таблица bot_users не найдена. Создайте её в SQL Editor: CREATE TABLE bot_users (user_id BIGINT PRIMARY KEY, first_seen TIMESTAMP DEFAULT NOW());")
        else:
            print(f"⚠️ Ошибка проверки таблицы: {e}")


def save_user_to_supabase(user_id):
    """Сохраняет пользователя в Supabase (таблица bot_users)"""
    if not supabase:
        print("⚠️ Supabase не инициализирован")
        return False

    try:
        data = {
            "user_id": user_id
        }
        result = supabase.table("bot_users").upsert(data).execute()
        print(f"✅ Пользователь {user_id} сохранён в Supabase")
        return True
    except Exception as e:
        print(f"❌ Ошибка сохранения в Supabase: {e}")
        return False


def register_bot_user(user_id, username, password, email=""):
    """Сохраняет пользователя в Supabase с хешем пароля для входа на сайт"""
    if not supabase:
        return False, "❌ Supabase не доступен"

    password_hash = hashlib.sha256(password.encode()).hexdigest()

    try:
        data = {
            "user_id": user_id,
            "username": username,
            "password_hash": password_hash,
            "email": email
        }
        supabase.table("bot_registrations").upsert(data).execute()
        return True, "✅ Регистрация успешна!"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"


def get_all_bot_users():
    """Получает список ВСЕХ пользователей из Supabase"""
    try:
        if supabase:
            result = supabase.table("bot_users").select("user_id").execute()
            users = [row['user_id'] for row in result.data]
            print(f"📊 Получено {len(users)} пользователей из Supabase")
            return users
    except Exception as e:
        print(f"❌ Ошибка получения пользователей из Supabase: {e}")

    return []


# ============================================================
# 5️⃣ ОБРАБОТЧИК СИГНАЛОВ
# ============================================================

def signal_handler(sig, frame):
    print("\n" + "=" * 60)
    print("🛑 Получен сигнал завершения программы")
    print("=" * 60)
    print("👋 Бот завершает работу...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ============================================================
# 6️⃣ ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (ЛОКАЛЬНОЙ)
# ============================================================
def init_database():
    try:
        conn = sqlite3.connect("votes.db")
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            cursor.execute('''
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    site_username TEXT,
                    site_password TEXT,
                    site_email TEXT,
                    is_email_user INTEGER DEFAULT 0
                )
            ''')

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='photos'")
        if not cursor.fetchone():
            cursor.execute('''
                CREATE TABLE photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    filename TEXT,
                    url TEXT,
                    description TEXT,
                    approved BOOLEAN DEFAULT 0,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

        conn.commit()
        conn.close()
        print("✅ Локальная база данных инициализирована")
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")


# ============================================================
# 7️⃣ КЛАВИАТУРЫ
# ============================================================

def get_start_keyboard():
    kb = VkKeyboard(one_time=False)
    kb.add_button("Старт", color=VkKeyboardColor.POSITIVE)
    return kb


def get_begin_keyboard():
    kb = VkKeyboard(one_time=True)
    kb.add_button("Начать", color=VkKeyboardColor.POSITIVE)
    return kb


def get_main_keyboard(is_registered=False):
    kb = VkKeyboard(one_time=False)
    kb.add_button("Отправить фото", color=VkKeyboardColor.POSITIVE)
    kb.add_line()

    if is_registered:
        kb.add_button("Участвовать в конкурсе", color=VkKeyboardColor.POSITIVE)
    else:
        kb.add_button("Регистрация", color=VkKeyboardColor.PRIMARY)

    kb.add_line()
    kb.add_button("Помощь", color=VkKeyboardColor.PRIMARY)
    kb.add_button("Перезапустить", color=VkKeyboardColor.NEGATIVE)
    return kb


def get_cancel_registration_keyboard():
    kb = VkKeyboard(one_time=False)
    kb.add_button("Отменить регистрацию", color=VkKeyboardColor.NEGATIVE)
    return kb


def get_cancel_keyboard():
    kb = VkKeyboard(one_time=False)
    kb.add_button("Прекратить отправку", color=VkKeyboardColor.NEGATIVE)
    return kb


def get_help_reset_keyboard():
    kb = VkKeyboard(one_time=False)
    kb.add_button("Помощь", color=VkKeyboardColor.PRIMARY)
    kb.add_button("Перезапустить", color=VkKeyboardColor.NEGATIVE)
    return kb


# ============================================================
# 8️⃣ ФУНКЦИЯ ОТПРАВКИ СООБЩЕНИЙ
# ============================================================

def send_message(user_id, text, keyboard=None):
    """Отправляет сообщение пользователю через VK API"""
    global vk

    if not vk:
        print("❌ VK API не инициализирован")
        return False

    data = {"user_id": user_id, "message": text, "random_id": 0}
    if keyboard:
        data["keyboard"] = keyboard.get_keyboard()
    try:
        vk.messages.send(**data)
        return True
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return False


# ============================================================
# 9️⃣ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def is_user_registered(user_id):
    """Проверяет, зарегистрирован ли пользователь (в локальной БД или Supabase)"""
    try:
        conn = sqlite3.connect("votes.db")
        cursor = conn.execute(
            'SELECT site_username FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return True
    except:
        pass

    # Проверяем в Supabase
    try:
        if supabase:
            result = supabase.table("bot_registrations").select(
                "*").eq("user_id", user_id).execute()
            if result.data:
                return True
    except:
        pass

    return False


user_state = {}
user_history = {}
users_to_notify = set()


def reset_user(user_id):
    """Сброс пользователя с сохранением в Supabase"""
    user_state[user_id] = {"stage": "start"}
    user_history[user_id] = []
    users_to_notify.add(user_id)

    if supabase:
        try:
            result = supabase.table("bot_users").upsert(
                {"user_id": user_id}).execute()
            print(f"✅ Пользователь {user_id} сохранён в Supabase")
        except Exception as e:
            print(
                f"❌ Ошибка сохранения пользователя {user_id} в Supabase: {e}")

    send_message(user_id, "👋 Нажми 'Старт', чтобы начать.",
                 get_start_keyboard())


def restart_user(user_id):
    """Ручной перезапуск пользователя"""
    user_state[user_id] = {"stage": "main"}
    user_history[user_id] = []
    registered = is_user_registered(user_id)

    if registered:
        send_message(user_id,
                     "🔄 Бот перезапущен.\n\n"
                     "🌐 Вы в главном меню.\n\n"
                     "📸 Нажмите 'Отправить фото' для советов\n"
                     "🏆 Нажмите 'Участвовать в конкурсе'\n"
                     "🔹 Нажмите 'Помощь' для подсказки",
                     get_main_keyboard(registered))
    else:
        send_message(user_id,
                     "🔄 Бот перезапущен.\n\n"
                     "🌐 Вы в главном меню.\n\n"
                     "📸 Нажмите 'Отправить фото' для советов\n"
                     "🔹 Нажмите 'Регистрация' для создания аккаунта\n"
                     "🔹 Нажмите 'Помощь' для подсказки",
                     get_main_keyboard(registered))


def tips_broadcast_loop():
    while True:
        try:
            if users_to_notify:
                tip = ask_gigachat("system", "", "", task="care")
                if tip:
                    tips_text = f"🧥 **Совет по уходу за одеждой!**\n\n{tip}"
                    for user_id in list(users_to_notify):
                        try:
                            send_message(user_id, tips_text)
                            time.sleep(0.2)
                        except:
                            pass
            time.sleep(300)
        except:
            time.sleep(300)


# ============================================================
# 🔟 ОСНОВНЫЕ ФУНКЦИИ БОТА
# ============================================================

def ask_gigachat(user_id, user_text, detected_clothing, task="upcycling"):
    return "Используйте кнопку 'Отправить фото' для получения советов."


def get_image_description(img_bytes):
    try:
        raw_image = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        inputs = processor(raw_image, return_tensors="pt")
        out = vision_model.generate(**inputs)
        return processor.decode(out[0], skip_special_tokens=True)
    except:
        return ""


def moderate_photo_with_giga(image_description):
    try:
        prompt = f"""
Ты — модератор конкурса по апсайклингу одежды. 
Проанализируй описание фото и ответь строго по формату:

Описание фото: {image_description}

Вопрос: Есть ли на этом фото ОДЕЖДА (любая: футболка, джинсы, платье, куртка, обувь, головной убор и т.д.)?

Правила:
- ДА, если на фото есть любая одежда (даже если её просто носят на человеке)
- НЕТ, если на фото НЕТ одежды (природа, животные, еда, машины и т.д.)

Ответь ТОЛЬКО одним из вариантов:
- ДА
- НЕТ

Также напиши краткую причину.

Формат ответа:
Статус: ДА/НЕТ
Причина: ...
"""

        response = giga_client.chat({
            "model": "GigaChat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3
        })

        ai_message = response.choices[0].message.content

        status = "НЕТ"
        reason = "Не удалось определить"

        if "Статус: ДА" in ai_message or "Статус: Да" in ai_message:
            status = "ДА"
        elif "Статус: НЕТ" in ai_message or "Статус: Нет" in ai_message:
            status = "НЕТ"

        if "Причина:" in ai_message:
            reason_match = re.search(r'Причина:\s*(.+)', ai_message)
            if reason_match:
                reason = reason_match.group(1).strip()

        if status not in ["ДА", "НЕТ"]:
            simple_prompt = f"На фото: {image_description}. Есть ли на этом фото одежда? Ответь только ДА или НЕТ."
            simple_response = giga_client.chat({
                "model": "GigaChat",
                "messages": [{"role": "user", "content": simple_prompt}],
                "temperature": 0.1
            })
            simple_answer = simple_response.choices[0].message.content.strip(
            ).upper()
            if "ДА" in simple_answer:
                status = "ДА"
                reason = "На фото есть одежда"
            elif "НЕТ" in simple_answer:
                status = "НЕТ"
                reason = "На фото нет одежды"

        return status == "ДА", reason

    except Exception as e:
        print(f"❌ Ошибка модерации: {e}")
        return True, "Модерация временно недоступна"


def get_upcycling_advice_with_moderation(image_description):
    try:
        prompt_check = f"""
На фото: {image_description}. 
Есть ли на этом фото одежда? Ответь только ДА или НЕТ.
"""
        check_response = giga_client.chat({
            "model": "GigaChat",
            "messages": [{"role": "user", "content": prompt_check}],
            "temperature": 0.1
        })

        check_answer = check_response.choices[0].message.content.strip(
        ).upper()

        if "НЕТ" in check_answer:
            return None, "❌ На этом фото нет одежды.\n\n📸 Отправьте другое фото именно с одеждой."

        prompt_advice = f"""
Ты — профессиональный стилист и эксперт по апсайклингу одежды.
На основе описания вещи дай 2-3 креативные идеи, как её перешить,
кастомизировать или улучшить. Отвечай кратко и по делу.

Описание: {image_description}

Важно: НЕ ПИШИ описание фото в ответе. Дай только советы.
"""

        response = giga_client.chat({
            "model": "GigaChat",
            "messages": [{"role": "user", "content": prompt_advice}],
            "temperature": 0.7
        })

        advice = response.choices[0].message.content
        return advice, None

    except Exception as e:
        return None, f"❌ Ошибка: {str(e)}"


def upload_vk_photo_to_supabase(photo_url, user_id, description=""):
    destination_name = f"user_{user_id}_{int(time.time())}.jpg"

    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "image/jpeg"
    }

    try:
        response = requests.get(photo_url, stream=True, timeout=10)
        if response.status_code != 200:
            return False, f"❌ Не удалось скачать фото (статус {response.status_code})"

        file_data = response.content
        upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{destination_name}"

        upload_response = requests.post(
            upload_url,
            headers=headers,
            data=file_data,
            timeout=15
        )

        if upload_response.status_code not in [200, 201]:
            os.makedirs("photos", exist_ok=True)
            filename = f"photos/user_{user_id}_{int(time.time())}.jpg"
            with open(filename, "wb") as f:
                f.write(file_data)
            return True, f"✅ Фото сохранено локально: {filename}"

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{destination_name}"

        try:
            conn = sqlite3.connect("votes.db")
            conn.execute(
                'INSERT INTO photos (user_id, filename, url, description, approved) VALUES (?, ?, ?, ?, ?)',
                (user_id, destination_name, public_url, description, 1)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Ошибка сохранения в БД: {e}")

        return True, f"✅ Фото загружено в облако!\n🔗 {public_url}"

    except Exception as e:
        return False, f"❌ Ошибка: {str(e)}"


# ============================================================
# 1️⃣1️⃣ ЗАПУСК БОТА
# ============================================================

def init():
    """Инициализация всего"""
    global vk_session, vk

    init_database()
    create_bot_users_table()

    try:
        conn = sqlite3.connect("votes.db")
        conn.execute('DELETE FROM users WHERE user_id = ?', (ADMIN_ID,))
        conn.commit()
        conn.close()
        print(f"🗑 Регистрация админа {ADMIN_ID} сброшена при запуске")
    except Exception as e:
        print(f"❌ Ошибка сброса регистрации админа: {e}")


init()

print("\n" + "=" * 60)
print("📌 БОТ ГОТОВ К РАБОТЕ")
print("=" * 60)
print("☁️ Supabase Storage: включён")
print("💾 Пользователи сохраняются в Supabase")
print("📨 Доступна массовая рассылка")
print("📨 Flask сервер запущен на порту 5000")
print("🌐 Эндпоинт: POST /send_broadcast")
print("🌐 Эндпоинт: GET /get_users")
print("🌐 Эндпоинт: GET /stats")
print("🌐 Эндпоинт: GET /health")
print("=" * 60 + "\n")
print("ℹ️ Нажмите Ctrl+C для остановки бота")
print("=" * 60 + "\n")

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
print("✅ Flask сервер запущен!")

try:
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    longpoll = VkBotLongPoll(vk_session, GROUP_ID)
    vk = vk_session.get_api()
    print("✅ Бот успешно подключился к VK!")
    print("✅ Бот запущен и готов к работе!")

    save_user_to_supabase(ADMIN_ID)

except Exception as e:
    print(f"❌ Ошибка запуска бота: {e}")
    sys.exit(1)

tips_thread = threading.Thread(target=tips_broadcast_loop, daemon=True)
tips_thread.start()

# ============================================================
# 1️⃣2️⃣ ОСНОВНОЙ ЦИКЛ
# ============================================================

for event in longpoll.listen():
    if event.type != VkBotEventType.MESSAGE_NEW:
        continue

    from_user = event.object.message["from_id"]
    raw_text = event.object.message.get("text", "")
    text = raw_text.lower().strip()
    attachments = event.object.message.get("attachments", [])

    save_user_to_supabase(from_user)

    print(f"📩 Сообщение от {from_user}: {text}")

    # Команда для массовой рассылки (только админ)
    if text.startswith("/mass") and from_user == ADMIN_ID:
        message = text[5:].strip()
        if message:
            send_message(from_user, f"⏳ Начинаю рассылку ВСЕМ пользователям...\n\n📝 Сообщение: {message}",
                         get_main_keyboard(True))
            users = get_all_bot_users()
            sent = 0
            for uid in users:
                try:
                    send_message(uid, message)
                    sent += 1
                    time.sleep(0.1)
                except:
                    pass
            send_message(from_user, f"✅ Рассылка завершена!\n📤 Отправлено: {sent} пользователям",
                         get_main_keyboard(True))
        else:
            send_message(from_user, "❌ Укажите сообщение для рассылки.\nПример: /mass Привет всем!",
                         get_main_keyboard(True))
        continue

    if text == "перезапустить":
        restart_user(from_user)
        continue

    if from_user not in user_state:
        reset_user(from_user)
        continue

    stage = user_state[from_user]["stage"]
    registered = is_user_registered(from_user)

    if text == "старт":
        if stage == "start":
            user_state[from_user]["stage"] = "main"
            if registered:
                send_message(from_user,
                             "🌐 **Главное меню**\n\n"
                             "📸 Нажмите 'Отправить фото' для советов\n"
                             "🏆 Нажмите 'Участвовать в конкурсе'\n"
                             "🔹 Нажмите 'Помощь' для подсказки",
                             get_main_keyboard(registered))
            else:
                send_message(from_user,
                             "🌐 **Главное меню**\n\n"
                             "📸 Нажмите 'Отправить фото' для советов\n"
                             "🔹 Нажмите 'Регистрация' для создания аккаунта\n"
                             "🔹 Нажмите 'Помощь' для подсказки",
                             get_main_keyboard(registered))
        else:
            reset_user(from_user)
        continue

    if text == "регистрация":
        if stage == "main":
            registered = is_user_registered(from_user)

            if registered:
                send_message(from_user,
                             "✅ Вы уже зарегистрированы!\n\n"
                             "🏆 Нажмите 'Участвовать в конкурсе'",
                             get_main_keyboard(registered))
                continue

            send_message(from_user,
                         "🌐 **Регистрация**\n\n"
                         "Придумайте логин (минимум 3 символа):\n\n"
                         "❌ Или нажмите 'Отменить регистрацию'",
                         get_cancel_registration_keyboard())
            user_state[from_user]["stage"] = "register_username"
        else:
            send_message(from_user, "Сначала нажмите 'Старт'.",
                         get_main_keyboard(registered))
        continue

    if stage == "register_username":
        if text == "отменить регистрацию":
            user_state[from_user]["stage"] = "main"
            send_message(from_user,
                         "❌ Регистрация отменена.\n\n"
                         "🌐 Вы вернулись в главное меню.",
                         get_main_keyboard(registered))
            continue

        username = text.strip()

        if len(username) < 3:
            send_message(from_user, "❌ Минимум 3 символа. Придумайте логин:",
                         get_cancel_registration_keyboard())
            continue

        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            send_message(from_user, "❌ Только буквы, цифры, _. Придумайте логин:",
                         get_cancel_registration_keyboard())
            continue

        user_state[from_user]["temp_username"] = username
        user_state[from_user]["stage"] = "register_password"
        send_message(from_user,
                     f"✅ Логин: {username}\n\n"
                     "Теперь придумайте пароль (минимум 6 символов):\n\n"
                     "❌ Или нажмите 'Отменить регистрацию'",
                     get_cancel_registration_keyboard())
        continue

    if stage == "register_password":
        if text == "отменить регистрацию":
            user_state[from_user]["stage"] = "main"
            if "temp_username" in user_state[from_user]:
                del user_state[from_user]["temp_username"]
            send_message(from_user,
                         "❌ Регистрация отменена.\n\n"
                         "🌐 Вы вернулись в главное меню.",
                         get_main_keyboard(registered))
            continue

        password = text.strip()

        if len(password) < 6:
            send_message(from_user, "❌ Минимум 6 символов. Придумайте пароль:",
                         get_cancel_registration_keyboard())
            continue

        user_state[from_user]["temp_password"] = password
        user_state[from_user]["stage"] = "register_email"
        send_message(from_user,
                     f"✅ Пароль: {password}\n\n"
                     "Теперь введите вашу почту (email):\n\n"
                     "❌ Или нажмите 'Отменить регистрацию'",
                     get_cancel_registration_keyboard())
        continue

    if stage == "register_email":
        if text == "отменить регистрацию":
            user_state[from_user]["stage"] = "main"
            if "temp_username" in user_state[from_user]:
                del user_state[from_user]["temp_username"]
            if "temp_password" in user_state[from_user]:
                del user_state[from_user]["temp_password"]
            send_message(from_user,
                         "❌ Регистрация отменена.\n\n"
                         "🌐 Вы вернулись в главное меню.",
                         get_main_keyboard(registered))
            continue

        email = text.strip()

        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            send_message(from_user,
                         "❌ Некорректный email. Введите email в формате: example@mail.ru\n\n"
                         "❌ Или нажмите 'Отменить регистрацию'",
                         get_cancel_registration_keyboard())
            continue

        username = user_state[from_user].get("temp_username", "")
        password = user_state[from_user].get("temp_password", "")

        send_message(from_user, "⏳ Регистрирую вас...",
                     get_cancel_registration_keyboard())

        # ✅ Регистрируем через Supabase (хеш пароля)
        success, message = register_bot_user(
            from_user, username, password, email)

        if success:
            try:
                conn = sqlite3.connect("votes.db")
                conn.execute(
                    'INSERT OR REPLACE INTO users (user_id, site_username, site_password, site_email, is_email_user) VALUES (?, ?, ?, ?, ?)',
                    (from_user, username, password, email, 0)
                )
                conn.commit()
                conn.close()
            except:
                pass

            registered = True

            send_message(from_user,
                         f"🎉 {message}\n\n"
                         f"🌐 Логин: {username}\n"
                         f"🌐 Пароль: {password}\n"
                         f"📧 Email: {email}\n\n"
                         "🔑 Теперь вы можете войти на сайт, используя этот логин и пароль!\n"
                         "🌐 Ссылка на сайт: https://ecobot-lbar.onrender.com/bot_login\n\n"
                         "🏆 Также вы можете участвовать в конкурсе!\n"
                         "📸 Или отправлять фото для советов.\n\n"
                         "👉 Нажмите 'Участвовать в конкурсе' чтобы отправить фото.",
                         get_main_keyboard(registered))

            if "temp_username" in user_state[from_user]:
                del user_state[from_user]["temp_username"]
            if "temp_password" in user_state[from_user]:
                del user_state[from_user]["temp_password"]
            user_state[from_user]["stage"] = "main"

        else:
            send_message(from_user,
                         f"❌ {message}\n\n"
                         "Попробуйте другие данные.\n\n"
                         "❌ Или нажмите 'Отменить регистрацию'",
                         get_cancel_registration_keyboard())
            user_state[from_user]["stage"] = "register_username"
        continue

    if text == "участвовать в конкурсе":
        registered = is_user_registered(from_user)

        if not registered:
            send_message(from_user,
                         "❌ Сначала зарегистрируйтесь!\n\n"
                         "Нажмите 'Регистрация' для создания аккаунта.",
                         get_main_keyboard(registered))
            continue

        if stage == "main":
