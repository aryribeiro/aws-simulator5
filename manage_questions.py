import streamlit as st
import pandas as pd
import json
import os
from pathlib import Path

CSV_PATH = Path(__file__).parent / "questions.csv"
COLUMNS = ["question", "options", "answer", "multiple", "explanation", "wrong", "reference", "domain"]

st.set_page_config(page_title="Gerenciar Perguntas AWS", layout="wide")

# --- CSS para centralizar ---
st.markdown("""
    <style>
    .block-container {
        max-width: 800px;
        margin-left: auto;
        margin-right: auto;
    }
    </style>
    """, unsafe_allow_html=True)


# Função para carregar o CSV
def load_csv():
    try:
        # keep_default_na=False: alternativas como "None" ou "NA" não viram NaN
        df = pd.read_csv(CSV_PATH, encoding="utf-8", dtype=str, keep_default_na=False)
    except FileNotFoundError:
        df = pd.DataFrame(columns=COLUMNS)
    for col in COLUMNS:  # CSV antigo (só 4 colunas) ganha as colunas novas vazias
        if col not in df.columns:
            df[col] = ""
    return df


# Função para salvar no CSV (grava num temporário e troca: nunca deixa o arquivo pela metade)
def save_csv(df):
    tmp = CSV_PATH.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, CSV_PATH)


def lines(text):
    return [t.strip() for t in text.split("\n") if t.strip()]


def show_list(cell):
    try:
        return json.loads(cell) if cell else []
    except json.JSONDecodeError:
        return [cell]


# Título
st.title("Gerenciar Perguntas do Simulado AWS")

# --- Formulário para adicionar perguntas ---
st.header("Adicionar Nova Pergunta")
with st.form(key="form_nova_pergunta"):
    question = st.text_area("Pergunta", placeholder="Digite a pergunta aqui")
    options = st.text_area("Opções (uma por linha)", placeholder="Opção 1\nOpção 2\nOpção 3\nOpção 4")
    answer = st.text_area("Resposta(s) Correta(s) (uma por linha, texto igual ao da opção)", placeholder="Opção 2")
    multiple = st.checkbox("Permite múltiplas respostas? (2 ou 3 corretas, como no AIP-C01)")
    explanation = st.text_area("Explicação da(s) correta(s) — opcional, um parágrafo por linha")
    wrong = st.text_area(
        "Motivo de cada incorreta — opcional, uma linha por opção incorreta, na ordem em que aparecem",
        placeholder="Motivo da 1ª incorreta\nMotivo da 2ª incorreta",
    )
    reference = st.text_input("Referência (URL) — opcional")
    domain = st.text_input("Domínio/Task — opcional", placeholder="D1 · Task 1.2 · Foundation Model Integration, ...")
    submit_button = st.form_submit_button("Adicionar Pergunta")

    if submit_button:
        opts_list = lines(options)
        ans_list = lines(answer)
        why_list = lines(wrong)
        wrong_opts = [o for o in opts_list if o not in ans_list]
        df = load_csv()
        if not question.strip():
            st.error("A pergunta não pode estar vazia.")
        elif not opts_list:
            st.error("É necessário fornecer pelo menos uma opção.")
        elif not ans_list:
            st.error("É necessário fornecer pelo menos uma resposta correta.")
        elif len(opts_list) != len(set(opts_list)):
            st.error("As opções não podem conter itens repetidos.")
        elif len(ans_list) != len(set(ans_list)):
            st.error("As respostas corretas não podem conter itens repetidos.")
        elif not all(ans in opts_list for ans in ans_list):
            st.error("Todas as respostas corretas devem estar nas opções fornecidas.")
        elif multiple and len(ans_list) < 2:
            st.error("Múltiplas respostas marcado, mas apenas uma resposta correta foi fornecida.")
        elif not multiple and len(ans_list) > 1:
            st.error("Mais de uma resposta correta exige marcar 'Permite múltiplas respostas?'. "
                     "Sem isso, a pergunta seria impossível de acertar no simulado.")
        elif why_list and len(why_list) != len(wrong_opts):
            st.error(f"São {len(wrong_opts)} opções incorretas, mas foram dados {len(why_list)} motivos.")
        elif question.strip() in df["question"].values:
            st.error("Esta pergunta já existe no banco.")
        else:
            # Listas gravadas como JSON (seguro para apóstrofos e vírgulas)
            new_row = {
                "question": question.strip(),
                "options": json.dumps(opts_list, ensure_ascii=False),
                "answer": json.dumps(ans_list, ensure_ascii=False),
                "multiple": str(multiple),
                "explanation": json.dumps(lines(explanation), ensure_ascii=False) if explanation.strip() else "",
                "wrong": json.dumps(dict(zip(wrong_opts, why_list)), ensure_ascii=False) if why_list else "",
                "reference": json.dumps([reference.strip()], ensure_ascii=False) if reference.strip() else "",
                "domain": domain.strip(),
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            save_csv(df)
            st.success("Pergunta adicionada com sucesso!")
            st.rerun()

# --- Visualizar e remover perguntas ---
st.header("Perguntas Existentes")
df = load_csv()
if df.empty:
    st.info("Nenhuma pergunta cadastrada ainda.")
else:
    st.caption(f"{len(df)} perguntas no banco.")
    # Exibe a tabela com botão de remoção
    for idx, row in df.iterrows():
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(f"**Pergunta {idx + 1}:** {row['question']}")
            if row["domain"]:
                st.caption(row["domain"])
            st.write(f"Opções: {row['options']}")
            st.write(f"Resposta(s): {row['answer']}")
            st.write(f"Múltipla escolha: {row['multiple']}")
            if row["explanation"] or row["wrong"]:
                with st.expander("Explicação e motivos das incorretas"):
                    for p in show_list(row["explanation"]):
                        st.markdown(f":green[{p}]")
                    try:
                        why = json.loads(row["wrong"]) if row["wrong"] else {}
                    except json.JSONDecodeError:
                        why = {}
                    for opt, motivo in why.items():
                        st.markdown(f":red[**{opt}** — {motivo}]")
                    for ref in show_list(row["reference"]):
                        st.caption(ref)
        with col2:
            if st.button("Remover", key=f"remove_{idx}"):
                df = df.drop(idx).reset_index(drop=True)
                save_csv(df)
                st.success("Pergunta removida com sucesso!")
                st.rerun()
