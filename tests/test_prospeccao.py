import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from app.controllers.csv_controller import _selecionar_novos, iterar_cnpjs_csv, processar_csv
from app.repositories.site_repository import (
    _buscar_emails_site_cached,
    buscar_emails_site,
    extrair_emails_pagina,
)
from app.services.prospeccao_service import gerar_resultado_prospeccao


class ProspeccaoTests(unittest.TestCase):
    def test_filtro_antes_do_limite_e_da_consulta(self):
        with tempfile.TemporaryDirectory() as pasta:
            entrada = Path(pasta) / 'entrada.csv'
            entrada.write_text(
                'CNPJ_COMPLETO,UF,SITUACAO_CADASTRAL,MATRIZ_FILIAL\n'
                '11111111000111,RJ,ATIVA,MATRIZ\n'
                '22222222000122, sp ,ATIVA,MATRIZ\n'
                '33333333000133,SP,BAIXADA,MATRIZ\n'
                '44444444000144,SP,ATIVA,FILIAL\n', encoding='utf-8-sig')
            registros, _ = _selecionar_novos(entrada, set(), 1)
            self.assertEqual([r['cnpj'] for r in registros], ['22222222000122'])
            with patch('app.controllers.csv_controller.gerar_resultado_prospeccao', return_value={}) as busca:
                processar_csv(entrada, Path(pasta) / 'saida.csv', workers=2)
            self.assertEqual(busca.call_count, 1)
            self.assertEqual(busca.call_args.args[0], '22222222000122')
            entrada.write_text('CNPJ\n11111111000111\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'coluna UF'):
                list(iterar_cnpjs_csv(entrada))

    def test_mailto_e_dados_estruturados(self):
        soup = BeautifulSoup('''
            <a href="MAILTO:vendas%40empresa.com.br;contato@empresa.com.br?subject=oi">Email</a>
            <script type="application/ld+json">{"contactPoint":[{"email":"diretoria@empresa.com.br"}]}</script>
            <script>var lixo = "oculto@terceiro.com";</script>
        ''', 'html.parser')
        self.assertEqual(extrair_emails_pagina(soup), {
            'vendas@empresa.com.br', 'contato@empresa.com.br', 'diretoria@empresa.com.br'})

    def test_link_real_tem_prioridade(self):
        _buscar_emails_site_cached.cache_clear()
        def resposta(url, **kwargs):
            html = '<a href="/canal-real#email">Contato</a>' if url.endswith('/') else 'vendas@empresa.com.br'
            return Mock(status_code=200, url=url, headers={'content-type': 'text/html'}, text=html)
        session = Mock()
        session.get.side_effect = resposta
        with patch('app.repositories.site_repository.procurar_site', return_value='https://empresa.com.br/'), patch('app.repositories.site_repository.get_session', return_value=session):
            emails = buscar_emails_site('empresa.com.br', limite_paginas=2)
        self.assertIn('vendas@empresa.com.br', emails)
        self.assertEqual(session.get.call_args_list[1].args[0], 'https://empresa.com.br/canal-real')

    def test_cache_e_site_confirmado_evitam_requisicoes_repetidas(self):
        _buscar_emails_site_cached.cache_clear()
        response = Mock(
            status_code=200, url='https://cache-teste.com.br/',
            headers={'content-type': 'text/html'}, text='vendas@cache-teste.com.br')
        session = Mock()
        session.get.return_value = response
        with patch('app.repositories.site_repository.procurar_site') as procurar, patch(
            'app.repositories.site_repository.get_session', return_value=session
        ):
            primeira = buscar_emails_site(
                'cache-teste.com.br', limite_paginas=1,
                site_confirmado='https://cache-teste.com.br/')
            segunda = buscar_emails_site(
                'cache-teste.com.br', limite_paginas=1,
                site_confirmado='https://cache-teste.com.br/')
        procurar.assert_not_called()
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(primeira, segunda)

    def test_email_api_e_multiplos_dominios(self):
        with patch('app.services.prospeccao_service.consultar_cnpj_brasilapi', return_value={'email': 'vendas@empresa.com.br'}), patch('app.services.prospeccao_service.ENABLE_RDAP', False), patch('app.services.prospeccao_service.buscar_emails_site', return_value=set()) as busca:
            resultado = gerar_resultado_prospeccao('11111111000111', email_base='contato@outra.com.br;financeiro@outra.com.br')
        self.assertEqual({c.args[0] for c in busca.call_args_list}, {'empresa.com.br', 'outra.com.br'})
        self.assertIn('vendas@empresa.com.br', resultado['emails_encontrados'])
        self.assertIn('financeiro@outra.com.br', resultado['emails_encontrados'])
        self.assertEqual(resultado['confianca_email'], 'media')


if __name__ == '__main__':
    unittest.main()
