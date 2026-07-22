import requests
from flask import Flask, render_template, redirect, url_for, request, session, send_from_directory, jsonify
import urllib.parse
import hmac
import hashlib
import sqlite3
import os
import time
import threading
from supabase import create_client, Client

# Настройки Supabase
SUPABASE_URL = "https://fmijtyjmliklxciqryap.supabase.co"
SUPABASE_KEY = "sb_secret_cRKj_FURc95dFCYSrxNDXw_oT7W7yiU"
SUPABASE_BUCKET = "images"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
SUPABASE_API_STORAGE_URL = f"{SUPABASE_URL}/storage/v1/object/list/{SUPABASE_BUCKET}"

# ---------- НАСТРОЙКИ ----------
VK_APP_ID = "54679818"
VK_APP_SECRET = "gjEcinHM4La0NrqTZ0Vr"
SECRET_KEY = "любая_случайная_строка"
VK_SERVICE_TOKEN = "330ecc69330ecc69330ecc69bb304c95633330e330ecc69595998ac4151ffecb210ea37"
VK_CLIENT_ID = "54679818"
VK_CLIENT_SECRET = "gjEcinHM4La0NrqTZ0Vr"
VK_REDIRECT_URI = "https://ecobot-lbar.onrender.com"

ADMIN_EMAILS = ["poma2283376@gmail.com"]

# Тексты конкурса по умолчанию
contest_messages = {
    "announcement": "📢 Внимание! Запущен конкурс стилистов!\nУспейте загрузить свои работы и получить лайки!",
    "winner_1": "🎉 Поздравляем! Вы заняли 1-е место 🥇 в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "winner_2": "🎉 Поздравляем! Вы заняли 2-е место 🥈 в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "winner_3": "🎉 Поздравляем! Вы заняли 3-е место 🥉 в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "winner_other": "🎉 Поздравляем! Вы заняли {place}-е место в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "loser": "Конкурс завершён! К сожалению, вы не заняли призовое место.\n\nПобедители:\n{winners}"
}

# Данные текущего конкурса
current_contest = {
    "active": False,
    "end_time": None,
    "timer_thread": None,
    "winners_count": 3  # по умолчанию топ-3
}

app = Flask(__name__, template_folder=os.path.join(
    os.path.dirname(__file__), 'templates'))
app.secret_key = SECRET_KEY

DB_PATH = os.path.join(os.path.dirname(__file__), "votes.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)  # ждать до 10 секунд
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # включить WAL-режим
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


def upload_photo_to_supabase(user_id, photo_url):
    temp_file = f"temp_{user_id}.jpg"
    object_name = f"user_{user_id}/{int(time.time())}.jpg"
    try:
        with requests.get(photo_url, stream=True) as r:
            r.raise_for_status()
            with open(temp_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        with open(temp_file, 'rb') as f:
            supabase.storage.from_(SUPABASE_BUCKET).upload(
                path=object_name, file=f, file_options={"content-type": "image/jpeg"})
        public_url = supabase.storage.from_(
            SUPABASE_BUCKET).get_public_url(object_name)
        return public_url
    except Exception as e:
        print(f"Ошибка загрузки в Supabase: {e}")
        return None
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def sync_photos():
    conn = get_db()
    existing = {row['filename'] for row in conn.execute(
        'SELECT filename FROM photos').fetchall()}
    actual = set()
    # ... (весь код получения actual остаётся без изменений)

    to_remove = existing - actual
    for url in to_remove:
        try:
            photo = conn.execute(
                'SELECT likes FROM photos WHERE filename = ?', (url,)).fetchone()
            if photo and photo['likes'] == 0:
                conn.execute('DELETE FROM photos WHERE filename = ?', (url,))
                conn.commit()  # фиксируем каждое удаление отдельно
                time.sleep(0.1)  # небольшая пауза
        except Exception as e:
            print(f"Ошибка при удалении {url}: {e}")

    conn.commit()
    conn.close()


def get_top_photos(limit=5):
    conn = get_db()
    return conn.execute('SELECT * FROM photos ORDER BY likes DESC, id DESC LIMIT ?', (limit,)).fetchall()


def get_random_photos(limit=30):
    conn = get_db()
    return conn.execute('SELECT * FROM photos ORDER BY RANDOM() LIMIT ?', (limit,)).fetchall()


def has_user_voted(user_id, photo_id):
    conn = get_db()
    return conn.execute('SELECT 1 FROM votes WHERE user_id = ? AND photo_id = ?', (user_id, photo_id)).fetchone() is not None


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


@app.route('/')
def index():
    sync_photos()
    user_id = session.get('user_id')
    user_name = None
    user_avatar = None
    user_email = None
    if user_id:
        conn = get_db()
        user_row = conn.execute(
            'SELECT name, avatar, is_email_user FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if not user_row:
            try:
                resp = requests.get('https://api.vk.com/method/users.get', params={
                                    'user_ids': user_id, 'fields': 'photo_100', 'access_token': VK_SERVICE_TOKEN, 'v': '5.199'}).json()
                if resp.get('response'):
                    info = resp['response'][0]
                    user_name = f"{info.get('first_name', '')} {info.get('last_name', '')}"
                    user_avatar = info.get('photo_100', '')
                    conn.execute('INSERT OR REPLACE INTO users (user_id, name, avatar) VALUES (?, ?, ?)', (
                        user_id, user_name, user_avatar))
                    conn.commit()
            except:
                user_name = f'Пользователь {user_id}'
        else:
            user_name = user_row['name']
            user_avatar = user_row['avatar']
            if user_row['is_email_user']:
                user_email = user_row['name']
        conn.close()

    top_photos = get_top_photos(5)
    random_photos = get_random_photos(30)

    def enrich(p):
        return {'id': p['id'], 'url': p['filename'], 'likes': p['likes'], 'liked': has_user_voted(user_id, p['id']) if user_id else False}

    top_data = [enrich(p) for p in top_photos]
    random_data = [enrich(p) for p in random_photos]

    html = '<!DOCTYPE html><html><head><title>Фото-сервис</title><meta charset="utf-8">'
    html += '<style>body{font-family:Arial;margin:20px}.top-bar{background:#f0f0f0;padding:10px;white-space:nowrap;overflow-x:auto}.top-item{display:inline-block;margin:0 10px;text-align:center}.top-item img{height:100px;border-radius:8px}.photo-grid{display:flex;flex-wrap:wrap;gap:15px;padding:20px}.photo-card{width:200px;text-align:center}.photo-card img{width:100%;border-radius:8px}.like-btn{cursor:pointer;font-size:18px}.liked{color:red}.user-info{display:flex;align-items:center;gap:10px;margin-bottom:20px}.user-info img{border-radius:50%}</style></head><body>'

    if user_id:
        if user_avatar:
            html += f'<div class="user-info"><img src="{user_avatar}" width="50" height="50"><span>{user_name}</span></div>'
        else:
            html += f'<p>Вы вошли как {user_name or user_id}</p>'
        if user_email and user_email in ADMIN_EMAILS:
            html += '<p><a href="/admin" style="background:#ff6600;color:white;padding:5px 10px;text-decoration:none;border-radius:5px">Админ-панель</a></p>'
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
    token_url = 'https://oauth.vk.com/access_token'
    params = {'client_id': VK_CLIENT_ID, 'client_secret': VK_CLIENT_SECRET,
              'redirect_uri': VK_REDIRECT_URI, 'code': code}
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
        existing = conn.execute(
            'SELECT user_id FROM users WHERE name = ? AND is_email_user = 1', (email,)).fetchone()
        if existing:
            conn.close()
            return 'Пользователь с таким email уже существует', 400
        import random
        new_id = random.randint(1000000, 9999999)
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        conn.execute('INSERT OR REPLACE INTO users (user_id, name, password_hash, is_email_user) VALUES (?, ?, ?, 1)',
                     (new_id, email, password_hash))
        conn.commit()
        conn.close()
        session['user_id'] = new_id
        return redirect('/')
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
        user = conn.execute(
            'SELECT user_id, password_hash FROM users WHERE name = ? AND is_email_user = 1', (email,)).fetchone()
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

# ==================== АДМИН-ПАНЕЛЬ ====================


@app.route('/admin')
def admin_panel():
    user_id = session.get('user_id')
    conn = get_db()
    user_row = conn.execute(
        'SELECT name, is_email_user FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    user_email = None
    if user_row and user_row['is_email_user']:
        user_email = user_row['name']

    if not user_email or user_email not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403

    conn = get_db()
    photos = conn.execute('SELECT * FROM photos ORDER BY id DESC').fetchall()
    conn.close()

    contest_status = "<p>Конкурс не запущен.</p>"
    if current_contest["active"]:
        remaining = int(current_contest["end_time"] - time.time())
        if remaining > 0:
            contest_status = f"<p style='color:green'>Конкурс идёт! Осталось примерно {remaining // 60} мин {remaining % 60} сек.</p>"
        else:
            contest_status = "<p style='color:red'>Конкурс завершается...</p>"

    html = f'''<h1>Админ-панель</h1><p><a href="/">На главную</a></p>
    <h2>Массовая рассылка</h2>
    <form action="/admin/send" method="post"><textarea name="message" rows="4" cols="50"></textarea><br><button type="submit">Отправить всем</button></form>
    
    <h2>Конкурс</h2>
    {contest_status}
    <form action="/admin/contest/start" method="post">
        <input name="duration" type="number" placeholder="Длительность (минуты)" required>
        <select name="winners_count">
            <option value="1" {"selected" if current_contest["winners_count"] == 1 else ""}>1 победитель</option>
            <option value="2" {"selected" if current_contest["winners_count"] == 2 else ""}>2 победителя</option>
            <option value="3" {"selected" if current_contest["winners_count"] == 3 else ""}>3 победителя</option>
            <option value="5" {"selected" if current_contest["winners_count"] == 5 else ""}>5 победителей</option>
            <option value="10" {"selected" if current_contest["winners_count"] == 10 else ""}>10 победителей</option>
        </select>
        <button type="submit">Запустить конкурс</button>
    </form>
    
    <h3>Тексты сообщений</h3>
    <form action="/admin/contest/texts" method="post">
        <p>Объявление о конкурсе (рассылается всем при запуске):</p>
        <textarea name="announcement" rows="3" cols="50">{contest_messages["announcement"]}</textarea><br>
        <p>1-е место (используйте {{likes}}):</p>
        <textarea name="winner_1" rows="3" cols="50">{contest_messages["winner_1"]}</textarea><br>
        <p>2-е место:</p>
        <textarea name="winner_2" rows="3" cols="50">{contest_messages["winner_2"]}</textarea><br>
        <p>3-е место:</p>
        <textarea name="winner_3" rows="3" cols="50">{contest_messages["winner_3"]}</textarea><br>
        <p>Остальные места (используйте {{place}} и {{likes}}):</p>
        <textarea name="winner_other" rows="3" cols="50">{contest_messages["winner_other"]}</textarea><br>
        <p>Проигравшим (используйте {{winners}}):</p>
        <textarea name="loser" rows="5" cols="50">{contest_messages["loser"]}</textarea><br>
        <button type="submit">Сохранить тексты</button>
    </form>
    
    <h2>Все фотографии</h2>'''
    for photo in photos:
        html += f'<div style="margin-bottom:10px"><img src="{photo["filename"]}" width="100"> ❤️ {photo["likes"]} <a href="/admin/delete/{photo["id"]}" onclick="return confirm(\'Удалить?\')">Удалить</a></div>'
    return html


@app.route('/admin/contest/texts', methods=['POST'])
def save_contest_texts():
    user_id = session.get('user_id')
    conn = get_db()
    user_row = conn.execute(
        'SELECT name, is_email_user FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    user_email = None
    if user_row and user_row['is_email_user']:
        user_email = user_row['name']

    if not user_email or user_email not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403

    contest_messages["announcement"] = request.form.get(
        'announcement', contest_messages["announcement"])
    contest_messages["winner_1"] = request.form.get(
        'winner_1', contest_messages["winner_1"])
    contest_messages["winner_2"] = request.form.get(
        'winner_2', contest_messages["winner_2"])
    contest_messages["winner_3"] = request.form.get(
        'winner_3', contest_messages["winner_3"])
    contest_messages["winner_other"] = request.form.get(
        'winner_other', contest_messages["winner_other"])
    contest_messages["loser"] = request.form.get(
        'loser', contest_messages["loser"])

    return redirect('/admin')


@app.route('/admin/delete/<int:photo_id>')
def admin_delete(photo_id):
    user_id = session.get('user_id')
    conn = get_db()
    user_row = conn.execute(
        'SELECT name, is_email_user FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    user_email = None
    if user_row and user_row['is_email_user']:
        user_email = user_row['name']

    if not user_email or user_email not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403

    conn = get_db()
    photo = conn.execute(
        'SELECT filename FROM photos WHERE id = ?', (photo_id,)).fetchone()
    if photo:
        try:
            object_path = photo['filename'].split(f'{SUPABASE_BUCKET}/')[-1]
            supabase.storage.from_(SUPABASE_BUCKET).remove([object_path])
        except:
            pass
        conn.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
        conn.execute('DELETE FROM votes WHERE photo_id = ?', (photo_id,))
        conn.commit()
    conn.close()
    return redirect('/admin')


@app.route('/admin/send', methods=['POST'])
def admin_send():
    user_id = session.get('user_id')
    conn = get_db()
    user_row = conn.execute(
        'SELECT name, is_email_user FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    user_email = None
    if user_row and user_row['is_email_user']:
        user_email = user_row['name']

    if not user_email or user_email not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403

    message = request.form.get('message', '')
    if not message:
        return 'Сообщение не может быть пустым', 400

    conn = get_db()
    users = conn.execute(
        'SELECT user_id FROM users WHERE is_email_user = 0 AND user_id < 1000000').fetchall()
    conn.close()

    sent = 0
    for u in users:
        try:
            requests.post('https://api.vk.com/method/messages.send', params={
                'user_id': u['user_id'],
                'message': message,
                'access_token': VK_SERVICE_TOKEN,
                'v': '5.199',
                'random_id': 0
            })
            sent += 1
            time.sleep(0.05)
        except:
            pass

    return f'Отправлено {sent} пользователям. <a href="/admin">Назад</a>'

# ==================== КОНКУРС ====================


def finish_contest():
    time.sleep(0.1)
    conn = get_db()
    winners_count = current_contest["winners_count"]
    top_winners = conn.execute(
        f'SELECT * FROM photos ORDER BY likes DESC LIMIT {winners_count}').fetchall()
    users = conn.execute(
        'SELECT user_id FROM users WHERE is_email_user = 0 AND user_id < 1000000').fetchall()
    conn.close()

    if not top_winners:
        current_contest["active"] = False
        return

    places = ["1-е место 🥇", "2-е место 🥈", "3-е место 🥉"]
    winners_ids = set()

    # Отправка победителям
    for i, photo in enumerate(top_winners):
        try:
            owner_id = int(photo['filename'].split('user_')[1].split('/')[0])
            winners_ids.add(owner_id)
            place = i + 1
            # Выбираем шаблон
            if place == 1:
                msg = contest_messages["winner_1"].format(likes=photo['likes'])
            elif place == 2:
                msg = contest_messages["winner_2"].format(likes=photo['likes'])
            elif place == 3:
                msg = contest_messages["winner_3"].format(likes=photo['likes'])
            else:
                msg = contest_messages["winner_other"].format(
                    place=place, likes=photo['likes'])
            requests.post('https://api.vk.com/method/messages.send', params={
                'user_id': owner_id, 'message': msg,
                'access_token': VK_SERVICE_TOKEN, 'v': '5.199', 'random_id': 0
            })
        except:
            pass

    # Отправка проигравшим
    winners_list = ""
    for i, photo in enumerate(top_winners):
        place = i + 1
        if place <= 3:
            winners_list += f"{places[i]}: фото с {photo['likes']} лайками\n"
        else:
            winners_list += f"{place}-е место: фото с {photo['likes']} лайками\n"
    losers_message = contest_messages["loser"].format(winners=winners_list)

    for u in users:
        if u['user_id'] not in winners_ids:
            try:
                requests.post('https://api.vk.com/method/messages.send', params={
                    'user_id': u['user_id'], 'message': losers_message,
                    'access_token': VK_SERVICE_TOKEN, 'v': '5.199', 'random_id': 0
                })
                time.sleep(0.05)
            except:
                pass

    current_contest["active"] = False


@app.route('/admin/contest/start', methods=['POST'])
def start_contest():
    user_id = session.get('user_id')
    conn = get_db()
    user_row = conn.execute(
        'SELECT name, is_email_user FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    user_email = None
    if user_row and user_row['is_email_user']:
        user_email = user_row['name']

    if not user_email or user_email not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403

    duration = int(request.form.get('duration', 5))
    winners_count = int(request.form.get('winners_count', 3))

    current_contest["active"] = True
    current_contest["end_time"] = time.time() + duration * 60
    current_contest["winners_count"] = winners_count
    current_contest["timer_thread"] = threading.Timer(
        duration * 60, finish_contest)
    current_contest["timer_thread"].start()

    # Рассылка объявления о конкурсе
    announcement = contest_messages["announcement"]
    conn = get_db()
    users = conn.execute(
        'SELECT user_id FROM users WHERE is_email_user = 0 AND user_id < 1000000').fetchall()
    conn.close()
    for u in users:
        try:
            requests.post('https://api.vk.com/method/messages.send', params={
                'user_id': u['user_id'],
                'message': announcement,
                'access_token': VK_SERVICE_TOKEN,
                'v': '5.199',
                'random_id': 0
            })
            time.sleep(0.05)
        except:
            pass

    return redirect('/admin')


# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
