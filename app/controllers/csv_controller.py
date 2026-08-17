import concurrent.futures
import csv
import os
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

from app.config import logger
from app.services.prospeccao_service import gerar_resultado_prospeccao
from app.utils.text import limpar_cnpj


CAMPOS_RESULTADO = [
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "decisores_qsa",
    "dominios",
    "origem_dominios",
    "emails_base",
    "emails_encontrados",
    "emails_corporativos",
    "emails_genericos",
    "email_prioritario",
    "tipo_email_prioritario",
    "confianca_email",
    "origem_emails",
    "obs",
]


def iterar_cnpjs_csv(path: Path, apenas_ativos: bool = True, apenas_matriz: bool = True) -> Iterator[Dict[str, str]]:
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
            yield {
                "cnpj": cnpj,
                "email_base": email,
                "nome_fantasia": linha.get("NOME_FANTASIA") or linha.get("nome_fantasia") or "",
                "razao_social": (
                    linha.get("RAZAO_SOCIAL")
                    or linha.get("razao_social")
                    or linha.get("NOME_FANTASIA")
                    or linha.get("nome_fantasia")
                    or ""
                ),
            }


def carregar_cnpjs_csv(path: Path, apenas_ativos: bool = True, apenas_matriz: bool = True) -> List[Dict[str, str]]:
    return list(iterar_cnpjs_csv(path, apenas_ativos, apenas_matriz))


def _migrar_cabecalho_saida(arquivo_saida: Path) -> bool:
    if not arquivo_saida.exists() or arquivo_saida.stat().st_size == 0:
        return False

    with arquivo_saida.open("r", newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        campos_atuais = reader.fieldnames or []
        if all(campo in campos_atuais for campo in CAMPOS_RESULTADO):
            return True
        linhas = list(reader)

    temporario = arquivo_saida.with_suffix(arquivo_saida.suffix + ".tmp")
    with temporario.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS_RESULTADO)
        writer.writeheader()
        for linha in linhas:
            writer.writerow({campo: linha.get(campo, "") or "" for campo in CAMPOS_RESULTADO})
    os.replace(temporario, arquivo_saida)
    logger.info("Cabeçalho de %s migrado para incluir as novas colunas de contato.", arquivo_saida)
    return True


def _ler_processados(arquivo_saida: Path) -> Set[str]:
    processados: Set[str] = set()
    if not arquivo_saida.exists() or arquivo_saida.stat().st_size == 0:
        return processados
    with arquivo_saida.open("r", newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cnpj = limpar_cnpj(row.get("cnpj", "") or "")
            if cnpj:
                processados.add(cnpj)
    return processados


def _selecionar_novos(path: Path, processados: Set[str], limit: Optional[int]) -> Tuple[List[Dict[str, str]], int]:
    novos: List[Dict[str, str]] = []
    vistos = set(processados)
    linhas_lidas = 0
    for registro in iterar_cnpjs_csv(path):
        linhas_lidas += 1
        cnpj = limpar_cnpj(registro.get("cnpj", ""))
        if not cnpj or cnpj in vistos:
            continue
        vistos.add(cnpj)
        novos.append(registro)
        if limit is not None and limit > 0 and len(novos) >= limit:
            break
    return novos, linhas_lidas


def _resultado_de_erro(registro: Dict[str, str], exc: Exception) -> Dict[str, str]:
    return {
        "cnpj": registro.get("cnpj", ""),
        "razao_social": registro.get("razao_social", ""),
        "nome_fantasia": registro.get("nome_fantasia", ""),
        "decisores_qsa": "",
        "dominios": "",
        "origem_dominios": "fallback",
        "emails_base": registro.get("email_base", ""),
        "emails_encontrados": "",
        "emails_corporativos": "",
        "emails_genericos": "",
        "email_prioritario": "",
        "tipo_email_prioritario": "",
        "confianca_email": "",
        "origem_emails": "nenhuma",
        "obs": f"Erro: {exc}",
    }


def processar_csv(
    input_path: Path,
    output_path: Path,
    limit: Optional[int] = None,
    workers: int = 3,
) -> None:
    writing_marker = output_path.with_name(output_path.name + ".writing")
    writing_marker.write_text(f"pid={os.getpid()}\\n", encoding="utf-8")
    try:
        logger.info("Iniciando processar_csv input=%s output=%s workers=%s limit=%s", input_path, output_path, workers, limit)
        file_exists = _migrar_cabecalho_saida(output_path)
        processados = _ler_processados(output_path)
        logger.info("Encontrados %s CNPJs já processados no arquivo de saída.", len(processados))

        novos, linhas_lidas = _selecionar_novos(input_path, processados, limit)
        logger.info("Linhas elegíveis lidas até selecionar os novos: %s", linhas_lidas)
        if not novos:
            logger.info("Nenhum novo CNPJ para processar. Saindo.")
            return

        logger.info("%s CNPJs novos selecionados (workers=%s).", len(novos), workers)
        mode = "a" if file_exists else "w"
        with output_path.open(mode, newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CAMPOS_RESULTADO)
            if not file_exists:
                writer.writeheader()

            if workers and workers > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    tarefas = [
                        (
                            executor.submit(
                                gerar_resultado_prospeccao,
                                registro["cnpj"],
                                registro.get("email_base"),
                                registro.get("nome_fantasia"),
                                registro.get("razao_social"),
                            ),
                            registro,
                        )
                        for registro in novos
                    ]
                    # Os workers executam em paralelo, mas a gravação permanece na
                    # ordem do CSV de entrada para facilitar retomada e auditoria.
                    for fut, registro in tarefas:
                        try:
                            resultado = fut.result()
                        except Exception as exc:
                            logger.exception("Worker falhou para CNPJ %s", registro.get("cnpj", ""))
                            resultado = _resultado_de_erro(registro, exc)
                        writer.writerow({campo: resultado.get(campo, "") for campo in CAMPOS_RESULTADO})
                        csvfile.flush()
            else:
                for registro in novos:
                    try:
                        resultado = gerar_resultado_prospeccao(
                            registro["cnpj"],
                            email_base=registro.get("email_base"),
                            nome_fantasia=registro.get("nome_fantasia"),
                            razao_social=registro.get("razao_social"),
                        )
                    except Exception as exc:
                        logger.exception("Erro no processamento do CNPJ %s", registro.get("cnpj", ""))
                        resultado = _resultado_de_erro(registro, exc)
                    writer.writerow({campo: resultado.get(campo, "") for campo in CAMPOS_RESULTADO})
                    csvfile.flush()
                    time.sleep(0.1)

        logger.info("processar_csv finalizado. Resultados salvos em: %s", output_path)
    finally:
        writing_marker.unlink(missing_ok=True)
