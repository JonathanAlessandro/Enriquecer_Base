import urllib.parse
from typing import List, Optional, Set

from bs4 import BeautifulSoup
from requests.exceptions import RequestException, Timeout

from app.config import HTTP_TIMEOUT, LINK_KEYWORDS, PATH_CANDIDATES, get_session, logger
from app.utils.text import dominio_valido, extract_emails_from_text, is_generic_domain, validar_email


def _hostname(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")


def procurar_site(dominio: str) -> Optional[str]:
    dominio_normalizado = dominio_valido(dominio)
    if not dominio_normalizado or is_generic_domain(dominio_normalizado):
        return None

    session = get_session()
    for esquema in ["https://", "http://"]:
        url = esquema + dominio_normalizado
        try:
            logger.info("Request started: procurar_site %s", url)
            response = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning("Site %s retornou 429; retry-after=%s", url, retry_after)
                continue
            if response.status_code == 403:
                logger.warning("Site %s retornou 403; ignorando este candidato", url)
                continue
            final_host = _hostname(response.url)
            if final_host and final_host != dominio_normalizado and not final_host.endswith("." + dominio_normalizado):
                logger.info("Site %s redirecionou para domínio externo %s; ignorando", url, final_host)
                continue
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code < 500 and ("text/html" in content_type or response.text.strip()):
                return response.url
        except Timeout as exc:
            logger.warning("Timeout ao procurar site %s após %ss: %s", url, HTTP_TIMEOUT, exc)
        except RequestException as exc:
            logger.warning("Erro ao procurar site %s: %s", url, exc)
        finally:
            logger.info("Request finished: procurar_site %s", url)
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
    hostname = (parsed.hostname or "").lower().rstrip(".")
    site_domain = site_domain.lower().rstrip(".")
    return not hostname or hostname == site_domain or hostname.endswith("." + site_domain)


def buscar_emails_site(dominio: str, limite_paginas: int = 8) -> Set[str]:
    dominio_normalizado = dominio_valido(dominio)
    if not dominio_normalizado or is_generic_domain(dominio_normalizado):
        return set()

    resultados: Set[str] = set()
    site = procurar_site(dominio_normalizado)
    if not site:
        return resultados

    session = get_session()
    urls = [site] + [urllib.parse.urljoin(site, path) for path in PATH_CANDIDATES]
    visitadas: Set[str] = set()
    fila: List[str] = []

    for url in urls:
        if url not in visitadas:
            fila.append(url)

    while fila and len(visitadas) < limite_paginas:
        url = fila.pop(0)
        if url in visitadas or not is_internal_link(url, dominio_normalizado):
            continue
        visitadas.add(url)

        try:
            logger.info("Request started: crawling %s", url)
            response = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        except Timeout as exc:
            logger.warning("Timeout ao acessar %s após %ss: %s", url, HTTP_TIMEOUT, exc)
            continue
        except RequestException as exc:
            logger.warning("Erro ao acessar %s: %s", url, exc)
            continue
        finally:
            logger.info("Request finished: crawling %s", url)

        if response.status_code in {403, 429}:
            logger.warning("Crawling %s retornou %s; ignorando página", url, response.status_code)
            continue
        if response.status_code >= 500:
            continue
        if not is_internal_link(response.url, dominio_normalizado):
            continue

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        resultados.update(extract_mailto_links(soup))
        resultados.update(extract_emails_from_text(soup.get_text(" ")))

        if len(resultados) >= 10:
            break

        for link in soup.select("a[href]"):
            href = link["href"].strip()
            if href.lower().startswith("mailto:"):
                continue
            full_url = urllib.parse.urljoin(response.url, href)
            if full_url in visitadas or len(fila) >= limite_paginas:
                continue
            if any(keyword in full_url.lower() for keyword in LINK_KEYWORDS) and is_internal_link(full_url, dominio_normalizado):
                fila.append(full_url)

    return resultados
