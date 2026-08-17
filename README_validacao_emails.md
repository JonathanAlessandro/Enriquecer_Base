# Validador passivo de e-mails

## Objetivo

`validar_emails.py` faz higiene automática da lista sem enviar nenhuma mensagem. Ele valida sintaxe, normaliza endereços, consulta registros MX por DNS, separa provedores genéricos, identifica caixas de função, aplica uma lista de supressão e marca domínios descartáveis.

O resultado é uma **triagem técnica**, não uma garantia de que a caixa existe, está ativa, pertence à empresa ou não é spam trap. A única forma de saber se uma conta aceita uma mensagem é enviar uma mensagem legítima ou usar um serviço especializado autorizado; este projeto deliberadamente não faz probing SMTP nem envia e-mails de teste.

## Instalação

```powershell
python -m pip install -r requirements-email-validation.txt
```

## Execução sobre o resultado da prospecção

```powershell
python validar_emails.py `
  --input-csv prospeccao_resultados.csv `
  --output-csv emails_validados.csv `
  --suppressions email_suppressions.csv `
  --disposable-domains disposable_domains.txt `
  --workers 4
```

O script deduplica os endereços antes da consulta DNS. Os workers são usados apenas para consultar domínios MX distintos; eles não enviam mensagens e não escrevem concorrencialmente no arquivo.

## Execução simultânea com `main.py`

O validador pode ser iniciado enquanto o enriquecimento principal está rodando, desde que o `csv_controller.py` esteja atualizado com o marcador de escrita. O processo principal cria:

```text
prospeccao_resultados.csv.writing
```

Enquanto esse arquivo existir, o validador aguarda. Quando o marcador desaparece e o CSV fica sem alterações durante `--stable-seconds`, o validador copia o arquivo para um snapshot temporário e trabalha somente sobre essa cópia. Assim, ele não lê uma linha enquanto ela está sendo gravada e não disputa a escrita da planilha principal.

Comandos em dois terminais PowerShell:

```powershell
# Terminal 1 — enriquecimento
python main.py `
  --input-csv csv_input\\BASE_TOTAL_ESTABELECIMENTOS_RECEITA.csv `
  --output-csv prospeccao_resultados.csv `
  --workers 3 `
  --limit 30
```

```powershell
# Terminal 2 — validação; aguarda até uma hora pelo CSV ficar estável
python validar_emails.py `
  --input-csv prospeccao_resultados.csv `
  --output-csv emails_validados.csv `
  --suppressions email_suppressions.csv `
  --disposable-domains disposable_domains.txt `
  --workers 4 `
  --wait-timeout 3600 `
  --stable-seconds 3 `
  --poll-seconds 1
```

O validador processa o snapshot disponível quando o enriquecimento termina. Ele não acompanha novas linhas que sejam adicionadas depois do snapshot; para validar essas novas linhas, execute-o novamente.

## Lista de supressão

Edite `email_suppressions.csv` com endereços ou domínios que nunca devem ser usados:

```csv
email,domain,reason
antigo@empresa.com.br,,hard bounce em 2026-08-17
,empresa-bloqueada.com.br,reclamação ou opt-out
```

A lista deve incluir endereços que retornaram hard bounce, reclamação, opt-out, pedido de remoção, abuso ou qualquer outra indicação de que não devem receber novos contatos.

## Interpretação do resultado

| Status | Significado | Recomendação |
|---|---|---|
| `valid_mx_review` | Sintaxe válida e domínio possui MX | Ainda requer revisão; não é garantia de caixa ativa |
| `no_mx` | Domínio não possui MX utilizável | Não enviar |
| `invalid_syntax` | Formato inválido ou contaminado | Não enviar |
| `disposable_domain` | Domínio temporário conhecido | Não enviar |
| `suppressed` | Endereço/domínio consta na supressão | Não enviar |
| `dns_pending` | DNS não foi confirmado | Revisão manual; não enviar automaticamente |

`recommendation=eligible_for_manual_review` significa que o contato passou pelas verificações passivas básicas. Não significa autorização para disparo automático. `recommendation=manual_review` é usado para provedores genéricos, caixas de função e resultados que precisam de avaliação contextual.

## O que o método consegue e não consegue detectar

Ele consegue remover duplicatas, corrigir caixa e pontuação externa, rejeitar endereços contaminados, detectar domínios sem MX, separar provedores genéricos e aplicar supressão própria. Ele não consegue confirmar a existência de uma caixa individual, diferenciar todos os spam traps, medir reputação de envio, avaliar consentimento, determinar se o destinatário quer contato ou provar que o e-mail pertence ao decisor do QSA.

Por isso, para proteger o domínio, use primeiro um grupo pequeno de contatos corporativos relevantes, mantenha uma lista de supressão centralizada, processe hard bounces e reclamações imediatamente e não envie para `no_mx`, `suppressed`, `disposable_domain` ou `invalid_syntax`.
