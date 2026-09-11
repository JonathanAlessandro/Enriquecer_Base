import argparse
import requests

DEFAULT_URL = "https://www.registro.br/"


def knock(url: str, method: str = "GET") -> None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
    }

    print(f"Fazendo {method} em: {url}")

    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            timeout=20,
            allow_redirects=True,
        )

        print(f"Status HTTP: {response.status_code}")
        print(f"URL final: {response.url}")

        if response.status_code == 403:
            print("Resultado: 403 Forbidden -> acesso bloqueado ou sem permissao.")
        elif 200 <= response.status_code < 300:
            print("Resultado: Aceito / acesso liberado.")
        elif 401:
            print("Resultado: 401 Unauthorized -> precisa de autenticação.")
        else:
            print(f"Resultado: resposta inesperada para esse endpoint: {response.status_code}")

        print("--- corpo (preview) ---")
        print(response.text[:800])

    except Exception as exc:
        print(f"Erro ao acessar a URL: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Faz um knock simples numa API e mostra se ela responde 403 ou aceita.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="URL da API ou endpoint a testar")
    parser.add_argument("--method", default="GET", help="Método HTTP: GET, HEAD, POST, etc.")
    args = parser.parse_args()

    knock(args.url, args.method)
