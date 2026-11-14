from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import py_compile
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database
import main
import notifier
import scraper


class AuditFixesTestSuite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp_dir.name) / "test_prices.db"
        database.init_db()

    def tearDown(self) -> None:
        database.DB_PATH = self.orig_db_path
        self.tmp_dir.cleanup()

    # =================================================================
    # ETAPA 1 — PROBLEMAS CRÍTICOS
    # =================================================================
    def test_etapa1_fresh_db_init_and_idempotency(self) -> None:
        """Verifica se init_db() cria banco do zero sem erro de coluna duplicada e é idempotente."""
        database.init_db()
        with database.get_connection() as conn:
            tables = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            expected_tables = {
                "users",
                "products",
                "price_history",
                "notification_logs",
                "monitoring_jobs",
                "system_logs",
                "audit_logs",
                "system_settings",
                "recovery_codes",
                "user_sessions",
                "login_attempts",
            }
            self.assertTrue(expected_tables.issubset(tables))

    def test_etapa1_legacy_db_migration_compatibility(self) -> None:
        """Verifica se init_db() migra corretamente um banco legado sem quebrar dados existentes."""
        legacy_path = Path(self.tmp_dir.name) / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.execute(
                "CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, target_price REAL)"
            )
            conn.execute(
                "INSERT INTO products (name, url, target_price) VALUES ('Item Legado', 'https://loja.com.br/p1', 99.9)"
            )
            conn.commit()
        finally:
            conn.close()

        database.DB_PATH = legacy_path
        database.init_db()

        prods = database.get_all_products()
        self.assertEqual(len(prods), 1)
        self.assertEqual(prods[0]["name"], "Item Legado")
        self.assertEqual(prods[0]["store_name"], "loja.com.br")

    def test_etapa1_no_privilege_escalation_by_username(self) -> None:
        """Garante que criar conta com username reservado ou similar jamais concede is_admin=1."""
        u1 = database.create_user("paulo", "123456", "Paulo Comum", "21999991111")
        self.assertEqual(u1["is_admin"], 0)
        self.assertFalse(database.is_user_admin(u1["id"]))

        # Mesmo após rodar init_db() novamente, 'paulo' continua sendo usuário comum (is_admin=0)
        database.init_db()
        self.assertFalse(database.is_user_admin(u1["id"]))

    # =================================================================
    # ETAPA 2 — SEGURANÇA
    # =================================================================
    def test_etapa2_login_rate_limiting_and_lockout(self) -> None:
        """Verifica bloqueio temporário após 5 tentativas de login inválidas."""
        database.create_user("maria", "senha123", "Maria", "11988887777")
        for _ in range(4):
            self.assertIsNone(database.authenticate_user("maria", "errada"))

        with self.assertRaises(PermissionError):
            database.authenticate_user("maria", "errada")

        # Mesmo informando a senha correta durante o bloqueio, deve lançar PermissionError
        with self.assertRaises(PermissionError):
            database.authenticate_user("maria", "senha123")

    def test_etapa2_standardized_password_validation(self) -> None:
        """Verifica padronização do mínimo de 6 caracteres em cadastro, reset e painel admin."""
        for short_pwd in ["", "123", "12345", "     "]:
            with self.assertRaises(ValueError):
                database.validate_password_strength(short_pwd)
            with self.assertRaises(ValueError):
                database.create_user("user_short", short_pwd, "Short", "11977776666")

        u = database.create_user("carlos", "123456", "Carlos", "11966665555")
        with database.get_connection() as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()["id"]

        with self.assertRaises(ValueError):
            database.admin_reset_user_password(admin_id, u["id"], "12345")

    def test_etapa2_recovery_code_not_exposed_in_url_and_brute_force_protected(self) -> None:
        """Garante que o código de recuperação nunca vai para URLs wa.me e invalida após 5 erros."""
        u = database.create_user("ana", "123456", "Ana", "21988884444")
        rec = database.create_recovery_token("ana", confirm_phone="4444")
        self.assertIn("***", rec["masked_phone"])

        notif_res = notifier.send_recovery_code_whatsapp(rec)
        self.assertEqual(notif_res["whatsapp_url"], "")

        for _ in range(5):
            with self.assertRaises(ValueError):
                database.reset_password_with_token(u["id"], "000000", "nova1234")

        # Na 6ª tentativa, mesmo com o código correto, deve falhar pois foi invalidado
        with self.assertRaises(ValueError):
            database.reset_password_with_token(u["id"], rec["code"], "nova1234")

        # Gerando novo código, consegue redefinir
        rec2 = database.create_recovery_token("ana", confirm_phone="21988884444")
        self.assertTrue(database.reset_password_with_token(u["id"], rec2["code"], "nova1234"))
        self.assertIsNotNone(database.authenticate_user("ana", "nova1234"))

    def test_etapa2_product_url_validation_ssrf_and_xss(self) -> None:
        """Bloqueia URLs sem http/https, javascript:, localhost e IPs privados/loopback."""
        blocked_urls = [
            "",
            "ftp://loja.com/item",
            "javascript:alert(1)",
            "http://localhost:8000/item",
            "http://127.0.0.1/admin",
            "http://192.168.1.10/router",
            "http://10.0.0.1/internal",
            "http://169.254.169.254/latest/meta-data/",
            "https://usuario:senha@loja.com.br/p",
        ]
        for bad in blocked_urls:
            with self.assertRaises(ValueError, msg=f"Deveria bloquear URL: {bad}"):
                database.validate_product_url(bad)
            with self.assertRaises(ValueError):
                database.add_product("Produto Inválido", bad, 100.0, user_id=1)

        valid_url = "https://www.mercadolivre.com.br/produto-teste"
        self.assertEqual(database.validate_product_url(valid_url), valid_url)

    # =================================================================
    # ETAPA 3 — BANCO DE DADOS E DESEMPENHO
    # =================================================================
    def test_etapa3_clean_price_brazilian_and_international_formats(self) -> None:
        """Valida interpretação correta de preços com milhar sem centavos, múltiplos preços e parcelamento."""
        self.assertEqual(scraper.clean_price("R$ 1.899,90"), 1899.90)
        self.assertEqual(scraper.clean_price("R$ 1.299"), 1299.00)
        self.assertEqual(scraper.clean_price("1.299"), 1299.00)
        self.assertEqual(scraper.clean_price("R$ 1.299.000"), 1299000.00)
        self.assertEqual(scraper.clean_price("De R$ 250,00 por R$ 199,90"), 199.90)
        self.assertEqual(scraper.clean_price("R$ 1.999,00 em até 10x de R$ 199,90"), 1999.00)
        self.assertEqual(scraper.clean_price("£51.77"), 51.77)
        self.assertEqual(scraper.clean_price("US$ 1,299.99"), 1299.99)

        for invalid in ["", "R$ 0,00", "0", "-R$ 20,00", "esgotado"]:
            with self.assertRaises(ValueError):
                scraper.clean_price(invalid)

    def test_etapa3_wal_mode_and_indexes(self) -> None:
        """Verifica ativação do WAL mode e criação dos 10 índices recomendados."""
        with database.get_connection() as conn:
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(journal.lower(), "wal")

            indexes = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
            }
            expected = {
                "idx_products_user_id",
                "idx_products_status",
                "idx_products_url",
                "idx_price_history_prod_checked",
                "idx_notif_logs_user_sent",
                "idx_notif_logs_status",
                "idx_jobs_status_prod",
                "idx_sys_logs_created_type",
                "idx_sessions_user_expires",
                "idx_recovery_user_used",
            }
            self.assertTrue(expected.issubset(indexes))

    def test_etapa3_session_cleanup_and_log_retention(self) -> None:
        """Verifica limpeza de sessões/códigos expirados e política de retenção de logs."""
        u = database.create_user("lucas", "123456", "Lucas", "11955554444")
        token = database.create_user_session(u["id"], duration_minutes=15)
        self.assertIsNotNone(database.get_user_from_session_token(token))

        past = (datetime.now() - timedelta(minutes=20)).isoformat(timespec="seconds")
        old_log_date = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")

        with database.get_connection() as conn:
            conn.execute("UPDATE user_sessions SET expires_at = ? WHERE session_token = ?", (past, token))
            conn.execute(
                "INSERT INTO system_logs (created_at, event_type, status, message) VALUES (?, 'scraper', 'erro', 'Antigo')",
                (old_log_date,),
            )

        self.assertIsNone(database.get_user_from_session_token(token))
        res_logs = database.cleanup_old_logs(retention_days=90)
        self.assertEqual(res_logs["system_logs_removed"], 1)

    # =================================================================
    # ETAPA 4 — BACKEND E ESTABILIDADE
    # =================================================================
    def test_etapa4_worker_lock_orphan_recovery_and_deduplication(self) -> None:
        """Verifica lock não-bloqueante do worker, recuperação de jobs órfãos e cache por URL."""
        with database.get_connection() as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()["id"]

        u2 = database.create_user("julia", "123456", "Julia", "11944443333")
        p1 = database.add_product("Notebook", "https://loja.com.br/nb", 3500.0, user_id=admin_id)
        database.add_product("Notebook Julia", "https://loja.com.br/nb", 3600.0, user_id=u2["id"])

        enqueued = database.admin_enqueue_global_verification(admin_id)
        self.assertEqual(enqueued, 2)

        # Simula job preso em 'processando' há 30 minutos
        stuck_time = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        with database.get_connection() as conn:
            conn.execute(
                "UPDATE monitoring_jobs SET status = 'processando', started_at = ? WHERE product_id = ?",
                (stuck_time, p1),
            )

        # Enquanto o lock estiver adquirido, outra chamada não deve bloquear nem duplicar execução
        main._WORKER_LOCK.acquire()
        try:
            busy_res = main.process_job_queue_sync()
            self.assertTrue(busy_res["already_running"])
            self.assertEqual(busy_res["processed"], 0)
        finally:
            main._WORKER_LOCK.release()

        http_calls = 0

        def mock_fetch(url: str) -> dict:
            nonlocal http_calls
            http_calls += 1
            return {
                "title": "Notebook Pro",
                "price": 3299.0,
                "store_name": "Loja",
                "http_status": 200,
                "response_time_ms": 15.0,
            }

        with patch("main.fetch_product_info", side_effect=mock_fetch), patch(
            "main.send_notification",
            return_value={
                "sent": True,
                "method": "whatsapp_direct",
                "detail": "Link gerado",
                "delivery_status": "Pendente",
                "message": "Alerta",
                "whatsapp_url": "https://wa.me/5511944443333",
            },
        ):
            res = main.process_job_queue_sync()
            self.assertFalse(res["already_running"])
            self.assertEqual(res["processed"], 2)
            self.assertEqual(res["succeeded"], 2)
            self.assertEqual(http_calls, 1)

    # =================================================================
    # ETAPA 5 & 7 — PERMISSÕES, ISOLAMENTO E SINTAXE DOS MÓDULOS
    # =================================================================
    def test_etapa5_and_7_user_isolation_admin_permissions_and_syntax(self) -> None:
        """Valida isolamento de produtos entre usuários, bloqueio de acesso não-admin e compilação Python."""
        u1 = database.create_user("user1", "123456", "User 1", "11911112222")
        u2 = database.create_user("user2", "123456", "User 2", "11933334444")

        pid1 = database.add_product("Fone U1", "https://loja.com.br/fone1", 150.0, user_id=u1["id"])
        database.add_product("Mouse U2", "https://loja.com.br/mouse2", 80.0, user_id=u2["id"])

        # Usuário 2 não vê nem consegue alterar/excluir produto do Usuário 1
        self.assertEqual(len(database.get_user_products(u1["id"])), 1)
        self.assertFalse(database.update_product_target(pid1, u2["id"], 10.0))
        self.assertFalse(database.delete_product(pid1, u2["id"]))

        # Usuário comum não consegue executar ações administrativas
        with self.assertRaises(PermissionError):
            database.admin_get_dashboard_kpis(u1["id"])
        with self.assertRaises(PermissionError):
            database.admin_set_user_admin(u1["id"], u1["id"], True)

        # Compilação de sintaxe de todos os arquivos principais do projeto
        project_root = Path(__file__).resolve().parent
        for py_file in ["database.py", "scraper.py", "notifier.py", "main.py", "admin_panel.py", "app.py"]:
            py_compile.compile(
                str(project_root / py_file),
                cfile=str(Path(self.tmp_dir.name) / f"{py_file}c"),
                doraise=True,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
