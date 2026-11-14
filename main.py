from __future__ import annotations
from datetime import datetime
import threading
import time
from database import (
    init_db,
    add_product,
    get_all_products,
    get_user_by_id,
    get_connection,
    get_system_settings,
    record_price,
    record_product_error,
    record_system_log,
    update_last_notified_price,
    log_notification,
    admin_enqueue_global_verification,
    recover_stuck_jobs,
    claim_next_monitoring_job,
)
from scraper import fetch_product_info, ScraperError
from notifier import send_notification

_WORKER_LOCK = threading.Lock()
_WORKER_RUNNING = False
_PERMANENT_HTTP_ERRORS = {400, 401, 403, 404, 405, 410, 422}


def is_worker_running() -> bool:
    """Retorna True se o consumidor da fila em segundo plano estiver em execução."""
    return _WORKER_RUNNING or _WORKER_LOCK.locked()


def _is_retryable_scraper_error(exc: Exception) -> bool:
    """
    Evita novas tentativas desnecessárias em erros permanentes (404, 410, URL inválida),
    permitindo retry apenas para falhas transitórias de rede, timeout ou HTTP 429/5xx.
    """
    if isinstance(exc, ScraperError):
        if exc.http_status in _PERMANENT_HTTP_ERRORS:
            return False
        if exc.http_status is None and "URL" in str(exc):
            return False
    if isinstance(exc, ValueError):
        return False
    return True


def _evaluate_and_notify(
    product: dict,
    info: dict,
    current_price: float,
) -> dict | None:
    """
    Avalia se o preço lido deve gerar notificação no WhatsApp do dono do produto,
    prevenindo notificações duplicadas e registrando logs.
    """
    try:
        pid = product["id"]
        owner_id = product.get("user_id", 1)
        name = product["name"]
        url = product["url"]
        target = float(product["target_price"])
        previous_price = product.get("last_price")
        last_notified = product.get("last_notified_price")

        owner = get_user_by_id(owner_id) or {}
        if owner.get("is_active", 1) == 0:
            return None

        user_name = owner.get("display_name") or product.get("user_name") or ""
        whatsapp_phone = owner.get("whatsapp_phone") or product.get("whatsapp_phone") or ""
        whatsapp_apikey = owner.get("whatsapp_apikey") or product.get("whatsapp_apikey") or ""
        notifications_enabled = bool(owner.get("notifications_enabled", 1))
        notify_on_any_drop = bool(owner.get("notify_on_any_drop", 1))

        if not notifications_enabled:
            return None

        # Evita mensagens duplicadas se já notificou exatamente por este preço ou menor
        already_notified_same_or_lower = (
            last_notified is not None and current_price >= float(last_notified)
        )
        if already_notified_same_or_lower:
            return None

        reached_target = current_price <= target
        dropped_from_last = (
            notify_on_any_drop
            and previous_price is not None
            and current_price < float(previous_price)
        )

        if not (reached_target or dropped_from_last):
            return None

        notif_result = send_notification(
            product_name=info.get("title") or name,
            current_price=current_price,
            target_price=target,
            url=url,
            whatsapp_phone=whatsapp_phone,
            whatsapp_apikey=whatsapp_apikey,
            previous_price=float(previous_price) if previous_price is not None else None,
            user_name=user_name,
            user_id=owner_id,
            product_id=pid,
        )

        if notif_result.get("delivery_status") != "Falha":
            update_last_notified_price(pid, current_price)

        log_notification(
            user_id=owner_id,
            product_id=pid,
            phone=whatsapp_phone or "Não informado",
            message=notif_result.get("message", ""),
            price=current_price,
            status=notif_result.get("detail", "Alerta gerado"),
            delivery_status=notif_result.get("delivery_status", "Enviada"),
            provider=notif_result.get("method", "whatsapp_direct"),
            error_message=notif_result.get("error_message", ""),
            is_duplicate=False,
        )

        return {
            "product_id": pid,
            "product_name": name,
            "current_price": current_price,
            "target_price": target,
            "whatsapp_phone": whatsapp_phone,
            "whatsapp_url": notif_result.get("whatsapp_url", ""),
            "detail": notif_result.get("detail", ""),
            "delivery_status": notif_result.get("delivery_status", "Enviada"),
        }
    except Exception as exc:
        record_system_log(
            event_type="whatsapp",
            status="erro",
            message=f"Erro ao processar notificação para produto ID {product.get('id')}: {exc}",
            user_id=product.get("user_id", 1),
            product_id=product.get("id"),
        )
        return None


def check_all_products(user_id: int | None = None) -> list[dict]:
    """
    Percorre os produtos cadastrados (apenas do user_id informado ou de todos os usuários),
    consulta o preço atual com cache por URL na mesma rodada para evitar requisições duplicadas,
    registra logs de diagnóstico e envia alertas via WhatsApp.
    """
    settings = get_system_settings()
    if user_id is None and settings.get("monitoring_paused") == "1":
        print("Monitoramento global está pausado nas configurações administrativas.")
        return []

    products = get_all_products(user_id=user_id)
    if not products:
        print("Nenhum produto cadastrado para verificação.")
        return []

    max_retries = max(int(settings.get("max_retries", "3") or 3), 1)
    delay_sec = max(float(settings.get("delay_between_requests_sec", "0.5") or 0.5), 0.0)

    print(f"Verificando {len(products)} produto(s)...\n")
    triggered_alerts: list[dict] = []
    url_cache: dict[str, dict] = {}
    network_requests_made = 0

    for product in products:
        pid = product["id"]
        owner_id = product.get("user_id", 1)
        name = product["name"]
        url = (product["url"] or "").strip()
        target = float(product["target_price"])

        norm_url = url.lower()
        info = url_cache.get(norm_url)
        attempts_used = 1 if info is not None else 0
        last_exc: Exception | None = None

        if info is None:
            # Só aguarda delay quando uma requisição HTTP real será disparada após outra anterior
            if network_requests_made > 0 and delay_sec > 0:
                time.sleep(min(delay_sec, 2.0))
            network_requests_made += 1

            for attempt in range(1, max_retries + 1):
                attempts_used = attempt
                try:
                    info = fetch_product_info(url)
                    url_cache[norm_url] = info
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_retries and _is_retryable_scraper_error(exc):
                        time.sleep(min(0.5 * attempt, 1.5))
                    else:
                        break

        try:
            if info is not None:
                current_price = float(info["price"])
                record_price(pid, current_price)
                record_system_log(
                    event_type="scraper",
                    status="sucesso",
                    message=f"[{name}] Preço lido com sucesso: R$ {current_price:.2f}",
                    user_id=owner_id,
                    product_id=pid,
                    url=url,
                    http_status=info.get("http_status", 200),
                    response_time_ms=info.get("response_time_ms"),
                    attempts=max(attempts_used, 1),
                )
                print(f"[{name}] Preço atual: R$ {current_price:.2f} | Alvo: R$ {target:.2f}")

                alert = _evaluate_and_notify(product, info, current_price)
                if alert:
                    triggered_alerts.append(alert)
            else:
                http_st = getattr(last_exc, "http_status", None) if isinstance(last_exc, ScraperError) else None
                resp_ms = getattr(last_exc, "response_time_ms", None) if isinstance(last_exc, ScraperError) else None
                err_str = str(last_exc) if last_exc else "Erro desconhecido no scraper"
                record_product_error(pid, err_str)
                record_system_log(
                    event_type="scraper",
                    status="erro",
                    message=f"[{name}] Falha após {attempts_used} tentativa(s): {err_str}",
                    user_id=owner_id,
                    product_id=pid,
                    url=url,
                    http_status=http_st,
                    response_time_ms=resp_ms,
                    attempts=attempts_used,
                )
                print(f"[{name}] Erro ao verificar URL ({url}): {err_str}")
        except Exception as unexpected_exc:
            record_system_log(
                event_type="scraper",
                status="erro",
                message=f"[{name}] Erro interno ao salvar resultado: {unexpected_exc}",
                user_id=owner_id,
                product_id=pid,
                url=url,
            )

    return triggered_alerts


def process_job_queue_sync(max_jobs: int | None = None) -> dict:
    """
    Processa gradualmente os itens da fila (monitoring_jobs) com lock não-bloqueante contra
    race conditions, recuperação de jobs órfãos e deduplicação de URLs na mesma rodada.
    """
    global _WORKER_RUNNING
    if not _WORKER_LOCK.acquire(blocking=False):
        return {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "already_running": True,
        }

    _WORKER_RUNNING = True
    processed = 0
    succeeded = 0
    failed = 0
    url_cache: dict[str, dict] = {}
    network_requests_made = 0

    try:
        recover_stuck_jobs(timeout_minutes=10)
        settings = get_system_settings()
        delay_sec = max(float(settings.get("delay_between_requests_sec", "1.0") or 1.0), 0.0)

        while True:
            if max_jobs is not None and processed >= max_jobs:
                break

            # Reconsulta configurações a cada 5 jobs para respeitar pausa global imediata
            if processed > 0 and processed % 5 == 0:
                settings = get_system_settings()
                delay_sec = max(float(settings.get("delay_between_requests_sec", "1.0") or 1.0), 0.0)

            if settings.get("monitoring_paused") == "1":
                break

            job = claim_next_monitoring_job()
            if not job:
                break

            pid = job["product_id"]
            uid = job["user_id"]
            url = (job["url"] or "").strip()
            norm_url = url.lower()
            max_attempts = max(int(job.get("max_attempts") or 3), 1)

            info = url_cache.get(norm_url)
            attempts_used = int(job.get("attempts") or 0)
            last_exc: Exception | None = None

            if info is None:
                if network_requests_made > 0 and delay_sec > 0:
                    time.sleep(min(delay_sec, 3.0))
                network_requests_made += 1

                while attempts_used < max_attempts:
                    attempts_used += 1
                    try:
                        info = fetch_product_info(url)
                        url_cache[norm_url] = info
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                        if attempts_used < max_attempts and _is_retryable_scraper_error(exc):
                            time.sleep(min(0.5 * attempts_used, 1.5))
                        else:
                            break
            else:
                attempts_used = max(attempts_used, 1)

            now_end = datetime.now().isoformat(timespec="seconds")

            try:
                if info is not None:
                    price = float(info["price"])
                    http_st = info.get("http_status", 200)
                    resp_ms = info.get("response_time_ms", 0.0)

                    record_price(pid, price)
                    with get_connection() as conn:
                        conn.execute(
                            """
                            UPDATE monitoring_jobs
                            SET status = 'concluido',
                                attempts = ?,
                                result_price = ?,
                                http_status = ?,
                                response_time_ms = ?,
                                error_message = '',
                                finished_at = ?
                            WHERE id = ?
                            """,
                            (attempts_used, price, http_st, resp_ms, now_end, job["id"]),
                        )

                    record_system_log(
                        event_type="queue",
                        status="sucesso",
                        message=f"Job #{job['id']} concluído ({job['name']}): R$ {price:.2f}",
                        user_id=uid,
                        product_id=pid,
                        url=url,
                        http_status=http_st,
                        response_time_ms=resp_ms,
                        attempts=attempts_used,
                    )
                    _evaluate_and_notify(job, info, price)
                    succeeded += 1
                else:
                    http_st = getattr(last_exc, "http_status", None) if isinstance(last_exc, ScraperError) else None
                    resp_ms = getattr(last_exc, "response_time_ms", None) if isinstance(last_exc, ScraperError) else None
                    err_str = str(last_exc) if last_exc else "Falha ao processar URL"

                    record_product_error(pid, err_str)
                    with get_connection() as conn:
                        conn.execute(
                            """
                            UPDATE monitoring_jobs
                            SET status = 'falha',
                                attempts = ?,
                                http_status = ?,
                                response_time_ms = ?,
                                error_message = ?,
                                finished_at = ?
                            WHERE id = ?
                            """,
                            (attempts_used, http_st, resp_ms, err_str[:500], now_end, job["id"]),
                        )
                    record_system_log(
                        event_type="queue",
                        status="erro",
                        message=f"Job #{job['id']} falhou após {attempts_used} tentativa(s): {err_str}",
                        user_id=uid,
                        product_id=pid,
                        url=url,
                        http_status=http_st,
                        response_time_ms=resp_ms,
                        attempts=attempts_used,
                    )
                    failed += 1
            except Exception as job_exc:
                failed += 1
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE monitoring_jobs SET status = 'falha', error_message = ?, finished_at = ? WHERE id = ?",
                        (str(job_exc)[:500], now_end, job["id"]),
                    )

            processed += 1

        now_run = datetime.now().isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES ('last_global_run', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (now_run, now_run),
            )
    finally:
        _WORKER_RUNNING = False
        _WORKER_LOCK.release()

    return {
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "already_running": False,
    }


def trigger_background_queue_worker(admin_user_id: int) -> dict:
    """
    Adiciona os produtos ativos à fila e inicia o processamento em uma thread
    em segundo plano sem bloquear a interface do usuário e sem duplicar workers.
    """
    enqueued = admin_enqueue_global_verification(admin_user_id)
    if is_worker_running():
        return {
            "enqueued": enqueued,
            "worker_started": False,
        }
    worker_thread = threading.Thread(target=process_job_queue_sync, daemon=True)
    worker_thread.start()
    return {
        "enqueued": enqueued,
        "worker_started": True,
    }


if __name__ == "__main__":
    init_db()
    if not get_all_products(user_id=1):
        sample_url = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
        add_product(
            name="Livro: A Light in the Attic",
            url=sample_url,
            target_price=55.00,
            user_id=1,
        )
    check_all_products()
