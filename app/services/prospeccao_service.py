from typing import Dict, Optional, Set

from app.config import ENABLE_RDAP, logger
from app.repositories.cnpj_repository import buscar_dominios_registro_br, consultar_cnpj_brasilapi
from app.repositories.site_repository import buscar_emails_site, procurar_site
from app.utils.text import (
    classificar_emails,
    dominio_de_email,
    extrair_dominios_de_email,
    extract_emails_from_text,
    gerar_dominios_candidatos,
    is_generic_email,
    limpar_cnpj,
    validar_email,
)


def _resultado_base(cnpj: str, razao_social: str = "", nome_fantasia: str = "", email_base: str = "") -> Dict:
    return {
        "cnpj": cnpj,
        "razao_social": razao_social,
        "nome_fantasia": nome_fantasia,
        "decisores_qsa": "",
        "dominios": "",
        "origem_dominios": "fallback",
        "emails_base": email_base,
        "emails_encontrados": "",
        "emails_corporativos": "",
        "emails_genericos": "",
        "email_prioritario": "",
        "tipo_email_prioritario": "",
        "confianca_email": "",
        "origem_emails": "nenhuma",
        "obs": "",
    }


def gerar_resultado_prospeccao(
    cnpj: str,
    email_base: Optional[str] = None,
    nome_fantasia: Optional[str] = None,
    razao_social: Optional[str] = None,
) -> Dict:
    cnpj_limpo = limpar_cnpj(cnpj)
    if not cnpj_limpo:
        logger.warning("CNPJ inválido recebido para prospecção: %s", cnpj)
        resultado = _resultado_base(cnpj, razao_social or "", nome_fantasia or "", email_base or "")
        resultado["obs"] = "CNPJ inválido"
        return resultado

    email_base_validado = validar_email(email_base or "")
    email_base_generico = bool(email_base_validado and is_generic_email(email_base_validado))
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
    emails_cadastrais = extract_emails_from_text(email_base or "")
    emails_api = extract_emails_from_text(dados_empresa.get("email") or "")
    emails_cadastrais.update(emails_api)

    dominios = []
    sites_confirmados = {}
    origem_dominios = "fallback"
    dominio_fundamental = None

    if ENABLE_RDAP:
        dominios = buscar_dominios_registro_br(cnpj_limpo)
        logger.info("[%s] Domínios RDAP encontrados: %s", cnpj_limpo, dominios)
        if dominios:
            origem_dominios = "RDAP"
            dominio_fundamental = dominios[0]
    else:
        logger.info("[%s] RDAP desativado para evitar consultas em massa", cnpj_limpo)

    # Só usa o domínio do e-mail como candidato quando ele é corporativo.
    if not dominios and any(not is_generic_email(e) for e in emails_cadastrais):
        dominios = sorted({dominio_de_email(e) for e in emails_cadastrais if not is_generic_email(e)})
        origem_dominios = "email_cadastral"
        dominio_fundamental = dominios[0] if dominios else None
        logger.info("[%s] Domínio corporativo derivado do e-mail-base: %s", cnpj_limpo, dominios)

    if not dominios:
        logger.info("[%s] Tentando candidatos de domínio derivados do nome", cnpj_limpo)
        candidatos = gerar_dominios_candidatos(nome_fantasia or razao_social)
        for candidato in candidatos:
            logger.info("[%s] Tentando candidato de domínio %s", cnpj_limpo, candidato)
            site_confirmado = procurar_site(candidato)
            if site_confirmado:
                dominios.append(candidato)
                sites_confirmados[candidato] = site_confirmado
                dominio_fundamental = candidato
                origem_dominios = "nome_fantasia"
                logger.info("[%s] Candidato de domínio acessível: %s", cnpj_limpo, candidato)
                break

    emails_encontrados: Set[str] = set()
    origem_emails = "nenhuma"
    for dominio in dominios:
        logger.info("[%s] Buscando e-mails no site candidato %s", cnpj_limpo, dominio)
        emails_encontrados.update(
            buscar_emails_site(dominio, site_confirmado=sites_confirmados.get(dominio))
        )
        if emails_encontrados:
            origem_emails = "site_oficial_candidato" if origem_dominios == "nome_fantasia" else "site_do_dominio_email"

    # Mantém o e-mail-base no resultado, mas o classifica separadamente.
    emails_do_site = set(emails_encontrados)
    if emails_cadastrais:
        emails_encontrados.update(emails_cadastrais)
        if origem_emails == "nenhuma":
            origem_emails = "cadastro_brasilapi" if emails_api else "email_base"
        elif emails_api:
            origem_emails += "; cadastro_brasilapi"

    emails_corporativos = sorted(e for e in emails_encontrados if not is_generic_email(e))
    emails_genericos = sorted(e for e in emails_encontrados if is_generic_email(e))
    emails_ordenados = classificar_emails(emails_corporativos or emails_genericos, dominio_fundamental, email_base_validado)
    email_prioritario = emails_ordenados[0] if emails_ordenados else ""
    tipo_email_prioritario = "corporativo" if email_prioritario and not is_generic_email(email_prioritario) else ("generico" if email_prioritario else "")

    if tipo_email_prioritario == "corporativo":
        confianca_email = "media" if email_prioritario in emails_do_site or email_prioritario in emails_cadastrais else "baixa"
        if origem_dominios == "nome_fantasia" and email_prioritario not in emails_cadastrais:
            confianca_email = "baixa"
    elif tipo_email_prioritario == "generico":
        confianca_email = "baixa"
    else:
        confianca_email = ""

    observacoes = []
    if origem_dominios == "nome_fantasia":
        observacoes.append("Vínculo do site candidato com o CNPJ não confirmado")
    if email_prioritario:
        observacoes.append("Titularidade do e-mail e vínculo com sócios não confirmados")
    if not dados_empresa:
        observacoes.append("Dados BrasilAPI não encontrados")
    if not dominios:
        observacoes.append("Nenhum domínio candidato encontrado")
    if not emails_corporativos:
        observacoes.append("Nenhum e-mail corporativo encontrado")
    if email_base_generico:
        observacoes.append("Email base genérico mantido separado")
    if not emails_encontrados:
        observacoes.append("Nenhum e-mail extraído")
    if not ENABLE_RDAP:
        observacoes.append("RDAP desativado")

    logger.info(
        "[%s] Resultado domínios=%s corporativos=%s genéricos=%s prioritário=%s confiança=%s",
        cnpj_limpo,
        dominios,
        emails_corporativos,
        emails_genericos,
        email_prioritario,
        confianca_email,
    )

    return {
        "cnpj": cnpj_limpo,
        "razao_social": razao_social,
        "nome_fantasia": nome_fantasia,
        "decisores_qsa": "; ".join(decisores),
        "dominios": "; ".join(dict.fromkeys(dominios)),
        "origem_dominios": origem_dominios,
        "emails_base": email_base or "",
        "emails_encontrados": "; ".join(sorted(emails_encontrados)),
        "emails_corporativos": "; ".join(emails_corporativos),
        "emails_genericos": "; ".join(emails_genericos),
        "email_prioritario": email_prioritario,
        "tipo_email_prioritario": tipo_email_prioritario,
        "confianca_email": confianca_email,
        "origem_emails": origem_emails,
        "obs": "; ".join(observacoes),
    }
