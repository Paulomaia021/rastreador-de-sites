from __future__ import annotations
import csv
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(__file__).parent / "prices.db"
BACKUP_DIR = Path(__file__).parent / "backups"

MIN_PASSWORD_LENGTH = 6
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15
MAX_RECOVERY_ATTEMPTS = 5


class _ClosingConnection(sqlite3.Connection):
    """Realiza commit/rollback da transação e fecha a conexão automaticamente ao sair do bloco 'with'."""

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20.0, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """Gera hash seguro PBKDF2-HMAC-SHA256 para armazenamento de senha."""
    if salt is None:
        salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"{salt.hex()}${pwd_hash.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verifica se a senha informada corresponde ao hash salvo em tempo constante."""
    try:
        salt_hex, _ = stored_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        expected_hash = _hash_password(password, salt)
        return hmac.compare_digest(expected_hash, stored_hash)
    except Exception:
        return False


def validate_password_strength(password: str) -> str:
    """Padroniza a validação de senhas em todo o sistema (mínimo de 6 caracteres)."""
    if not password or not isinstance(password, str):
        raise ValueError(f"A senha é obrigatória e deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
    cleaned = password.strip()
    if len(cleaned) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
    return cleaned


def validate_product_url(url: str) -> str:
    """
    Valida a URL de um produto garantindo esquema http/https válido, domínio público real
    e bloqueando endereços locais, loopback ou IPs privados (prevenção de SSRF e XSS).
    """
    if not url or not isinstance(url, str):
        raise ValueError("A URL do produto é obrigatória.")

    clean_url = url.strip()
    if len(clean_url) > 2048 or any(c in clean_url for c in (" ", "\n", "\r", "\t", "<", ">", '"', "'")):
        raise ValueError("URL inválida: formato malformado ou caracteres não permitidos.")

    parsed = urlparse(clean_url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("URL inválida: o link deve começar obrigatoriamente com http:// ou https://.")

    if parsed.username or parsed.password:
        raise ValueError("URL inválida: credenciais não são permitidas na URL.")

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname or "." not in hostname:
        raise ValueError("URL inválida: informe um domínio público válido (ex: https://loja.com.br/produto).")

    blocked_hosts = {"localhost", "0.0.0.0", "127.0.0.1", "::1", "metadata.google.internal"}
    if hostname in blocked_hosts or hostname.endswith((".local", ".internal", ".localhost", ".lan", ".arpa")):
        raise ValueError("URL bloqueada por segurança: endereços locais ou internos não são permitidos.")

    try:
        ip = ipaddress.ip_address(hostname)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("URL bloqueada por segurança: endereços IP privados ou reservados não são permitidos.")
    except ValueError as err:
        if "URL bloqueada" in str(err):
            raise

    return clean_url


def mask_whatsapp_phone(phone: str) -> str:
    """Mascara um número de WhatsApp para exibição segura (ex: +5521*****8888)."""
    clean = normalize_whatsapp_phone(phone)
    if len(clean) <= 6:
        return "***"
    visible_prefix = clean[:4]
    visible_suffix = clean[-4:]
    masked_len = max(len(clean) - 8, 3)
    return f"+{visible_prefix}{'*' * masked_len}{visible_suffix}"


def _sanitize_log_text(text: str) -> str:
    """Remove potenciais tokens, senhas ou chaves de API antes de gravar nos logs."""
    if not text:
        return ""
    sanitized = re.sub(r"(apikey|token|password|senha|secret)=([^&\s]+)", r"\1=***REDACTED***", str(text), flags=re.IGNORECASE)
    sanitized = re.sub(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer ***REDACTED***", sanitized, flags=re.IGNORECASE)
    return sanitized[:1000]


def extract_store_name(url: str) -> str:
    """Identifica o nome da loja a partir da URL do produto."""
    try:
        netloc = urlparse(url).netloc.lower()
        if "mercadolivre" in netloc or "mercadolibre" in netloc:
            return "Mercado Livre"
        if "amazon" in netloc:
            return "Amazon"
        if "books.toscrape" in netloc:
            return "Books to Scrape"
        if "magazineluiza" in netloc or "magalu" in netloc:
            return "Magazine Luiza"
        if "kabum" in netloc:
            return "KaBuM!"
        if "shopee" in netloc:
            return "Shopee"
        clean_domain = netloc.replace("www.", "")
        return clean_domain if clean_domain else "Outra Loja"
    except Exception:
        return "Outra Loja"


def normalize_whatsapp_phone(phone: str) -> str:
    """
    Remove caracteres não numéricos e garante DDI (padrão 55 para números brasileiros de 10 ou 11 dígitos).
    """
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = f"55{digits}"
    return digits


def is_valid_whatsapp_phone(phone: str) -> bool:
    """Valida se o número de WhatsApp possui formato numérico internacional plausível (10 a 15 dígitos)."""
    clean = normalize_whatsapp_phone(phone)
    return bool(re.fullmatch(r"\d{10,15}", clean))


def init_db() -> None:
    """
    Inicializa e migra automaticamente o banco de dados SQLite preservando todos os dados existentes.
    """
    now = datetime.now().isoformat(timespec="seconds")
    BACKUP_DIR.mkdir(exist_ok=True)

    with get_connection() as conn:
        # 1. Tabela de Usuários (Perfis individuais + Controle de Acesso Admin)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                whatsapp_phone TEXT DEFAULT '',
                whatsapp_apikey TEXT DEFAULT '',
                notifications_enabled INTEGER DEFAULT 1,
                notify_on_any_drop INTEGER DEFAULT 1,
                is_admin INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )

        user_cols = [c["name"] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "is_admin" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        if "is_active" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")

        # Garante que exista pelo menos um administrador na plataforma sem escalar privilégios por username fixo
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_admin, 0) = 1").fetchone()[0]
        if admin_count == 0:
            default_user = conn.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
            if not default_user:
                initial_admin_pwd = os.getenv("ADMIN_INITIAL_PASSWORD", "admin123")
                conn.execute(
                    """
                    INSERT INTO users (
                        id, username, display_name, password_hash, whatsapp_phone,
                        is_admin, is_active, created_at
                    )
                    VALUES (1, 'admin', 'Administrador', ?, '', 1, 1, ?)
                    """,
                    (_hash_password(initial_admin_pwd), now),
                )
            else:
                conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (default_user["id"],))

        # 2. Tabela de Produtos Monitorados
        table_info = conn.execute("PRAGMA table_info(products)").fetchall()
        prod_cols = [col["name"] for col in table_info]

        if not table_info:
            conn.execute(
                """
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    store_name TEXT DEFAULT '',
                    target_price REAL NOT NULL,
                    previous_price REAL,
                    last_price REAL,
                    last_notified_price REAL,
                    created_at TEXT,
                    last_checked_at TEXT,
                    monitoring_status TEXT DEFAULT 'ativo',
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    UNIQUE(user_id, url),
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
                """
            )
            prod_cols = [c["name"] for c in conn.execute("PRAGMA table_info(products)").fetchall()]
        elif "user_id" not in prod_cols:
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                """
                CREATE TABLE products_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    store_name TEXT DEFAULT '',
                    target_price REAL NOT NULL,
                    previous_price REAL,
                    last_price REAL,
                    last_notified_price REAL,
                    created_at TEXT,
                    last_checked_at TEXT,
                    monitoring_status TEXT DEFAULT 'ativo',
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    UNIQUE(user_id, url),
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
                """
            )
            last_notif_expr = "last_notified_price" if "last_notified_price" in prod_cols else "NULL"
            conn.execute(
                f"""
                INSERT INTO products_new (id, user_id, name, url, target_price, last_notified_price, created_at)
                SELECT id, 1, name, url, target_price, {last_notif_expr}, ?
                FROM products
                """,
                (now,),
            )
            conn.execute("DROP TABLE products")
            conn.execute("ALTER TABLE products_new RENAME TO products")
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            prod_cols = [c["name"] for c in conn.execute("PRAGMA table_info(products)").fetchall()]

        # Migrações incrementais de colunas novas em products (apenas para bancos existentes sem essas colunas)
        for col_name, col_def in [
            ("last_price", "REAL"),
            ("previous_price", "REAL"),
            ("last_notified_price", "REAL"),
            ("created_at", "TEXT"),
            ("last_checked_at", "TEXT"),
            ("store_name", "TEXT DEFAULT ''"),
            ("monitoring_status", "TEXT DEFAULT 'ativo'"),
            ("error_count", "INTEGER DEFAULT 0"),
            ("last_error", "TEXT DEFAULT ''"),
        ]:
            if col_name not in prod_cols:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_def}")

        # Preenche store_name para produtos antigos que ainda estejam vazios
        empty_stores = conn.execute("SELECT id, url FROM products WHERE store_name IS NULL OR store_name = ''").fetchall()
        for r in empty_stores:
            conn.execute("UPDATE products SET store_name = ? WHERE id = ?", (extract_store_name(r["url"]), r["id"]))

        # 3. Tabela de Histórico de Preços
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                price REAL NOT NULL,
                previous_price REAL,
                variation_pct REAL DEFAULT 0.0,
                store_name TEXT DEFAULT '',
                checked_at TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
            )
            """
        )
        hist_cols = [c["name"] for c in conn.execute("PRAGMA table_info(price_history)").fetchall()]
        for col_name, col_def in [
            ("previous_price", "REAL"),
            ("variation_pct", "REAL DEFAULT 0.0"),
            ("store_name", "TEXT DEFAULT ''"),
        ]:
            if col_name not in hist_cols:
                conn.execute(f"ALTER TABLE price_history ADD COLUMN {col_name} {col_def}")

        # 4. Tabela de Log de Notificações WhatsApp
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                phone TEXT NOT NULL,
                message TEXT NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,
                delivery_status TEXT DEFAULT 'Enviada',
                provider TEXT DEFAULT 'whatsapp_direct',
                error_message TEXT DEFAULT '',
                is_duplicate INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                sent_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
            )
            """
        )
        notif_cols = [c["name"] for c in conn.execute("PRAGMA table_info(notification_logs)").fetchall()]
        for col_name, col_def in [
            ("delivery_status", "TEXT DEFAULT 'Enviada'"),
            ("provider", "TEXT DEFAULT 'whatsapp_direct'"),
            ("error_message", "TEXT DEFAULT ''"),
            ("is_duplicate", "INTEGER DEFAULT 0"),
            ("retry_count", "INTEGER DEFAULT 0"),
        ]:
            if col_name not in notif_cols:
                conn.execute(f"ALTER TABLE notification_logs ADD COLUMN {col_name} {col_def}")

        # 5. Tabela de Fila de Processamento (Job Queue)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitoring_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'aguardando',
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                result_price REAL,
                http_status INTEGER,
                response_time_ms REAL,
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )

        # 6. Tabela de Logs do Sistema e Diagnóstico (Scraper, WhatsApp, HTTP)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                user_id INTEGER,
                product_id INTEGER,
                url TEXT DEFAULT '',
                http_status INTEGER,
                response_time_ms REAL,
                attempts INTEGER DEFAULT 1,
                message TEXT NOT NULL
            )
            """
        )

        # 7. Tabela de Auditoria Administrativa
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                admin_username TEXT NOT NULL,
                action TEXT NOT NULL,
                target_user_id INTEGER,
                details TEXT NOT NULL
            )
            """
        )

        # 8. Tabela de Configurações Globais do Sistema
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        default_settings = {
            "monitoring_paused": "0",
            "check_interval_minutes": "60",
            "max_retries": "3",
            "delay_between_requests_sec": "1.0",
            "log_retention_days": "90",
            "last_global_run": "",
            "last_backup_at": "",
            "last_backup_file": "",
        }
        for k, v in default_settings.items():
            conn.execute(
                "INSERT OR IGNORE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, now),
            )

        # 9. Tabela de Códigos de Recuperação de Conta via WhatsApp
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )
        rec_cols = [c["name"] for c in conn.execute("PRAGMA table_info(recovery_codes)").fetchall()]
        if "attempts" not in rec_cols:
            conn.execute("ALTER TABLE recovery_codes ADD COLUMN attempts INTEGER DEFAULT 0")

        # 10. Tabela de Sessões Persistentes com Prazo de Expiração (Evita logout ao atualizar página)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )

        # 11. Tabela de Proteção contra Força Bruta no Login (Rate Limiting)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_attempts (
                username TEXT PRIMARY KEY,
                failed_count INTEGER DEFAULT 0,
                locked_until TEXT DEFAULT '',
                last_attempt_at TEXT NOT NULL
            )
            """
        )

        # 12. Índices de Desempenho Recomendados na Auditoria
        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_products_status ON products(monitoring_status)",
            "CREATE INDEX IF NOT EXISTS idx_products_url ON products(url)",
            "CREATE INDEX IF NOT EXISTS idx_price_history_prod_checked ON price_history(product_id, checked_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notif_logs_user_sent ON notification_logs(user_id, sent_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notif_logs_status ON notification_logs(delivery_status)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_prod ON monitoring_jobs(status, product_id)",
            "CREATE INDEX IF NOT EXISTS idx_sys_logs_created_type ON system_logs(created_at DESC, event_type)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_expires ON user_sessions(user_id, expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_recovery_user_used ON recovery_codes(user_id, used)",
        ]
        for idx_sql in index_statements:
            conn.execute(idx_sql)

    cleanup_expired_auth_data()
    cleanup_old_logs()


# =====================================================================
# CONTROLE DE ACESSO E VALIDAÇÃO DE PERMISSÕES NO BACKEND (ETAPA 2)
# =====================================================================

def is_user_admin(user_id: int) -> bool:
    """Verifica no banco de dados se o usuário possui permissão administrativa ativa."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_admin, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return bool(row and row["is_active"] == 1 and row["is_admin"] == 1)


def require_admin(admin_user_id: int) -> dict:
    """
    Valida no backend se o usuário requisitante é um administrador ativo.
    Lança PermissionError caso um usuário comum ou inativo tente executar funções administrativas.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, display_name, is_admin, is_active FROM users WHERE id = ?",
            (admin_user_id,),
        ).fetchone()
        if not row or row["is_active"] != 1 or row["is_admin"] != 1:
            raise PermissionError(
                "Acesso negado: apenas administradores autorizados podem executar esta operação."
            )
        return dict(row)


def record_audit_log(
    admin_user_id: int,
    action: str,
    details: str,
    target_user_id: int | None = None,
) -> None:
    """Registra uma ação administrativa na trilha de auditoria (nunca armazena senhas/tokens)."""
    admin = require_admin(admin_user_id)
    now = datetime.now().isoformat(timespec="seconds")
    clean_details = _sanitize_log_text(details)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (created_at, admin_id, admin_username, action, target_user_id, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, admin["id"], admin["username"], action, target_user_id, clean_details),
        )


def record_system_log(
    event_type: str,
    status: str,
    message: str,
    user_id: int | None = None,
    product_id: int | None = None,
    url: str = "",
    http_status: int | None = None,
    response_time_ms: float | None = None,
    attempts: int = 1,
) -> None:
    """Registra evento técnico de diagnóstico (Scraper, HTTP, WhatsApp, Fila) com dados sanitizados."""
    now = datetime.now().isoformat(timespec="seconds")
    clean_msg = _sanitize_log_text(message)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO system_logs (
                created_at, event_type, status, user_id, product_id,
                url, http_status, response_time_ms, attempts, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                event_type,
                status,
                user_id,
                product_id,
                url,
                http_status,
                round(float(response_time_ms), 2) if response_time_ms is not None else None,
                attempts,
                clean_msg,
            ),
        )


# =====================================================================
# FUNÇÕES DE USUÁRIOS E PERFIS
# =====================================================================

def create_user(
    username: str,
    password: str,
    display_name: str = "",
    whatsapp_phone: str = "",
    whatsapp_apikey: str = "",
) -> dict:
    """Cria um novo perfil de usuário com senha validada/criptografada e WhatsApp configurado."""
    clean_username = (username or "").strip().lower()
    if not clean_username:
        raise ValueError("O nome de usuário é obrigatório.")
    if len(clean_username) < 3 or len(clean_username) > 40 or not re.fullmatch(r"[a-z0-9_.\-]+", clean_username):
        raise ValueError("O nome de usuário deve ter entre 3 e 40 caracteres (letras, números, '.', '_' ou '-').")

    valid_password = validate_password_strength(password)
    clean_name = display_name.strip() or clean_username
    clean_phone = normalize_whatsapp_phone(whatsapp_phone)
    if clean_phone and not is_valid_whatsapp_phone(clean_phone):
        raise ValueError("Informe um número de WhatsApp válido com DDD (10 a 15 dígitos).")

    now = datetime.now().isoformat(timespec="seconds")

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE lower(username) = ?", (clean_username,)
        ).fetchone()
        if existing:
            raise ValueError("Este nome de usuário já está em uso. Escolha outro.")

        if clean_phone:
            phone_owner = conn.execute(
                "SELECT username FROM users WHERE whatsapp_phone = ?", (clean_phone,)
            ).fetchone()
            if phone_owner:
                raise ValueError(
                    f"Este número de WhatsApp já está cadastrado em outra conta (@{phone_owner['username']}). "
                    "Cada perfil deve possuir um número exclusivo."
                )

        # Novos cadastros públicos nunca recebem privilégio administrativo automaticamente por nome de usuário
        is_admin_flag = 0

        cursor = conn.execute(
            """
            INSERT INTO users (
                username, display_name, password_hash, whatsapp_phone,
                whatsapp_apikey, notifications_enabled, notify_on_any_drop,
                is_admin, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, 1, 1, ?, 1, ?)
            RETURNING id, username, display_name, whatsapp_phone, whatsapp_apikey,
                      notifications_enabled, notify_on_any_drop, is_admin, is_active, created_at
            """,
            (
                clean_username,
                clean_name,
                _hash_password(valid_password),
                clean_phone,
                whatsapp_apikey.strip(),
                is_admin_flag,
                now,
            ),
        )
        created = dict(cursor.fetchone())

    record_system_log(
        event_type="auth",
        status="sucesso",
        message=f"Novo usuário cadastrado: {clean_username}",
        user_id=created["id"],
    )
    return created


def authenticate_user(username: str, password: str) -> dict | None:
    """
    Autentica um usuário pelo login e senha com proteção contra força bruta (rate limiting)
    e verificação de conta ativa.
    """
    clean_username = (username or "").strip().lower()
    if not clean_username or not password:
        return None

    now = datetime.now()
    now_str = now.isoformat(timespec="seconds")

    with get_connection() as conn:
        # 1. Verifica se o usuário está temporariamente bloqueado por excesso de tentativas
        attempt_row = conn.execute(
            "SELECT failed_count, locked_until, last_attempt_at FROM login_attempts WHERE username = ?",
            (clean_username,),
        ).fetchone()

        if attempt_row and attempt_row["locked_until"] and attempt_row["locked_until"] > now_str:
            raise PermissionError(
                f"Muitas tentativas de login incorretas. Por segurança, o acesso para '{clean_username}' "
                f"está bloqueado temporariamente até {attempt_row['locked_until'][11:16]}."
            )

        row = conn.execute(
            "SELECT * FROM users WHERE lower(username) = ?", (clean_username,)
        ).fetchone()

        if row and _verify_password(password, row["password_hash"]):
            # Login correto -> limpa histórico de falhas
            conn.execute("DELETE FROM login_attempts WHERE username = ?", (clean_username,))
            if row["is_active"] == 0:
                conn.commit()
                raise PermissionError("Sua conta está temporariamente desativada pelo administrador.")
            user_dict = dict(row)
            user_dict.pop("password_hash", None)
            return user_dict

        # 2. Login falhou -> contabiliza tentativa falha na janela de 15 minutos
        window_start = (now - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat(timespec="seconds")
        if attempt_row and attempt_row["last_attempt_at"] >= window_start:
            new_count = int(attempt_row["failed_count"] or 0) + 1
        else:
            new_count = 1

        locked_until = ""
        if new_count >= MAX_LOGIN_ATTEMPTS:
            locked_until = (now + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat(timespec="seconds")

        conn.execute(
            """
            INSERT INTO login_attempts (username, failed_count, locked_until, last_attempt_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                failed_count = excluded.failed_count,
                locked_until = excluded.locked_until,
                last_attempt_at = excluded.last_attempt_at
            """,
            (clean_username, new_count, locked_until, now_str),
        )

    if locked_until:
        record_system_log(
            event_type="auth",
            status="aviso",
            message=f"Bloqueio temporário ativado para '{clean_username}' após {new_count} tentativas falhas de login.",
        )
        raise PermissionError(
            f"Muitas tentativas de login incorretas ({MAX_LOGIN_ATTEMPTS} falhas). "
            f"Conta bloqueada temporariamente por {LOGIN_LOCKOUT_MINUTES} minutos."
        )

    return None


def get_user_by_id(user_id: int) -> dict | None:
    """Busca os dados de perfil de um usuário específico."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        user_dict = dict(row)
        user_dict.pop("password_hash", None)
        return user_dict


def update_user_profile(
    user_id: int,
    display_name: str,
    whatsapp_phone: str,
    whatsapp_apikey: str = "",
    notifications_enabled: bool = True,
    notify_on_any_drop: bool = True,
) -> dict | None:
    """Atualiza número de WhatsApp e preferências de notificação do perfil."""
    clean_phone = normalize_whatsapp_phone(whatsapp_phone)
    if clean_phone and not is_valid_whatsapp_phone(clean_phone):
        raise ValueError("Informe um número de WhatsApp válido com DDD (10 a 15 dígitos).")

    with get_connection() as conn:
        if clean_phone:
            phone_owner = conn.execute(
                "SELECT id, username FROM users WHERE whatsapp_phone = ? AND id != ?",
                (clean_phone, user_id),
            ).fetchone()
            if phone_owner:
                raise ValueError(
                    f"Este número de WhatsApp já está em uso na conta @{phone_owner['username']}. "
                    "Cada conta deve ter um número único."
                )

        conn.execute(
            """
            UPDATE users
            SET display_name = ?,
                whatsapp_phone = ?,
                whatsapp_apikey = ?,
                notifications_enabled = ?,
                notify_on_any_drop = ?
            WHERE id = ?
            """,
            (
                display_name.strip(),
                clean_phone,
                whatsapp_apikey.strip(),
                1 if notifications_enabled else 0,
                1 if notify_on_any_drop else 0,
                user_id,
            ),
        )
    return get_user_by_id(user_id)


def create_recovery_token(identifier: str, confirm_phone: str = "") -> dict:
    """
    Localiza o usuário por username ou número de WhatsApp, gera um código criptograficamente seguro
    de 6 dígitos com validade de 15 minutos e retorna metadados com telefone mascarado (sem expor na URL).
    """
    clean_id = (identifier or "").strip().lower()
    clean_phone = normalize_whatsapp_phone(identifier)

    if not clean_id:
        raise ValueError("Informe seu nome de usuário ou número de WhatsApp cadastrado.")

    with get_connection() as conn:
        user_row = conn.execute(
            """
            SELECT id, username, display_name, whatsapp_phone, whatsapp_apikey, is_active
            FROM users
            WHERE lower(username) = ? OR (whatsapp_phone != '' AND whatsapp_phone = ?)
            """,
            (clean_id, clean_phone),
        ).fetchone()

        if not user_row:
            raise ValueError("Nenhuma conta foi encontrada com os dados informados.")

        user = dict(user_row)
        if user.get("is_active", 1) == 0:
            raise PermissionError("Esta conta está desativada. Entre em contato com o administrador.")

        registered_phone = user.get("whatsapp_phone") or ""
        if not registered_phone:
            raise ValueError(
                "Esta conta não possui número de WhatsApp cadastrado. "
                "Solicite ao administrador a redefinição de sua senha."
            )

        # Se o usuário buscou apenas pelo username e confirm_phone foi fornecido, valida correspondência
        if confirm_phone.strip():
            norm_confirm = normalize_whatsapp_phone(confirm_phone)
            if norm_confirm != registered_phone and not registered_phone.endswith(re.sub(r"\D", "", confirm_phone)[-4:]):
                raise ValueError("O número de WhatsApp informado não corresponde ao cadastrado nesta conta.")

        # Gera código numérico criptograficamente seguro de 6 dígitos (CSPRNG)
        code = f"{secrets.randbelow(900000) + 100000}"
        now = datetime.now()
        expires = (now + timedelta(minutes=15)).isoformat(timespec="seconds")
        now_str = now.isoformat(timespec="seconds")

        # Invalida códigos anteriores não usados deste usuário
        conn.execute(
            "UPDATE recovery_codes SET used = 1 WHERE user_id = ? AND used = 0",
            (user["id"],),
        )

        conn.execute(
            """
            INSERT INTO recovery_codes (user_id, code, expires_at, used, attempts, created_at)
            VALUES (?, ?, ?, 0, 0, ?)
            """,
            (user["id"], code, expires, now_str),
        )

    record_system_log(
        event_type="auth",
        status="sucesso",
        message=f"Código de recuperação gerado para @{user['username']}",
        user_id=user["id"],
    )

    return {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "whatsapp_phone": registered_phone,
        "masked_phone": mask_whatsapp_phone(registered_phone),
        "whatsapp_apikey": user.get("whatsapp_apikey", ""),
        "code": code,
        "expires_at": expires,
    }


def reset_password_with_token(user_id: int, code: str, new_password: str) -> bool:
    """
    Valida o código de 6 dígitos dentro da validade com limite de tentativas contra força bruta
    e redefine a senha do usuário usando a validação padronizada.
    """
    clean_code = (code or "").strip()
    if not clean_code:
        raise ValueError("O código de recuperação de 6 dígitos deve ser informado.")

    valid_password = validate_password_strength(new_password)
    now_str = datetime.now().isoformat(timespec="seconds")

    with get_connection() as conn:
        active_rec = conn.execute(
            """
            SELECT id, code, expires_at, used, COALESCE(attempts, 0) as attempts
            FROM recovery_codes
            WHERE user_id = ? AND used = 0
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if not active_rec:
            raise ValueError("Código de recuperação inválido ou já utilizado.")

        if active_rec["expires_at"] < now_str:
            conn.execute("UPDATE recovery_codes SET used = 1 WHERE id = ?", (active_rec["id"],))
            conn.commit()
            raise ValueError("Este código de recuperação expirou (validade de 15 minutos). Solicite um novo código.")

        if not hmac.compare_digest(str(active_rec["code"]), clean_code):
            new_attempts = int(active_rec["attempts"]) + 1
            if new_attempts >= MAX_RECOVERY_ATTEMPTS:
                conn.execute(
                    "UPDATE recovery_codes SET attempts = ?, used = 1 WHERE id = ?",
                    (new_attempts, active_rec["id"]),
                )
                conn.commit()
                raise ValueError(
                    "Código invalidado por excesso de tentativas incorretas. Solicite um novo código."
                )
            conn.execute(
                "UPDATE recovery_codes SET attempts = ? WHERE id = ?",
                (new_attempts, active_rec["id"]),
            )
            conn.commit()
            remaining = MAX_RECOVERY_ATTEMPTS - new_attempts
            raise ValueError(f"Código de recuperação incorreto. Tentativas restantes: {remaining}.")

        # Marca código como utilizado e limpa bloqueios de login da conta
        conn.execute("UPDATE recovery_codes SET used = 1 WHERE id = ?", (active_rec["id"],))
        user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_row:
            conn.execute("DELETE FROM login_attempts WHERE username = ?", (user_row["username"].lower(),))

        # Atualiza a senha com hash PBKDF2
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (_hash_password(valid_password), user_id),
        )

    record_system_log(
        event_type="auth",
        status="sucesso",
        message=f"Senha redefinida com sucesso via código de recuperação para usuário ID {user_id}",
        user_id=user_id,
    )
    return True


# =====================================================================
# SESSÕES PERSISTENTES COM LOGOUT APÓS 15 MINUTOS DE INATIVIDADE
# =====================================================================

def cleanup_expired_auth_data() -> dict:
    """
    Remove sessões expiradas, códigos de recuperação vencidos/antigos e bloqueios de login já expirados.
    """
    now = datetime.now()
    now_str = now.isoformat(timespec="seconds")
    used_code_cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    login_window_cutoff = (now - timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat(timespec="seconds")

    with get_connection() as conn:
        cur_sessions = conn.execute(
            "DELETE FROM user_sessions WHERE expires_at != '' AND expires_at < ?",
            (now_str,),
        )
        cur_codes = conn.execute(
            "DELETE FROM recovery_codes WHERE expires_at < ? OR (used = 1 AND created_at < ?)",
            (now_str, used_code_cutoff),
        )
        cur_attempts = conn.execute(
            """
            DELETE FROM login_attempts
            WHERE (locked_until != '' AND locked_until < ?)
               OR (locked_until = '' AND last_attempt_at < ?)
            """,
            (now_str, login_window_cutoff),
        )
        return {
            "expired_sessions_removed": cur_sessions.rowcount,
            "expired_codes_removed": cur_codes.rowcount,
            "expired_login_attempts_removed": cur_attempts.rowcount,
        }


def cleanup_old_logs(retention_days: int | None = None) -> dict:
    """
    Aplica política de retenção de logs removendo registros antigos de system_logs,
    monitoring_jobs finalizados e notification_logs além do período configurado (padrão: 90 dias).
    """
    with get_connection() as conn:
        if retention_days is None:
            row = conn.execute(
                "SELECT value FROM system_settings WHERE key = 'log_retention_days'"
            ).fetchone()
            try:
                retention_days = int(row["value"]) if row else 90
            except (ValueError, TypeError):
                retention_days = 90

        days = max(int(retention_days), 7)
        cutoff_str = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

        cur_sys = conn.execute(
            "DELETE FROM system_logs WHERE created_at < ?",
            (cutoff_str,),
        )
        cur_jobs = conn.execute(
            "DELETE FROM monitoring_jobs WHERE status IN ('concluido', 'falha', 'erro') AND created_at < ?",
            (cutoff_str,),
        )
        cur_notif = conn.execute(
            "DELETE FROM notification_logs WHERE sent_at < ?",
            (cutoff_str,),
        )
        return {
            "retention_days": days,
            "system_logs_removed": cur_sys.rowcount,
            "monitoring_jobs_removed": cur_jobs.rowcount,
            "notification_logs_removed": cur_notif.rowcount,
        }


def create_user_session(user_id: int, duration_minutes: int = 15) -> str:
    """
    Gera um token seguro de sessão (UUID4) com prazo de 15 minutos de inatividade.
    Ao recarregar a página dentro dos 15 minutos, o login é mantido.
    Após 15 minutos sem atividade, o logout é realizado automaticamente.
    """
    cleanup_expired_auth_data()
    token = uuid.uuid4().hex
    now = datetime.now()
    now_str = now.isoformat(timespec="seconds")
    expires = (now + timedelta(minutes=duration_minutes)).isoformat(timespec="seconds")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_sessions (session_token, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, user_id, expires, now_str),
        )
    return token


def get_user_from_session_token(token: str, renew_minutes: int = 15) -> dict | None:
    """
    Valida um token de sessão salvo:
    - Se ultrapassar 15 minutos de inatividade (expirado), exclui a sessão e retorna None (logout).
    - Se for válido e o usuário estiver ativo, renova o prazo por mais 15 minutos a partir da última ação,
      com throttling de escrita (evita UPDATE redundante em reruns na mesma janela de 15 segundos).
    """
    if not token or not isinstance(token, str):
        return None

    clean_token = token.strip()
    if not clean_token:
        return None

    now = datetime.now()
    now_str = now.isoformat(timespec="seconds")
    new_expires = (now + timedelta(minutes=renew_minutes)).isoformat(timespec="seconds")
    throttle_threshold = (now + timedelta(minutes=renew_minutes, seconds=-15)).isoformat(timespec="seconds")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.session_token, s.expires_at, u.*
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.session_token = ?
            """,
            (clean_token,),
        ).fetchone()

        if not row:
            return None

        # Se passou de 15 minutos de inatividade, sessão expirada -> limpa expiradas e faz logout
        if row["expires_at"] and row["expires_at"] < now_str:
            conn.execute(
                "DELETE FROM user_sessions WHERE session_token = ? OR (expires_at != '' AND expires_at < ?)",
                (clean_token, now_str),
            )
            return None

        # Se usuário foi desativado pelo administrador
        if row["is_active"] == 0:
            conn.execute("DELETE FROM user_sessions WHERE session_token = ?", (clean_token,))
            return None

        # Renova o prazo da sessão por mais 15 minutos (sessão deslizante com throttling de 15s)
        if not row["expires_at"] or row["expires_at"] <= throttle_threshold:
            conn.execute(
                "UPDATE user_sessions SET expires_at = ? WHERE session_token = ?",
                (new_expires, clean_token),
            )

        user_dict = dict(row)
        user_dict.pop("password_hash", None)
        user_dict.pop("session_token", None)
        user_dict.pop("expires_at", None)
        return user_dict


def delete_user_session(token: str) -> bool:
    """Exclui o token de sessão do banco de dados quando o usuário clica em Sair da Conta."""
    if not token:
        return False
    with get_connection() as conn:
        conn.execute("DELETE FROM user_sessions WHERE session_token = ?", (token.strip(),))
    return True


# =====================================================================
# FUNÇÕES DE PRODUTOS (COM ISOLAMENTO POR PERFIL DE USUÁRIO)
# =====================================================================

def add_product(name: str, url: str, target_price: float, user_id: int = 1) -> int:
    """
    Valida a URL e os dados do produto e cadastra/atualiza exclusivamente no perfil (user_id).
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("O nome do produto é obrigatório.")
    clean_url = validate_product_url(url)
    if float(target_price) <= 0:
        raise ValueError("O preço-alvo deve ser maior que zero.")

    now = datetime.now().isoformat(timespec="seconds")
    store = extract_store_name(clean_url)
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO products (user_id, name, url, store_name, target_price, monitoring_status, created_at)
            VALUES (?, ?, ?, ?, ?, 'ativo', ?)
            ON CONFLICT(user_id, url) DO UPDATE SET
                name = excluded.name,
                store_name = excluded.store_name,
                target_price = excluded.target_price,
                monitoring_status = 'ativo'
            RETURNING id
            """,
            (user_id, clean_name, clean_url, store, float(target_price), now),
        )
        row = cursor.fetchone()
        return row["id"]


def get_user_products(user_id: int) -> list[dict]:
    """Retorna apenas os produtos pertencentes ao usuário informado."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_products(user_id: int | None = None) -> list[dict]:
    """
    Retorna produtos cadastrados.
    Se user_id for especificado, retorna estritamente os produtos daquele usuário.
    Caso contrário, retorna todos (apenas de usuários ativos) para o motor de verificação.
    """
    if user_id is not None:
        return get_user_products(user_id)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   u.username as owner_username,
                   u.display_name as user_name,
                   u.whatsapp_phone,
                   u.whatsapp_apikey,
                   u.notifications_enabled,
                   u.notify_on_any_drop,
                   u.is_active as user_is_active
            FROM products p
            LEFT JOIN users u ON u.id = p.user_id
            WHERE COALESCE(u.is_active, 1) = 1
              AND COALESCE(p.monitoring_status, 'ativo') != 'pausado'
            ORDER BY p.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def update_product_target(product_id: int, user_id: int, new_target_price: float) -> bool:
    """Atualiza o preço-alvo de um produto garantindo que pertença ao usuário logado."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE products
            SET target_price = ?, last_notified_price = NULL
            WHERE id = ? AND user_id = ?
            """,
            (float(new_target_price), product_id, user_id),
        )
        return cursor.rowcount > 0


def delete_product(product_id: int, user_id: int) -> bool:
    """
    Remove um produto do monitoramento garantindo que pertença exclusivamente ao user_id.
    Interrompe imediatamente o monitoramento e apaga o histórico associado.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        ).fetchone()
        if not row:
            return False

        conn.execute("DELETE FROM monitoring_jobs WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM price_history WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM notification_logs WHERE product_id = ?", (product_id,))
        cursor = conn.execute(
            "DELETE FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        )
        return cursor.rowcount > 0


def record_price(product_id: int, price: float) -> None:
    """
    Salva uma nova leitura de preço no histórico com cálculo de variação percentual
    e atualiza previous_price, last_price e last_checked_at no produto.
    """
    now = datetime.now().isoformat(timespec="seconds")
    new_price = float(price)

    with get_connection() as conn:
        prod = conn.execute(
            "SELECT last_price, url, store_name FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        prev_price = float(prod["last_price"]) if prod and prod["last_price"] is not None else None
        store = (prod["store_name"] if prod and prod["store_name"] else extract_store_name(prod["url"])) if prod else ""

        variation_pct = 0.0
        if prev_price and prev_price > 0:
            variation_pct = round(((new_price - prev_price) / prev_price) * 100.0, 2)

        conn.execute(
            """
            INSERT INTO price_history (product_id, price, previous_price, variation_pct, store_name, checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (product_id, new_price, prev_price, variation_pct, store, now),
        )
        conn.execute(
            """
            UPDATE products
            SET previous_price = last_price,
                last_price = ?,
                last_checked_at = ?,
                store_name = ?,
                monitoring_status = 'ativo',
                error_count = 0,
                last_error = ''
            WHERE id = ?
            """,
            (new_price, now, store, product_id),
        )


def record_product_error(product_id: int, error_msg: str) -> None:
    """Registra falha de scraping no produto para visibilidade no painel de controle."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE products
            SET error_count = COALESCE(error_count, 0) + 1,
                last_error = ?,
                last_checked_at = ?,
                monitoring_status = 'erro'
            WHERE id = ?
            """,
            (_sanitize_log_text(error_msg), now, product_id),
        )


def update_last_notified_price(product_id: int, price: float) -> None:
    """Registra o preço pelo qual o usuário foi notificado para evitar alertas repetidos."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE products SET last_notified_price = ? WHERE id = ?",
            (float(price), product_id),
        )


def get_user_price_history(user_id: int, limit: int = 30) -> list[dict]:
    """Retorna o histórico de preços apenas dos produtos do usuário especificado."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT h.id,
                   h.checked_at,
                   p.name as product_name,
                   h.price,
                   h.previous_price,
                   h.variation_pct,
                   h.store_name,
                   p.target_price
            FROM price_history h
            JOIN products p ON p.id = h.product_id
            WHERE p.user_id = ?
            ORDER BY h.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def log_notification(
    user_id: int,
    product_id: int,
    phone: str,
    message: str,
    price: float,
    status: str,
    delivery_status: str = "Enviada",
    provider: str = "whatsapp_direct",
    error_message: str = "",
    is_duplicate: bool = False,
) -> int:
    """Registra o envio da notificação de WhatsApp no histórico com status padronizado."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO notification_logs (
                user_id, product_id, phone, message, price, status,
                delivery_status, provider, error_message, is_duplicate, sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                user_id,
                product_id,
                phone,
                message,
                float(price),
                status,
                delivery_status,
                provider,
                _sanitize_log_text(error_message),
                1 if is_duplicate else 0,
                now,
            ),
        )
        row = cursor.fetchone()
        return row["id"]


def get_user_notifications(user_id: int, limit: int = 20) -> list[dict]:
    """Lista as últimas notificações geradas para o usuário logado."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT n.id,
                   n.sent_at,
                   p.name as product_name,
                   p.url as product_url,
                   n.phone,
                   n.price,
                   n.status,
                   n.delivery_status,
                   n.message
            FROM notification_logs n
            JOIN products p ON p.id = n.product_id
            WHERE n.user_id = ?
            ORDER BY n.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# =====================================================================
# ETAPA 3: DASHBOARD ADMINISTRATIVO (KPIs) E GESTÃO DE USUÁRIOS
# =====================================================================

def admin_get_dashboard_kpis(admin_user_id: int) -> dict:
    """Retorna todos os indicadores globais (KPIs) em tempo real para o Dashboard Admin."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_users = conn.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_active, 1) = 1").fetchone()[0]
        inactive_users = total_users - active_users
        admin_users = conn.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_admin, 0) = 1").fetchone()[0]

        total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        dropped_products = conn.execute(
            """
            SELECT COUNT(*) FROM products
            WHERE last_price IS NOT NULL
              AND (
                  (previous_price IS NOT NULL AND last_price < previous_price)
                  OR last_price <= target_price
              )
            """
        ).fetchone()[0]

        # Calcula maior desconto encontrado (em R$ e em %)
        discount_rows = conn.execute(
            """
            SELECT name, target_price, previous_price, last_price
            FROM products
            WHERE last_price IS NOT NULL
            """
        ).fetchall()
        max_discount_value = 0.0
        max_discount_pct = 0.0
        max_discount_product = "Nenhum"
        for r in discount_rows:
            ref = r["previous_price"] if (r["previous_price"] and r["previous_price"] > r["last_price"]) else r["target_price"]
            if ref and ref > r["last_price"]:
                diff = float(ref) - float(r["last_price"])
                pct = (diff / float(ref)) * 100.0 if float(ref) > 0 else 0.0
                if diff > max_discount_value:
                    max_discount_value = diff
                    max_discount_pct = pct
                    max_discount_product = r["name"]

        sent_notifications = conn.execute(
            "SELECT COUNT(*) FROM notification_logs WHERE COALESCE(delivery_status, 'Enviada') IN ('Enviada', 'Entregue')"
        ).fetchone()[0]
        failed_notifications = conn.execute(
            "SELECT COUNT(*) FROM notification_logs WHERE COALESCE(delivery_status, '') = 'Falha'"
        ).fetchone()[0]
        pending_notifications = conn.execute(
            "SELECT COUNT(*) FROM notification_logs WHERE COALESCE(delivery_status, '') = 'Pendente'"
        ).fetchone()[0]

        paused_row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'monitoring_paused'"
        ).fetchone()
        is_paused = bool(paused_row and paused_row["value"] == "1")

        last_run_row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'last_global_run'"
        ).fetchone()
        last_global_run = last_run_row["value"] if last_run_row and last_run_row["value"] else "Nunca executado"

        active_jobs = conn.execute(
            "SELECT COUNT(*) FROM monitoring_jobs WHERE status IN ('aguardando', 'processando')"
        ).fetchone()[0]

        return {
            "total_users": total_users,
            "active_users": active_users,
            "inactive_users": inactive_users,
            "admin_users": admin_users,
            "total_products": total_products,
            "dropped_products": dropped_products,
            "max_discount_value": round(max_discount_value, 2),
            "max_discount_pct": round(max_discount_pct, 1),
            "max_discount_product": max_discount_product,
            "sent_notifications": sent_notifications,
            "failed_notifications": failed_notifications,
            "pending_notifications": pending_notifications,
            "system_status": "⏸️ Pausado" if is_paused else ("⚡ Processando Fila" if active_jobs > 0 else "🟢 Operacional"),
            "is_paused": is_paused,
            "last_global_run": last_global_run,
            "active_jobs": active_jobs,
        }


def admin_list_users(admin_user_id: int) -> list[dict]:
    """Lista todos os usuários com contagem de produtos e status da conta."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id,
                   u.username,
                   u.display_name,
                   u.whatsapp_phone,
                   u.notifications_enabled,
                   u.notify_on_any_drop,
                   COALESCE(u.is_admin, 0) as is_admin,
                   COALESCE(u.is_active, 1) as is_active,
                   u.created_at,
                   COUNT(p.id) as products_count
            FROM users u
            LEFT JOIN products p ON p.user_id = u.id
            GROUP BY u.id
            ORDER BY u.id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def admin_set_user_active(admin_user_id: int, target_user_id: int, is_active: bool) -> bool:
    """Ativa ou desativa a conta de um usuário (impedindo desativar a si próprio) e revoga sessões."""
    require_admin(admin_user_id)
    if admin_user_id == target_user_id and not is_active:
        raise ValueError("Você não pode desativar a sua própria conta de administrador.")

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, target_user_id),
        )
        changed = cursor.rowcount > 0
        if changed and not is_active:
            conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (target_user_id,))

    if changed:
        action_name = "ATIVAR_USUARIO" if is_active else "DESATIVAR_USUARIO"
        record_audit_log(
            admin_user_id,
            action=action_name,
            details=f"Conta do usuário ID {target_user_id} alterada para {'Ativa' if is_active else 'Desativada'}.",
            target_user_id=target_user_id,
        )
    return changed


def admin_set_user_admin(admin_user_id: int, target_user_id: int, make_admin: bool) -> bool:
    """Promove ou remove permissão de administrador de outro usuário."""
    require_admin(admin_user_id)
    if admin_user_id == target_user_id and not make_admin:
        raise ValueError("Você não pode remover sua própria permissão de administrador.")

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET is_admin = ? WHERE id = ?",
            (1 if make_admin else 0, target_user_id),
        )
        changed = cursor.rowcount > 0

    if changed:
        action_name = "PROMOVER_ADMIN" if make_admin else "REVOGAR_ADMIN"
        record_audit_log(
            admin_user_id,
            action=action_name,
            details=f"Permissão de admin do usuário ID {target_user_id} definida como {make_admin}.",
            target_user_id=target_user_id,
        )
    return changed


def admin_reset_user_password(admin_user_id: int, target_user_id: int, new_password: str) -> bool:
    """Redefine de forma segura a senha de um usuário com hash PBKDF2 e validação padronizada."""
    require_admin(admin_user_id)
    valid_password = validate_password_strength(new_password)

    hashed = _hash_password(valid_password)
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hashed, target_user_id),
        )
        changed = cursor.rowcount > 0
        if changed:
            u_row = conn.execute("SELECT username FROM users WHERE id = ?", (target_user_id,)).fetchone()
            if u_row:
                conn.execute("DELETE FROM login_attempts WHERE username = ?", (u_row["username"].lower(),))
            conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (target_user_id,))

    if changed:
        record_audit_log(
            admin_user_id,
            action="RESETAR_SENHA",
            details=f"Senha redefinida pelo administrador para o usuário ID {target_user_id}.",
            target_user_id=target_user_id,
        )
    return changed


def admin_delete_user(admin_user_id: int, target_user_id: int) -> bool:
    """
    Exclui um usuário e todos os seus produtos, históricos, jobs e notificações,
    respeitando a integridade referencial do banco.
    """
    require_admin(admin_user_id)
    if admin_user_id == target_user_id:
        raise ValueError("Você não pode excluir sua própria conta de administrador ativa.")

    with get_connection() as conn:
        target = conn.execute("SELECT username FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if not target:
            return False

        # Remove dados dependentes em cascata controlada
        prod_ids = [
            r["id"]
            for r in conn.execute("SELECT id FROM products WHERE user_id = ?", (target_user_id,)).fetchall()
        ]
        for pid in prod_ids:
            conn.execute("DELETE FROM monitoring_jobs WHERE product_id = ?", (pid,))
            conn.execute("DELETE FROM price_history WHERE product_id = ?", (pid,))
            conn.execute("DELETE FROM notification_logs WHERE product_id = ?", (pid,))

        conn.execute("DELETE FROM monitoring_jobs WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM notification_logs WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM products WHERE user_id = ?", (target_user_id,))
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (target_user_id,))
        deleted = cursor.rowcount > 0

    if deleted:
        record_audit_log(
            admin_user_id,
            action="EXCLUIR_USUARIO",
            details=f"Usuário '{target['username']}' (ID {target_user_id}) e {len(prod_ids)} produto(s) excluídos.",
            target_user_id=target_user_id,
        )
    return deleted


# =====================================================================
# ETAPA 4: GERENCIAMENTO GLOBAL DE PRODUTOS E DETECÇÃO DE DUPLICADOS
# =====================================================================

def admin_list_all_products(
    admin_user_id: int,
    user_filter: int | None = None,
    store_filter: str = "",
    status_filter: str = "",
    search_query: str = "",
    max_price: float | None = None,
) -> list[dict]:
    """
    Retorna todos os produtos de todos os usuários com filtros avançados
    e indicador de URLs duplicadas entre diferentes perfis.
    """
    require_admin(admin_user_id)
    with get_connection() as conn:
        # Conta quantos usuários monitoram cada URL para identificar duplicados
        dup_rows = conn.execute(
            "SELECT lower(trim(url)) as norm_url, COUNT(*) as cnt FROM products GROUP BY lower(trim(url))"
        ).fetchall()
        url_counts = {r["norm_url"]: r["cnt"] for r in dup_rows}

        query = """
            SELECT p.*,
                   u.username as owner_username,
                   u.display_name as owner_name,
                   u.whatsapp_phone as owner_phone
            FROM products p
            LEFT JOIN users u ON u.id = p.user_id
            WHERE 1=1
        """
        params: list = []

        if user_filter is not None:
            query += " AND p.user_id = ?"
            params.append(user_filter)
        if store_filter and store_filter != "Todas":
            query += " AND p.store_name = ?"
            params.append(store_filter)
        if status_filter and status_filter != "Todos":
            query += " AND COALESCE(p.monitoring_status, 'ativo') = ?"
            params.append(status_filter)
        if max_price is not None and max_price > 0:
            query += " AND COALESCE(p.last_price, p.target_price) <= ?"
            params.append(float(max_price))
        if search_query.strip():
            q = f"%{search_query.strip().lower()}%"
            query += " AND (lower(p.name) LIKE ? OR lower(p.url) LIKE ?)"
            params.extend([q, q])

        query += " ORDER BY p.id DESC"
        rows = conn.execute(query, params).fetchall()

        results = []
        for r in rows:
            item = dict(r)
            norm_u = (item.get("url") or "").strip().lower()
            item["duplicate_count"] = url_counts.get(norm_u, 1)
            item["is_duplicate"] = item["duplicate_count"] > 1
            if not item.get("store_name"):
                item["store_name"] = extract_store_name(item.get("url", ""))
            results.append(item)
        return results


def admin_toggle_product_status(admin_user_id: int, product_id: int, new_status: str) -> bool:
    """Permite ao administrador pausar ou reativar o monitoramento de um produto específico."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE products SET monitoring_status = ? WHERE id = ?",
            (new_status, product_id),
        )
        changed = cursor.rowcount > 0
    if changed:
        record_audit_log(
            admin_user_id,
            action="ALTERAR_STATUS_PRODUTO",
            details=f"Produto ID {product_id} alterado para status '{new_status}'.",
        )
    return changed


# =====================================================================
# ETAPA 5: CONTROLE GLOBAL DE MONITORAMENTO E FILA DE TAREFAS (JOB QUEUE)
# =====================================================================

def get_system_settings() -> dict:
    """Retorna todas as configurações globais do sistema."""
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def admin_update_system_settings(admin_user_id: int, updates: dict) -> dict:
    """Atualiza configurações globais do sistema e registra auditoria."""
    require_admin(admin_user_id)
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        for k, v in updates.items():
            conn.execute(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(k), str(v), now),
            )
    record_audit_log(
        admin_user_id,
        action="ATUALIZAR_CONFIGURACOES",
        details=f"Configurações atualizadas: {json.dumps(updates, ensure_ascii=False)}",
    )
    return get_system_settings()


def recover_stuck_jobs(timeout_minutes: int = 10) -> int:
    """
    Recupera tarefas órfãs que ficaram presas no status 'processando' há mais de timeout_minutes
    (ex: por reinício do processo ou queda de conexão), recolocando-as em 'aguardando'.
    """
    cutoff = (datetime.now() - timedelta(minutes=max(timeout_minutes, 1))).isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE monitoring_jobs
            SET status = 'aguardando'
            WHERE status = 'processando'
              AND (started_at IS NULL OR started_at = '' OR started_at < ?)
            """,
            (cutoff,),
        )
        return cursor.rowcount


def claim_next_monitoring_job() -> dict | None:
    """
    Reivindica atomicamente o próximo job com status 'aguardando' na fila usando transação imediata,
    evitando race conditions entre workers.
    """
    now_start = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        job_row = conn.execute(
            """
            SELECT j.*, p.name, p.url, p.target_price, p.last_price, p.last_notified_price
            FROM monitoring_jobs j
            JOIN products p ON p.id = j.product_id
            WHERE j.status = 'aguardando'
            ORDER BY j.id ASC
            LIMIT 1
            """
        ).fetchone()
        if not job_row:
            conn.execute("COMMIT")
            return None

        job = dict(job_row)
        cursor = conn.execute(
            """
            UPDATE monitoring_jobs
            SET status = 'processando', started_at = ?
            WHERE id = ? AND status = 'aguardando'
            """,
            (now_start, job["id"]),
        )
        conn.execute("COMMIT")
        if cursor.rowcount == 0:
            return None
        job["started_at"] = now_start
        return job
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def admin_enqueue_global_verification(admin_user_id: int) -> int:
    """
    Adiciona todos os produtos ativos à fila de processamento (Job Queue),
    recuperando antes tarefas órfãs e evitando enfileirar duplicadas para o mesmo product_id.
    """
    require_admin(admin_user_id)
    recover_stuck_jobs(timeout_minutes=10)
    now = datetime.now().isoformat(timespec="seconds")
    settings = get_system_settings()
    max_retries = int(settings.get("max_retries", "3") or 3)

    with get_connection() as conn:
        products = conn.execute(
            """
            SELECT p.id, p.user_id
            FROM products p
            JOIN users u ON u.id = p.user_id
            WHERE COALESCE(u.is_active, 1) = 1
              AND COALESCE(p.monitoring_status, 'ativo') != 'pausado'
            """
        ).fetchall()

        enqueued = 0
        for p in products:
            existing = conn.execute(
                """
                SELECT id FROM monitoring_jobs
                WHERE product_id = ? AND status IN ('aguardando', 'processando')
                """,
                (p["id"],),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO monitoring_jobs (product_id, user_id, status, attempts, max_attempts, created_at)
                    VALUES (?, ?, 'aguardando', 0, ?, ?)
                    """,
                    (p["id"], p["user_id"], max_retries, now),
                )
                enqueued += 1

    record_audit_log(
        admin_user_id,
        action="DISPARAR_FILA_GLOBAL",
        details=f"Verificação global acionada: {enqueued} produto(s) adicionado(s) à fila.",
    )
    return enqueued


def admin_get_queue_summary(admin_user_id: int, limit: int = 50) -> dict:
    """Retorna contadores e lista detalhada de jobs na fila de monitoramento."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        counts = {
            "aguardando": 0,
            "processando": 0,
            "concluido": 0,
            "falha": 0,
        }
        for r in conn.execute("SELECT status, COUNT(*) as c FROM monitoring_jobs GROUP BY status").fetchall():
            counts[r["status"]] = r["c"]

        jobs = conn.execute(
            """
            SELECT j.*,
                   p.name as product_name,
                   p.url as product_url,
                   u.display_name as user_name
            FROM monitoring_jobs j
            LEFT JOIN products p ON p.id = j.product_id
            LEFT JOIN users u ON u.id = j.user_id
            ORDER BY j.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {
            "counts": counts,
            "jobs": [dict(r) for r in jobs],
        }


def admin_clear_finished_jobs(admin_user_id: int) -> int:
    """Limpa jobs concluídos/falhos antigos do histórico da fila."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM monitoring_jobs WHERE status IN ('concluido', 'falha')")
        return cursor.rowcount


# =====================================================================
# ETAPA 6: HISTÓRICO DE PREÇOS E ANÁLISES GLOBAIS
# =====================================================================

def admin_get_price_analytics(admin_user_id: int, product_id: int | None = None, limit: int = 100) -> dict:
    """
    Retorna análises completas de histórico de preços (menor/maior preço histórico,
    variação percentual, quantidade de alterações e produtos com maiores quedas).
    """
    require_admin(admin_user_id)
    with get_connection() as conn:
        params: list = []
        where_clause = ""
        if product_id is not None:
            where_clause = "WHERE h.product_id = ?"
            params.append(product_id)

        history_rows = conn.execute(
            f"""
            SELECT h.id,
                   h.product_id,
                   p.name as product_name,
                   u.display_name as user_name,
                   COALESCE(h.store_name, p.store_name) as store_name,
                   h.price,
                   h.previous_price,
                   h.variation_pct,
                   p.target_price,
                   h.checked_at
            FROM price_history h
            JOIN products p ON p.id = h.product_id
            LEFT JOIN users u ON u.id = p.user_id
            {where_clause}
            ORDER BY h.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

        stats_rows = conn.execute(
            f"""
            SELECT p.id as product_id,
                   p.name as product_name,
                   p.store_name,
                   MIN(h.price) as min_price,
                   MAX(h.price) as max_price,
                   COUNT(h.id) as readings_count,
                   SUM(CASE WHEN h.previous_price IS NOT NULL AND h.price != h.previous_price THEN 1 ELSE 0 END) as changes_detected,
                   p.last_price,
                   p.target_price
            FROM products p
            LEFT JOIN price_history h ON h.product_id = p.id
            {"WHERE p.id = ?" if product_id is not None else ""}
            GROUP BY p.id
            ORDER BY p.id DESC
            """,
            (product_id,) if product_id is not None else (),
        ).fetchall()

        product_stats = []
        biggest_drops = []
        for s in stats_rows:
            item = dict(s)
            min_p = item.get("min_price")
            max_p = item.get("max_price")
            last_p = item.get("last_price")
            if max_p and max_p > 0 and last_p is not None:
                drop_pct = round(((float(last_p) - float(max_p)) / float(max_p)) * 100.0, 2)
                drop_val = round(float(max_p) - float(last_p), 2)
            else:
                drop_pct = 0.0
                drop_val = 0.0
            item["overall_variation_pct"] = drop_pct
            item["drop_from_max"] = drop_val
            product_stats.append(item)
            if drop_val > 0:
                biggest_drops.append(item)

        biggest_drops.sort(key=lambda x: x["drop_from_max"], reverse=True)

        return {
            "history": [dict(r) for r in history_rows],
            "product_stats": product_stats,
            "biggest_drops": biggest_drops[:10],
        }


# =====================================================================
# ETAPA 7: CENTRAL DE NOTIFICAÇÕES WHATSAPP
# =====================================================================

def admin_list_notifications(
    admin_user_id: int,
    status_filter: str = "",
    user_filter: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """Lista todas as notificações WhatsApp com validação de número e identificação de duplicadas."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        query = """
            SELECT n.*,
                   u.display_name as user_name,
                   u.username as username,
                   p.name as product_name,
                   p.url as product_url,
                   p.target_price
            FROM notification_logs n
            LEFT JOIN users u ON u.id = n.user_id
            LEFT JOIN products p ON p.id = n.product_id
            WHERE 1=1
        """
        params: list = []
        if status_filter and status_filter != "Todos":
            query += " AND COALESCE(n.delivery_status, 'Enviada') = ?"
            params.append(status_filter)
        if user_filter is not None:
            query += " AND n.user_id = ?"
            params.append(user_filter)

        query += " ORDER BY n.id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            item = dict(r)
            item["phone_valid"] = is_valid_whatsapp_phone(item.get("phone", ""))
            results.append(item)
        return results


def admin_update_notification_status(
    admin_user_id: int,
    notification_id: int,
    delivery_status: str,
    error_message: str = "",
    increment_retry: bool = False,
) -> bool:
    """Atualiza o status de uma notificação após tentativa de reenvio."""
    require_admin(admin_user_id)
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        retry_sql = ", retry_count = COALESCE(retry_count, 0) + 1" if increment_retry else ""
        cursor = conn.execute(
            f"""
            UPDATE notification_logs
            SET delivery_status = ?,
                error_message = ?,
                sent_at = ?
                {retry_sql}
            WHERE id = ?
            """,
            (delivery_status, _sanitize_log_text(error_message), now, notification_id),
        )
        return cursor.rowcount > 0


# =====================================================================
# ETAPA 8: LOGS DO SISTEMA, AUDITORIA E DIAGNÓSTICO
# =====================================================================

def admin_get_system_logs(
    admin_user_id: int,
    event_type: str = "",
    status_filter: str = "",
    user_filter: int | None = None,
    date_filter: str = "",
    limit: int = 100,
) -> list[dict]:
    """Consulta logs técnicos do sistema com filtros por data, usuário, tipo e status."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        query = """
            SELECT l.*,
                   u.username as username,
                   p.name as product_name
            FROM system_logs l
            LEFT JOIN users u ON u.id = l.user_id
            LEFT JOIN products p ON p.id = l.product_id
            WHERE 1=1
        """
        params: list = []
        if event_type and event_type != "Todos":
            query += " AND l.event_type = ?"
            params.append(event_type)
        if status_filter and status_filter != "Todos":
            query += " AND l.status = ?"
            params.append(status_filter)
        if user_filter is not None:
            query += " AND l.user_id = ?"
            params.append(user_filter)
        if date_filter.strip():
            query += " AND l.created_at LIKE ?"
            params.append(f"{date_filter.strip()}%")

        query += " ORDER BY l.id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def admin_get_audit_logs(
    admin_user_id: int,
    action_filter: str = "",
    date_filter: str = "",
    limit: int = 100,
) -> list[dict]:
    """Consulta trilha de auditoria das ações administrativas."""
    require_admin(admin_user_id)
    with get_connection() as conn:
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params: list = []
        if action_filter and action_filter != "Todas":
            query += " AND action = ?"
            params.append(action_filter)
        if date_filter.strip():
            query += " AND created_at LIKE ?"
            params.append(f"{date_filter.strip()}%")
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# =====================================================================
# ETAPA 9: BACKUP, RESTAURAÇÃO SEGURA E EXPORTAÇÃO DE DADOS (CSV / JSON)
# =====================================================================

def admin_create_backup(admin_user_id: int) -> dict:
    """Gera um backup consistente do banco SQLite usando a API nativa sqlite3.backup."""
    require_admin(admin_user_id)
    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"prices_backup_{timestamp}.db"
    backup_path = BACKUP_DIR / backup_filename
    now_iso = datetime.now().isoformat(timespec="seconds")

    with get_connection() as src_conn:
        with sqlite3.connect(backup_path) as dst_conn:
            src_conn.backup(dst_conn)

    admin_update_system_settings(
        admin_user_id,
        {
            "last_backup_at": now_iso,
            "last_backup_file": backup_filename,
        },
    )
    record_audit_log(
        admin_user_id,
        action="GERAR_BACKUP",
        details=f"Backup gerado com sucesso: {backup_filename}",
    )
    return {
        "filename": backup_filename,
        "path": str(backup_path),
        "created_at": now_iso,
        "size_bytes": backup_path.stat().st_size,
    }


def admin_list_backups(admin_user_id: int) -> list[dict]:
    """Lista os arquivos de backup disponíveis no diretório de backups."""
    require_admin(admin_user_id)
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = []
    for f in sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        backups.append(
            {
                "filename": f.name,
                "path": str(f),
                "size_kb": round(f.stat().st_size / 1024.0, 2),
                "modified_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return backups


def admin_restore_backup(admin_user_id: int, backup_filename: str) -> bool:
    """
    Restaura o banco de dados a partir de um arquivo de backup válido,
    criando antes um snapshot de segurança e validando a integridade SQLite.
    """
    require_admin(admin_user_id)
    target_backup = (BACKUP_DIR / Path(backup_filename).name).resolve()
    if not target_backup.exists() or target_backup.parent != BACKUP_DIR.resolve():
        raise FileNotFoundError("Arquivo de backup não encontrado ou inválido.")

    # Verifica integridade do arquivo antes de restaurar
    with sqlite3.connect(target_backup) as check_conn:
        integrity = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Arquivo de backup corrompido: {integrity}")

    # Copia o backup validado para o banco ativo
    with sqlite3.connect(target_backup) as src_conn:
        with sqlite3.connect(DB_PATH) as dst_conn:
            src_conn.backup(dst_conn)

    init_db()
    record_audit_log(
        admin_user_id,
        action="RESTAURAR_BACKUP",
        details=f"Banco de dados restaurado a partir de: {backup_filename}",
    )
    return True


def admin_export_data(admin_user_id: int, entity: str, fmt: str = "json") -> str:
    """
    Exporta dados da plataforma (users, products, price_history, notifications, logs)
    em formato JSON ou CSV, omitindo hashes de senha e chaves sensíveis.
    """
    require_admin(admin_user_id)
    with get_connection() as conn:
        if entity == "users":
            rows = conn.execute(
                "SELECT id, username, display_name, whatsapp_phone, notifications_enabled, notify_on_any_drop, is_admin, is_active, created_at FROM users"
            ).fetchall()
        elif entity == "products":
            rows = conn.execute(
                "SELECT id, user_id, name, url, store_name, target_price, previous_price, last_price, last_notified_price, monitoring_status, created_at, last_checked_at FROM products"
            ).fetchall()
        elif entity == "price_history":
            rows = conn.execute("SELECT * FROM price_history ORDER BY id DESC LIMIT 1000").fetchall()
        elif entity == "notifications":
            rows = conn.execute(
                "SELECT id, user_id, product_id, phone, price, status, delivery_status, provider, error_message, is_duplicate, retry_count, sent_at FROM notification_logs ORDER BY id DESC LIMIT 1000"
            ).fetchall()
        elif entity == "logs":
            rows = conn.execute("SELECT * FROM system_logs ORDER BY id DESC LIMIT 1000").fetchall()
        else:
            raise ValueError(f"Entidade desconhecida para exportação: {entity}")

        data = [dict(r) for r in rows]

    if fmt.lower() == "csv":
        if not data:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
        return output.getvalue()

    return json.dumps(data, ensure_ascii=False, indent=2)
