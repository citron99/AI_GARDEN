#!/bin/bash
# AI Garden — bootstrap для SpaceWeb shared hosting (mod_wsgi python3.11).
# Запускается через cron. Идемпотентен: повторные запуски продолжают с места обрыва.
BASE=/home/c/citron99ra/garden
REPO=$BASE/AI_GARDEN
VENV=$BASE/venv
DATA=$BASE/data
LOG=$BASE/bootstrap.log
LOCK=$BASE/bootstrap.lock
ZIP=$BASE/repo.zip

exec >> "$LOG" 2>&1
echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [ -f "$BASE/bootstrap.done" ]; then
  echo "already done"
  exit 0
fi
if [ -f "$LOCK" ]; then
  now=$(date +%s)
  mtime=$(stat -c %Y "$LOCK" 2>/dev/null || echo 0)
  if [ $(( now - mtime )) -lt 3600 ]; then
    echo "another run holds the lock"
    exit 0
  fi
  echo "stale lock removed"
fi
touch "$LOCK"

echo "--- environment ---"
uname -a
id
which python3.11 python3 python unzip curl git 2>/dev/null
ls /opt 2>/dev/null | head

PY=""
for cand in python3.11 /opt/Python-3.11/bin/python3.11 /usr/local/bin/python3.11 /usr/bin/python3.11; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  PY=$(command -v python3 || true)
fi
echo "PY=$PY"
"$PY" --version || { echo "no python"; rm -f "$LOCK"; exit 1; }

echo "--- repo ---"
if [ ! -d "$REPO/app" ]; then
  rm -f "$ZIP"
  curl -fsSL -o "$ZIP" https://github.com/citron99/AI_GARDEN/archive/refs/heads/main.zip \
    || { echo "download failed"; rm -f "$LOCK"; exit 1; }
  rm -rf "$BASE/AI_GARDEN-main" "$REPO"
  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$ZIP" -d "$BASE"
  else
    "$PY" -c "import zipfile; zipfile.ZipFile('$ZIP').extractall('$BASE')"
  fi
  mv "$BASE/AI_GARDEN-main" "$REPO" || { echo "move failed"; ls -la "$BASE"; rm -f "$LOCK"; exit 1; }
fi
ls "$REPO" | head -20

echo "--- venv ---"
if [ ! -x "$VENV/bin/python" ]; then
  rm -rf "$VENV"
  "$PY" -m venv "$VENV" || { echo "venv failed"; rm -f "$LOCK"; exit 1; }
fi
"$VENV/bin/python" --version
"$VENV/bin/pip" --version || "$VENV/bin/python" -m ensurepip

echo "--- pip install ---"
"$VENV/bin/pip" install --no-input --upgrade pip >/dev/null 2>&1
"$VENV/bin/pip" install --no-input --prefer-binary \
  "fastapi==0.139.0" "starlette==1.3.1" "sqlalchemy==2.0.41" "alembic==1.16.1" \
  "Mako==1.3.12" "pydantic-settings==2.9.1" "python-multipart==0.0.32" \
  "pwdlib[argon2]==0.2.1" "argon2-cffi==23.1.0" "argon2-cffi-bindings==21.2.0" "PyJWT==2.13.0" "Pillow==10.4.0" \
  "openai>=2.0,<3" "httpx[socks]==0.28.1" "boto3==1.43.45" "celery==5.6.3" \
  "reportlab>=4.4,<5" "a2wsgi" \
  || { echo "pip install failed"; rm -f "$LOCK"; exit 1; }

echo "--- data dir / .env ---"
mkdir -p "$DATA/uploads"
if [ ! -f "$BASE/.env" ]; then
  "$VENV/bin/python" - <<'PYEOF'
import os
import secrets
base = "/home/c/citron99ra/garden"
jwt = secrets.token_urlsafe(48)
safety = secrets.token_urlsafe(48)
partner = secrets.token_urlsafe(48)
env = f"""AI_PROVIDER=mock
ENVIRONMENT=development
DEMO_LOGIN_ENABLED=false
DATABASE_URL=sqlite:////home/c/citron99ra/garden/data/gardener.db
UPLOAD_DIR=/home/c/citron99ra/garden/data/uploads
STORAGE_BACKEND=local
JWT_SECRET={jwt}
AI_SAFETY_SECRET={safety}
PARTNER_ATTRIBUTION_SECRET={partner}
PUBLIC_BASE_URL=https://citron99.garden.swtest.ru
TRUSTED_HOSTS=citron99.garden.swtest.ru,citron99.garden,127.0.0.1,localhost
DIAGNOSIS_EXECUTION_MODE=sync
BILLING_PROVIDER=disabled
EMAIL_DELIVERY_MODE=log
METRICS_ENABLED=false
"""
env_path = base + "/.env"
open(env_path, "w").write(env)
os.chmod(env_path, 0o600)
print(".env written")
PYEOF
fi

echo "--- wsgi.py (docroot) ---"
cat > "$BASE/public_html/wsgi.py" <<'WSGIEOF'
import os
import sys

BASE = "/home/c/citron99ra/garden"
REPO = BASE + "/AI_GARDEN"
VENV = BASE + "/venv"
sys.path.insert(0, REPO)
sys.path.insert(0, VENV + "/lib/python3.11/site-packages")
os.chdir(BASE)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from app.config import settings, validate_runtime_settings

validate_runtime_settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)

from app.main import app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(app)
WSGIEOF

echo "--- .htaccess (docroot) ---"
cat > "$BASE/public_html/.htaccess" <<'HTEOF'
AddDefaultCharset utf-8
AddHandler wsgi-script .py
WSGIPassAuthorization On
RewriteEngine On
RewriteCond %{REQUEST_FILENAME} !-f
RewriteRule ^(.*)$ wsgi.py/$1 [QSA,L]
HTEOF

echo "--- alembic ---"
set -a
. "$BASE/.env"
set +a
cd "$REPO"
"$VENV/bin/python" -m alembic upgrade head || { echo "alembic failed"; rm -f "$LOCK"; exit 1; }

echo "--- self test ---"
"$VENV/bin/python" -c "import sys, os; sys.path.insert(0, '$REPO'); os.chdir('$BASE'); from app.main import app; print('app import OK')" \
  || { echo "import test failed"; rm -f "$LOCK"; exit 1; }

touch "$BASE/bootstrap.done"
rm -f "$LOCK"
echo "=== DONE ==="
