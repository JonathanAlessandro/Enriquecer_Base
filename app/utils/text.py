import html
import re
import urllib.parse
from typing import Iterable, List, Optional, Set

from app.config import (
    CNPJ_REGEX,
    DOMAIN_STOPWORDS,
    EMAIL_FIND_REGEX,
    EMAIL_REGEX,
    GENERIC_EMAIL_DOMAINS,
    GENERIC_EMAIL_PATTERNS,
    PHONEISH_EMAIL_LOCAL_REGEX,
)


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
RESERVED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}
DOMAIN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


def limpar_cnpj(cnpj: str) -> str:
    cnpj_limpo = CNPJ_REGEX.sub("", str(cnpj or ""))
    return cnpj_limpo if len(cnpj_limpo) == 14 else ""


def normalizar_email_bruto(email: str) -> str:
    if not email:
        return ""
    bruto = str(email).strip().lower()
    # Não decodificar fragmentos URL-encoded encontrados no meio de HTML/URLs.
    if re.search(r"%[0-9a-f]{2}", bruto):
        return ""
    valor = html.unescape(urllib.parse.unquote(bruto)).strip().lower()
    if valor.startswith("mailto:"):
        valor = valor[7:].split("?", 1)[0]
    return valor.strip(" \t\r\n.,;:()[]{}<>\"'")


def validar_email(email: str) -> Optional[str]:
    valor = normalizar_email_bruto(email)
    if not valor or any(ch in valor for ch in ("%", " ", "\n", "\r", "<", ">", "/", "\\")):
        return None
    if not EMAIL_REGEX.fullmatch(valor):
        return None
    if len(valor) > 254 or valor.count("@") != 1:
        return None
    local, dominio = valor.rsplit("@", 1)
    if not local or len(local) > 64 or dominio in RESERVED_EMAIL_DOMAINS:
        return None
    if PHONEISH_EMAIL_LOCAL_REGEX.match(local):
        return None
    if valor.endswith(IMAGE_SUFFIXES):
        return None
    return valor


def dominio_de_email(email: str) -> str:
    email_validado = validar_email(email)
    return email_validado.rsplit("@", 1)[1] if email_validado else ""


def dominio_valido(dominio: str) -> Optional[str]:
    if not dominio:
        return None
    valor = str(dominio).strip().lower().rstrip(".")
    if valor.startswith(("http://", "https://")):
        valor = valor.split("://", 1)[1].split("/", 1)[0]
    if "@" in valor or any(ch in valor for ch in ("%", " ", "\\")):
        return None
    if not DOMAIN_REGEX.fullmatch(valor):
        return None
    return valor


def is_generic_domain(dominio: str) -> bool:
    return (dominio_valido(dominio) or "") in GENERIC_EMAIL_DOMAINS


def is_generic_email(email: str) -> bool:
    valor = validar_email(email)
    if not valor:
        return False
    dominio = dominio_de_email(valor)
    if dominio in GENERIC_EMAIL_DOMAINS:
        return True
    return any(re.search(pattern, valor) for pattern in GENERIC_EMAIL_PATTERNS)


def extract_emails_from_text(text: str) -> Set[str]:
    if not text:
        return set()
    texto = html.unescape(str(text))
    encontrados: Set[str] = set()
    for candidato in EMAIL_FIND_REGEX.findall(texto):
        email = validar_email(candidato)
        if email:
            encontrados.add(email)
    return encontrados


def extrair_dominios_de_email(email: str) -> List[str]:
    dominio = dominio_de_email(email)
    return [dominio] if dominio else []


def normalizar_nome_para_dominio(nome: str) -> str:
    if not nome:
        return ""
    texto = nome.lower()
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    palavras = [p for p in texto.split() if p and p not in DOMAIN_STOPWORDS]
    if not palavras:
        palavras = [p for p in texto.split() if p]
    return "".join(palavras[:3]) if palavras else ""


def gerar_dominios_candidatos(nome: str, limite: int = 5) -> List[str]:
    base = normalizar_nome_para_dominio(nome)
    if not base:
        return []
    candidatos = [base, base.replace(" ", "-"), base.replace(" ", "")]
    partes = base.split()
    if len(partes) > 1:
        candidatos.extend(["".join(partes[:2]), "-".join(partes[:2]), "".join(partes[-2:])])
    dominios: List[str] = []
    for candidato in candidatos:
        raiz = re.sub(r"[^a-z0-9-]", "", candidato)
        if not raiz:
            continue
        for tld in (".com.br", ".com", ".net", ".org", ".br"):
            dominios.append(raiz + tld)
            if len(dominios) >= limite:
                return list(dict.fromkeys(dominios))[:limite]
    return list(dict.fromkeys(dominios))[:limite]


def pontuar_email(email: str, dominio_site: Optional[str] = None) -> int:
    valor = validar_email(email)
    if not valor:
        return -999
    local, _, dominio = valor.partition("@")
    score = -100 if is_generic_email(valor) else 0
    dominio_site_valido = dominio_valido(dominio_site or "")
    if dominio_site_valido and dominio == dominio_site_valido:
        score += 20
    for pattern, peso in [
        (r"vendas|comercial|marketing", 12),
        (r"atendimento|sac|suporte|help|cliente", 9),
        (r"financeiro|faturamento", 5),
        (r"contato", 4),
        (r"info|informacoes|informações", 3),
    ]:
        if re.search(pattern, valor):
            score += peso
    if re.search(r"^(cnpj|meucnpj|contatocnpj|secretaria)@", valor):
        score -= 8
    if re.search(r"^(info|contact|hello)@", valor):
        score += 1
    if re.search(r"^sales@", valor):
        score += 5
    if "." in dominio and local == dominio.split(".")[-2]:
        score += 2
    return score


def classificar_emails(emails: Iterable[str], dominio_site: Optional[str] = None, email_base: Optional[str] = None) -> List[str]:
    candidatos = []
    email_base_validado = validar_email(email_base or "")
    for email in {validar_email(item) for item in emails} - {None}:
        score = pontuar_email(email, dominio_site)
        if email_base_validado and email == email_base_validado:
            score += 2
        candidatos.append((score, email))
    candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [email for _, email in candidatos]
