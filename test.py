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

contest_messages = {
    "announcement": "📢 Запущен конкурс!",
    "winner_1": "🎉 1-е место! {likes} лайков.",
    "winner_2": "🎉 2-е место! {likes} лайков.",
    "winner_3": "🎉 3-е место! {likes} лайков.",
    "winner_other": "🎉 {place}-е место! {likes} лайков.",
    "loser": "Конкурс завершён. Победители:\n{winners}"
}
current_contest = {"active": False, "end_time": None,
                   "timer_thread": None, "winners_count": 3}

app = Flask(__name__)
app.secret_key = SECRET_KEY
DB_PATH = os.path.join(os.path.dirname(__file__), "votes.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT UNIQUE NOT NULL)''')
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS votes (user_id INTEGER, photo_id INTEGER, PRIMARY KEY (user_id, photo_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, avatar TEXT, password_hash TEXT, is_email_user INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()


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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/gallery')
def gallery():
    sync_photos()
    user_id = session.get('user_id')
    conn = get_db()
    photos = conn.execute(
        'SELECT * FROM photos ORDER BY RANDOM() LIMIT 30').fetchall()
    conn.close()
    photo_list = [{'id': p['id'], 'url': p['filename'], 'likes': get_photo_likes(
        p['filename']), 'liked': has_user_voted(user_id, p['filename']) if user_id else False} for p in photos]
    return render_template('gallery.html', photos=photo_list, user_id=user_id)


@app.route('/like/<path:photo_url>', methods=['POST'])
def like_photo(photo_url):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Авторизуйтесь'}), 401
    liked, likes = toggle_like(user_id, photo_url)
    return jsonify({'liked': liked, 'likes': likes})


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


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
