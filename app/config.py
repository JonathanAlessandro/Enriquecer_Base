import logging
import os
import re
import threading

import requests
from requests.adapters import HTTPAdapter


USER_AGENT = os.getenv(
    "ENRIQUECER_USER_AGENT",
    "Enriquecer_Base/1.1 (+https://github.com/JonathanAlessandro/Enriquecer_Base)",
)
REQUEST_TIMEOUT = 30
ENABLE_RDAP = os.getenv("ENRIQUECER_ENABLE_RDAP", "0").strip().lower() in {"1", "true", "yes"}

_SESSION_LOCAL = threading.local()


def get_session() -> requests.Session:
    session = getattr(_SESSION_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        })
        adapter = HTTPAdapter(max_retries=3)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _SESSION_LOCAL.session = session
    return session


# Mantido por compatibilidade com módulos externos; o código interno usa get_session().
SESSION = get_session()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)
logger = logging.getLogger("enrich_cnpj")

CNPJ_REGEX = re.compile(r"\D+")
EMAIL_REGEX = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
EMAIL_FIND_REGEX = re.compile(
    r"(?<![\w@])" + EMAIL_REGEX.pattern + r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "gmail.com.br", "hotmail.com", "hotmail.com.br",
    "outlook.com", "outlook.com.br", "yahoo.com", "yahoo.com.br",
    "uol.com.br", "bol.com.br", "terra.com.br", "ig.com.br",
    "live.com", "icloud.com", "protonmail.com",
}
GENERIC_EMAIL_PATTERNS = [
    r"^cnpj@",
    r"^meucnpj@",
    r"^secretaria@",
]
PHONEISH_EMAIL_LOCAL_REGEX = re.compile(r"^\d{3,}[-\s]?\d{2,}[a-z]", re.IGNORECASE)

DOMAIN_STOPWORDS = {
    "ltda", "eireli", "sa", "s", "mei", "me", "empresa", "com", "servicos", "serviços",
    "sistemas", "industria", "indústria", "brasil", "brazil",
}
PATH_CANDIDATES = [
    "/", "/contato", "/contato/", "/fale-conosco", "/fale-conosco/",
    "/atendimento", "/atendimento/", "/suporte", "/suporte/",
    "/quem-somos", "/quem-somos/", "/sobre", "/sobre/", "/sac", "/sitemap.xml",
]
LINK_KEYWORDS = [
    "contato", "fale", "atendimento", "suporte", "sac", "ouvidoria",
    "sobre", "quem-somos", "help", "faq",
]
EMAIL_SCORE = [
    (r"vendas|comercial|marketing", 12),
    (r"atendimento|sac|suporte|help|cliente", 9),
    (r"financeiro|faturamento", 5),
    (r"contato", 4),
    (r"info|informacoes|informações", 3),
]
