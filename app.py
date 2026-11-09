from flask import Flask, flash, render_template, request, redirect, session, abort, url_for, jsonify, Response, stream_with_context
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import sqlite3
import math
import csv
import hmac
import io
import json
import os
import secrets
import sys
import pandas as pd
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('secret_key') or secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limite de upload de 16MB
#csrf = CSRFProtect(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="redis://localhost:6379"
)

# ======================
# BANCO SQLITE
# ======================
def get_db():
    conn= sqlite3.connect('database.db', timeout=30, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA foreign_keys=ON;')

    return conn


def criar_banco():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT,
        email TEXT UNIQUE,
        senha TEXT,
        trocar_senha INTEGER DEFAULT 1,
        ativo INTEGER DEFAULT 1,
        perfil TEXT DEFAULT 'viewer'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS erros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT,
        quant_envio INTEGER,
        quant_erros INTEGER,
        tipo_envio TEXT,
        empresa TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS erros_aws (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
        erro_id INTERGER,
        sendtimestamp TEXT,
        destination TEXT,
        subject TEXT,
        diagnostic_code TEXT,
        foreign key (erro_id) references erros(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS erros_flux (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        erro_id INTERGER,
        telefone TEXT,
        tipo_servico TEXT,
        data_envio TEXT,
        status TEXT,
        operadora TEXT,
        foreign key (erro_id) references erros(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS erros_locaweb (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        erro_id INTEGER,
        data_envio TEXT,
        destinatario TEXT,
        motivo_bounce TEXT,
        assunto TEXT,
        foreign key (erro_id) references erros(id)
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_erros_data_empresa ON erros(data, empresa);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_erros_aws_code ON erros_aws(diagnostic_code);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_erros_flux_operadora ON erros_flux(operadora);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_erros_locaweb_bounce ON erros_locaweb(motivo_bounce);")

    # ADMIN PADRÃO
    cursor.execute("SELECT * FROM usuarios WHERE email=?", ("ti@northtecnologia.com.br",))
    admin = cursor.fetchone()

    if not admin:
        senha = generate_password_hash("North@@TI2022")

        cursor.execute("""
        INSERT INTO usuarios (nome, email, senha, trocar_senha, ativo, perfil)
        VALUES (?, ?, ?, 0, 1,'admin')
        """, ("Admin", "ti@northtecnologia.com.br", senha))

        print("✅ Admin criado: ti@northtecnologia.com.br / North@@TI2022")

    conn.commit()
    conn.close()


criar_banco()

# ======================
# PROTEÇÃO
# ======================
def login_required():
    return 'usuario' in session
def admin_required():
    return session.get('perfil') == 'admin'
def gestor_required():
    return session.get('perfil') in ['admin', 'gestor']
def analyst_required():
    return session.get('perfil') in ['admin', 'analista']
def pode_ver():
    return session.get('perfil') in ['admin', 'gestor', 'analista']
# ======================
# ROTAS
# ======================

@app.route('/')
def home():
    return redirect('/login')

# 🔐 LOGIN
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM usuarios WHERE email=?", (email,))
        user = cursor.fetchone()

        conn.close()

        if user and check_password_hash(user[3], senha):

            if user[5] == 0:
                return render_template('login.html', error="Usuário desativado")

            session['usuario'] = user[1]
            session['user_id'] = user[0]
            session['perfil'] = user[6]

            if user[4] == 1:
                return redirect('/trocar_senha')

            return redirect('/erros')

        return render_template('login.html', error="❌ Login inválido")

    return render_template('login.html', hide_sidebar=True)


# 🔐 TROCAR SENHA
@app.route('/trocar_senha', methods=['GET', 'POST'])
def trocar_senha():
    if not login_required():
        return redirect('/login')

    if request.method == 'POST':
        nova_senha = generate_password_hash(request.form['senha'])

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE usuarios
        SET senha=?, trocar_senha=0
        WHERE id=?
        """, (nova_senha, session['user_id']))

        conn.commit()
        conn.close()

        return redirect('/erros')

    return render_template('trocar_senha.html')

# 🔑 RESET SENHA
@app.route('/reset_senha/<int:id>')
def reset_senha(id):
    if not login_required() or not admin_required():
        abort(403)

    if id == session['user_id']:
        abort(400, "Não é possível resetar a própria senha")

    nova_senha_temp = secrets.token_urlsafe(10)
    nova = generate_password_hash(nova_senha_temp)

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE usuarios
    SET senha=?, trocar_senha=1
    WHERE id=?
    """, (nova, id))

    conn.commit()
    conn.close()

    flash(f"Senha temporária para o usuário: {nova_senha_temp}", "senha_reset")

    return redirect('/usuarios') 

# 🔄 ATIVAR/DESATIVAR
@app.route('/toggle_usuario/<int:id>')
def toggle_usuario(id):
    if not login_required() or not admin_required():
        abort(403)

    if id == session.get('user_id'):
        abort(400, "Não é possível desativar o próprio usuário")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ativo FROM usuarios WHERE id=?", (id,))
    row = cursor.fetchone()

    if row is None:
        conn.close()
        abort(404, "Usuário não encontrado")

    status_atual = int(row[0] or 0)
    novo_status = 0 if status_atual == 1 else 1
    cursor.execute("UPDATE usuarios SET ativo=? WHERE id=?", (novo_status, id))
    conn.commit()
    conn.close()
    return redirect('/usuarios')

# 🚪 LOGOUT
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# 👤 LISTA USUÁRIOS
@app.route('/usuarios')
def usuarios():
    if not login_required() or not admin_required():
        abort(403)

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT id, nome, email, ativo, perfil FROM usuarios")
    dados = cursor.fetchall()

    conn.close()

    return render_template('list_user.html', usuarios=dados)

# ➕ CADASTRO USUÁRIO
@app.route('/cadastro_usuario', methods=['GET', 'POST'])
def cadastro_usuario():
    if not login_required() or not admin_required():
        abort(403)

    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = generate_password_hash(request.form['senha'])
        perfil = request.form['perfil']

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO usuarios (nome, email, senha, trocar_senha, ativo, perfil)
        VALUES (?, ?, ?, 1, 1, ?)
        """, (nome, email, senha, perfil))

        conn.commit()
        conn.close()

        return redirect('/usuarios')

    return render_template('cad_user.html')

# 📊 ADICIONAR ERRO
@app.route('/adicionar_erro', methods=['GET', 'POST'])
def adicionar_erro():
    if not login_required() or not admin_required():
        abort(403)

    if request.method == 'POST':
        data = request.form['data']
        quant_envio = request.form['quant_envio']
        quant_erros = request.form['quant_erros']
        tipo_envio = request.form['tipo_envio']
        empresa = request.form['empresa']
        arquivo = request.files.get('arquivo')
        caminho = None

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO erros (data, quant_envio, quant_erros, tipo_envio, empresa)
        VALUES (?, ?, ?, ?, ?)
        """, (data, quant_envio, quant_erros, tipo_envio, empresa))

        erro_id = cursor.lastrowid

        if tipo_envio == 'Envio de email AWS' and arquivo:
            caminho= os.path.join(
                'uploads',
                secure_filename(arquivo.filename)
            )
            os.makedirs('uploads', exist_ok=True)
            arquivo.save(caminho)

            with open(
                caminho,
                newline='',
                encoding='utf-8-sig'
            ) as csvfile:
                reader = csv.DictReader(csvfile)
                print(reader.fieldnames)
                for row in reader:
                    cursor.execute("""
                    INSERT INTO erros_aws
                    (
                        erro_id,
                        sendtimestamp,
                        destination,
                        subject,
                        diagnostic_code) VALUES (?, ?, ?, ?, ?)
                    """,(
                        erro_id,
                        row['sendtimestamp'],
                        row['destination'],
                        row['subject'],
                        row['diagnostic_code']
                    ))
        
        if tipo_envio == 'Envio de SMS' and arquivo:
            caminho = os.path.join(
                'uploads',
                secure_filename(arquivo.filename)
            )
            os.makedirs('uploads', exist_ok=True)
            arquivo.save(caminho)

            with open(
                caminho,
                newline='',
                encoding='utf-8-sig'
            ) as csvfile:
                reader = csv.DictReader(csvfile, delimiter=';')
                
                # Normaliza as chaves do cabeçalho para maiúsculas (remove espaços extras)
                if reader.fieldnames:
                    reader.fieldnames = [str(name).strip().upper() for name in reader.fieldnames]
                
                print("Cabeçalhos detetados no CSV de SMS:", reader.fieldnames)
                
                for row in reader:
                    # Limpa as chaves do dicionário lido para maiúsculas para evitar erros de match
                    row_limpo = {str(k).strip().upper(): str(v).strip() for k, v in row.items() if k}
                    
                    status = row_limpo.get('STATUS', '').lower()
                    
                    # Se o status não for de sucesso (ou seja, é falha/erro), guarda
                    if status not in ['entregue', 'enviada']:
                        ddd = row_limpo.get('DDD', '')
                        telefone = row_limpo.get('TELEFONE', '')
                        tipo_servico = row_limpo.get('TIPO SERVICO', row_limpo.get('TIPO_SERVICO', ''))
                        data_envio = row_limpo.get('DATA ENVIO', row_limpo.get('DATA_ENVIO', ''))
                        operadora = row_limpo.get('OPERADORA', '')

                        telefone_completo = f"{ddd}{telefone}" if ddd else telefone

                        cursor.execute("""
                            INSERT INTO erros_flux
                            (
                                erro_id,
                                telefone,
                                tipo_servico,
                                data_envio,
                                status,
                                operadora
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            erro_id,
                            telefone_completo,
                            tipo_servico,
                            data_envio,
                            row_limpo.get('STATUS', ''),
                            operadora
                        ))

        if tipo_envio == 'Envio de Email Locaweb' and arquivo:
            caminho= os.path.join(
                'uploads',
                secure_filename(arquivo.filename)
            )
            os.makedirs('uploads', exist_ok=True)
            arquivo.save(caminho)

            with open(
                caminho,
                newline='',
                encoding='latin1'
            ) as csvfile:
                reader = csv.DictReader(csvfile)
                print(reader.fieldnames)
                for row in reader:
                    cursor.execute("""
                    INSERT INTO erros_locaweb
                    (
                        erro_id,
                        data_envio,
                        destinatario,
                        motivo_bounce,
                        assunto) VALUES (?, ?, ?, ?, ?)
                    """,(
                        erro_id,
                        row['Data de envio'],
                        row['Destinatário'],
                        row['Motivo do bounce'],
                        row['Assunto']
                    ))        
        

        conn.commit()
        conn.close()

        if caminho and os.path.exists(caminho):
            os.remove(caminho)

        return redirect('/erros')

    return render_template('cad_erros.html')

# 📊 LISTA ERROS
@app.route('/erros')
def erros():
    if not login_required() or not analyst_required():
        return redirect('/login')

    #----------------------------------------------------------
    # contador de paginas

    page = request.args.get('page', 1, type=int)

    per_page = request.args.get('per_page', 10, type=int)

    offset = (page - 1)*per_page

    #----------------------------------------------------------
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("select count(*) from erros")
    total = cursor.fetchone()[0]

    cursor.execute("""
    SELECT id, data, quant_envio, quant_erros, tipo_envio, empresa
    FROM erros
    ORDER BY id DESC
    LIMIT ? OFFSET ?
    """,(per_page, offset))

    dados_brutos = cursor.fetchall()
    conn.close()

    # Normaliza os campos numéricos para evitar divisão de strings no Jinja.
    def numero_seguro(valor):
        try:
            if valor is None or str(valor).strip() == "":
                return 0.0
            return float(str(valor).strip().replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    dados = []
    for linha in dados_brutos:
        linha = list(linha)
        linha[2] = numero_seguro(linha[2])  # quant_envio
        linha[3] = numero_seguro(linha[3])  # quant_erros
        dados.append(tuple(linha))

    total_pages = (total + per_page - 1)//per_page
    return render_template('list_erros.html', erros=dados, page=page, per_page=per_page, total_pages=total_pages)

# 🗑️ excluir erros
@app.route('/deletar_erro/<int:id>')
def deletar_erro(id):

    if not login_required() or not admin_required():
        return "Acesso negado"
    
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('delete from erros where id=?', (id,))
    cursor.execute('delete from erros_aws where erro_id=?', (id,))
    cursor.execute('delete from erros_flux where erro_id=?', (id,))
    cursor.execute('delete from erros_locaweb where erro_id=?', (id,))

    conn.commit()
    conn.close()

    return redirect('/erros')

# ☁️ Detalhe AWS
@app.route('/erros_aws/<int:erro_id>')
def erros_aws(erro_id):

    if not login_required() or not analyst_required():
        return redirect('/graficos')

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1 )* per_page

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        select COUNT(*) from erros_aws where erro_id=?
    """,(erro_id,))

    total = cursor.fetchone()[0]

    cursor.execute("""
        select 
            sendtimestamp,
            destination,
            subject
        from erros_aws
        where erro_id=?
        limit ? offset ?
    """, (erro_id, per_page, offset))

    dados = cursor.fetchall()

    conn.close()

    total_pages = (total + per_page - 1) // per_page

    return render_template('list_aws.html', erros=dados, page=page, total_pages=total_pages, erro_id=erro_id)

# 📳 Detalhe Flux
@app.route('/erros_sms/<int:erro_id>')
def erros_sms(erro_id):
    if not login_required() or not analyst_required():
        return redirect('/graficos')

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    conn = get_db()
    cursor = conn.cursor()

    operadora = request.args.get('operadora', '')
    
    query = """SELECT telefone, tipo_servico, data_envio, status, operadora FROM erros_flux WHERE erro_id=?"""

    params = [erro_id]

    if operadora:
        query += " AND operadora=?"
        params.append(operadora)

    count_query = query.replace("SELECT telefone, tipo_servico, data_envio, status, operadora", "SELECT COUNT(*)")
    
    cursor.execute(count_query, tuple(params))

    total = cursor.fetchone()[0]

    query += " LIMIT ? OFFSET ?"

    params.append(per_page)
    params.append(offset)

    cursor.execute(query, tuple(params))

    dados = cursor.fetchall()

    cursor.execute("SELECT DISTINCT operadora FROM erros_flux WHERE erro_id=?", (erro_id,))
    
    operadoras = cursor.fetchall()

    conn.close()

    total_pages = (total + per_page - 1) // per_page

    return render_template('list_sms.html', erros=dados, page=page, total_pages=total_pages, erro_id=erro_id, operadoras=operadoras, operadora_selecionada=operadora)

# 📨 Detalhe Locaweb
@app.route('/erros_locaweb/<int:erro_id>')
def erros_locaweb(erro_id):
    if not login_required() or not analyst_required():
        return redirect('/graficos')

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1 )* per_page

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        select COUNT(*) from erros_locaweb where erro_id=?
    """,(erro_id,))

    total = cursor.fetchone()[0]

    cursor.execute("""
        select 
            data_envio,
            destinatario,
            motivo_bounce,
            assunto
        from erros_locaweb
        where erro_id=?
        limit ? offset ?
    """, (erro_id, per_page, offset))

    dados = cursor.fetchall()

    conn.close()

    total_pages = (total + per_page - 1) // per_page

    return render_template('list_loca.html', erros=dados, page=page, total_pages=total_pages, erro_id=erro_id)

@app.route('/graficos')
def graficos():
    if not login_required():
        return redirect('/login')
    
    conn = get_db()
    conn.row_factory = sqlite3.Row

    # 1. Busca lista global de empresas para alimentar o Dropdown de filtros no HTML
    df_lista_empresas = pd.read_sql_query("SELECT DISTINCT empresa FROM erros WHERE empresa IS NOT NULL ORDER BY empresa", conn)
    lista_empresas = df_lista_empresas['empresa'].tolist()
    
    # 2. Captura o filtro selecionado via parâmetro de URL (Query String)
    empresa_selecionada = request.args.get('empresa', 'TODAS').strip()
    
    # 3. Extração dos dados brutos das tabelas do SQLite
    df_erros = pd.read_sql_query("SELECT * FROM erros", conn)
    df_aws = pd.read_sql_query("SELECT * FROM erros_aws", conn)
    df_flux = pd.read_sql_query("SELECT * FROM erros_flux", conn)
    df_locaweb = pd.read_sql_query("SELECT * FROM erros_locaweb", conn)
    conn.commit()
    conn.close()

    # 4. APLICAÇÃO DO FILTRO DINÂMICO (BLINDADO CONTRA VALUERROR DE COMPARAÇÃO)
    # Se o Flask receber o parâmetro como uma lista, tupla ou set de múltiplos elementos
    if isinstance(empresa_selecionada, (list, tuple, set)):
        filtros_finais = [e for e in empresa_selecionada if e != 'TODAS']
        if filtros_finais:
            df_erros = df_erros[df_erros['empresa'].isin(filtros_finais)]
            
            # Aplica o filtro em cascata para as tabelas filhas usando os IDs relacionados
            ids_filtrados = df_erros['id'].tolist()
            df_aws = df_aws[df_aws['erro_id'].isin(ids_filtrados)]
            df_flux = df_flux[df_flux['erro_id'].isin(ids_filtrados)]
            df_locaweb = df_locaweb[df_locaweb['erro_id'].isin(ids_filtrados)]
            
    # Se o parâmetro chegar como uma string simples padrão
    else:
        if empresa_selecionada != 'TODAS':
            df_erros = df_erros[df_erros['empresa'] == empresa_selecionada]
            
            # Aplica o filtro em cascata para as tabelas filhas
            ids_filtrados = df_erros['id'].tolist()
            df_aws = df_aws[df_aws['erro_id'].isin(ids_filtrados)]
            df_flux = df_flux[df_flux['erro_id'].isin(ids_filtrados)]
            df_locaweb = df_locaweb[df_locaweb['erro_id'].isin(ids_filtrados)]

    # 5. ESTRUTURA DE SALVAGUARDA (Se o filtro ou banco resultar em DataFrames vazios)
    if df_erros.empty:
        dashboard_data = {
            "filtros": {"lista_empresas": lista_empresas, "selecionada": empresa_selecionada},
            "kpis": {
                "total_envios": "0", "total_erros": "0", "taxa_erro_global": "0.0%", "sla_global": "100.0%",
                "delta_envios": "0.0%", "delta_erros": "0.0%", "tendencia_erros": "estavel"
            },
            "charts": {
                "temporal_labels": [], "temporal_envios": [], "temporal_erros": [],
                "empresa_labels": [], "empresa_erros": [], "canal_labels": [], "canal_erros": [],
                "aws_labels": [], "aws_valores": [], "flux_labels": [], "flux_valores": [], "locaweb_labels": [], "locaweb_valores": []
            }
        }
        return render_template('graficos.html', data=dashboard_data)

    # Normaliza colunas numéricas que podem ter sido importadas como texto.
    for coluna in ['quant_envio', 'quant_erros']:
        df_erros[coluna] = pd.to_numeric(df_erros[coluna], errors='coerce').fillna(0)

    # 6. ENGENHARIA DE MÉTRICAS E KPIs OPERACIONAIS
    total_envios = int(df_erros['quant_envio'].sum())
    total_erros = int(df_erros['quant_erros'].sum())
    taxa_erro_global = round((total_erros / total_envios * 100), 2) if total_envios > 0 else 0.0
    sla_global = round(100 - taxa_erro_global, 2)

    # 7. CÁLCULO DE TENDÊNCIAS TEMPORAIS (Período Atual vs Período Anterior)
    df_erros['data'] = pd.to_datetime(df_erros['data'], errors='coerce')
    df_erros = df_erros.dropna(subset=['data'])
    temporal = df_erros.groupby(df_erros['data'].dt.strftime('%Y-%m-%d')).agg({
        'quant_envio': 'sum',
        'quant_erros': 'sum'
    }).sort_index().reset_index()

    delta_envios = 0.0
    delta_erros = 0.0
    tendencia_erros = "estavel"

    if len(temporal) >= 2:
        ultimo_p = temporal.iloc[-1]
        anterior_p = temporal.iloc[-2]
        
        if anterior_p['quant_envio'] > 0:
            delta_envios = round(((ultimo_p['quant_envio'] - anterior_p['quant_envio']) / anterior_p['quant_envio']) * 100, 1)
        if anterior_p['quant_erros'] > 0:
            delta_erros = round(((ultimo_p['quant_erros'] - anterior_p['quant_erros']) / anterior_p['quant_erros']) * 100, 1)
        
        if delta_erros > 5.0:
            tendencia_erros = "subindo"
        elif delta_erros < -5.0:
            tendencia_erros = "descendo"

    # 8. PROCESSAMENTO E RANKING DOS GRÁFICOS
    empresa_perf = df_erros.groupby('empresa').agg({'quant_erros': 'sum'}).reset_index()
    empresa_perf = empresa_perf.sort_values(by='quant_erros', ascending=False)

    canal_perf = df_erros.groupby('tipo_envio').agg({'quant_erros': 'sum'}).reset_index()

    # Agrupamento detalhado por falhas de infraestrutura (Causa Raiz)
    top_aws = df_aws.groupby('diagnostic_code').size().sort_values(ascending=False).head(5).reset_index(name='total')
    top_flux = df_flux.groupby('operadora').size().sort_values(ascending=False).head(5).reset_index(name='total')
    top_locaweb = df_locaweb.groupby('motivo_bounce').size().sort_values(ascending=False).head(5).reset_index(name='total')

    # 9. SINALIZAÇÃO E CONVERSÃO COMPATÍVEL COM NATIVE JSON (Evitando tipos do numpy/pandas)
    dashboard_data = {
        "filtros": {
            "lista_empresas": lista_empresas,
            "selecionada": empresa_selecionada
        },
        "kpis": {
            "total_envios": f"{total_envios:,}".replace(",", "."),
            "total_erros": f"{total_erros:,}".replace(",", "."),
            "taxa_erro_global": f"{taxa_erro_global}%",
            "sla_global": f"{sla_global}%",
            "delta_envios": f"+{delta_envios}%" if delta_envios > 0 else f"{delta_envios}%",
            "delta_erros": f"+{delta_erros}%" if delta_erros > 0 else f"{delta_erros}%",
            "tendencia_erros": tendencia_erros
        },
        "charts": {
            "temporal_labels": temporal['data'].tolist(),
            "temporal_envios": [int(x) for x in temporal['quant_envio']],
            "temporal_erros": [int(x) for x in temporal['quant_erros']],
            
            "empresa_labels": empresa_perf['empresa'].tolist(),
            "empresa_erros": [int(x) for x in empresa_perf['quant_erros']],
            
            "canal_labels": canal_perf['tipo_envio'].tolist(),
            "canal_erros": [int(x) for x in canal_perf['quant_erros']],
            
            "aws_labels": top_aws['diagnostic_code'].tolist(),
            "aws_valores": [int(x) for x in top_aws['total']],
            
            "flux_labels": top_flux['operadora'].tolist(),
            "flux_valores": [int(x) for x in top_flux['total']],
            
            "locaweb_labels": top_locaweb['motivo_bounce'].tolist(),
            "locaweb_valores": [int(x) for x in top_locaweb['total']]
        }
    }

    return render_template('graficos.html', data=dashboard_data)

@app.route('/dashboard/sms')
def dashboard_sms():
    if not login_required():
        return redirect('/login')
    
    conn = get_db()
    
    data_inicio_raw = request.args.get('data_inicio', '')
    data_fim_raw = request.args.get('data_fim', '')
    data_inicio = data_inicio_raw.strip() if data_inicio_raw else ''
    data_fim = data_fim_raw.strip() if data_fim_raw else ''
    
    # ========================================================
    # 🔍 TELEMETRIA E DIAGNÓSTICO (Olhe o terminal do seu PC)
    # ========================================================
    print("\n" + "="*50)
    print("🚀 INICIANDO DIAGNÓSTICO DO DASHBOARD SMS")
    print(f"📅 Filtros da URL -> Início: '{data_inicio}' | Fim: '{data_fim}'")
    
    # Teste 1: Quantos registros existem no total?
    df_todos = pd.read_sql_query("SELECT * FROM erros", conn)
    print(f"📊 Total absoluto na tabela 'erros': {len(df_todos)} linhas.")
    if not df_todos.empty and 'tipo_envio' in df_todos.columns:
        print(f"❓ Tipos de envio cadastrados no banco: {df_todos['tipo_envio'].unique()}")
    
    # Teste 2: Buscando com UPPER para evitar problemas de maiúscula/minúscula
    df_erros = pd.read_sql_query("SELECT * FROM erros WHERE tipo_envio = 'Envio de SMS'", conn)
    print(f"📱 Linhas encontradas com tipo_envio = 'SMS': {len(df_erros)}")
    
    df_flux = pd.read_sql_query("SELECT * FROM erros_flux", conn)
    print(f"🔗 Total absoluto na tabela filha 'erros_flux': {len(df_flux)} linhas.")
    # ========================================================

    # Garantir conversão de data
    df_erros['data'] = pd.to_datetime(df_erros['data'])
    
    if data_inicio:
        df_erros = df_erros[df_erros['data'] >= pd.to_datetime(data_inicio)]
    if data_fim:
        df_erros = df_erros[df_erros['data'] <= pd.to_datetime(data_fim)]
        
    print(f"📅 Linhas após aplicar filtro de data: {len(df_erros)}")

    if df_erros.empty:
        print("⚠️ AVISO: df_erros ficou vazio. Retornando template zerado.")
        print("="*50 + "\n")
        sms_data = {
            "filtros": {"data_inicio": data_inicio, "data_fim": data_fim},
            "kpis": {"total_envios": "0", "total_erros": "0", "taxa_erro": "0.0%", "top_numero_ofensor": "N/A"},
            "charts": {"temporal_labels": [], "temporal_envios": [], "temporal_erros": [], "operadoras_labels": [], "operadoras_valores": []},
            "lista_numeros": []
        }
        conn.close()
        return render_template('dashboard_sms.html', data=sms_data)

    # 🚨 BLINDAGEM DE TIPO: Garante que os IDs sejam texto para o Pandas não falhar o JOIN de tipos diferentes (Ex: int de um lado e str do outro)
    df_erros['id'] = df_erros['id'].astype(str)
    df_flux['erro_id'] = df_flux['erro_id'].astype(str)

    df_flux_filtrado = df_flux[df_flux['erro_id'].isin(df_erros['id'])]
    print(f"🎯 Linhas na tabela filha após o relacionamento (.isin): {len(df_flux_filtrado)}")
    print("="*50 + "\n")
    
    conn.close()
    
    # Normaliza valores numéricos antes dos cálculos.
    for coluna in ['quant_envio', 'quant_erros']:
        df_erros[coluna] = pd.to_numeric(df_erros[coluna], errors='coerce').fillna(0)

    # --- Cálculos matemáticos ---
    total_envios = int(df_erros['quant_envio'].sum())
    total_erros = int(df_erros['quant_erros'].sum())
    taxa_erro = round((total_erros / total_envios * 100), 2) if total_envios > 0 else 0.0

    lista_numeros_ofensores = []
    top_numero_ofensor = "N/A"
    top_ofensor_erros = 0
    
    if not df_flux_filtrado.empty and 'telefone' in df_flux_filtrado.columns:
        df_ranking_numeros = df_flux_filtrado.groupby('telefone').size().reset_index(name='quant_erros')
        df_ranking_numeros = df_ranking_numeros.sort_values(by='quant_erros', ascending=False)
        if not df_ranking_numeros.empty:
            top_numero_ofensor = str(df_ranking_numeros.iloc[0]['telefone'])
            top_ofensor_erros = int(df_ranking_numeros.iloc[0]['quant_erros'])
            df_ranking_numeros = df_ranking_numeros.rename(columns={'telefone': 'numero_cliente'})
            lista_numeros_ofensores = df_ranking_numeros.head(10).to_dict(orient='records')

    temporal = df_erros.groupby(df_erros['data'].dt.strftime('%Y-%m-%d')).agg({
        'quant_envio': 'sum', 'quant_erros': 'sum'
    }).sort_index().reset_index()
    
    operadoras = df_flux_filtrado.groupby('operadora').size().sort_values(ascending=False).reset_index(name='total')

    sms_data = {
        "filtros": {"data_inicio": data_inicio, "data_fim": data_fim},
        "kpis": {
            "total_envios": f"{total_envios:,}".replace(",", "."),
            "total_erros": f"{total_erros:,}".replace(",", "."),
            "taxa_erro": f"{taxa_erro}%",
            "top_numero_ofensor": f"{top_numero_ofensor} ({top_ofensor_erros} erros)"
        },
        "charts": {
            "temporal_labels": temporal['data'].tolist(),
            "temporal_envios": [int(x) for x in temporal['quant_envio']],
            "temporal_erros": [int(x) for x in temporal['quant_erros']],
            "operadoras_labels": operadoras['operadora'].tolist(),
            "operadoras_valores": [int(x) for x in operadoras['total']]
        },
        "lista_numeros": lista_numeros_ofensores
    }
    return render_template('dashboard_sms.html', data=sms_data)

@app.route('/dashboard/aws')
def dashboard_aws():
    if not login_required():
        return redirect('/login')
 
    conn = get_db()
 
    # 1. PARÂMETROS DE FILTRO
    data_inicio = request.args.get('data_inicio', '').strip()
    data_fim    = request.args.get('data_fim', '').strip()
    empresa_selecionada = request.args.get('empresa', 'TODAS').strip()
 
    # 2. CARREGA DADOS CORRETOS — erros_aws, não erros_flux (que é SMS)
    df_erros = pd.read_sql_query(
        "SELECT * FROM erros WHERE tipo_envio LIKE '%AWS%'", conn
    )
    df_aws = pd.read_sql_query("SELECT * FROM erros_aws", conn)
 
    lista_empresas = []
    if not df_erros.empty:
        lista_empresas = sorted(df_erros['empresa'].dropna().unique().tolist())
 
    conn.close()
 
    # Payload vazio reutilizável
    def empty_data():
        return {
            "filtros": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "empresa": empresa_selecionada,
                "lista_empresas": lista_empresas
            },
            "kpis": {
                "total_envios": "0",
                "total_erros": "0",
                "taxa_erro": "0,0%",
                "status_reputacao": "SAUDÁVEL",
                "reputacao_cor": "#10b981"
            },
            "charts": {
                "temporal_labels": [],
                "temporal_envios": [],
                "temporal_erros": [],
                "codigos_labels": [],
                "codigos_valores": []
            },
            "lista_ofensores": []
        }
 
    if df_erros.empty:
        return render_template('dashboard_aws.html', data=empty_data())
 
    # 3. FILTRAGEM POR DATA E EMPRESA
    df_erros['data'] = pd.to_datetime(df_erros['data'])
    if data_inicio:
        df_erros = df_erros[df_erros['data'] >= pd.to_datetime(data_inicio)]
    if data_fim:
        df_erros = df_erros[df_erros['data'] <= pd.to_datetime(data_fim)]
    if empresa_selecionada != 'TODAS':
        df_erros = df_erros[df_erros['empresa'] == empresa_selecionada]
 
    if df_erros.empty:
        return render_template('dashboard_aws.html', data=empty_data())
 
    # 4. FILTRA erros_aws PELOS IDs JÁ FILTRADOS
    ids_filtrados = df_erros['id'].astype(str).tolist()
    df_aws['erro_id'] = df_aws['erro_id'].astype(str)
    df_aws_filtrado = df_aws[df_aws['erro_id'].isin(ids_filtrados)].copy()
 
    # 5. KPIs
    total_envios = int(df_erros['quant_envio'].sum())
    total_erros  = int(df_erros['quant_erros'].sum())
    taxa_erro    = round((total_erros / total_envios * 100), 2) if total_envios > 0 else 0.0
 
    if taxa_erro >= 2.2:
        status_reputacao, reputacao_cor = "CRÍTICO", "#ef4444"
    elif taxa_erro >= 1.5:
        status_reputacao, reputacao_cor = "ALERTA",  "#f59e0b"
    else:
        status_reputacao, reputacao_cor = "SAUDÁVEL", "#10b981"
 
    # 6. TOP 10 OFENSORES — agrupado por e-mail de destino (coluna 'destination')
    lista_ofensores = []
    if not df_aws_filtrado.empty and 'destination' in df_aws_filtrado.columns:
        df_ranking = (
            df_aws_filtrado
            .groupby('destination')
            .size()
            .reset_index(name='quant_erros')
            .sort_values('quant_erros', ascending=False)
            .head(10)
            .rename(columns={'destination': 'identificador'})
        )
        lista_ofensores = df_ranking.to_dict(orient='records')
 
    # 7. TOP CÓDIGOS DE ERRO (substitui soft/hard bounce que não se aplica aqui)
    codigos_labels  = []
    codigos_valores = []
    if not df_aws_filtrado.empty and 'diagnostic_code' in df_aws_filtrado.columns:
        top_codigos = (
            df_aws_filtrado['diagnostic_code']
            .dropna()
            .value_counts()
            .head(8)
        )
        codigos_labels  = top_codigos.index.tolist()
        codigos_valores = top_codigos.values.tolist()
 
    # 8. EVOLUÇÃO TEMPORAL
    temporal = (
        df_erros
        .groupby(df_erros['data'].dt.strftime('%Y-%m-%d'))
        .agg(quant_envio=('quant_envio', 'sum'), quant_erros=('quant_erros', 'sum'))
        .sort_index()
        .reset_index()
    )
 
    # 9. PAYLOAD FINAL
    aws_data = {
        "filtros": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "empresa": empresa_selecionada,
            "lista_empresas": lista_empresas
        },
        "kpis": {
            "total_envios":      f"{total_envios:,}".replace(",", "."),
            "total_erros":       f"{total_erros:,}".replace(",", "."),
            "taxa_erro":         f"{taxa_erro}%",
            "status_reputacao":  status_reputacao,
            "reputacao_cor":     reputacao_cor
        },
        "charts": {
            "temporal_labels":  temporal['data'].tolist(),
            "temporal_envios":  [int(x) for x in temporal['quant_envio']],
            "temporal_erros":   [int(x) for x in temporal['quant_erros']],
            "codigos_labels":   codigos_labels,
            "codigos_valores":  codigos_valores
        },
        "lista_ofensores": lista_ofensores
    }
 
    return render_template('dashboard_aws.html', data=aws_data)
    
    
# ======================
# API POWER BI
# ======================
# Mesmo arquivo que get_db() usa ('database.db' relativo ao diretório de execução).
# Se o app roda de outro diretório, defina DB_PATH com o caminho absoluto.
DB_PATH_POWERBI = os.environ.get('DB_PATH') or os.path.abspath('database.db')

# 'usuarios' fica de fora de propósito: a tabela guarda o hash das senhas.
TABELAS_POWERBI = ('erros', 'erros_aws', 'erros_flux', 'erros_locaweb')


def _conexao_somente_leitura():
    """Conexão que não consegue escrever, mesmo que haja algum bug."""
    conn = sqlite3.connect(f"file:{DB_PATH_POWERBI}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _token_valido():
    """None = token não configurado no servidor; True/False = resultado da checagem."""
    esperado = os.environ.get('POWERBI_TOKEN')
    if not esperado:
        return None
    enviado = request.headers.get('Authorization', '')
    return hmac.compare_digest(enviado, f"Bearer {esperado}")


@app.route('/api/powerbi/<tabela>')
def api_powerbi(tabela):
    # 1. Autenticação
    valido = _token_valido()
    if valido is None:
        return jsonify({"erro": "API desativada: defina a variável POWERBI_TOKEN no servidor."}), 503
    if not valido:
        return jsonify({"erro": "Token inválido ou ausente."}), 401

    # 2. Validação de parâmetros (a tabela só entra no SQL depois de passar pela lista)
    if tabela not in TABELAS_POWERBI:
        return jsonify({"erro": "Tabela não permitida."}), 403

    formato = request.args.get('formato', 'json').lower()
    if formato not in ('json', 'csv'):
        return jsonify({"erro": "O parâmetro 'formato' deve ser 'json' ou 'csv'."}), 400

    limit = request.args.get('limit', default=-1, type=int)   # -1 = sem limite no SQLite
    offset = request.args.get('offset', default=0, type=int)
    params = (limit if limit >= 0 else -1, max(offset, 0))

    # 3. Consulta (ORDER BY id deixa a paginação estável)
    sql = f"SELECT * FROM {tabela} ORDER BY id LIMIT ? OFFSET ?"
    conn = None
    try:
        conn = _conexao_somente_leitura()
        cursor = conn.execute(sql, params)
        colunas = [d[0] for d in cursor.description]
    except sqlite3.Error:
        if conn:
            conn.close()
        app.logger.exception("Falha na API do Power BI (tabela=%s)", tabela)
        return jsonify({"erro": "Falha ao consultar o banco de dados."}), 500

    # 4a. JSON: lista simples de registros, sem envelope.
    #     Usa sqlite3 direto (não pandas), então NULL vira null e nunca NaN,
    #     e as colunas mantêm a ordem da tabela.
    if formato == 'json':
        try:
            registros = [dict(linha) for linha in cursor.fetchall()]
        finally:
            conn.close()
        corpo = json.dumps(registros, ensure_ascii=False)
        return Response(corpo, mimetype='application/json')

    # 4b. CSV: enviado aos poucos (streaming), não carrega a tabela toda na memória.
    def gerar_csv():
        try:
            buffer = io.StringIO()
            escritor = csv.writer(buffer)
            escritor.writerow(colunas)
            yield buffer.getvalue()
            while True:
                lote = cursor.fetchmany(1000)
                if not lote:
                    break
                buffer.seek(0)
                buffer.truncate(0)
                escritor.writerows(lote)
                yield buffer.getvalue()
        finally:
            conn.close()

    return Response(stream_with_context(gerar_csv()), mimetype='text/csv')

print("\n--- ROTAS REGISTADAS NO FLASK ---")
for rule in app.url_map.iter_rules():
    print(rule)
print("----------------------------------\n")
print("🔍 FICHEIRO EXECUTADO PELO PYTHON:", __file__)
print("🔍 DIRETÓRIO ATUAL:", os.getcwd())
# 💩 START
if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)