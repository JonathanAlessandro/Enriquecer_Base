import argparse
from pathlib import Path

from app.controllers.csv_controller import processar_csv
from app.services.prospeccao_service import gerar_resultado_prospeccao
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de prospecção de e-mails por CNPJ e domínio oficial.")
    parser.add_argument("--cnpj", help="CNPJ a ser processado isoladamente")
    parser.add_argument("--input-csv", help="Arquivo CSV de entrada com CNPJ_COMPLETO e EMAIL")
    parser.add_argument("--output-csv", default="prospeccao_resultados.csv", help="Arquivo CSV de saída")
    parser.add_argument("--limit", type=int, help="Limite de registros a processar")
    parser.add_argument("--workers", type=int, default=1, help="Número de threads para processar em paralelo (padrão: 1)")
    args = parser.parse_args()

    if args.input_csv:
        processar_csv(Path(args.input_csv), Path(args.output_csv), limit=args.limit, workers=args.workers)
        return

    if not args.cnpj:
        print("Informe --cnpj ou --input-csv para processar.")
        return

    resultado = gerar_resultado_prospeccao(args.cnpj)
    for chave, valor in resultado.items():
        print(f"{chave}: {valor}")


if __name__ == "__main__":
    main()
