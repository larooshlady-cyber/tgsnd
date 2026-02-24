import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY', 'tg-sender-dev-key-change-in-prod')
SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'data', 'app.db')}"
SQLALCHEMY_TRACK_MODIFICATIONS = False

SESSIONS_DIR = os.path.join(BASE_DIR, 'sessions')
