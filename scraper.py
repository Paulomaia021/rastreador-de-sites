from __future__ import annotations
import re
import time
import requests
from bs4 import BeautifulSoup
from database import extract_store_name


# Cabeçalhos para simular um navegador real e evitar bloqueios simples
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


class ScraperError(Exception):
    """Exceção enriquecida com código HTTP e tempo de resposta para diagnóstico."""

    def __init__(self, message: str, http_status: int | None = None, response_time_ms: float | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.response_time_ms = response_time_ms


def clean_price(price_text: str) -> float:
    """
    Transforma textos como 'R$ 1.899,90' ou '£51.77' em float (1899.90 ou 51.77).
    """
    cleaned = re.sub(r"[^\d.,]", "", price_text.strip())

    if not cleaned:
        raise ValueError(f"Não foi possível extrair números de: '{price_text}'")

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    return float(cleaned)


def fetch_product_info(url: str) -> dict:
    """
    Acessa a página do produto e retorna um dicionário com título, preço,
    loja, código HTTP e tempo de resposta em milissegundos.
    """
    start_ts = time.perf_counter()
    http_status = None
    store_name = extract_store_name(url)

    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        elapsed_ms = round((time.perf_counter() - start_ts) * 1000.0, 2)
        http_status = response.status_code
        response.raise_for_status()
    except requests.RequestException as exc:
        elapsed_ms = round((time.perf_counter() - start_ts) * 1000.0, 2)
        if hasattr(exc, "response") and exc.response is not None:
            http_status = exc.response.status_code
        raise ScraperError(
            f"Falha HTTP ao consultar '{url}': {exc}",
            http_status=http_status,
            response_time_ms=elapsed_ms,
        ) from exc

    soup = BeautifulSoup(response.text, "html.parser")

    # 1. Tentativa para Mercado Livre
    if "mercadolivre.com" in url or "mercadolibre.com" in url:
        title_tag = soup.find("h1", class_="ui-pdp-title")
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
                "store_name": store_name,
                "http_status": http_status,
                "response_time_ms": elapsed_ms,
            }

    # 2. Tentativa para Books to Scrape
    price_color = soup.find("p", class_="price_color")
    if price_color:
        title_tag = soup.find("h1")
        return {
            "title": title_tag.get_text(strip=True) if title_tag else "Produto Teste",
            "price": clean_price(price_color.get_text(strip=True)),
            "store_name": store_name,
            "http_status": http_status,
            "response_time_ms": elapsed_ms,
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
            "store_name": store_name,
            "http_status": http_status,
            "response_time_ms": elapsed_ms,
        }

    raise ScraperError(
        "Não foi possível localizar o elemento de preço na página informada.",
        http_status=http_status,
        response_time_ms=elapsed_ms,
    )


if __name__ == "__main__":
    test_url = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    print(f"Buscando dados em: {test_url}...\n")

    try:
        data = fetch_product_info(test_url)
        print(f"Produto encontrado: {data['title']}")
        print(f"Preço extraído (float): {data['price']:.2f}")
        print(f"HTTP {data['http_status']} em {data['response_time_ms']} ms")
    except Exception as e:
        print(f"Erro ao buscar produto: {e}")
