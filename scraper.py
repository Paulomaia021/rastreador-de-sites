from __future__ import annotations
import re
import time
import requests
from bs4 import BeautifulSoup
from database import extract_store_name, validate_product_url


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


_HTTP_SESSION: requests.Session | None = None


def _get_http_session() -> requests.Session:
    """Reutiliza conexões TCP/TLS via requests.Session com cabeçalhos padrão."""
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _HTTP_SESSION = session
    return _HTTP_SESSION


def clean_price(price_text: str) -> float:
    """
    Transforma textos como 'R$ 1.899,90', 'R$ 1.299', 'De R$ 250,00 por R$ 199,90'
    ou '£51.77' em float positivo (1899.90, 1299.0, 199.90, 51.77).
    """
    if not price_text or not isinstance(price_text, str):
        raise ValueError(f"Não foi possível extrair números de: '{price_text}'")

    raw = price_text.strip()
    if not raw:
        raise ValueError(f"Não foi possível extrair números de: '{price_text}'")

    # Rejeita valores explicitamente negativos (ex: "-R$ 10,00" ou "-10.00")
    if re.match(r"^\s*-\s*(?:R\$|\$|£|€)?\s*\d", raw):
        raise ValueError(f"Preço negativo não é permitido: '{price_text}'")

    # 1. Trata padrão promocional "De X por Y" priorizando o preço final Y
    de_por_match = re.search(
        r"(?i)\bde\s+(?:R\$|\$|£|€)?\s*\d+(?:[.,]\d+)*\s+por\s+((?:R\$|\$|£|€)?\s*\d+(?:[.,]\d+)*)",
        raw,
    )
    if de_por_match:
        raw = de_por_match.group(1)
    else:
        # 2. Remove trechos de parcelamento (ex: "em até 10x de R$ 199,90") quando há preço principal antes
        raw_no_installments = re.sub(
            r"(?i)(?:em\s+at[eé]\s+)?\b\d{1,2}\s*x\s*(?:de\s*)?(?:R\$|\$|£|€)?\s*\d+(?:[.,]\d+)*.*$",
            "",
            raw,
        ).strip()
        if re.search(r"\d", raw_no_installments):
            raw = raw_no_installments

    # 3. Extrai o primeiro token numérico bem formado (ex: "1.899,90", "1.299", "51.77")
    tokens = re.findall(r"\d+(?:[.,]\d+)*", raw)
    if not tokens:
        raise ValueError(f"Não foi possível extrair números de: '{price_text}'")

    cleaned = tokens[0]

    # 4. Normaliza separadores de milhar e decimal com precisão para formato BR e internacional
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # Formato BR/Europeu: 1.899,90 ou 1.299.000,90
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Formato US/UK: 1,899.90
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        if cleaned.count(",") > 1:
            # Múltiplas vírgulas como separador de milhar: 1,299,000
            cleaned = cleaned.replace(",", "")
        else:
            parts = cleaned.split(",")
            # Ex: US$ 1,299 (sem centavos e com símbolo internacional) vs R$ 1,99 / 199,90
            if len(parts[1]) == 3 and ("US$" in raw or "£" in raw) and "R$" not in raw:
                cleaned = cleaned.replace(",", "")
            else:
                cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        if cleaned.count(".") > 1:
            # Múltiplos pontos como separador de milhar: 1.299.000
            cleaned = cleaned.replace(".", "")
        else:
            parts = cleaned.split(".")
            # Se houver exatamente 3 dígitos após o único ponto (ex: "R$ 1.299" ou "1.299"),
            # trata-se de separador de milhar brasileiro (preços reais não usam 3 casas decimais)
            if len(parts[1]) == 3:
                cleaned = cleaned.replace(".", "")

    value = round(float(cleaned), 2)
    if value <= 0:
        raise ValueError(f"Preço inválido ou zerado ({value}) extraído de: '{price_text}'")

    return value


def fetch_product_info(url: str) -> dict:
    """
    Valida a URL, acessa a página do produto e retorna um dicionário com título, preço,
    loja, código HTTP e tempo de resposta em milissegundos.
    """
    try:
        clean_url = validate_product_url(url)
    except ValueError as err:
        raise ScraperError(str(err), http_status=None, response_time_ms=0.0) from err

    start_ts = time.perf_counter()
    http_status = None
    store_name = extract_store_name(clean_url)

    try:
        session = _get_http_session()
        response = session.get(clean_url, headers=HEADERS, timeout=15)
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
