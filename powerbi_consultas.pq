// ======================================================================
// POWER BI: Obter Dados > Consulta em branco > Editor Avançado
// Cole UMA das duas versões abaixo (JSON ou CSV).
// Ao pedir credenciais, escolha "Anônimo" (o token já vai no cabeçalho).
// Troque o IP, a porta, o nome da tabela e o SEU_TOKEN.
// ======================================================================


// ---------- VERSÃO 1: JSON (tabelas pequenas/médias) -----------------
let
    Url      = "http://192.168.204.5:5000/api/powerbi/erros",
    Token    = "SEU_TOKEN",
    Resposta = Web.Contents(Url, [Headers = [Authorization = "Bearer " & Token]]),
    Registros = Json.Document(Resposta),
    Tabela   = Table.FromRecords(Registros),
    // Os tipos vêm do SQLite: números chegam como número, datas chegam como texto.
    // Converta as datas conforme o formato em que você as grava no banco.
    Tipada   = Table.TransformColumnTypes(Tabela, {
        {"id", Int64.Type},
        {"quant_envio", Int64.Type},
        {"quant_erros", Int64.Type}
    })
in
    Tipada


// ---------- VERSÃO 2: CSV (tabelas grandes, mais leve e rápido) ------
let
    Url      = "http://192.168.204.5:5000/api/powerbi/erros?formato=csv",
    Token    = "SEU_TOKEN",
    Bruto    = Web.Contents(Url, [Headers = [Authorization = "Bearer " & Token]]),
    Csv      = Csv.Document(Bruto, [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Cabecalho = Table.PromoteHeaders(Csv, [PromoteAllScalars = true]),
    Tipada   = Table.TransformColumnTypes(Cabecalho, {
        {"id", Int64.Type},
        {"quant_envio", Int64.Type},
        {"quant_erros", Int64.Type}
    })
in
    Tipada
