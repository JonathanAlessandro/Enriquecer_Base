import urllib.parse
from typing import List, Optional, Set

from bs4 import BeautifulSoup
from requests.exceptions import RequestException, Timeout

from app.config import LINK_KEYWORDS, PATH_CANDIDATES, REQUEST_TIMEOUT, SESSION, logger
from app.utils.text import extract_emails_from_text, validar_email


def procurar_site(dominio: str) -> Optional[str]:
    if not dominio:
        return None

    for esquema in ["https://", "http://"]:
        url = esquema + dominio
        try:
            logger.info("Request started: procurar_site %s", url)
            response = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning("Site %s retornou 429; retry-after=%s", url, retry_after)
                continue
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code < 500 and ("text/html" in content_type or response.text.strip()):
                return response.url
        except Timeout as exc:
            logger.warning("Timeout ao procurar site %s: %s", url, exc)
            continue
        except RequestException as exc:
            logger.debug("Erro ao procurar site %s: %s", url, exc)
            continue
    return None


def extract_mailto_links(soup: BeautifulSoup) -> Set[str]:
    emails: Set[str] = set()
    for link in soup.select("a[href]"):
        href = link["href"].strip()
        if href.lower().startswith("mailto:"):
            email = href.split("mailto:", 1)[1].split("?", 1)[0]
            email_validado = validar_email(email)
            if email_validado:
                emails.add(email_validado)
    return emails


def is_internal_link(url: str, site_domain: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False
    hostname = parsed.hostname
    if hostname and hostname != site_domain and not hostname.endswith("." + site_domain):
        return False
    return True


def buscar_emails_site(dominio: str, limite_paginas: int = 8) -> Set[str]:
    resultados: Set[str] = set()
    site = procurar_site(dominio)
    if not site:
        return resultados

    urls = [site] + [urllib.parse.urljoin(site, path) for path in PATH_CANDIDATES]
    visitadas: Set[str] = set()
    fila: List[str] = []

    for url in urls:
        if url not in visitadas:
            fila.append(url)

    while fila and len(visitadas) < limite_paginas:
        url = fila.pop(0)
        if url in visitadas:
            continue
        visitadas.add(url)

        try:
            logger.info("Request started: crawling %s", url)
            response = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except Timeout as exc:
            logger.warning("Timeout ao acessar %s: %s", url, exc)
            continue
        except RequestException as exc:
            logger.debug("Erro ao acessar %s: %s", url, exc)
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            logger.warning("Crawling %s retornou 429; retry-after=%s", url, retry_after)
            continue

        if response.status_code >= 500:
            continue

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        resultados.update(extract_mailto_links(soup))
        resultados.update(extract_emails_from_text(soup.get_text()))

        if len(resultados) >= 10:
            break

        for link in soup.select("a[href]"):
            href = link["href"].strip()
            if href.startswith("mailto:"):
                continue
            full_url = urllib.parse.urljoin(response.url, href)
            if full_url in visitadas or len(fila) >= limite_paginas:
                continue
            if any(keyword in full_url.lower() for keyword in LINK_KEYWORDS) and is_internal_link(full_url, dominio):
                fila.append(full_url)

    return resultados
