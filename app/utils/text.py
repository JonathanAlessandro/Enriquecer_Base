import re
from typing import Iterable, List, Optional, Set

from app.config import CNPJ_REGEX, EMAIL_REGEX, GENERIC_EMAIL_PATTERNS


def limpar_cnpj(cnpj: str) -> str:
    cnpj_limpo = CNPJ_REGEX.sub("", str(cnpj or ""))
    return cnpj_limpo if len(cnpj_limpo) == 14 else ""


def validar_email(email: str) -> Optional[str]:
    if not email:
        return None
    email = email.strip().lower()
    if not EMAIL_REGEX.fullmatch(email):
        return None
    if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
        return None
    return email


def is_generic_email(email: str) -> bool:
    email = email.lower()
    return any(re.search(pattern, email) for pattern in GENERIC_EMAIL_PATTERNS)


def extract_emails_from_text(text: str) -> Set[str]:
    if not text:
        return set()
    matches = EMAIL_REGEX.findall(text)
    return {email.lower().strip(".,;:\n\r\t") for email in matches if validar_email(email)}


def extrair_dominios_de_email(email: str) -> List[str]:
    email_validado = validar_email(email)
    if not email_validado:
        return []
    dominio = email_validado.split("@", 1)[-1]
    return [dominio]


def normalizar_nome_para_dominio(nome: str) -> str:
    if not nome:
        return ""
    texto = nome.lower()
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    palavras = [p for p in texto.split() if p and p not in {"ltda", "eireli", "sa", "s", "mei", "me", "empresa", "com", "servicos", "serviços", "sistemas", "industria", "indústria", "brasil", "brazil"}]
    if not palavras:
        palavras = [p for p in texto.split() if p]
    if not palavras:
        return ""
    dominio = "".join(palavras[:3])
    return dominio


def gerar_dominios_candidatos(nome: str, limite: int = 5) -> List[str]:
    base = normalizar_nome_para_dominio(nome)
    if not base:
        return []

    candidatos: List[str] = []
    if base:
        candidatos.append(base)
        candidatos.append(base.replace(" ", ""))
        candidatos.append(base.replace(" ", "-"))
    partes = base.split()
    if len(partes) > 1:
        candidatos.append("".join(partes[:2]))
        candidatos.append("-".join(partes[:2]))
        candidatos.append("".join(partes[-2:]))
    dominios = []
    tlds = [".com.br", ".com", ".net", ".org", ".br"]
    for candidato in candidatos:
        raiz = re.sub(r"[^a-z0-9-]", "", candidato)
        if not raiz:
            continue
        for tld in tlds:
            dominios.append(raiz + tld)
        if len(dominios) >= limite:
            break
    return [d for d in dict.fromkeys(dominios)][:limite]


def pontuar_email(email: str, dominio_site: Optional[str] = None) -> int:
    score = 0
    local, _, dominio = email.partition("@")
    local = local.lower()
    dominio = dominio.lower()

    if dominio_site and dominio_site.lower() in dominio:
        score += 3

    for pattern, peso in [
        (r"vendas|comercial|marketing", 12),
        (r"atendimento|sac|suporte|help|cliente", 9),
        (r"financeiro|faturamento", 5),
        (r"contato", 4),
        (r"info|informacoes|informações", 3),
    ]:
        if re.search(pattern, email):
            score += peso

    if re.search(r"^(cnpj|meucnpj|contatocnpj|secretaria)@", email):
        score -= 8
    if re.search(r"^(info|contact|hello)@", email):
        score += 1
    if re.search(r"^sales@", email):
        score += 5

    if "." in dominio and local == dominio.split(".")[-2]:
        score += 2

    return score


def classificar_emails(emails: Iterable[str], dominio_site: Optional[str] = None, email_base: Optional[str] = None) -> List[str]:
    candidatos = []
    for email in {email for email in emails if validar_email(email)}:
        score = pontuar_email(email, dominio_site)
        if email_base and email == email_base:
            score += 2
        candidatos.append((score, email))
    candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [email for _, email in candidatos]
