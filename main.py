from __future__ import annotations
from database import (
    init_db,
    add_product,
    get_all_products,
    get_user_by_id,
    record_price,
    update_last_notified_price,
    log_notification,
)
from scraper import fetch_product_info
from notifier import send_notification


def check_all_products(user_id: int | None = None) -> list[dict]:
    """
    Percorre os produtos cadastrados (apenas do user_id informado ou de todos os usuários),
    consulta o preço atual, evita notificações duplicadas e envia o alerta via WhatsApp
    para o número configurado no perfil de cada usuário.
    """
    products = get_all_products(user_id=user_id)
    if not products:
        print("Nenhum produto cadastrado para verificação.")
        return []

    print(f"Verificando {len(products)} produto(s)...\n")
    triggered_alerts: list[dict] = []

    for product in products:
        pid = product["id"]
        owner_id = product.get("user_id", 1)
        name = product["name"]
        url = product["url"]
        target = float(product["target_price"])
        previous_price = product.get("last_price")
        last_notified = product.get("last_notified_price")

        # Carrega configurações de WhatsApp do dono do produto
        owner = get_user_by_id(owner_id) or {}
        user_name = owner.get("display_name") or product.get("user_name") or ""
        whatsapp_phone = owner.get("whatsapp_phone") or product.get("whatsapp_phone") or ""
        whatsapp_apikey = owner.get("whatsapp_apikey") or product.get("whatsapp_apikey") or ""
        notifications_enabled = bool(owner.get("notifications_enabled", 1))
        notify_on_any_drop = bool(owner.get("notify_on_any_drop", 1))

        try:
            info = fetch_product_info(url)
            current_price = float(info["price"])

            # Registra o preço no histórico e atualiza last_price do produto
            record_price(pid, current_price)

            print(f"[{name}] Preço atual: R$ {current_price:.2f} | Alvo: R$ {target:.2f}")

            if not notifications_enabled:
                continue

            # Evita mensagens duplicadas se já notificou exatamente por este preço ou menor
            already_notified_same_or_lower = (
                last_notified is not None and current_price >= float(last_notified)
            )
            if already_notified_same_or_lower:
                continue

            # Condição 1: Atingiu ou ficou abaixo do preço alvo definido pelo usuário
            reached_target = current_price <= target

            # Condição 2: Sofreu queda relevante em relação à última leitura (promoção)
            dropped_from_last = (
                notify_on_any_drop
                and previous_price is not None
                and current_price < float(previous_price)
            )

            if reached_target or dropped_from_last:
                notif_result = send_notification(
                    product_name=info.get("title") or name,
                    current_price=current_price,
                    target_price=target,
                    url=url,
                    whatsapp_phone=whatsapp_phone,
                    whatsapp_apikey=whatsapp_apikey,
                    previous_price=float(previous_price) if previous_price is not None else None,
                    user_name=user_name,
                )
                update_last_notified_price(pid, current_price)

                log_notification(
                    user_id=owner_id,
                    product_id=pid,
                    phone=whatsapp_phone or "Não informado",
                    message=notif_result.get("message", ""),
                    price=current_price,
                    status=notif_result.get("detail", "Alerta gerado"),
                )

                triggered_alerts.append(
                    {
                        "product_id": pid,
                        "product_name": name,
                        "current_price": current_price,
                        "target_price": target,
                        "whatsapp_phone": whatsapp_phone,
                        "whatsapp_url": notif_result.get("whatsapp_url", ""),
                        "detail": notif_result.get("detail", ""),
                    }
                )

        except Exception as e:
            print(f"[{name}] Erro ao verificar URL ({url}): {e}")

    return triggered_alerts


if __name__ == "__main__":
    # 1. Garante que as tabelas e migrações do banco SQLite existem
    init_db()

    # 2. Se não houver nenhum produto no perfil padrão (id=1), cadastra um exemplo
    if not get_all_products(user_id=1):
        sample_url = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
        add_product(
            name="Livro: A Light in the Attic",
            url=sample_url,
            target_price=55.00,
            user_id=1,
        )

    # 3. Executa a verificação de todos os produtos
    check_all_products()
