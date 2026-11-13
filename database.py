import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "prices.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Cria as tabelas de produtos e histórico caso ainda não existam."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                target_price REAL NOT NULL,
                last_notified_price REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                price REAL NOT NULL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
            """
        )


def add_product(name: str, url: str, target_price: float) -> int:
    """Cadastra ou atualiza um produto para monitoramento."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO products (name, url, target_price)
            VALUES (?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                name = excluded.name,
                target_price = excluded.target_price
            RETURNING id
            """,
            (name, url, target_price),
        )
        row = cursor.fetchone()
        return row["id"]


def get_all_products() -> list[dict]:
    """Retorna todos os produtos cadastrados."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM products").fetchall()
        return [dict(r) for r in rows]


def record_price(product_id: int, price: float) -> None:
    """Salva uma nova leitura de preço no histórico."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO price_history (product_id, price, checked_at) VALUES (?, ?, ?)",
            (product_id, price, now),
        )


def update_last_notified_price(product_id: int, price: float) -> None:
    """Registra o preço pelo qual o usuário foi notificado para evitar alertas repetidos."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE products SET last_notified_price = ? WHERE id = ?",
            (price, product_id),
        )
