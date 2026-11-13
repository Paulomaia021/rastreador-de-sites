from __future__ import annotations
import hashlib
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "prices.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """Gera hash seguro PBKDF2-HMAC-SHA256 para armazenamento de senha."""
    if salt is None:
        salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"{salt.hex()}${pwd_hash.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verifica se a senha informada corresponde ao hash salvo."""
    try:
        salt_hex, hash_hex = stored_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        expected_hash = _hash_password(password, salt)
        return expected_hash == stored_hash
    except Exception:
        return False


def normalize_whatsapp_phone(phone: str) -> str:
    """
    Remove caracteres não numéricos e garante DDI (padrão 55 para números brasileiros de 10 ou 11 dígitos).
    Ex: '(21) 99999-8888' -> '5521999998888'
    """
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = f"55{digits}"
    return digits


def init_db() -> None:
    """
    Cria e migra automaticamente as tabelas de usuários, produtos,
    histórico de preços e log de notificações via WhatsApp.
    """
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        # 1. Tabela de Usuários (Perfis individuais)
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
                created_at TEXT NOT NULL
            )
            """
        )

        # Garante que exista ao menos um usuário padrão caso o script seja rodado via CLI legado
        default_user = conn.execute("SELECT id FROM users WHERE id = 1").fetchone()
        if not default_user:
            conn.execute(
                """
                INSERT INTO users (id, username, display_name, password_hash, whatsapp_phone, created_at)
                VALUES (1, 'admin', 'Administrador', ?, '', ?)
                """,
                (_hash_password("admin123"), now),
            )

        # 2. Verifica se a tabela products já existe e se precisa de migração para multiusuário
        table_info = conn.execute("PRAGMA table_info(products)").fetchall()
        columns = [col["name"] for col in table_info]

        if not table_info:
            # Criação limpa já com isolamento por user_id
            conn.execute(
                """
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    last_price REAL,
                    last_notified_price REAL,
                    created_at TEXT,
                    UNIQUE(user_id, url),
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
                """
            )
        elif "user_id" not in columns:
            # Migra tabela antiga preservando dados e mudando UNIQUE(url) para UNIQUE(user_id, url)
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                """
                CREATE TABLE products_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    last_price REAL,
                    last_notified_price REAL,
                    created_at TEXT,
                    UNIQUE(user_id, url),
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                INSERT INTO products_new (id, user_id, name, url, target_price, last_notified_price, created_at)
                SELECT id, 1, name, url, target_price, last_notified_price, ?
                FROM products
                """,
                (now,),
            )
            conn.execute("DROP TABLE products")
            conn.execute("ALTER TABLE products_new RENAME TO products")
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
        else:
            if "last_price" not in columns:
                conn.execute("ALTER TABLE products ADD COLUMN last_price REAL")
            if "created_at" not in columns:
                conn.execute("ALTER TABLE products ADD COLUMN created_at TEXT")

        # 3. Tabela de Histórico de Preços
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                price REAL NOT NULL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
            )
            """
        )

        # 4. Tabela de Log de Notificações WhatsApp (evita duplicatas e exibe histórico ao usuário)
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
                sent_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
            )
            """
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
    """Cria um novo perfil de usuário com senha criptografada e WhatsApp configurado."""
    clean_username = username.strip().lower()
    if not clean_username or not password:
        raise ValueError("Usuário e senha são obrigatórios.")

    clean_name = display_name.strip() or clean_username
    clean_phone = normalize_whatsapp_phone(whatsapp_phone)
    now = datetime.now().isoformat(timespec="seconds")

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE lower(username) = ?", (clean_username,)
        ).fetchone()
        if existing:
            raise ValueError("Este nome de usuário já está em uso. Escolha outro.")

        cursor = conn.execute(
            """
            INSERT INTO users (
                username, display_name, password_hash, whatsapp_phone,
                whatsapp_apikey, notifications_enabled, notify_on_any_drop, created_at
            )
            VALUES (?, ?, ?, ?, ?, 1, 1, ?)
            RETURNING id, username, display_name, whatsapp_phone, whatsapp_apikey,
                      notifications_enabled, notify_on_any_drop, created_at
            """,
            (
                clean_username,
                clean_name,
                _hash_password(password),
                clean_phone,
                whatsapp_apikey.strip(),
                now,
            ),
        )
        return dict(cursor.fetchone())


def authenticate_user(username: str, password: str) -> dict | None:
    """Autentica um usuário pelo login e senha. Retorna os dados do perfil ou None."""
    clean_username = username.strip().lower()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(username) = ?", (clean_username,)
        ).fetchone()
        if not row:
            return None
        if _verify_password(password, row["password_hash"]):
            user_dict = dict(row)
            user_dict.pop("password_hash", None)
            return user_dict
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
    with get_connection() as conn:
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


# =====================================================================
# FUNÇÕES DE PRODUTOS (COM ISOLAMENTO POR PERFIL DE USUÁRIO)
# =====================================================================

def add_product(name: str, url: str, target_price: float, user_id: int = 1) -> int:
    """
    Cadastra ou atualiza um produto associado exclusivamente ao perfil (user_id).
    """
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO products (user_id, name, url, target_price, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, url) DO UPDATE SET
                name = excluded.name,
                target_price = excluded.target_price
            RETURNING id
            """,
            (user_id, name.strip(), url.strip(), float(target_price), now),
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
    Caso contrário, retorna todos (junto com dados de WhatsApp do dono para o monitor).
    """
    if user_id is not None:
        return get_user_products(user_id)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   u.display_name as user_name,
                   u.whatsapp_phone,
                   u.whatsapp_apikey,
                   u.notifications_enabled,
                   u.notify_on_any_drop
            FROM products p
            LEFT JOIN users u ON u.id = p.user_id
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
        # Verifica se o produto realmente pertence a este usuário
        row = conn.execute(
            "SELECT id FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        ).fetchone()
        if not row:
            return False

        conn.execute("DELETE FROM price_history WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM notification_logs WHERE product_id = ?", (product_id,))
        cursor = conn.execute(
            "DELETE FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        )
        return cursor.rowcount > 0


def record_price(product_id: int, price: float) -> None:
    """Salva uma nova leitura de preço no histórico e atualiza o último preço lido no produto."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO price_history (product_id, price, checked_at) VALUES (?, ?, ?)",
            (product_id, float(price), now),
        )
        conn.execute(
            "UPDATE products SET last_price = ? WHERE id = ?",
            (float(price), product_id),
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
) -> None:
    """Registra o envio da notificação de WhatsApp no histórico do usuário."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO notification_logs (user_id, product_id, phone, message, price, status, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, product_id, phone, message, float(price), status, now),
        )


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
