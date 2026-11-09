# Sistema de Erros de Envio

Sistema web interno para registrar, consultar e analisar **erros de envio** de e-mail (AWS e Locaweb) e SMS (Flux), com dashboards de acompanhamento e API para integração com Power BI.

## Sumário

- [Funcionalidades](#funcionalidades)
- [Tecnologias](#tecnologias)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Como executar](#como-executar)
- [Perfis de acesso](#perfis-de-acesso)
- [Importação de arquivos CSV](#importação-de-arquivos-csv)
- [Rotas principais](#rotas-principais)
- [API Power BI](#api-power-bi)
- [Estrutura do banco de dados](#estrutura-do-banco-de-dados)
- [Licença](#licença)

## Funcionalidades

- Login com senha criptografada e troca de senha obrigatória no primeiro acesso.
- Gestão de usuários (cadastro, ativar/desativar e reset de senha), restrita ao administrador.
- Cadastro de lotes de envio com quantidade enviada, quantidade de erros, tipo de envio e empresa.
- Importação de CSV com o detalhamento dos erros de cada provedor (AWS, Flux/SMS e Locaweb).
- Listagem paginada de erros, com detalhe por provedor.
- Dashboards com filtros por período e empresa:
  - **Gráficos gerais** (`/graficos`): visão consolidada de todos os canais.
  - **Dashboard AWS**: taxa de erro, status de reputação (Saudável, Alerta ou Crítico), principais códigos de erro e principais destinatários ofensores.
  - **Dashboard SMS**: taxa de erro, erros por operadora e principais números ofensores.
- Endpoint JSON protegido por token para consumo no Power BI.
- Limite de requisições no login (10 por minuto) e limite de upload de 16 MB.

## Tecnologias

- Python 3
- Flask
- Flask-Limiter (com Redis)
- SQLite
- Pandas

## Pré-requisitos

- Python 3.9 ou superior
- Redis em execução em `localhost:6379` (usado pelo controle de limite de requisições)
- `pip`

## Instalação

```bash
# 1. Clone o repositório
git clone <url-do-repositorio>
cd <pasta-do-projeto>

# 2. (Opcional) Crie e ative um ambiente virtual
python -m venv venv
source venv/bin/activate      # Linux/macOS
venv\Scripts\activate         # Windows

# 3. Instale as dependências
pip install -r requirements.txt
```

## Variáveis de ambiente

| Variável       | Descrição                                                                 | Padrão                                  |
|----------------|---------------------------------------------------------------------------|-----------------------------------------|
| `secret_key`   | Chave de assinatura da sessão do Flask                                    | Gerada aleatoriamente a cada inicialização |
| `POWERBI_TOKEN`| Token Bearer exigido pela API do Power BI                                 | Valor padrão definido no código         |
| `FLASK_DEBUG`  | Ativa o modo debug (`true` ou `false`)                                    | `false`                                 |

> **Importante:** em produção, defina `secret_key` e `POWERBI_TOKEN` com valores próprios. Sem `secret_key` fixa, as sessões dos usuários são invalidadas a cada reinício do sistema.

Exemplo (Linux/macOS):

```bash
export secret_key="troque-por-uma-chave-segura"
export POWERBI_TOKEN="troque-por-um-token-seguro"
```

## Como executar

```bash
python app.py
```

O sistema fica disponível em `http://localhost:5000`.

Na primeira execução, o banco `database.db` (SQLite) e todas as tabelas são criados automaticamente, junto com um usuário administrador padrão:

- **E-mail:** `ti@northtecnologia.com.br`
- **Senha:** consulte a equipe de TI responsável pelo sistema.

> Altere a senha do administrador no primeiro acesso.

## Perfis de acesso

| Perfil     | Permissões                                                              |
|------------|-------------------------------------------------------------------------|
| `admin`    | Acesso total: usuários, cadastro e exclusão de erros, listagens e dashboards |
| `gestor`   | Visualização geral                                                      |
| `analista` | Visualização de listagens de erros e dashboards                         |
| `viewer`   | Perfil padrão de novos usuários, com acesso limitado                    |

## Importação de arquivos CSV

Ao cadastrar um erro em `/adicionar_erro`, é possível anexar o CSV do provedor. O tipo de envio selecionado define o formato esperado:

| Tipo de envio              | Colunas esperadas no CSV                                                              | Codificação |
|----------------------------|---------------------------------------------------------------------------------------|-------------|
| `Envio de email AWS`       | `sendtimestamp`, `destination`, `subject`, `diagnostic_code`                          | UTF-8       |
| `Envio de SMS`             | `STATUS`, `DDD`, `TELEFONE`, `TIPO SERVICO`, `DATA ENVIO`, `OPERADORA`                | UTF-8       |
| `Envio de Email Locaweb`   | `Data de envio`, `Destinatário`, `Motivo do bounce`, `Assunto`                        | Latin-1     |

Observações:

- No SMS, linhas com status `entregue` ou `enviada` são ignoradas; somente as falhas são gravadas.
- O arquivo enviado é removido do servidor após a importação.

## Rotas principais

| Rota                        | Descrição                                   | Acesso              |
|-----------------------------|---------------------------------------------|---------------------|
| `/login`                    | Tela de login                               | Público             |
| `/trocar_senha`             | Troca de senha                              | Usuário logado      |
| `/erros`                    | Lista paginada de erros                     | Admin e analista    |
| `/adicionar_erro`           | Cadastro de erro e importação de CSV        | Admin               |
| `/erros_aws/<id>`           | Detalhe dos erros AWS                       | Admin e analista    |
| `/erros_sms/<id>`           | Detalhe dos erros de SMS                    | Admin e analista    |
| `/erros_locaweb/<id>`       | Detalhe dos erros Locaweb                   | Admin e analista    |
| `/graficos`                 | Dashboard geral                             | Usuário logado      |
| `/dashboard/aws`            | Dashboard de e-mail AWS                     | Usuário logado      |
| `/dashboard/sms`            | Dashboard de SMS                            | Usuário logado      |
| `/usuarios`                 | Gestão de usuários                          | Admin               |
| `/cadastro_usuario`         | Cadastro de usuário                         | Admin               |
| `/api/powerbi/<tabela>`     | API de dados para Power BI                  | Token Bearer        |

## API Power BI

Retorna o conteúdo de uma tabela em JSON.

```
GET /api/powerbi/<tabela>
Authorization: Bearer <POWERBI_TOKEN>
```

Tabelas disponíveis: `erros`, `erros_aws`, `erros_flux`, `erros_locaweb` e `usuarios`.

Exemplo:

```bash
curl -H "Authorization: Bearer SEU_TOKEN" http://localhost:5000/api/powerbi/erros
```

Resposta:

```json
{
  "status": "sucesso",
  "tabela": "erros",
  "total_linhas": 10,
  "colunas": ["id", "data", "quant_envio", "quant_erros", "tipo_envio", "empresa"],
  "dados": []
}
```

## Estrutura do banco de dados

Banco SQLite (`database.db`), criado automaticamente.

- `usuarios`: usuários, perfis e controle de troca de senha.
- `erros`: lote de envio (data, quantidade enviada, quantidade de erros, tipo e empresa).
- `erros_aws`: detalhe dos erros de e-mail AWS, ligado a `erros`.
- `erros_flux`: detalhe dos erros de SMS, ligado a `erros`.
- `erros_locaweb`: detalhe dos bounces da Locaweb, ligado a `erros`.

## Licença

Copyright (c) 2026 Gabriel Souza

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
