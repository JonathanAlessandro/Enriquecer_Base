from typing import Dict, Iterable, List, Optional, Set

from app.config import logger
from app.repositories.cnpj_repository import buscar_dominios_registro_br, consultar_cnpj_brasilapi
from app.repositories.site_repository import buscar_emails_site, procurar_site
from app.utils.text import classificar_emails, extrair_dominios_de_email, gerar_dominios_candidatos, is_generic_email, limpar_cnpj, validar_email


def gerar_resultado_prospeccao(
    cnpj: str,
    email_base: Optional[str] = None,
    nome_fantasia: Optional[str] = None,
    razao_social: Optional[str] = None,
) -> Dict:
    cnpj_limpo = limpar_cnpj(cnpj)
    if not cnpj_limpo:
        logger.warning("CNPJ inválido recebido para prospecção: %s", cnpj)
        return {
            "cnpj": cnpj,
            "razao_social": razao_social or "",
            "nome_fantasia": nome_fantasia or "",
            "dominios": "",
            "emails_base": email_base or "",
            "emails_encontrados": "",
            "email_prioritario": "",
            "obs": "CNPJ inválido",
        }

    logger.info("[%s] Iniciando prospecção", cnpj_limpo)
    dados_empresa = consultar_cnpj_brasilapi(cnpj_limpo)
    if dados_empresa:
        logger.info("[%s] BrasilAPI retornou dados da empresa", cnpj_limpo)
    else:
        logger.warning("[%s] BrasilAPI não retornou dados ou houve falha", cnpj_limpo)

    razao_social = razao_social or dados_empresa.get("razao_social", "")
    nome_fantasia = nome_fantasia or dados_empresa.get("nome_fantasia") or razao_social
    qsa = dados_empresa.get("qsa", [])
    decisores = [socio.get("nome_socio") for socio in qsa if socio.get("nome_socio")]

    dominios = buscar_dominios_registro_br(cnpj_limpo)
    logger.info("[%s] Domínios RDAP encontrados: %s", cnpj_limpo, dominios)
    origem_dominios = "RDAP" if dominios else "fallback"
    dominio_fundamental = dominios[0] if dominios else None

    if not dominios and email_base:
        dominios = extrair_dominios_de_email(email_base)
        logger.info("[%s] Extraindo domínios do email_base %s -> %s", cnpj_limpo, email_base, dominios)
        origem_dominios = "email_base"
        dominio_fundamental = dominios[0] if dominios else None

    if not dominios:
        logger.info("[%s] Não encontrou domínios RDAP nem em email_base; tentando candidatos de nome_fantasia", cnpj_limpo)
        candidatos = gerar_dominios_candidatos(nome_fantasia or razao_social)
        for candidato in candidatos:
            logger.info("[%s] Tentando candidato de domínio %s", cnpj_limpo, candidato)
            if procurar_site(candidato):
                dominios.append(candidato)
                dominio_fundamental = candidato
                logger.info("[%s] Encontrou domínio candidato %s", cnpj_limpo, candidato)
                break
        origem_dominios = "nome_fantasia" if dominios else origem_dominios

    emails_encontrados: Set[str] = set()
    if dominio_fundamental:
        logger.info("[%s] Buscando emails no site %s", cnpj_limpo, dominio_fundamental)
        emails_encontrados = buscar_emails_site(dominio_fundamental)
        logger.info("[%s] Emails encontrados inicialmente em %s: %s", cnpj_limpo, dominio_fundamental, sorted(emails_encontrados))
        if not emails_encontrados and email_base:
            logger.info("[%s] Nenhum email encontrado em %s; tentando domínio do email base", cnpj_limpo, dominio_fundamental)
            candidato_dominio = extrair_dominios_de_email(email_base)
            for dom in candidato_dominio:
                if dom not in dominios and procurar_site(dom):
                    emails_encontrados.update(buscar_emails_site(dom))
                    dominios.append(dom)
                    logger.info("[%s] Encontrou emails secundários em %s", cnpj_limpo, dom)
                    break
    if email_base and validar_email(email_base):
        emails_encontrados.add(email_base.lower())

    observacoes = []
    if not dados_empresa:
        observacoes.append("Dados BrasilAPI não encontrados")
    if not dominios:
        observacoes.append("Nenhum domínio confirmado")
    if not emails_encontrados:
        observacoes.append("Nenhum e-mail extraído")
    if email_base and is_generic_email(email_base):
        observacoes.append("Email base genérico")

    emails_ordenados = classificar_emails(emails_encontrados, dominio_fundamental, email_base)
    email_prioritario = emails_ordenados[0] if emails_ordenados else ""
    logger.info(
        "[%s] Resultado final domínios=%s emails=%s email_prioritario=%s obs=%s",
        cnpj_limpo,
        dominios,
        sorted(emails_encontrados),
        email_prioritario,
        "; ".join(observacoes) if observacoes else "sem dados",
    )

    return {
        "cnpj": cnpj_limpo,
        "razao_social": razao_social,
        "nome_fantasia": nome_fantasia,
        "decisores_qsa": "; ".join(decisores),
        "dominios": "; ".join(dominios),
        "origem_dominios": origem_dominios,
        "emails_base": email_base or "",
        "emails_encontrados": "; ".join(sorted(emails_encontrados)),
        "email_prioritario": email_prioritario,
        "obs": "; ".join(observacoes),
    }
