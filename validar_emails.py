"""Validador passivo de e-mails para higiene de listas.

O script não envia mensagens, não faz probing SMTP e não tenta contornar
controles de provedores. Ele valida sintaxe, normaliza endereços, consulta
MX via DNS, identifica domínios genéricos/descartáveis e aplica supressões.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    import dns.exception
    import dns.resolver
except ImportError:  # validação continua funcionando, mas DNS ficará pendente
    dns = None  # type: ignore[assignment]


EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
EMAIL_FIND_RE = re.compile(
    r"(?<![\w@])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
GENERIC_DOMAINS = {
    "gmail.com", "gmail.com.br", "hotmail.com", "hotmail.com.br",
    "outlook.com", "outlook.com.br", "yahoo.com", "yahoo.com.br",
    "uol.com.br", "bol.com.br", "terra.com.br", "ig.com.br",
    "live.com", "icloud.com", "protonmail.com",
}
ROLE_LOCALS = {
    "admin", "administrator", "atendimento", "comercial", "contact",
    "contato", "financeiro", "fiscal", "hello", "info", "marketing",
    "noreply", "no-reply", "postmaster", "sales", "sac", "support",
    "suporte", "vendas", "webmaster",
}
DEFAULT_DISPOSABLE_DOMAINS = {
    "10minutemail.com", "guerrillamail.com", "mailinator.com",
    "maildrop.cc", "tempmail.com", "temp-mail.org", "yopmail.com",
}
DEFAULT_EMAIL_COLUMNS = {
    "email", "emails", "email_base", "emails_base", "emails_encontrados",
    "emails_corporativos", "emails_genericos", "email_prioritario",
}
OUTPUT_FIELDS = [
    "email_original", "email", "status", "recommendation", "risk", "score",
    "domain", "has_mx", "dns_status", "is_generic", "is_role",
    "is_disposable", "suppressed", "source_cnpj", "source_company",
    "source_origin", "reason", "checked_at",
]


@dataclass(frozen=True)
class EmailCandidate:
    original: str
    email: str
    source_cnpj: str = ""
    source_company: str = ""
    source_origin: str = ""


def normalize_email(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value or re.search(r"%[0-9a-f]{2}", value):
        return ""
    if value.startswith("mailto:"):
        value = value[7:].split("?", 1)[0]
    return value.strip(" \t\r\n.,;:()[]{}<>\"'")


def extract_email_candidates(value: str) -> List[str]:
    if not value:
        return []
    normalized = normalize_email(value)
    if normalized and EMAIL_RE.fullmatch(normalized):
        return [normalized]
    return [normalize_email(item) for item in EMAIL_FIND_RE.findall(str(value)) if normalize_email(item)]


def is_syntactically_valid(email: str) -> bool:
    if not email or len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64 or domain in {"example.com", "example.org", "example.net"}:
        return False
    if any(char in email for char in ("%", " ", "\n", "\r", "<", ">", "/", "\\")):
        return False
    return bool(EMAIL_RE.fullmatch(email))


def load_domain_file(path: Optional[Path], defaults: Iterable[str]) -> Set[str]:
    domains = {item.strip().lower().rstrip(".") for item in defaults if item.strip()}
    if path and path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip().lower().rstrip(".")
            if line and not line.startswith("#"):
                domains.add(line)
    return domains


def load_suppressions(path: Optional[Path]) -> Tuple[Set[str], Set[str]]:
    emails: Set[str] = set()
    domains: Set[str] = set()
    if not path or not path.exists():
        return emails, domains
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            email = normalize_email(row.get("email", ""))
            domain = str(row.get("domain", "") or "").strip().lower().rstrip(".")
            if email:
                emails.add(email)
            if domain:
                domains.add(domain)
    return emails, domains


def collect_candidates(path: Path) -> List[EmailCandidate]:
    candidates: Dict[str, EmailCandidate] = {}
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        email_fields = {
            name for name in fieldnames
            if name.strip().lower() in DEFAULT_EMAIL_COLUMNS or "email" in name.strip().lower()
        }
        for row in reader:
            cnpj = str(row.get("cnpj", "") or row.get("CNPJ", "") or "")
            company = str(row.get("nome_fantasia", "") or row.get("razao_social", "") or "")
            origin = str(row.get("origem_emails", "") or row.get("origem_dominios", "") or "")
            for field in email_fields:
                for original in str(row.get(field, "") or "").split(";"):
                    for email in extract_email_candidates(original):
                        if email and email not in candidates:
                            candidates[email] = EmailCandidate(original.strip(), email, cnpj, company, origin)
    return list(candidates.values())


def _writing_marker(path: Path) -> Path:
    return path.with_name(path.name + ".writing")


def _file_signature(path: Path) -> Tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def wait_for_stable_input(
    path: Path,
    wait_timeout: float = 3600.0,
    stable_seconds: float = 3.0,
    poll_seconds: float = 1.0,
) -> None:
    """Aguarda o produtor terminar e o CSV permanecer estável."""
    deadline = time.monotonic() + max(0.0, wait_timeout)
    last_signature: Optional[Tuple[int, int]] = None
    stable_since: Optional[float] = None
    marker = _writing_marker(path)

    while True:
        if path.exists() and path.stat().st_size > 0:
            signature = _file_signature(path)
            marker_exists = marker.exists()
            if not marker_exists and signature == last_signature:
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= stable_seconds:
                    return
            else:
                stable_since = None
            last_signature = signature

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Arquivo não ficou disponível/estável em {wait_timeout}s: {path}"
            )
        time.sleep(max(0.1, poll_seconds))


def create_consistent_snapshot(
    path: Path,
    wait_timeout: float = 3600.0,
    stable_seconds: float = 3.0,
    poll_seconds: float = 1.0,
) -> Path:
    """Cria uma cópia estável para o validador não ler o CSV em mutação."""
    deadline = time.monotonic() + max(0.0, wait_timeout)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Não foi possível criar snapshot estável em {path}")
        wait_for_stable_input(path, remaining, stable_seconds, poll_seconds)
        snapshot = path.with_name(f".{path.name}.{os.getpid()}.validator.snapshot")
        before = _file_signature(path)
        shutil.copyfile(path, snapshot)
        after = _file_signature(path)
        if not _writing_marker(path).exists() and before == after:
            return snapshot
        snapshot.unlink(missing_ok=True)


def check_mx(domain: str, timeout: float = 3.0) -> Tuple[Optional[bool], str]:
    if dns is None:
        return None, "dns_library_unavailable"
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    try:
        answers = resolver.resolve(domain, "MX")
        return bool(list(answers)), "mx_found"
    except dns.resolver.NXDOMAIN:
        return False, "domain_not_found"
    except dns.resolver.NoAnswer:
        return False, "mx_missing"
    except (dns.exception.Timeout, dns.resolver.NoNameservers):
        return None, "dns_timeout_or_no_nameserver"
    except Exception as exc:  # a falha DNS não deve derrubar o lote
        return None, f"dns_error:{type(exc).__name__}"


def classify_candidate(
    candidate: EmailCandidate,
    domain_cache: Dict[str, Tuple[Optional[bool], str]],
    suppression_emails: Set[str],
    suppression_domains: Set[str],
    disposable_domains: Set[str],
) -> Dict[str, str]:
    email = candidate.email
    now = datetime.now(timezone.utc).isoformat()
    if not is_syntactically_valid(email):
        return row(candidate, "invalid_syntax", "do_not_send", "high", 0, "", None, "not_checked", False, False, False, False, "Formato inválido", now)

    local, domain = email.rsplit("@", 1)
    is_generic = domain in GENERIC_DOMAINS
    is_role = local in ROLE_LOCALS
    is_disposable = domain in disposable_domains
    suppressed = email in suppression_emails or domain in suppression_domains
    if suppressed:
        return row(candidate, "suppressed", "do_not_send", "high", 0, domain, None, "suppression_list", is_generic, is_role, is_disposable, True, "Consta na lista de supressão", now)
    if is_disposable:
        return row(candidate, "disposable_domain", "do_not_send", "high", 0, domain, None, "not_checked", is_generic, is_role, True, False, "Domínio descartável", now)

    has_mx, dns_status = domain_cache.get(domain, (None, "not_checked"))
    if has_mx is False:
        return row(candidate, "no_mx", "do_not_send", "high", 0, domain, has_mx, dns_status, is_generic, is_role, False, False, "Domínio sem MX utilizável", now)
    if has_mx is None:
        return row(candidate, "dns_pending", "manual_review", "medium", 40, domain, has_mx, dns_status, is_generic, is_role, False, False, "DNS não confirmado", now)

    score = 100
    reasons = ["Sintaxe válida", "MX encontrado"]
    if is_generic:
        score -= 40
        reasons.append("provedor genérico")
    if is_role:
        score -= 10
        reasons.append("caixa de função")
    risk = "low" if score >= 80 else "medium"
    recommendation = "manual_review" if is_generic or is_role else "eligible_for_manual_review"
    status = "valid_mx_review"
    return row(candidate, status, recommendation, risk, score, domain, has_mx, dns_status, is_generic, is_role, False, False, "; ".join(reasons), now)


def row(candidate: EmailCandidate, status: str, recommendation: str, risk: str, score: int, domain: str, has_mx: Optional[bool], dns_status: str, is_generic: bool, is_role: bool, is_disposable: bool, suppressed: bool, reason: str, checked_at: str) -> Dict[str, str]:
    return {
        "email_original": candidate.original,
        "email": candidate.email,
        "status": status,
        "recommendation": recommendation,
        "risk": risk,
        "score": str(score),
        "domain": domain,
        "has_mx": "" if has_mx is None else str(has_mx).lower(),
        "dns_status": dns_status,
        "is_generic": str(is_generic).lower(),
        "is_role": str(is_role).lower(),
        "is_disposable": str(is_disposable).lower(),
        "suppressed": str(suppressed).lower(),
        "source_cnpj": candidate.source_cnpj,
        "source_company": candidate.source_company,
        "source_origin": candidate.source_origin,
        "reason": reason,
        "checked_at": checked_at,
    }


def validate_file(
    input_path: Path,
    output_path: Path,
    suppressions_path: Optional[Path],
    disposable_path: Optional[Path],
    workers: int,
    wait_timeout: float = 3600.0,
    stable_seconds: float = 3.0,
    poll_seconds: float = 1.0,
) -> int:
    snapshot = create_consistent_snapshot(input_path, wait_timeout, stable_seconds, poll_seconds)
    try:
        candidates = collect_candidates(snapshot)
        suppression_emails, suppression_domains = load_suppressions(suppressions_path)
        disposable_domains = load_domain_file(disposable_path, DEFAULT_DISPOSABLE_DOMAINS)
        domains = sorted({candidate.email.rsplit("@", 1)[1] for candidate in candidates if is_syntactically_valid(candidate.email)})

        domain_cache: Dict[str, Tuple[Optional[bool], str]] = {}
        max_workers = max(1, min(int(workers or 1), 16))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(check_mx, domain): domain for domain in domains}
            for future in as_completed(futures):
                domain_cache[futures[future]] = future.result()

        rows = [classify_candidate(candidate, domain_cache, suppression_emails, suppression_domains, disposable_domains) for candidate in candidates]
        temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
        with temporary_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_output, output_path)
        print(f"E-mails únicos analisados: {len(candidates)}")
        print(f"Domínios consultados via MX: {len(domains)}")
        print(f"Snapshot validado: {snapshot}")
        print(f"Resultado salvo em: {output_path}")
        return len(rows)
    finally:
        snapshot.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validação passiva de e-mails sem envio de mensagens")
    parser.add_argument("--input-csv", required=True, type=Path, help="CSV com emails_base/emails_encontrados ou colunas de e-mail")
    parser.add_argument("--output-csv", default="emails_validados.csv", type=Path)
    parser.add_argument("--suppressions", default="email_suppressions.csv", type=Path, help="CSV opcional com colunas email,domain,reason")
    parser.add_argument("--disposable-domains", default="disposable_domains.txt", type=Path, help="TXT opcional: um domínio descartável por linha")
    parser.add_argument("--workers", default=4, type=int, help="Threads somente para consultas DNS")
    parser.add_argument("--wait-timeout", default=3600.0, type=float, help="Máximo de segundos aguardando o CSV ficar estável")
    parser.add_argument("--stable-seconds", default=3.0, type=float, help="Segundos sem alteração antes de criar o snapshot")
    parser.add_argument("--poll-seconds", default=1.0, type=float, help="Intervalo de verificação do arquivo")
    args = parser.parse_args()
    validate_file(
        args.input_csv,
        args.output_csv,
        args.suppressions,
        args.disposable_domains,
        args.workers,
        args.wait_timeout,
        args.stable_seconds,
        args.poll_seconds,
    )


if __name__ == "__main__":
    main()
