import os
import sqlite3
import hashlib
import hmac
import urllib.parse
from flask import Flask, render_template, redirect, url_for, request, session, send_from_directory, jsonify

# ---------- НАСТРОЙКИ (замени на свои) ----------
PHOTO_ROOT = os.path.join(os.path.expanduser("~"), "Desktop", "VK_Photos")
VK_APP_ID = "1234567"                     # ID твоего мини-приложения
VK_APP_SECRET = "gjEcinHM4La0NrqTZ0Vr"     # Секретный ключ из настроек
SECRET_KEY = "любая_случайная_строка"     # Для Flask-сессий (придумай любую)
# -----------------------------------------------

app = Flask(__name__)
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
    conn.commit()
    conn.close()


def sync_photos():
    """Добавляет в БД новые фото из папок, удаляет несуществующие."""
    conn = get_db()
    existing = {row['filename'] for row in conn.execute(
        'SELECT filename FROM photos').fetchall()}
    actual = set()
    if os.path.exists(PHOTO_ROOT):
        for user_folder in os.listdir(PHOTO_ROOT):
            folder_path = os.path.join(PHOTO_ROOT, user_folder)
            if os.path.isdir(folder_path):
                for fname in os.listdir(folder_path):
                    if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        rel_path = os.path.join(user_folder, fname)
                        actual.add(rel_path)
                        if rel_path not in existing:
                            conn.execute(
                                'INSERT OR IGNORE INTO photos (filename) VALUES (?)', (rel_path,))
    # Удаляем записи о файлах, которых больше нет
    to_remove = existing - actual
    for rel_path in to_remove:
        conn.execute('DELETE FROM photos WHERE filename = ?', (rel_path,))
    conn.commit()
    conn.close()


def get_top_photos(limit=5):
    conn = get_db()
    photos = conn.execute(
        'SELECT * FROM photos ORDER BY likes DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return photos


def get_random_photos(limit=30):
    conn = get_db()
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
    user_id = verify_vk_signature(request)
    if user_id:
        session['user_id'] = int(user_id)

# ---------- МАРШРУТЫ ----------


@app.route('/')
def index():
    user_id = session.get('user_id')
    sync_photos()
    top_photos = get_top_photos(5)
    random_photos = get_random_photos(30)

    def enrich(photo):
        return {
            'id': photo['id'],
            'url': url_for('serve_photo', filepath=photo['filename']),
            'likes': photo['likes'],
            'liked': has_user_voted(user_id, photo['id']) if user_id else False
        }

    top_data = [enrich(p) for p in top_photos]
    random_data = [enrich(p) for p in random_photos]

    # Пока используем встроенный шаблон, позже друг заменит на свой
    return render_template('index.html', user_id=user_id, top_data=top_data, random_data=random_data)


@app.route('/like/<int:photo_id>', methods=['POST'])
def like_photo(photo_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Необходима авторизация'}), 401
    liked, likes = toggle_like(user_id, photo_id)
    return jsonify({'liked': liked, 'likes': likes})


@app.route('/photos/<path:filepath>')
def serve_photo(filepath):
    return send_from_directory(PHOTO_ROOT, filepath)


# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
