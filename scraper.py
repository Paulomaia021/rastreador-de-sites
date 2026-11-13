import re
import requests
from bs4 import BeautifulSoup


# Cabeçalhos para simular um navegador real e evitar bloqueios simples
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def clean_price(price_text: str) -> float:
    """
    Transforma textos como 'R$ 1.899,90' ou '£51.77' em float (1899.90 ou 51.77).
    """
    # Remove tudo que não for dígito, ponto ou vírgula
    cleaned = re.sub(r"[^\d.,]", "", price_text.strip())

    if not cleaned:
        raise ValueError(f"Não foi possível extrair números de: '{price_text}'")

    # Caso brasileiro: tem vírgula como separador decimal (ex: 1.899,90)
    if "," in cleaned and "." in cleaned:
        # Se a vírgula vem depois do ponto, formato BR (1.234,56)
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Formato US (1,234.56)
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Apenas vírgula (ex: 89,90) -> vira 89.90
        cleaned = cleaned.replace(",", ".")

    return float(cleaned)


def fetch_product_info(url: str) -> dict:
    """
    Acessa a página do produto e retorna um dicionário com título e preço.
    Suporta Mercado Livre e o site de testes Books to Scrape.
    """
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # 1. Tentativa para Mercado Livre
    if "mercadolivre.com" in url or "mercadolibre.com" in url:
        title_tag = soup.find("h1", class_="ui-pdp-title")
        # No ML, o container de preço principal costuma ficar em ui-pdp-price__second-line
        price_container = soup.find("div", class_="ui-pdp-price__second-line")
        if price_container:
            fraction = price_container.find("span", class_="andes-money-amount__fraction")
            cents = price_container.find("span", class_="andes-money-amount__cents")
        else:
            fraction = soup.find("span", class_="andes-money-amount__fraction")
            cents = soup.find("span", class_="andes-money-amount__cents")

        if title_tag and fraction:
            price_str = fraction.get_text(strip=True)
            if cents:
                price_str = f"{price_str},{cents.get_text(strip=True)}"
            return {
                "title": title_tag.get_text(strip=True),
                "price": clean_price(price_str),
            }

    # 2. Tentativa para Books to Scrape (ótimo site sandbox para testar scraping)
    price_color = soup.find("p", class_="price_color")
    if price_color:
        title_tag = soup.find("h1")
        return {
            "title": title_tag.get_text(strip=True) if title_tag else "Produto Teste",
            "price": clean_price(price_color.get_text(strip=True)),
        }

    # 3. Fallback genérico via meta tags de e-commerce (OpenGraph / Schema.org)
    meta_price = (
        soup.find("meta", property="product:price:amount")
        or soup.find("meta", property="og:price:amount")
        or soup.find("meta", itemprop="price")
    )
    if meta_price and meta_price.get("content"):
        title_tag = soup.find("h1") or soup.find("title")
        return {
            "title": title_tag.get_text(strip=True) if title_tag else "Produto",
            "price": clean_price(meta_price["content"]),
        }

    raise ValueError("Não foi possível localizar o preço na página informada.")


if __name__ == "__main__":
    # URL de teste segura (sandbox oficial para praticar web scraping)
    test_url = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    print(f"Buscando dados em: {test_url}...\n")

    try:
        data = fetch_product_info(test_url)
        print(f"Produto encontrado: {data['title']}")
        print(f"Preço extraído (float): {data['price']:.2f}")
    except Exception as e:
        print(f"Erro ao buscar produto: {e}")
