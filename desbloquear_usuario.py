"""
Ferramenta Admin - Desbloquear Usuários
Porta 3005
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import urllib.parse
from datetime import datetime

# ── Ler .env do diretório do script ou do backend ─────────────────
def load_env(path=None):
    candidates = [
        path,
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "../backend/.env"),
        "/home/visionlpr/backend/.env",
    ]
    for f in candidates:
        if f and os.path.isfile(f):
            print(f"📄  Lendo .env: {f}")
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
            return
    print("⚠️   Nenhum .env encontrado — usando variáveis de ambiente existentes")

load_env()

# ── Configuração do banco ──────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "10.3.1.3"),
    "port":     int(os.getenv("DB_PORT", "6033")),
    "user":     os.getenv("DB_USER",     "econdo"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME",     "AdmGeral"),
    "charset":  "utf8mb4",
}

def get_conn():
    import pymysql
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)

def buscar_usuario(email: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT mo.id, mo.email, mo.nome, mo.role, mo.ativo,
                       mo.login_falhos, mo.bloqueado_ate, mo.ultimo_login,
                       mo.criado_em, c.nome AS condominio_nome
                FROM mobile_operadores mo
                LEFT JOIN condominios c ON c.id = mo.condominio_id
                WHERE LOWER(mo.email) = LOWER(%s)
            """, (email,))
            return cur.fetchone()
    finally:
        conn.close()

def desbloquear(email: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 1) Desbloquear o usuário
            cur.execute("""
                UPDATE mobile_operadores
                SET bloqueado_ate = NULL, login_falhos = 0
                WHERE LOWER(email) = LOWER(%s)
            """, (email,))
            affected = cur.rowcount

            # 2) Pegar IPs que tentaram com este email
            cur.execute("""
                SELECT DISTINCT ip FROM mobile_login_attempts
                WHERE LOWER(identifier) = LOWER(%s) AND sucesso = 0
            """, (email,))
            ips = [r['ip'] for r in cur.fetchall()]

            # 3) Limpar tentativas pelo identifier
            cur.execute("""
                DELETE FROM mobile_login_attempts
                WHERE LOWER(identifier) = LOWER(%s)
            """, (email,))

            # 4) Limpar tentativas falhas pelos IPs relacionados
            if ips:
                placeholders = ','.join(['%s'] * len(ips))
                cur.execute(f"""
                    DELETE FROM mobile_login_attempts
                    WHERE sucesso = 0 AND ip IN ({placeholders})
                """, ips)

        conn.commit()
        return affected
    finally:
        conn.close()

# ── HTML ───────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin · Desbloquear Usuário</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:      #0a0e17;
    --surface: #111827;
    --border:  #1e2d40;
    --accent:  #00d4ff;
    --warn:    #ff4444;
    --ok:      #00e676;
    --text:    #e2e8f0;
    --muted:   #64748b;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2rem;
  }

  /* subtle grid background */
  body::before {
    content: '';
    position: fixed; inset: 0;
    background-image:
      linear-gradient(rgba(0,212,255,.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,212,255,.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    width: 100%;
    max-width: 560px;
    padding: 2.5rem;
    position: relative;
    box-shadow: 0 0 60px rgba(0,212,255,.06);
  }

  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 2rem; right: 2rem;
    height: 2px;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    border-radius: 2px;
  }

  .logo {
    display: flex; align-items: center; gap: .75rem;
    margin-bottom: 2rem;
  }
  .logo-icon {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, var(--accent), #0066ff);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.1rem;
  }
  .logo-text {
    font-family: 'IBM Plex Mono', monospace;
    font-size: .75rem;
    color: var(--muted);
    letter-spacing: .15em;
    text-transform: uppercase;
  }
  .logo-text strong {
    display: block;
    color: var(--text);
    font-size: .95rem;
    letter-spacing: .05em;
  }

  h1 {
    font-size: 1.4rem;
    font-weight: 600;
    margin-bottom: .4rem;
  }
  .subtitle {
    color: var(--muted);
    font-size: .875rem;
    margin-bottom: 2rem;
  }

  label {
    display: block;
    font-size: .8rem;
    font-family: 'IBM Plex Mono', monospace;
    color: var(--accent);
    letter-spacing: .1em;
    text-transform: uppercase;
    margin-bottom: .5rem;
  }

  .input-row {
    display: flex; gap: .75rem;
    margin-bottom: 1rem;
  }

  input[type=email] {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-family: 'IBM Plex Mono', monospace;
    font-size: .9rem;
    padding: .75rem 1rem;
    outline: none;
    transition: border-color .2s;
  }
  input[type=email]:focus { border-color: var(--accent); }
  input[type=email]::placeholder { color: var(--muted); }

  .btn {
    background: transparent;
    border: 1px solid var(--accent);
    color: var(--accent);
    border-radius: 8px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: .85rem;
    padding: .75rem 1.25rem;
    cursor: pointer;
    transition: background .2s, color .2s;
    white-space: nowrap;
  }
  .btn:hover { background: var(--accent); color: #000; }
  .btn:disabled { opacity: .4; cursor: not-allowed; }

  .btn-unlock {
    width: 100%;
    padding: .9rem;
    font-size: .9rem;
    margin-top: .5rem;
    background: rgba(0,212,255,.08);
  }
  .btn-unlock:hover { background: var(--accent); color: #000; }

  /* user card */
  #user-info {
    margin-top: 1.5rem;
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    display: none;
  }
  .info-header {
    background: rgba(0,212,255,.06);
    padding: .75rem 1rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: .75rem;
    color: var(--accent);
    letter-spacing: .1em;
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
  }
  .info-body { padding: 1rem; }
  .info-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: .45rem 0;
    border-bottom: 1px solid rgba(255,255,255,.04);
    font-size: .875rem;
  }
  .info-row:last-child { border-bottom: none; }
  .info-key { color: var(--muted); font-family: 'IBM Plex Mono', monospace; font-size: .8rem; }
  .info-val { color: var(--text); font-weight: 600; text-align: right; max-width: 60%; word-break: break-all; }

  .badge {
    display: inline-block;
    padding: .2rem .6rem;
    border-radius: 20px;
    font-size: .75rem;
    font-family: 'IBM Plex Mono', monospace;
  }
  .badge-ok    { background: rgba(0,230,118,.15); color: var(--ok);  border: 1px solid rgba(0,230,118,.3); }
  .badge-warn  { background: rgba(255,68,68,.15);  color: var(--warn); border: 1px solid rgba(255,68,68,.3); }
  .badge-muted { background: rgba(100,116,139,.15); color: var(--muted); border: 1px solid rgba(100,116,139,.3); }

  /* toast */
  #toast {
    position: fixed; bottom: 2rem; right: 2rem;
    padding: .85rem 1.4rem;
    border-radius: 10px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: .85rem;
    opacity: 0;
    transform: translateY(10px);
    transition: opacity .3s, transform .3s;
    pointer-events: none;
    z-index: 99;
  }
  #toast.show { opacity: 1; transform: translateY(0); }
  #toast.success { background: rgba(0,230,118,.2); color: var(--ok);  border: 1px solid rgba(0,230,118,.4); }
  #toast.error   { background: rgba(255,68,68,.2);  color: var(--warn); border: 1px solid rgba(255,68,68,.4); }

  .spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid rgba(0,212,255,.3);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin .6s linear infinite;
    vertical-align: middle;
    margin-right: .4rem;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div class="card">
  <div class="logo">
    <div class="logo-icon">🔐</div>
    <div class="logo-text">
      <strong>e-Condomínio</strong>
      Painel Admin
    </div>
  </div>

  <h1>Desbloquear Usuário</h1>
  <p class="subtitle">Consulte e desbloqueie contas com login_falhos ou bloqueado_ate ativo.</p>

  <label for="email-input">E-mail do usuário</label>
  <div class="input-row">
    <input type="email" id="email-input" placeholder="usuario@dominio.com.br" />
    <button class="btn" onclick="buscar()">Buscar</button>
  </div>

  <div id="user-info">
    <div class="info-header">Dados do usuário</div>
    <div class="info-body" id="info-body"></div>
    <div style="padding:.75rem 1rem; border-top:1px solid var(--border);">
      <button class="btn btn-unlock" id="btn-unlock" onclick="desbloquear()">
        🔓 Desbloquear Usuário
      </button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
let currentEmail = '';

function toast(msg, type='success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show ' + type;
  setTimeout(() => { el.className = ''; }, 3500);
}

function badge(text, type) {
  return `<span class="badge badge-${type}">${text}</span>`;
}

function row(key, val) {
  return `<div class="info-row"><span class="info-key">${key}</span><span class="info-val">${val}</span></div>`;
}

async function buscar() {
  const email = document.getElementById('email-input').value.trim();
  if (!email) { toast('Informe um e-mail', 'error'); return; }
  currentEmail = email;

  const res  = await fetch('/buscar', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({email})
  });
  const data = await res.json();

  const box = document.getElementById('user-info');
  const body = document.getElementById('info-body');

  if (!data.ok) {
    toast(data.msg, 'error');
    box.style.display = 'none';
    return;
  }

  const u = data.user;
  const bloqueado = u.bloqueado_ate ? new Date(u.bloqueado_ate) > new Date() : false;
  const statusBadge = !u.ativo
    ? badge('Inativo', 'warn')
    : bloqueado
      ? badge('Bloqueado', 'warn')
      : badge('Ativo', 'ok');

  body.innerHTML =
    row('Nome',          u.nome) +
    row('E-mail',        u.email) +
    row('Condomínio',    u.condominio_nome || '—') +
    row('Role',          badge(u.role, 'muted')) +
    row('Status',        statusBadge) +
    row('Tentativas',    `<span style="color:${u.login_falhos>0?'#ff4444':'#00e676'}">${u.login_falhos}</span>`) +
    row('Bloqueado até', u.bloqueado_ate
        ? `<span style="color:#ff4444">${u.bloqueado_ate}</span>`
        : badge('Livre', 'ok')) +
    row('Último login',  u.ultimo_login || '—');

  const btnUnlock = document.getElementById('btn-unlock');
  if (!bloqueado && u.login_falhos === 0) {
    btnUnlock.disabled = true;
    btnUnlock.textContent = '✅ Usuário já está desbloqueado';
  } else {
    btnUnlock.disabled = false;
    btnUnlock.innerHTML = '🔓 Desbloquear Usuário';
  }

  box.style.display = 'block';
}

async function desbloquear() {
  if (!currentEmail) return;
  const btn = document.getElementById('btn-unlock');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Desbloqueando...';

  const res  = await fetch('/desbloquear', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({email: currentEmail})
  });
  const data = await res.json();

  if (data.ok) {
    toast('✅ ' + data.msg, 'success');
    buscar(); // refresh
  } else {
    toast('❌ ' + data.msg, 'error');
    btn.disabled = false;
    btn.innerHTML = '🔓 Desbloquear Usuário';
  }
}

// Enter no campo dispara busca
document.getElementById('email-input')
  .addEventListener('keydown', e => { if (e.key === 'Enter') buscar(); });
</script>
</body>
</html>
"""

# ── Handler HTTP ───────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def send_json(self, code, data):
        body = json.dumps(data, default=str, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw    = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            self.send_json(400, {"ok": False, "msg": "JSON inválido"})
            return

        path = urllib.parse.urlparse(self.path).path

        if path == "/buscar":
            email = (payload.get("email") or "").strip()
            if not email:
                self.send_json(400, {"ok": False, "msg": "E-mail obrigatório"})
                return
            try:
                user = buscar_usuario(email)
            except Exception as e:
                self.send_json(500, {"ok": False, "msg": f"Erro DB: {e}"})
                return
            if not user:
                self.send_json(404, {"ok": False, "msg": "Usuário não encontrado"})
                return
            self.send_json(200, {"ok": True, "user": user})

        elif path == "/desbloquear":
            email = (payload.get("email") or "").strip()
            if not email:
                self.send_json(400, {"ok": False, "msg": "E-mail obrigatório"})
                return
            try:
                affected = desbloquear(email)
            except Exception as e:
                self.send_json(500, {"ok": False, "msg": f"Erro DB: {e}"})
                return
            if affected == 0:
                self.send_json(404, {"ok": False, "msg": "Usuário não encontrado"})
                return
            self.send_json(200, {"ok": True, "msg": f"Usuário {email} desbloqueado com sucesso!"})

        else:
            self.send_json(404, {"ok": False, "msg": "Rota não encontrada"})


# ── Main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    PORT = 3005
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅  Servidor rodando em http://0.0.0.0:{PORT}")
    print(f"   Banco: {DB_CONFIG['database']} @ {DB_CONFIG['host']}")
    print("   Ctrl+C para parar\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑  Servidor encerrado.")
