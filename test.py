import requests
from flask import Flask, render_template, redirect, url_for, request, session, jsonify
import urllib.parse
import hmac
import hashlib
import sqlite3
import os
import time
import threading
from supabase import create_client, Client

# ---------- НАСТРОЙКИ ----------
VK_TOKEN = "vk1.a.zDmGVDdiQH-j2MHwzh0rRoNDPfzDFNrpoje5sC7NtZKsSrElAi3rUeUfEEi0sqgNDuxwYRkeSMpMoABD8tlugCc_pYTGG93SavFBtyiaLiphwjQQ-AjKEFqJpsFBewUnqbIM262W96Tn08BXMHGs_RpFIS64bu6cXEuIWb6QKvd6hSd0OG8bYF7iIWM95EoGz2DkdVLISrwqh25Yg001mg"
SUPABASE_URL = "https://fmijtyjmliklxciqryap.supabase.co"
SUPABASE_KEY = "sb_secret_cRKj_FURc95dFCYSrxNDXw_oT7W7yiU"
SUPABASE_BUCKET = "images"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
SUPABASE_API_STORAGE_URL = f"{SUPABASE_URL}/storage/v1/object/list/{SUPABASE_BUCKET}"

VK_APP_ID = "54679818"
VK_APP_SECRET = "gjEcinHM4La0NrqTZ0Vr"
SECRET_KEY = "любая_случайная_строка"
VK_SERVICE_TOKEN = "330ecc69330ecc69330ecc69bb304c95633330e330ecc69595998ac4151ffecb210ea37"
VK_CLIENT_ID = "240220666"
VK_CLIENT_SECRET = "gjEcinHM4La0NrqTZ0Vr"
VK_REDIRECT_URI = "https://ecobot-lbar.onrender.com"

ADMIN_EMAILS = ["poma2283376@gmail.com"]

# Тексты конкурса
contest_messages = {
    "announcement": "📢 Внимание! Запущен конкурс стилистов!\nУспейте загрузить свои работы и получить лайки!",
    "winner_1": "🎉 Поздравляем! Вы заняли 1-е место 🥇 в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "winner_2": "🎉 Поздравляем! Вы заняли 2-е место 🥈 в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "winner_3": "🎉 Поздравляем! Вы заняли 3-е место 🥉 в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "winner_other": "🎉 Поздравляем! Вы заняли {place}-е место в конкурсе стилистов!\nВаше фото набрало {likes} лайков.",
    "loser": "Конкурс завершён! К сожалению, вы не заняли призовое место.\n\nПобедители:\n{winners}"
}

current_contest = {
    "active": False,
    "end_time": None,
    "timer_thread": None,
    "winners_count": 3
}

app = Flask(__name__, template_folder=os.path.join(
    os.path.dirname(__file__), 'templates'))
app.secret_key = SECRET_KEY

DB_PATH = os.path.join(os.path.dirname(__file__), "votes.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE NOT NULL, likes INTEGER DEFAULT 0)''')
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS votes (user_id INTEGER, photo_id INTEGER, PRIMARY KEY (user_id, photo_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, avatar TEXT, password_hash TEXT, is_email_user INTEGER DEFAULT 0)''')
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS photo_likes_cache (photo_url TEXT PRIMARY KEY, likes INTEGER DEFAULT 0, updated_at REAL)''')
    conn.commit()
    conn.close()


def get_cached_likes(photo_url):
    conn = get_db()
    cached = conn.execute(
        'SELECT likes, updated_at FROM photo_likes_cache WHERE photo_url = ?', (photo_url,)).fetchone()
    if cached and time.time() - cached['updated_at'] < 60:
        conn.close()
        return cached['likes']
    likes = get_photo_likes(photo_url)
    conn.execute('INSERT OR REPLACE INTO photo_likes_cache (photo_url, likes, updated_at) VALUES (?, ?, ?)',
                 (photo_url, likes, time.time()))
    conn.commit()
    conn.close()
    return likes


def get_photo_likes(photo_url):
    try:
        return supabase.table("photo_likes").select("*", count="exact").eq("photo_url", photo_url).execute().count
    except:
        return 0


def has_user_voted(user_id, photo_url):
    try:
        return len(supabase.table("photo_likes").select("*").eq("photo_url", photo_url).eq("user_id", user_id).execute().data) > 0
    except:
        return False


def toggle_like(user_id, photo_url):
    try:
        if has_user_voted(user_id, photo_url):
            supabase.table("photo_likes").delete().eq(
                "photo_url", photo_url).eq("user_id", user_id).execute()
            liked = False
        else:
            supabase.table("photo_likes").insert(
                {"photo_url": photo_url, "user_id": user_id}).execute()
            liked = True
        likes = supabase.table("photo_likes").select(
            "*", count="exact").eq("photo_url", photo_url).execute().count
        return liked, likes
    except:
        return False, 0


def sync_photos():
    conn = get_db()
    existing = {row['filename'] for row in conn.execute(
        'SELECT filename FROM photos').fetchall()}
    headers = {"Authorization": f"Bearer {SUPABASE_KEY}",
               "apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    try:
        response = requests.post(SUPABASE_API_STORAGE_URL, json={
                                 "prefix": ""}, headers=headers, timeout=10)
        if response.status_code == 200:
            for item in response.json():
                if item.get('id') and item.get('name') and item['name'].lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{item['name']}"
                    if public_url not in existing:
                        conn.execute(
                            'INSERT OR IGNORE INTO photos (filename) VALUES (?)', (public_url,))
    except:
        pass
    conn.commit()
    conn.close()


@app.before_request
def auto_auth():
    try:
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
    except:
        pass


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/gallery')
def gallery():
    sync_photos()
    user_id = session.get('user_id')
    user_name = None
    user_email = None
    if user_id:
        conn = get_db()
        user_row = conn.execute(
            'SELECT name, avatar, is_email_user FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if user_row:
            user_name = user_row['name']
            if user_row['is_email_user']:
                user_email = user_row['name']
        conn.close()

    conn = get_db()
    photos = conn.execute(
        'SELECT * FROM photos ORDER BY RANDOM() LIMIT 30').fetchall()
    conn.close()
    photo_list = [{'id': p['id'], 'url': p['filename'],
                   'likes': get_cached_likes(p['filename']),
                   'liked': has_user_voted(user_id, p['filename']) if user_id else False}
                  for p in photos]
    return render_template('gallery.html', photos=photo_list, user_id=user_id,
                           user_name=user_name, user_email=user_email, ADMIN_EMAILS=ADMIN_EMAILS)


@app.route('/like/<path:photo_url>', methods=['POST'])
def like_photo(photo_url):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Авторизуйтесь'}), 401
    liked, likes = toggle_like(user_id, photo_url)
    return jsonify({'liked': liked, 'likes': likes})


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        name = request.form.get('name', '').strip() or email.split('@')[0]
        if not email or not password:
            return 'Заполните все поля', 400
        conn = get_db()
        if conn.execute('SELECT user_id FROM users WHERE name = ? AND is_email_user = 1', (email,)).fetchone():
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
    return '''<h2>Регистрация</h2><form method="POST"><input name="name" placeholder="Имя"><br><input name="email" type="email" placeholder="Email"><br><input name="password" type="password" placeholder="Пароль"><br><button type="submit">Зарегистрироваться</button></form>'''


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
    return '''<h2>Вход</h2><form method="POST"><input name="email" type="email" placeholder="Email"><br><input name="password" type="password" placeholder="Пароль"><br><button type="submit">Войти</button></form>'''


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect('/')

# ---------- АДМИН-ПАНЕЛЬ (с конкурсами) ----------


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

    photos = get_db().execute('SELECT * FROM photos ORDER BY id DESC').fetchall()

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
            <option value="1">1 победитель</option>
            <option value="2">2 победителя</option>
            <option value="3" selected>3 победителя</option>
            <option value="5">5 победителей</option>
            <option value="10">10 победителей</option>
        </select>
        <button type="submit">Запустить конкурс</button>
    </form>
    
    <h3>Тексты сообщений</h3>
    <form action="/admin/contest/texts" method="post">
        <p>Объявление о конкурсе:</p>
        <textarea name="announcement" rows="3" cols="50">{contest_messages["announcement"]}</textarea><br>
        <p>1-е место:</p>
        <textarea name="winner_1" rows="3" cols="50">{contest_messages["winner_1"]}</textarea><br>
        <p>2-е место:</p>
        <textarea name="winner_2" rows="3" cols="50">{contest_messages["winner_2"]}</textarea><br>
        <p>3-е место:</p>
        <textarea name="winner_3" rows="3" cols="50">{contest_messages["winner_3"]}</textarea><br>
        <p>Остальные места:</p>
        <textarea name="winner_other" rows="3" cols="50">{contest_messages["winner_other"]}</textarea><br>
        <p>Проигравшим:</p>
        <textarea name="loser" rows="5" cols="50">{contest_messages["loser"]}</textarea><br>
        <button type="submit">Сохранить тексты</button>
    </form>
    
    <h2>Все фотографии</h2>'''
    for photo in photos:
        likes = get_photo_likes(photo['filename'])
        html += f'<div style="margin-bottom:10px"><img src="{photo["filename"]}" width="100"> ❤️ {likes} <a href="/admin/delete/{photo["id"]}" onclick="return confirm(\'Удалить?\')">Удалить</a></div>'
    return html


@app.route('/admin/contest/texts', methods=['POST'])
def save_contest_texts():
    if session.get('user_id') not in ADMIN_EMAILS:
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
    if session.get('user_id') not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403
    conn = get_db()
    photo = conn.execute(
        'SELECT filename FROM photos WHERE id = ?', (photo_id,)).fetchone()
    if photo:
        try:
            supabase.storage.from_(SUPABASE_BUCKET).remove(
                [photo['filename'].split(f'{SUPABASE_BUCKET}/')[-1]])
        except:
            pass
        conn.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
        conn.commit()
    conn.close()
    return redirect('/admin')


@app.route('/admin/send', methods=['POST'])
def admin_send():
    if session.get('user_id') not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403
    message = request.form.get('message', '')
    if not message:
        return 'Сообщение не может быть пустым', 400
    try:
        users = [u['user_id'] for u in supabase.table(
            "bot_users").select("user_id").execute().data]
    except:
        return 'Ошибка получения пользователей', 500
    for uid in users:
        try:
            requests.post('https://api.vk.com/method/messages.send', params={
                          'user_id': uid, 'message': message, 'access_token': VK_TOKEN, 'v': '5.199', 'random_id': 0})
            time.sleep(0.05)
        except:
            pass
    return f'Отправлено {len(users)} пользователям. <a href="/admin">Назад</a>'

# ---------- КОНКУРС (запуск и завершение) ----------


def finish_contest():
    time.sleep(0.1)
    conn = get_db()
    winners_count = current_contest["winners_count"]
    all_photos = conn.execute('SELECT * FROM photos').fetchall()
    photo_list = [{'id': p['id'], 'filename': p['filename'],
                   'likes': get_photo_likes(p['filename'])} for p in all_photos]
    photo_list.sort(key=lambda x: x['likes'], reverse=True)
    top_winners = photo_list[:winners_count]

    try:
        users = [u['user_id'] for u in supabase.table(
            "bot_users").select("user_id").execute().data]
    except:
        users = []

    if not top_winners:
        current_contest["active"] = False
        conn.close()
        return

    places = ["1-е место 🥇", "2-е место 🥈", "3-е место 🥉"]
    winners_ids = set()
    for i, p in enumerate(top_winners):
        try:
            owner_id = int(p['filename'].split('user_')[1].split('/')[0])
            winners_ids.add(owner_id)
            place = i + 1
            if place == 1:
                msg = contest_messages["winner_1"].format(likes=p['likes'])
            elif place == 2:
                msg = contest_messages["winner_2"].format(likes=p['likes'])
            elif place == 3:
                msg = contest_messages["winner_3"].format(likes=p['likes'])
            else:
                msg = contest_messages["winner_other"].format(
                    place=place, likes=p['likes'])
            requests.post('https://api.vk.com/method/messages.send', params={
                          'user_id': owner_id, 'message': msg, 'access_token': VK_TOKEN, 'v': '5.199', 'random_id': 0})
        except:
            pass

    winners_list = "\n".join([f"{places[i] if i < 3 else f'{i+1}-е место'}: {
                             p['likes']} лайков" for i, p in enumerate(top_winners)])
    losers_message = contest_messages["loser"].format(winners=winners_list)
    for uid in users:
        if uid not in winners_ids:
            try:
                requests.post('https://api.vk.com/method/messages.send', params={
                              'user_id': uid, 'message': losers_message, 'access_token': VK_TOKEN, 'v': '5.199', 'random_id': 0})
                time.sleep(0.05)
            except:
                pass

    # Удаление проигравших фото
    winner_ids = [p['id'] for p in top_winners]
    for photo in all_photos:
        if photo['id'] not in winner_ids:
            try:
                supabase.storage.from_(SUPABASE_BUCKET).remove(
                    [photo['filename'].split(f'{SUPABASE_BUCKET}/')[-1]])
            except:
                pass
    if winner_ids:
        placeholders = ','.join(['?'] * len(winner_ids))
        conn.execute(
            f'DELETE FROM photos WHERE id NOT IN ({placeholders})', winner_ids)
    else:
        conn.execute('DELETE FROM photos')
    conn.commit()
    conn.close()
    current_contest["active"] = False


@app.route('/admin/contest/start', methods=['POST'])
def start_contest():
    if session.get('user_id') not in ADMIN_EMAILS:
        return 'Доступ запрещён', 403
    duration = int(request.form.get('duration', 5))
    winners_count = int(request.form.get('winners_count', 3))
    current_contest["active"] = True
    current_contest["end_time"] = time.time() + duration * 60
    current_contest["winners_count"] = winners_count
    current_contest["timer_thread"] = threading.Timer(
        duration * 60, finish_contest)
    current_contest["timer_thread"].start()

    announcement = contest_messages["announcement"]
    try:
        users = [u['user_id'] for u in supabase.table(
            "bot_users").select("user_id").execute().data]
    except:
        users = []
    for uid in users:
        try:
            requests.post('https://api.vk.com/method/messages.send', params={
                          'user_id': uid, 'message': announcement, 'access_token': VK_TOKEN, 'v': '5.199', 'random_id': 0})
            time.sleep(0.05)
        except:
            pass
    return redirect('/admin')


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=5000)
