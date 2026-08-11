import urllib.parse
from typing import Dict, List, Set

from requests.exceptions import RequestException

from app.config import SESSION
from app.utils.text import limpar_cnpj


def consultar_cnpj_brasilapi(cnpj: str) -> Dict:
    cnpj_limpo = limpar_cnpj(cnpj)
    if not cnpj_limpo:
        return {}

    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}"
    try:
        response = SESSION.get(url, timeout=12)
        if response.status_code == 200:
            return response.json()
        print(f"[-] BrasilAPI retornou {response.status_code} para {cnpj_limpo}")
    except RequestException as exc:
        print(f"[-] Erro BrasilAPI para {cnpj_limpo}: {exc}")
    return {}


def buscar_dados_registro_br(cnpj: str) -> Dict:
    cnpj_limpo = limpar_cnpj(cnpj)
    if not cnpj_limpo:
        return {}

    urls = [
        f"https://rdap.registro.br/entity/{cnpj_limpo}",
        f"https://rdap.registro.br/domain/{cnpj_limpo}",
    ]
    for url in urls:
        try:
            response = SESSION.get(url, timeout=12)
            if response.status_code == 200:
                return response.json()
        except RequestException:
            continue
    return {}


def _collect_urls(obj, urls: Set[str]) -> None:
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_urls(value, urls)
    elif isinstance(obj, list):
        for item in obj:
            _collect_urls(item, urls)
    elif isinstance(obj, str):
        if obj.startswith("http://") or obj.startswith("https://"):
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
