import csv
import concurrent.futures
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.config import logger
from app.services.prospeccao_service import gerar_resultado_prospeccao
from app.utils.text import limpar_cnpj


def carregar_cnpjs_csv(path: Path, apenas_ativos: bool = True, apenas_matriz: bool = True) -> List[Dict[str, str]]:
    registros: List[Dict[str, str]] = []
    with path.open(newline="", encoding="utf-8", errors="ignore") as csvfile:
        leitor = csv.DictReader(csvfile)
        for linha in leitor:
            cnpj = linha.get("CNPJ_COMPLETO") or linha.get("CNPJ") or linha.get("cnpj") or ""
            email = linha.get("EMAIL") or linha.get("email") or ""
            if not cnpj:
                continue
            if apenas_ativos and linha.get("SITUACAO_CADASTRAL", "").strip().upper() != "ATIVA":
                continue
            if apenas_matriz and linha.get("MATRIZ_FILIAL", "").strip().upper() != "MATRIZ":
                continue
            registros.append({
                "cnpj": cnpj,
                "email_base": email,
                "nome_fantasia": linha.get("NOME_FANTASIA") or linha.get("nome_fantasia") or "",
                "razao_social": linha.get("NOME_FANTASIA") or linha.get("nome_fantasia") or "",
            })
    return registros


def salvar_resultados_csv(resultados: List[Dict[str, str]], arquivo_saida: Path) -> None:
    campos = [
        "cnpj",
        "razao_social",
        "nome_fantasia",
        "decisores_qsa",
        "dominios",
        "origem_dominios",
        "emails_base",
        "emails_encontrados",
        "email_prioritario",
        "obs",
    ]
    with arquivo_saida.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=campos)
        writer.writeheader()
        for resultado in resultados:
            writer.writerow({campo: resultado.get(campo, "") for campo in campos})


def processar_csv(
    input_path: Path,
    output_path: Path,
    limit: Optional[int] = None,
    workers: int = 3,
) -> None:
    logger.info("Iniciando processar_csv input=%s output=%s workers=%s limit=%s", input_path, output_path, workers, limit)
    registros = carregar_cnpjs_csv(input_path)
    logger.info("Arquivo CSV carregado com %s registros", len(registros))
    if limit is not None:
        registros = registros[:limit]

    campos = [
        "cnpj",
        "razao_social",
        "nome_fantasia",
        "decisores_qsa",
        "dominios",
        "origem_dominios",
        "emails_base",
        "emails_encontrados",
        "email_prioritario",
        "obs",
    ]

    processed_cnpjs: Set[str] = set()
    file_exists = output_path.exists()
    if file_exists:
        try:
            with output_path.open("r", newline="", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    c = row.get("cnpj", "") or ""
                    c_limpo = limpar_cnpj(c)
                    if c_limpo:
                        processed_cnpjs.add(c_limpo)
            logger.info("Encontrados %s CNPJs já processados no arquivo de saída.", len(processed_cnpjs))
        except Exception as exc:
            logger.error("Não foi possível ler arquivo de saída existente: %s", exc)

    novos = [r for r in registros if limpar_cnpj(r.get("cnpj", "")) not in processed_cnpjs]
    if not novos:
        logger.info("Nenhum novo CNPJ para processar. Saindo.")
        return

    logger.info("%s CNPJs novos para processar (workers=%s).", len(novos), workers)

    mode = "a" if file_exists else "w"
    with output_path.open(mode, newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=campos)
        if not file_exists:
            writer.writeheader()

        if workers and workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_reg = {}
                registros_iter = iter(novos)
                total = len(novos)
                submitted = 0
                completed = 0

                for _ in range(min(workers, total)):
                    reg = next(registros_iter, None)
                    if reg is None:
                        break
                    cnpj = reg.get("cnpj", "")
                    logger.info("Enviando worker para CNPJ %s", cnpj)
                    future = executor.submit(
                        gerar_resultado_prospeccao,
                        reg["cnpj"],
                        reg.get("email_base"),
                        reg.get("nome_fantasia"),
                        reg.get("razao_social"),
                    )
                    future_to_reg[future] = reg
                    submitted += 1

                logger.info("Workers iniciados: %s/%s", submitted, total)
                while future_to_reg:
                    logger.info("Aguardando conclusão de %s workers ativos", len(future_to_reg))
                    done, _ = concurrent.futures.wait(
                        future_to_reg,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for fut in done:
                        reg = future_to_reg.pop(fut)
                        cnpj = reg.get("cnpj", "")
                        try:
                            resultado = fut.result()
                            logger.info("Worker finalizado CNPJ %s", cnpj)
                        except Exception as e:
                            logger.error("Worker falhou para CNPJ %s: %s", cnpj, e)
                            resultado = {
                                "cnpj": cnpj,
                                "razao_social": reg.get("razao_social", ""),
                                "nome_fantasia": reg.get("nome_fantasia", ""),
                                "decisores_qsa": "",
                                "dominios": "",
                                "origem_dominios": "",
                                "emails_base": reg.get("email_base", ""),
                                "emails_encontrados": "",
                                "email_prioritario": "",
                                "obs": f"Erro: {e}",
                            }
                        writer.writerow({campo: resultado.get(campo, "") for campo in campos})
                        csvfile.flush()
                        completed += 1
                        pending = submitted - completed
                        remaining = total - submitted
                        logger.info("Concluídos: %s, em andamento: %s, restantes por submeter: %s", completed, pending, remaining)

                        reg = next(registros_iter, None)
                        if reg is not None:
                            cnpj = reg.get("cnpj", "")
                            logger.info("Enviando novo worker para CNPJ %s", cnpj)
                            future = executor.submit(
                                gerar_resultado_prospeccao,
                                reg["cnpj"],
                                reg.get("email_base"),
                                reg.get("nome_fantasia"),
                                reg.get("razao_social"),
                            )
                            future_to_reg[future] = reg
                            submitted += 1
                            logger.info("Workers iniciados: %s/%s", submitted, total)

                logger.info("Todos os workers concluídos. Processados %s registros.", completed)
        else:
            for registro in novos:
                logger.info("Processando CNPJ %s", registro["cnpj"])
                try:
                    resultado = gerar_resultado_prospeccao(
                        registro["cnpj"],
                        email_base=registro.get("email_base"),
                        nome_fantasia=registro.get("nome_fantasia"),
                        razao_social=registro.get("razao_social"),
                    )
                except Exception as e:
                    logger.error("Erro no processamento sequencial do CNPJ %s: %s", registro["cnpj"], e)
                    resultado = {
                        "cnpj": registro.get("cnpj", ""),
                        "razao_social": registro.get("razao_social", ""),
                        "nome_fantasia": registro.get("nome_fantasia", ""),
                        "decisores_qsa": "",
                        "dominios": "",
                        "origem_dominios": "",
                        "emails_base": registro.get("email_base", ""),
                        "emails_encontrados": "",
                        "email_prioritario": "",
                        "obs": f"Erro: {e}",
                    }

                writer.writerow({campo: resultado.get(campo, "") for campo in campos})
                csvfile.flush()
                time.sleep(0.1)

    logger.info("processar_csv finalizado. Resultados salvos em: %s", output_path)
