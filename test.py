import requests
from flask import Flask, render_template, redirect, url_for, request, session, send_from_directory, jsonify
import urllib.parse
import hmac
import hashlib
import sqlite3
import os
import time
from supabase import create_client, Client

# Настройки Supabase
SUPABASE_URL = "https://fmijtyjmliklxciqryap.supabase.co"
SUPABASE_KEY = "sb_secret_cRKj_FURc95dFCYSrxNDXw_oT7W7yiU"
SUPABASE_BUCKET = "images"

# Оставляем клиент для загрузки файлов (функция upload_photo_to_supabase его использует)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Ссылка для прямых HTTP-запросов к хранилищу
SUPABASE_API_STORAGE_URL = f"{SUPABASE_URL}/storage/v1/object/list/{SUPABASE_BUCKET}"


def upload_photo_to_supabase(user_id, photo_url):
    """Скачивает фото из ВК, загружает в Supabase Storage и возвращает публичный URL"""
    temp_file = f"temp_{user_id}.jpg"
    # Путь внутри бакета Supabase
    object_name = f"user_{user_id}/{int(time.time())}.jpg"

    try:
        # 1. Скачиваем фото во временный файл на Render
        with requests.get(photo_url, stream=True) as r:
            r.raise_for_status()
            with open(temp_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        # 2. Загружаем файл в бакет Supabase Storage
        with open(temp_file, 'rb') as f:
            supabase.storage.from_(SUPABASE_BUCKET).upload(
                path=object_name,
                file=f,
                file_options={"content-type": "image/jpeg"}
            )

        # 3. Получаем готовую публичную ссылку на изображение
        public_url = supabase.storage.from_(
            SUPABASE_BUCKET).get_public_url(object_name)
        return public_url

    except Exception as e:
        print(f"Ошибка загрузки в Supabase: {e}")
        return None

    finally:
        # 4. Обязательно чистим за собой временный файл на Render
        if os.path.exists(temp_file):
            os.remove(temp_file)


# ---------- НАСТРОЙКИ (замени на свои) ----------
VK_APP_ID = "54679818"                     # ID твоего мини-приложения
VK_APP_SECRET = "gjEcinHM4La0NrqTZ0Vr"     # Секретный ключ из настроек
SECRET_KEY = "любая_случайная_строка"     # Для Flask-сессий (придумай любую)
VK_SERVICE_TOKEN = "330ecc69330ecc69330ecc69bb304c95633330e330ecc69595998ac4151ffecb210ea37"

VK_CLIENT_ID = "54679818"  # ID твоего приложения
VK_CLIENT_SECRET = "gjEcinHM4La0NrqTZ0Vr"  # Защищённый ключ из настроек
VK_REDIRECT_URI = "https://ecobot-lbar.onrender.com"  # Для локального теста
# -----------------------------------------------

app = Flask(__name__, template_folder=os.path.join(
    os.path.dirname(__file__), 'templates'))
app.secret_key = SECRET_KEY

# ---------- БАЗА ДАННЫХ ДЛЯ ЛАЙКОВ ----------
DB_PATH = os.path.join(os.path.dirname(__file__), "votes.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT UNIQUE NOT NULL,
        likes INTEGER DEFAULT 0
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS votes (
        user_id INTEGER,
        photo_id INTEGER,
        PRIMARY KEY (user_id, photo_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        avatar TEXT,
        password_hash TEXT,
        is_email_user INTEGER DEFAULT 0
    )''')
    conn.commit()
    conn.close()


def sync_photos():
    """Сканирует хранилище Supabase и автоматически добавляет ВСЕ картинки из корня и папок в SQLite"""
    conn = get_db()
    existing = {row['filename'] for row in conn.execute(
        'SELECT filename FROM photos').fetchall()}
    actual = set()

    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json"
    }

    try:
        # 1. Запрашиваем список ВСЕХ файлов из корня бакета (передаем пустой префикс)
        response = requests.post(SUPABASE_API_STORAGE_URL, json={
                                 "prefix": ""}, headers=headers, timeout=10)

        if response.status_code == 200:
            items = response.json()
            for item in items:
                # Проверяем, что это файл (у него есть id), а не папка
                if item.get('id') is not None and item.get('name'):
                    fname = item['name']

                    # Проверяем, что файл является картинкой
                    if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        # Формируем прямой публичный интернет-URL к файлу в корне
                        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{fname}"
                        actual.add(public_url)

                        # Если этой картинки еще нет в нашей БД, добавляем её URL
                        if public_url not in existing:
                            conn.execute(
                                'INSERT OR IGNORE INTO photos (filename) VALUES (?)', (public_url,))

                # 2. АВТОМАТИЗМ: Если это папка (id отсутствует), заглянем и в неё тоже
                elif item.get('id') is None and item.get('name'):
                    folder_name = item['name']
                    file_response = requests.post(SUPABASE_API_STORAGE_URL, json={
                                                  "prefix": folder_name}, headers=headers, timeout=10)

                    if file_response.status_code == 200:
                        sub_items = file_response.json()
                        for sub_item in sub_items:
                            sub_fname = sub_item.get('name')
                            if sub_fname and sub_fname.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                                object_path = f"{folder_name}/{sub_fname}"
                                public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{object_path}"
                                actual.add(public_url)

                                if public_url not in existing:
                                    conn.execute(
                                        'INSERT OR IGNORE INTO photos (filename) VALUES (?)', (public_url,))
        else:
            print(
                f"Supabase вернул код ошибки: {response.status_code}, текст: {response.text}")

    except Exception as e:
        print(
            f"Ошибка при автоматической синхронизации с Supabase Storage: {e}")

    # 3. Чистим базу от удаленных картинок
    to_remove = existing - actual
    for url in to_remove:
        conn.execute('DELETE FROM photos WHERE filename = ?', (url,))

    conn.commit()
    conn.close()


def get_top_photos(limit=5):
    conn = get_db()
    # ✅ ИСПРАВЛЕНО: Сортируем сначала по лайкам (DESC), а потом по ID,
    # чтобы даже с 0 лайков фотки попадали в топ, а не возвращали пустоту
    photos = conn.execute(
        'SELECT * FROM photos ORDER BY likes DESC, id DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return photos


def get_random_photos(limit=30):
    conn = get_db()
    # ✅ ИСПРАВЛЕНО: Берем фотографии, даже если лайков 0
    photos = conn.execute(
        'SELECT * FROM photos ORDER BY RANDOM() LIMIT ?', (limit,)).fetchall()
    conn.close()
    return photos


def has_user_voted(user_id, photo_id):
    conn = get_db()
    vote = conn.execute(
        'SELECT 1 FROM votes WHERE user_id = ? AND photo_id = ?', (user_id, photo_id)).fetchone()
    conn.close()
    return vote is not None


def toggle_like(user_id, photo_id):
    conn = get_db()
    if has_user_voted(user_id, photo_id):
        conn.execute(
            'DELETE FROM votes WHERE user_id = ? AND photo_id = ?', (user_id, photo_id))
        conn.execute(
            'UPDATE photos SET likes = likes - 1 WHERE id = ?', (photo_id,))
        liked = False
    else:
        conn.execute(
            'INSERT INTO votes (user_id, photo_id) VALUES (?, ?)', (user_id, photo_id))
        conn.execute(
            'UPDATE photos SET likes = likes + 1 WHERE id = ?', (photo_id,))
        liked = True
    conn.commit()
    new_likes = conn.execute(
        'SELECT likes FROM photos WHERE id = ?', (photo_id,)).fetchone()[0]
    conn.close()
    return liked, new_likes

# ---------- ПРОВЕРКА ПОДПИСИ VK (МИНИ-ПРИЛОЖЕНИЕ) ----------


def verify_vk_signature(request):
    query_string = request.query_string.decode()
    params = dict(urllib.parse.parse_qsl(query_string))
    sign = params.pop('sign', None)
    if not sign:
        return None
    sorted_params = sorted(params.items())
    query = urllib.parse.urlencode(sorted_params)
    h = hmac.new(VK_APP_SECRET.encode(), query.encode(), hashlib.sha256)
    if h.hexdigest() == sign:
        return params.get('vk_user_id')
    return None


@app.before_request
def auto_auth():
    query_string = request.query_string.decode()
    params = dict(urllib.parse.parse_qsl(query_string))
    sign = params.pop('sign', None)
    if not sign:
        return
    sorted_params = sorted(params.items())
    query = urllib.parse.urlencode(sorted_params)
    h = hmac.new(VK_APP_SECRET.encode(), query.encode(), hashlib.sha256)
    if h.hexdigest() == sign:
        session['user_id'] = int(params.get('vk_user_id', 0))
        session['access_token'] = params.get('vk_access_token', '')

# ---------- МАРШРУТЫ ----------


@app.route('/')
def index():
    # 1. Синхронизируем базу данных с картинками из Supabase Storage
    sync_photos()

    user_id = session.get('user_id')
    user_name = None
    user_avatar = None

    if user_id:
        conn = get_db()
        user_row = conn.execute(
            'SELECT name, avatar FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if not user_row:
            try:
                resp = requests.get('https://api.vk.com/method/users.get', params={
                    'user_ids': user_id, 'fields': 'photo_100',
                    'access_token': VK_SERVICE_TOKEN, 'v': '5.199'
                }).json()
                if resp.get('response'):
                    info = resp['response'][0]
                    user_name = f"{info.get('first_name', '')} {info.get('last_name', '')}"
                    user_avatar = info.get('photo_100', '')
                    conn.execute('INSERT OR REPLACE INTO users (user_id, name, avatar) VALUES (?, ?, ?)',
                                 (user_id, user_name, user_avatar))
                    conn.commit()
            except:
                user_name = f'Пользователь {user_id}'
        else:
            user_name = user_row['name']
            user_avatar = user_row['avatar']
        conn.close()

    top_photos = get_top_photos(5)
    random_photos = get_random_photos(30)

    # ✅ ИСПРАВЛЕНО: Добавлено поле 'likes', чтобы код не выдавал ошибку KeyError
    def enrich(p):
        return {
            'id': p['id'],
            # Здесь теперь лежит прямая ссылка на Supabase
            'url': p['filename'],
            'likes': p['likes'],  # Получаем лайки из БД
            'liked': has_user_voted(user_id, p['id']) if user_id else False
        }

    top_data = [enrich(p) for p in top_photos]
    random_data = [enrich(p) for p in random_photos]

    html = '<!DOCTYPE html><html><head><title>Фото-сервис</title><meta charset="utf-8">'
    html += '<style>body{font-family:Arial;margin:20px}.top-bar{background:#f0f0f0;padding:10px;white-space:nowrap;overflow-x:auto}.top-item{display:inline-block;margin:0 10px;text-align:center}.top-item img{height:100px;border-radius:8px}.photo-grid{display:flex;flex-wrap:wrap;gap:15px;padding:20px}.photo-card{width:200px;text-align:center}.photo-card img{width:100%;border-radius:8px}.like-btn{cursor:pointer;font-size:18px}.liked{color:red}.user-info{display:flex;align-items:center;gap:10px;margin-bottom:20px}.user-info img{border-radius:50%}</style></head><body>'

    if user_id:
        if user_avatar:
            html += f'<div class="user-info"><img src="{user_avatar}" width="50" height="50"><span>{user_name}</span></div>'
        else:
            html += f'<p>Вы вошли как {user_name or user_id}</p>'
    else:
        html += '<p><a href="/login" style="background:#0077FF;color:white;padding:10px 20px;text-decoration:none;border-radius:5px">Войти</a> '
        html += '<a href="/register" style="background:#4CAF50;color:white;padding:10px 20px;text-decoration:none;border-radius:5px">Регистрация</a></p>'

    html += '<h2>🏆 Лучшие работы</h2><div class="top-bar">'
    for p in top_data:
        html += f'<div class="top-item"><img src="{p["url"]}"><br>❤️ {p["likes"]}</div>'
    html += '</div><h2>📸 Случайные работы</h2><div class="photo-grid">'
    for p in random_data:
        liked = 'liked' if p['liked'] else ''
        html += f'<div class="photo-card"><img src="{p["url"]}"><div><span class="like-btn {liked}" onclick="like({p["id"]}, this)">❤️ <span class="count">{p["likes"]}</span></span></div></div>'
    html += '</div><script>async function like(id, btn){const r=await fetch("/like/"+id,{method:"POST"});if(r.ok){const d=await r.json();btn.querySelector(".count").textContent=d.likes;if(d.liked)btn.classList.add("liked");else btn.classList.remove("liked")}else{alert("Оценивать могут только авторизованные пользователи")}}</script></body></html>'
    return html


@app.route('/like/<int:photo_id>', methods=['POST'])
def like_photo(photo_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Необходима авторизация'}), 401
    liked, likes = toggle_like(user_id, photo_id)
    return jsonify({'liked': liked, 'likes': likes})


@app.route('/vk_login')
def vk_login():
    url = f'https://oauth.vk.com/authorize?client_id={VK_CLIENT_ID}&display=page&redirect_uri={VK_REDIRECT_URI}&response_type=code&v=5.131'
    return redirect(url)


@app.route('/vk_callback')
def vk_callback():
    code = request.args.get('code')
    # Обмен кода на токен
    token_url = 'https://oauth.vk.com/access_token'
    params = {
        'client_id': VK_CLIENT_ID,
        'client_secret': VK_CLIENT_SECRET,
        'redirect_uri': VK_REDIRECT_URI,
        'code': code
    }
    resp = requests.get(token_url, params=params).json()
    if 'user_id' in resp:
        session['user_id'] = resp['user_id']
        return redirect(url_for('index'))
    return 'Ошибка авторизации', 400


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        name = request.form.get('name', '').strip() or email.split('@')[0]

        if not email or not password:
            return 'Заполните все поля', 400

        conn = get_db()
        # Проверяем, нет ли уже такого email
        existing = conn.execute(
            'SELECT user_id FROM users WHERE name = ? AND is_email_user = 1', (email,)).fetchone()
        if existing:
            conn.close()
            return 'Пользователь с таким email уже существует', 400

        # Создаём ID для email-пользователя (отрицательные, чтобы не пересекались с VK ID)
        import random
        new_id = random.randint(1000000, 9999999)
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        conn.execute('INSERT OR REPLACE INTO users (user_id, name, password_hash, is_email_user) VALUES (?, ?, ?, 1)',
                     (new_id, email, password_hash))
        conn.commit()
        conn.close()

        session['user_id'] = new_id
        return redirect('/')

    # GET — показываем форму регистрации
    return '''
    <h2>Регистрация</h2>
    <form method="POST">
        <input name="name" placeholder="Имя" required><br><br>
        <input name="email" type="email" placeholder="Email" required><br><br>
        <input name="password" type="password" placeholder="Пароль" required><br><br>
        <button type="submit">Зарегистрироваться</button>
    </form>
    <p>Уже есть аккаунт? <a href="/login">Войти</a></p>
    '''


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        conn = get_db()
        user = conn.execute('SELECT user_id, password_hash FROM users WHERE name = ? AND is_email_user = 1',
                            (email,)).fetchone()
        conn.close()

        if user and user['password_hash'] == hashlib.sha256(password.encode()).hexdigest():
            session['user_id'] = user['user_id']
            return redirect('/')
        return 'Неверный email или пароль', 400

    return '''
    <h2>Вход</h2>
    <form method="POST">
        <input name="email" type="email" placeholder="Email" required><br><br>
        <input name="password" type="password" placeholder="Пароль" required><br><br>
        <button type="submit">Войти</button>
    </form>
    <p>Нет аккаунта? <a href="/register">Зарегистрироваться</a></p>
    '''


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect('/')


port = 0

# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    init_db()
    if __name__ == '__main__':
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
