import logging
import re
import requests
from requests.adapters import HTTPAdapter

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
REQUEST_TIMEOUT = 30
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
SESSION.mount("https://", HTTPAdapter(max_retries=3))
SESSION.mount("http://", HTTPAdapter(max_retries=3))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)
logger = logging.getLogger("enrich_cnpj")

CNPJ_REGEX = re.compile(r"\D+")
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
GENERIC_EMAIL_PATTERNS = [
    r"^cnpj@",
    r"^meucnpj@",
    r"^contato(cnpj)?@",
    r"@contabil",
    r"@outlook\.com$",
    r"@gmail\.com$",
    r"@yahoo\.com$",
    r"@hotmail\.com$",
]
DOMAIN_STOPWORDS = {
    "ltda", "eireli", "sa", "s", "mei", "me", "empresa", "com", "servicos", "serviços",
    "sistemas", "industria", "indústria", "brasil", "brazil",
}
PATH_CANDIDATES = [
    "/",
    "/contato",
    "/contato/",
    "/fale-conosco",
    "/fale-conosco/",
    "/atendimento",
    "/atendimento/",
    "/suporte",
    "/suporte/",
    "/quem-somos",
    "/quem-somos/",
    "/sobre",
    "/sobre/",
    "/sac",
    "/sitemap.xml",
]
LINK_KEYWORDS = [
    "contato",
    "fale",
    "atendimento",
    "suporte",
    "sac",
    "ouvidoria",
    "sobre",
    "quem-somos",
    "help",
    "faq",
]
EMAIL_SCORE = [
    (r"vendas|comercial|marketing", 12),
    (r"atendimento|sac|suporte|help|cliente", 9),
    (r"financeiro|faturamento", 5),
    (r"contato", 4),
    (r"info|informacoes|informações", 3),
]
