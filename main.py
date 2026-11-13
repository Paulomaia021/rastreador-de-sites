from database import (
    init_db,
    add_product,
    get_all_products,
    record_price,
    update_last_notified_price,
)
from scraper import fetch_product_info
from notifier import send_notification


def check_all_products() -> None:
    """Percorre todos os produtos do banco, consulta preço atual e dispara alerta se necessário."""
    products = get_all_products()
    if not products:
        print("Nenhum produto cadastrado no banco.")
        return

    print(f"Verificando {len(products)} produto(s)...\n")

    for product in products:
        pid = product["id"]
        name = product["name"]
        url = product["url"]
        target = product["target_price"]
        last_notified = product["last_notified_price"]

        try:
            info = fetch_product_info(url)
            current_price = info["price"]

            # Salva no histórico
            record_price(pid, current_price)

            print(f"[{name}] Preço atual: {current_price:.2f} | Alvo: {target:.2f}")

            # Verifica se atingiu a meta e evita mandar alerta repetido para o mesmo preço
            if current_price <= target and (last_notified is None or current_price < last_notified):
                send_notification(
                    product_name=info["title"] or name,
                    current_price=current_price,
                    target_price=target,
                    url=url,
                )
                update_last_notified_price(pid, current_price)

        except Exception as e:
            print(f"[{name}] Erro ao verificar URL ({url}): {e}")


if __name__ == "__main__":
    # 1. Garante que as tabelas do banco SQLite existem
    init_db()

    # 2. Cadastra um produto de exemplo para você ver funcionando de imediato
    # (O livro custa 51.77 no site de testes; colocamos o alvo em 55.00 para forçar o alerta!)
    sample_url = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    add_product(
        name="Livro: A Light in the Attic",
        url=sample_url,
        target_price=55.00,
    )

    # 3. Executa a verificação
    check_all_products()
