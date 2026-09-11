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


def iterar_cnpjs_csv(
    path: Path, apenas_ativos: bool = True, apenas_matriz: bool = True, uf: str = "SP"
) -> Iterator[Dict[str, str]]:
    uf = uf.strip().upper()
    if len(uf) != 2 or not uf.isalpha():
        raise ValueError("Informe uma UF com duas letras, por exemplo SP.")
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as csvfile:
        leitor = csv.DictReader(csvfile)
        leitor.fieldnames = [campo.strip().upper() for campo in (leitor.fieldnames or [])]
        if "UF" not in leitor.fieldnames:
            raise ValueError("O CSV precisa da coluna UF para filtrar empresas por estado.")
        for linha in leitor:
            if (linha.get("UF") or "").strip().upper() != uf:
                continue
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


def carregar_cnpjs_csv(
    path: Path, apenas_ativos: bool = True, apenas_matriz: bool = True, uf: str = "SP"
) -> List[Dict[str, str]]:
    return list(iterar_cnpjs_csv(path, apenas_ativos, apenas_matriz, uf))


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


def _selecionar_novos(path: Path, processados: Set[str], limit: Optional[int], uf: str = "SP") -> Tuple[List[Dict[str, str]], int]:
    novos = list(_iterar_novos(path, processados, limit, uf))
    return novos, len(novos)


def _iterar_novos(
    path: Path, processados: Set[str], limit: Optional[int], uf: str = "SP"
) -> Iterator[Dict[str, str]]:
    """Entrega registros aos poucos para não carregar o lote inteiro na memória."""
    vistos = set(processados)
    selecionados = 0
    for registro in iterar_cnpjs_csv(path, uf=uf):
        cnpj = limpar_cnpj(registro.get("cnpj", ""))
        if not cnpj or cnpj in vistos:
            continue
        vistos.add(cnpj)
        selecionados += 1
        yield registro
        if limit is not None and limit > 0 and selecionados >= limit:
            break


def _executar_registro(registro: Dict[str, str]) -> Dict:
    return gerar_resultado_prospeccao(
        registro["cnpj"],
        email_base=registro.get("email_base"),
        nome_fantasia=registro.get("nome_fantasia"),
        razao_social=registro.get("razao_social"),
    )


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
    uf: str = "SP",
    progress_every: int = 100,
) -> None:
    writing_marker = output_path.with_name(output_path.name + ".writing")
    writing_marker.write_text(f"pid={os.getpid()}\n", encoding="utf-8")
    try:
        logger.info("Iniciando processar_csv input=%s output=%s workers=%s limit=%s", input_path, output_path, workers, limit)
        file_exists = _migrar_cabecalho_saida(output_path)
        processados = _ler_processados(output_path)
        logger.info("Encontrados %s CNPJs já processados no arquivo de saída.", len(processados))

        logger.info("Filtrando empresas pela UF=%s antes da prospecção.", uf)
        novos = _iterar_novos(input_path, processados, limit, uf=uf)
        logger.info("Processamento em fluxo iniciado (workers=%s).", workers)
        mode = "a" if file_exists else "w"
        concluidos = 0
        inicio = time.monotonic()

        def gravar(writer: csv.DictWriter, csvfile, resultado: Dict) -> None:
            nonlocal concluidos
            writer.writerow({campo: resultado.get(campo, "") for campo in CAMPOS_RESULTADO})
            concluidos += 1
            if concluidos % 20 == 0:
                csvfile.flush()
            if progress_every > 0 and concluidos % progress_every == 0:
                duracao = max(time.monotonic() - inicio, 0.001)
                logger.info("Progresso: %s concluídos (%.2f empresas/s).", concluidos, concluidos / duracao)

        with output_path.open(mode, newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CAMPOS_RESULTADO)
            if not file_exists:
                writer.writeheader()

            if workers and workers > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    pendentes = {}
                    capacidade = max(workers * 2, workers)

                    def preencher_fila() -> None:
                        while len(pendentes) < capacidade:
                            try:
                                registro = next(novos)
                            except StopIteration:
                                break
                            pendentes[executor.submit(_executar_registro, registro)] = registro

                    preencher_fila()
                    while pendentes:
                        concluidas, _ = concurrent.futures.wait(
                            pendentes, return_when=concurrent.futures.FIRST_COMPLETED
                        )
                        for fut in concluidas:
                            registro = pendentes.pop(fut)
                            try:
                                resultado = fut.result()
                            except Exception as exc:
                                logger.exception("Worker falhou para CNPJ %s", registro.get("cnpj", ""))
                                resultado = _resultado_de_erro(registro, exc)
                            gravar(writer, csvfile, resultado)
                        preencher_fila()
            else:
                for registro in novos:
                    try:
                        resultado = _executar_registro(registro)
                    except Exception as exc:
                        logger.exception("Erro no processamento do CNPJ %s", registro.get("cnpj", ""))
                        resultado = _resultado_de_erro(registro, exc)
                    gravar(writer, csvfile, resultado)

            csvfile.flush()

        if concluidos:
            duracao = max(time.monotonic() - inicio, 0.001)
            logger.info("Finalizado: %s registros em %.1fs (%.2f empresas/s).", concluidos, duracao, concluidos / duracao)
        else:
            logger.info("Nenhum novo CNPJ elegível para processar.")
        logger.info("Resultados salvos em: %s", output_path)
    finally:
        writing_marker.unlink(missing_ok=True)
