import csv
import concurrent.futures
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

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
    registros = carregar_cnpjs_csv(input_path)
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
            print(f"[i] Encontrados {len(processed_cnpjs)} CNPJs já processados no arquivo de saída.")
        except Exception as exc:
            print(f"[-] Não foi possível ler arquivo de saída existente: {exc}")

    novos = [r for r in registros if limpar_cnpj(r.get("cnpj", "")) not in processed_cnpjs]
    if not novos:
        print("[i] Nenhum novo CNPJ para processar. Saindo.")
        return

    print(f"[i] {len(novos)} CNPJs novos para processar (workers={workers}).")

    mode = "a" if file_exists else "w"
    with output_path.open(mode, newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=campos)
        if not file_exists:
            writer.writeheader()

        if workers and workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_reg = {}
                for reg in novos:
                    cnpj = reg.get("cnpj", "")
                    print(f"\n=== Processando CNPJ {cnpj} ===", flush=True)
                    future_to_reg[executor.submit(
                        gerar_resultado_prospeccao,
                        reg["cnpj"],
                        reg.get("email_base"),
                        reg.get("nome_fantasia"),
                        reg.get("razao_social"),
                    )] = reg

                for fut in concurrent.futures.as_completed(future_to_reg):
                    reg = future_to_reg[fut]
                    try:
                        resultado = fut.result()
                    except Exception as e:
                        resultado = {
                            "cnpj": reg.get("cnpj", ""),
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
        else:
            for registro in novos:
                print(f"\n=== Processando CNPJ {registro['cnpj']} ===")
                try:
                    resultado = gerar_resultado_prospeccao(
                        registro["cnpj"],
                        email_base=registro.get("email_base"),
                        nome_fantasia=registro.get("nome_fantasia"),
                        razao_social=registro.get("razao_social"),
                    )
                except Exception as e:
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

    print(f"\nResultados salvos em: {output_path}")
