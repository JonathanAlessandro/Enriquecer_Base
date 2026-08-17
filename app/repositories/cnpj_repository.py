import threading
import urllib.parse
from typing import Dict, List, Set

from requests.exceptions import RequestException, Timeout

from app.config import ENABLE_RDAP, REQUEST_TIMEOUT, get_session, logger
from app.utils.text import limpar_cnpj


_RDAP_BLOCKED = threading.Event()


def consultar_cnpj_brasilapi(cnpj: str) -> Dict:
    cnpj_limpo = limpar_cnpj(cnpj)
    if not cnpj_limpo:
        return {}

    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}"
    try:
        logger.info("Request started: BrasilAPI %s", url)
        response = get_session().get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            logger.warning("BrasilAPI retornou 429 para %s; retry-after=%s", cnpj_limpo, retry_after)
            return {}
        logger.warning("BrasilAPI retornou %s para %s", response.status_code, cnpj_limpo)
    except Timeout as exc:
        logger.warning("Timeout BrasilAPI para %s: %s", cnpj_limpo, exc)
    except RequestException as exc:
        logger.error("Erro BrasilAPI para %s: %s", cnpj_limpo, exc)
    return {}


def buscar_dados_registro_br(cnpj: str) -> Dict:
    if not ENABLE_RDAP or _RDAP_BLOCKED.is_set():
        return {}

    cnpj_limpo = limpar_cnpj(cnpj)
    if not cnpj_limpo:
        return {}

    # CNPJ é consultado como entidade. Não consultar /domain/{CNPJ}:
    # essa rota exige um nome de domínio, como empresa.com.br.
    url = f"https://rdap.registro.br/entity/{cnpj_limpo}"
    try:
        logger.info("Request started: Registro.br %s", url)
        response = get_session().get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return response.json()

        retry_after = response.headers.get("Retry-After")
        logger.warning(
            "Registro.br retornou %s para %s em %s; retry-after=%s",
            response.status_code,
            cnpj_limpo,
            url,
            retry_after,
        )
        if response.status_code in {403, 429}:
            _RDAP_BLOCKED.set()
            logger.error("Consultas RDAP interrompidas nesta execução após status %s", response.status_code)
    except Timeout as exc:
        logger.warning("Timeout Registro.br para %s em %s: %s", cnpj_limpo, url, exc)
    except RequestException as exc:
        logger.debug("Erro de rede ao acessar %s: %s", url, exc)
    return {}


def _collect_urls(obj, urls: Set[str]) -> None:
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_urls(value, urls)
    elif isinstance(obj, list):
        for item in obj:
            _collect_urls(item, urls)
    elif isinstance(obj, str) and (obj.startswith("http://") or obj.startswith("https://")):
        urls.add(obj)


def extrair_dominios_de_rdap(data: Dict) -> List[str]:
    urls: Set[str] = set()
    _collect_urls(data, urls)
    dominios: Set[str] = set()
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if hostname and hostname != "rdap.registro.br":
            dominios.add(hostname.lower())
    return sorted(dominios)


def buscar_dominios_registro_br(cnpj: str) -> List[str]:
    data = buscar_dados_registro_br(cnpj)
    if not data:
        return []
    return extrair_dominios_de_rdap(data)
