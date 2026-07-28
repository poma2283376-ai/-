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
VK_TOKEN = "vk1.a.zDmGVDdiQH-j2MHwzh0rRoNDPfzDFNrpoje5sC7NtZKsSrElAi3rUeUfEEi0sqgNDuxwYRkeSMpMoABD8tlugCc_pYTGG93SavFBtyiaLiphwjQQ-AjKEFqJpsFBewUnqbIM262W96Tn08BXMHGs_RpFIS64bu6cXEuIWb6QKvd6hSd0OG8bYF7iIWM95EoGz2DkdVLISrwqh25Yg001mg"
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
    conn.execute('''CREATE TABLE IF NOT EXISTS photo_likes_cache (
        photo_url TEXT PRIMARY KEY,
        likes INTEGER DEFAULT 0,
        updated_at REAL
    )''')
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


_last_sync_time = 0


def sync_photos():
    global _last_sync_time
    current_time = time.time()
    if current_time - _last_sync_time < 300:
        return
    conn = get_db()
    existing = {row['filename'] for row in conn.execute(
        'SELECT filename FROM photos').fetchall()}
    actual = set()
    headers = {"Authorization": f"Bearer {SUPABASE_KEY}",
               "apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    try:
        response = requests.post(SUPABASE_API_STORAGE_URL, json={
                                 "prefix": ""}, headers=headers, timeout=10)
        if response.status_code == 200:
            for item in response.json():
                if item.get('id') is not None and item.get('name'):
                    fname = item['name']
                    if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{fname}"
                        actual.add(public_url)
                        if public_url not in existing:
                            conn.execute(
                                'INSERT OR IGNORE INTO photos (filename) VALUES (?)', (public_url,))
    except Exception as e:
        print(f"Ошибка синхронизации: {e}")
    conn.commit()
    conn.close()
    _last_sync_time = current_time


def get_top_photos(limit=5):
    conn = get_db()
    photos = conn.execute('SELECT * FROM photos').fetchall()
    conn.close()
    photo_list = []
    for p in photos:
        likes = get_cached_likes(p['filename'])
        photo_list.append(
            {'id': p['id'], 'filename': p['filename'], 'likes': likes})
    photo_list.sort(key=lambda x: x['likes'], reverse=True)
    return photo_list[:limit]


def get_random_photos(limit=10):
    conn = get_db()
    photos = conn.execute(
        'SELECT * FROM photos ORDER BY RANDOM() LIMIT ?', (limit,)).fetchall()
    conn.close()
    photo_list = []
    for p in photos:
        likes = get_cached_likes(p['filename'])
        photo_list.append(
            {'id': p['id'], 'filename': p['filename'], 'likes': likes})
    return photo_list


def has_user_voted(user_id, photo_url):
    try:
        result = supabase.table("photo_likes").select(
            "*").eq("photo_url", photo_url).eq("user_id", user_id).execute()
        return len(result.data) > 0
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
        result = supabase.table("photo_likes").select(
            "*", count="exact").eq("photo_url", photo_url).execute()
        likes = result.count
        return liked, likes
    except:
        return False, 0


def get_photo_likes(photo_url):
    try:
        result = supabase.table("photo_likes").select(
            "*", count="exact").eq("photo_url", photo_url).execute()
        return result.count
    except:
        return 0


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
    random_photos = get_random_photos(10)

    def enrich(p):
        url = p['filename']
        return {'id': p['id'], 'url': url, 'likes': get_cached_likes(url), 'liked': has_user_voted(user_id, url) if user_id else False}

    top_data = [enrich(p) for p in top_photos]
    random_data = [enrich(p) for p in random_photos]

    # ========== НОВЫЙ ДИЗАЙН (вставлен вместо старого HTML) ==========
    html = '''<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Eco bot - Апсайклинг вещей</title>
<style>@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Inter:wght@400;500;600&display=swap');*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Inter',system-ui,sans-serif;line-height:1.6;color:#333;background-color:#f8f9fa}header{background:linear-gradient(135deg,#1e6b4a,#2e8b57);color:white;padding:1rem 0;position:fixed;width:100%;top:0;z-index:1000;box-shadow:0 4px 20px rgba(0,0,0,0.15)}nav{max-width:1200px;margin:0 auto;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:0 1.25rem}.logo{font-size:1.75rem;font-weight:700;letter-spacing:-0.8px;justify-self:start}.nav-links{display:flex;list-style:none;gap:2.2rem;align-items:center;justify-self:center}.nav-links a{color:white;text-decoration:none;font-weight:500;transition:all 0.3s ease;position:relative}.nav-links a:after{content:'';position:absolute;width:0;height:2px;bottom:-4px;left:0;background-color:#75D9A9;transition:width 0.3s ease}.nav-links a:hover:after{width:100%}.auth-btn{background:rgba(255,255,255,0.15);color:white;padding:10px 24px;border:2px solid rgba(255,255,255,0.75);border-radius:50px;text-decoration:none;font-weight:600;font-size:0.98rem;transition:all 0.3s ease;display:flex;align-items:center;gap:8px;backdrop-filter:blur(10px);cursor:pointer;justify-self:end}.auth-btn:hover{background:#75D9A9;color:#1e3a2f;transform:translateY(-2px);border-color:#75D9A9}.hamburger{display:none;flex-direction:column;cursor:pointer;gap:5px;z-index:1001;margin-left:1rem;justify-self:end}.hamburger span{width:28px;height:3px;background:white;border-radius:3px;transition:0.4s}.mobile-menu{position:fixed;top:0;left:0;width:100%;height:100vh;background:linear-gradient(180deg,#1e6b4a 0%,#2e8b57 100%);display:none;flex-direction:column;align-items:center;justify-content:center;z-index:999;padding:2rem 1.5rem;text-align:center;opacity:0;transition:all 0.5s}.mobile-menu.active{display:flex;opacity:1}.mobile-menu .logo{font-size:2.4rem;margin-bottom:5rem;color:white}.mobile-nav-links{list-style:none;width:100%;max-width:340px;display:flex;flex-direction:column;gap:2rem;margin-bottom:4rem}.mobile-nav-links a{color:white;text-decoration:none;font-size:1.55rem;font-weight:500;padding:14px 0;transition:all 0.3s ease;display:block}.mobile-nav-links a:hover{color:#a8e6cf;transform:translateX(15px)}.mobile-auth{background:rgba(255,255,255,0.2);color:white;padding:16px 48px;border:2px solid rgba(255,255,255,0.85);border-radius:50px;text-decoration:none;font-weight:600;font-size:1.2rem;transition:all 0.4s ease;display:flex;align-items:center;justify-content:center;gap:10px;width:100%;max-width:300px;cursor:pointer}.mobile-auth:hover{background:white;color:#1e6b4a;transform:scale(1.05)}.hamburger.active span:nth-child(1){transform:rotate(45deg) translate(6px,6px)}.hamburger.active span:nth-child(2){opacity:0}.hamburger.active span:nth-child(3){transform:rotate(-45deg) translate(5px,-5px)}@media(max-width:768px){nav{display:flex;justify-content:space-between}.nav-links{display:none}.hamburger{display:flex;justify-self:initial}.auth-btn{display:none}}.hero{height:100vh;min-height:520px;background:linear-gradient(rgba(0,0,0,0.45),rgba(0,0,0,0.45)),url('https://cdnstatic.rg.ru/crop1000x667/uploads/images/2023/06/28/22p_verhg_seredina_2_850_57a.jpg') center/cover no-repeat;display:flex;align-items:center;justify-content:center;text-align:center;color:white;margin-top:70px}.hero-content{max-width:90%;padding:1.5rem}.hero h1{font-size:2.9rem;margin-bottom:1.2rem;text-shadow:0 4px 15px rgba(0,0,0,0.6)}.hero p{font-size:1.25rem;margin-bottom:2rem}.cta-button{background:linear-gradient(90deg,#75D9A9,#4ec9b8);color:white;padding:16px 44px;border:none;border-radius:50px;font-size:1.1rem;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:12px;box-shadow:0 10px 30px rgba(117,217,169,0.45);transition:all 0.4s}.cta-button:hover{transform:translateY(-6px) scale(1.06);box-shadow:0 20px 45px rgba(117,217,169,0.6)}section{padding:4.5rem 0;max-width:1200px;margin:0 auto;padding-left:1.25rem;padding-right:1.25rem}h2{text-align:center;font-size:2.35rem;margin-bottom:2.8rem;color:#1e6b4a}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1.8rem}.card{background:white;border-radius:18px;overflow:hidden;box-shadow:0 8px 25px rgba(0,0,0,0.08);transition:all 0.4s ease}.card:hover{transform:translateY(-12px);box-shadow:0 20px 40px rgba(0,0,0,0.12)}.card img{width:100%;height:200px;object-fit:cover}.card-content{padding:1.4rem}.gallery-cta{text-align:center;max-width:640px;margin:0 auto}.gallery-cta p{font-size:1.15rem;color:#444;margin-bottom:2rem}footer{background:#1e6b4a;color:white;text-align:center;padding:3rem 1rem}.photo-grid{display:flex;flex-wrap:wrap;gap:15px;padding:20px}.photo-card{width:200px;text-align:center}.photo-card img{width:100%;border-radius:8px}.like-btn{cursor:pointer;font-size:18px}.liked{color:red}.top-bar{background:#f0f0f0;padding:10px;white-space:nowrap;overflow-x:auto}.top-item{display:inline-block;margin:0 10px;text-align:center}.top-item img{height:100px;border-radius:8px}.user-info{display:flex;align-items:center;gap:10px;margin-bottom:20px}.user-info img{border-radius:50%}</style></head><body><header><nav><div class="logo">Eco bot</div><ul class="nav-links"><li><a href="#about">Что это</a></li><li><a href="#benefits">Преимущества</a></li><li><a href="#ideas">Идеи</a></li><li><a href="#gallery">Галерея</a></li><li><a href="#start">Начать</a></li></ul>'''

    if user_id:
        html += '<a href="/logout" class="auth-btn">Выйти</a>'
    else:
        html += '<a href="/login" class="auth-btn">Войти</a>'

    html += '<div class="hamburger" id="hamburger"><span></span><span></span><span></span></div></nav></header><div class="mobile-menu" id="mobileMenu"><div class="logo">Eco bot</div><ul class="mobile-nav-links"><li><a href="#about">Что это</a></li><li><a href="#benefits">Преимущества</a></li><li><a href="#ideas">Идеи</a></li><li><a href="#gallery">Галерея</a></li><li><a href="#start">Начать</a></li></ul></div>'

    if user_id:
        if user_avatar:
            html += f'<div class="user-info"><img src="{user_avatar}" width="50" height="50"><span>{user_name}</span></div>'
        else:
            html += f'<p>Вы вошли как {user_name or user_id}</p>'
        if user_email and user_email in ADMIN_EMAILS:
            html += '<p><a href="/admin" style="background:#ff6600;color:white;padding:5px 10px;text-decoration:none;border-radius:5px">Админ-панель</a></p>'

    html += '<section class="hero"><div class="hero-content"><h1>Апсайклинг: меняй вещи, а не планету</h1><p>Превращайте отходы в стильные и полезные предметы. Экологично, креативно, экономно.</p><a href="#start" class="cta-button">Начать апсайклинг</a></div></section>'

    # Секция с фото из Supabase
    html += '<section id="gallery"><h2>🏆 Лучшие работы</h2><div class="top-bar">'
    for p in top_data:
        html += f'<div class="top-item"><img src="{p["url"]}"><br>❤️ {p["likes"]}</div>'
    html += '</div><h2>📸 Случайные работы</h2><div class="photo-grid">'
    for p in random_data:
        liked = 'liked' if p['liked'] else ''
        html += f'<div class="photo-card"><img src="{p["url"]}"><div><span class="like-btn {liked}" data-url="{p["url"]}" onclick="like(this)">❤️ <span class="count">{p["likes"]}</span></span></div></div>'
    html += '</div></section>'

    # Остальные секции из дизайна
    html += '''<section id="about"><h2>Что такое апсайклинг?</h2><div class="grid"><div class="card"><img src="https://th.bing.com/th/id/R.8c97045a0cebfcda5a7475394564a082?rik=EIDsPgfszPdLeg&pid=ImgRaw&r=0" alt=""><div class="card-content"><h3>Определение</h3><p>Апсайклинг — это процесс переработки старых вещей в новые продукты более высокого качества.</p></div></div><div class="card"><img src="https://th.bing.com/th/id/OIP.nmRucft3NDUK7axupTbvmgHaE7?r=0&o=7rm=3&rs=1&pid=ImgDetMain&o=7&rm=3" alt=""><div class="card-content"><h3>Отличие от переработки</h3><p>В отличие от обычной переработки, апсайклинг создаёт вещи лучшего качества и ценности.</p></div></div><div class="card"><img src="https://img.freepik.com/premium-photo/planet-earth-pile-garbage-earth-day-concept-pollution-save-planet_89381-5441.jpg?w=1060" alt=""><div class="card-content"><h3>Почему это важно?</h3><p>Снижает количество мусора, экономит ресурсы планеты и развивает креативность.</p></div></div></div></section><section id="benefits" style="background:#e8f5e9;"><h2>Преимущества апсайклинга</h2><div class="grid"><div class="card"><div class="card-content"><h3>Экология</h3><p>Сокращение отходов и сохранение природных ресурсов.</p></div></div><div class="card"><div class="card-content"><h3>Экономия</h3><p>Создавайте вещи бесплатно из того, что уже есть дома.</p></div></div><div class="card"><div class="card-content"><h3>Креативность</h3><p>Развивайте воображение и создавайте уникальные предметы.</p></div></div><div class="card"><div class="card-content"><h3>Уют</h3><p>Персонализируйте свой дом оригинальными вещами.</p></div></div></div></section><section id="ideas"><h2>Идеи для апсайклинга</h2><div class="grid"><div class="card"><img src="https://tse2.mm.bing.net/th/id/OIP.zMylv1yYsK_gPgxdjMU0fQHaEJ?r=0&rs=1&pid=ImgDetMain&o=7&rm=3" alt=""><div class="card-content"><h3>Пластиковые бутылки</h3><p>Вазы, органайзеры, кашпо для растений.</p></div></div><div class="card"><img src="https://tse1.mm.bing.net/th/id/OIP.VFfLPt8RvP4rOEa2jv7H0QHaEK?r=0&rs=1&pid=ImgDetMain&o=7&rm=3" alt=""><div class="card-content"><h3>Старая одежда</h3><p>Патчворк, сумки, коврики, чехлы.</p></div></div><div class="card"><img src="https://th.bing.com/th/id/R.7d6710d58688357507cdc92ad31c7e4c?rik=rzxQeFYdX0bdKg&pid=ImgRaw&r=0" alt=""><div class="card-content"><h3>Старая мебель</h3><p>Перекраска, реставрация, новые функции.</p></div></div></div></section><section id="start" style="background:#e8f5e9;"><h2>Как начать апсайклинг?</h2><div class="grid"><div class="card"><div class="card-content"><h3>1. Соберите материалы</h3><p>Осмотрите дом: поищите старые или ненужные вещи.</p></div></div><div class="card"><div class="card-content"><h3>2. Перейдите в наш VK бот</h3><p>Нажмите на кнопку "Начать" ниже.</p></div></div><div class="card"><div class="card-content"><h3>3. Творите!</h3><p>Не бойтесь экспериментировать. Начинайте творить!</p></div></div></div><div style="text-align:center;margin-top:3rem;"><a href="https://vk.com/im/convo/-240220666?entrypoint=list_all" class="cta-button">Начать</a></div></section><footer><p>&copy; 2026 СберLife. Сайт создан для вдохновения на экологичный образ жизни.</p><p>Присоединяйся к сообществу!</p></footer>'''

    # Скрипты
    html += '''<script>const hamburger=document.getElementById('hamburger');const mobileMenu=document.getElementById('mobileMenu');hamburger.addEventListener('click',()=>{hamburger.classList.toggle('active');mobileMenu.classList.toggle('active')});document.querySelectorAll('.mobile-nav-links a').forEach(link=>{link.addEventListener('click',()=>{hamburger.classList.remove('active');mobileMenu.classList.remove('active')})});document.querySelectorAll('a[href^="#"]').forEach(anchor=>{anchor.addEventListener('click',function(e){if(this.getAttribute('href')!=='#'){e.preventDefault();const target=document.querySelector(this.getAttribute('href'));if(target)target.scrollIntoView({behavior:'smooth'})}})})</script>'''
    html += '<script>async function like(btn){const url=btn.dataset.url;const r=await fetch("/like/"+encodeURIComponent(url),{method:"POST"});if(r.ok){const d=await r.json();btn.querySelector(".count").textContent=d.likes;if(d.liked)btn.classList.add("liked");else btn.classList.remove("liked")}else{alert("Оценивать могут только авторизованные пользователи")}}</script></body></html>'
    return html


# Остальные маршруты остаются без изменений (как в твоём оригинальном коде)
@app.route('/like/<path:photo_url>', methods=['POST'])
def like_photo(photo_url):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Необходима авторизация'}), 401
    liked, likes = toggle_like(user_id, photo_url)
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

    try:
        result = supabase.table("bot_users").select("user_id").execute()
        users = [u['user_id'] for u in result.data]
    except:
        return 'Ошибка получения пользователей', 500

    sent = 0
    for uid in users:
        try:
            requests.post('https://api.vk.com/method/messages.send', params={
                'user_id': uid,
                'message': message,
                'access_token': VK_TOKEN,
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

    all_photos = conn.execute('SELECT * FROM photos').fetchall()
    photo_list = []
    for p in all_photos:
        likes = get_photo_likes(p['filename'])
        photo_list.append(
            {'id': p['id'], 'filename': p['filename'], 'likes': likes})
    photo_list.sort(key=lambda x: x['likes'], reverse=True)
    top_winners = photo_list[:winners_count]

    try:
        result = supabase.table("bot_users").select("user_id").execute()
        users = [{'user_id': u['user_id']} for u in result.data]
    except:
        users = []

    if not top_winners:
        current_contest["active"] = False
        conn.close()
        return

    winner_photo_ids = [p['id'] for p in top_winners]
    winners_ids = set()
    places = ["1-е место 🥇", "2-е место 🥈", "3-е место 🥉"]

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
                'user_id': owner_id, 'message': msg,
                'access_token': VK_TOKEN, 'v': '5.199', 'random_id': 0
            })
        except:
            pass

    winners_list = ""
    for i, p in enumerate(top_winners):
        place = i + 1
        if place <= 3:
            winners_list += f"{places[i]}: фото с {p['likes']} лайками\n"
        else:
            winners_list += f"{place}-е место: фото с {p['likes']} лайками\n"
    losers_message = contest_messages["loser"].format(winners=winners_list)

    for u in users:
        if u['user_id'] not in winners_ids:
            try:
                requests.post('https://api.vk.com/method/messages.send', params={
                    'user_id': u['user_id'], 'message': losers_message,
                    'access_token': VK_TOKEN, 'v': '5.199', 'random_id': 0
                })
                time.sleep(0.05)
            except:
                pass

    for photo in all_photos:
        if photo['id'] not in winner_photo_ids:
            try:
                object_path = photo['filename'].split(
                    f'{SUPABASE_BUCKET}/')[-1]
                supabase.storage.from_(SUPABASE_BUCKET).remove([object_path])
            except:
                pass

    if winner_photo_ids:
        placeholders = ','.join(['?'] * len(winner_photo_ids))
        conn.execute(
            f'DELETE FROM photos WHERE id NOT IN ({placeholders})', winner_photo_ids)
        conn.execute(
            f'DELETE FROM votes WHERE photo_id NOT IN ({placeholders})', winner_photo_ids)
    else:
        conn.execute('DELETE FROM photos')
        conn.execute('DELETE FROM votes')

    conn.commit()
    conn.close()
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

    announcement = contest_messages["announcement"]
    try:
        result = supabase.table("bot_users").select("user_id").execute()
        users = [u['user_id'] for u in result.data]
    except:
        users = []

    for uid in users:
        try:
            requests.post('https://api.vk.com/method/messages.send', params={
                'user_id': uid,
                'message': announcement,
                'access_token': VK_TOKEN,
                'v': '5.199',
                'random_id': 0
            })
            time.sleep(0.05)
        except:
            pass
    return redirect('/admin')


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
