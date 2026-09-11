import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.controllers.csv_controller import processar_csv
from app.services.prospeccao_service import gerar_resultado_prospeccao


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de prospecção de e-mails por CNPJ e domínio oficial.")
    parser.add_argument("--cnpj", help="CNPJ a ser processado isoladamente")
    parser.add_argument("--input-csv", help="Arquivo CSV de entrada com CNPJ_COMPLETO e EMAIL")
    parser.add_argument("--output-csv", help="Arquivo CSV de saída (padrão: prospeccao_resultados_<uf>.csv)")
    parser.add_argument("--uf", default="SP", type=lambda valor: valor.strip().upper(),
                        choices="AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split(),
                        help="UF das empresas no CSV (padrão: SP, estado de São Paulo)")
    parser.add_argument("--limit", type=int, help="Limite de registros a processar")
    parser.add_argument("--workers", type=int, default=1, help="Número de threads para processar em paralelo (padrão: 1)")
    parser.add_argument("--progress-every", type=int, default=100, help="Exibe progresso a cada N empresas (padrão: 100)")
    args = parser.parse_args()

    if args.input_csv:
        output_path = Path(args.output_csv or f"prospeccao_resultados_{args.uf.lower()}.csv")
        processar_csv(
            Path(args.input_csv), output_path, limit=args.limit, workers=args.workers,
            uf=args.uf, progress_every=args.progress_every,
        )
        return

    if not args.cnpj:
        print("Informe --cnpj ou --input-csv para processar.")
        return

    resultado = gerar_resultado_prospeccao(args.cnpj)
    for chave, valor in resultado.items():
        print(f"{chave}: {valor}")


if __name__ == "__main__":
    main()
