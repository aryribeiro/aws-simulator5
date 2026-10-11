import base64
import html
import json
import ast
import os
import random
import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env (no Streamlit Cloud, os Secrets viram variáveis de ambiente)
load_dotenv()
GOOGLE_DRIVE_CSV_URL = os.getenv("GOOGLE_DRIVE_CSV_URL")
BASE_DIR = Path(__file__).parent
LOCAL_CSV = BASE_DIR / "questions.csv"
AUDIO_FILE = BASE_DIR / "static" / "som.mp3"  # som íntegro (432 Hz), sem recompressão

EXAM_MINUTES = 180          # duração oficial do AIP-C01
ESL_EXTRA_MINUTES = 30      # acomodação ESL +30 da AWS para quem não é nativo em inglês
CACHE_TTL = 24 * 60 * 60    # 24 horas (questões do Drive e som)
DRIVE_COOLDOWN = 5 * 60     # após falha do Drive, usa o CSV local por 5 min sem tentar de novo
LETTERS = "ABCDEFGH"
MODOS = {
    f"⏱️ Com tempo ({EXAM_MINUTES} minutos, como no exame real)": EXAM_MINUTES * 60,
    f"🌎 Com tempo + {ESL_EXTRA_MINUTES} min de acomodação ESL "
    f"({EXAM_MINUTES + ESL_EXTRA_MINUTES} minutos)": (EXAM_MINUTES + ESL_EXTRA_MINUTES) * 60,
    "🍃 Sem limite de tempo": None,
}

# Configuração do app Streamlit
st.set_page_config(page_title="AWS Simulator (AIP-C01)", page_icon="📝", layout="centered")
st.title("📝 AWS Simulator")
st.markdown("Simulado AWS **AIP-C01** 2026 — Generative AI Developer – Professional.🚀")

st.markdown("""
<style>
    /* centraliza o container principal */
    .block-container {
        max-width: 800px;
        margin-left: auto;
        margin-right: auto;
        padding-top: 1rem;
        padding-bottom: 3rem;
    }
    /* Esconde completamente todos os elementos da barra padrão do Streamlit */
    header {display: none !important;}
    footer {display: none !important;}
    #MainMenu {display: none !important;}
    /* Garante quebra de linha em alternativas longas (radio e checkbox) */
    div[data-testid="stRadio"] div[data-testid="stMarkdownContainer"] p,
    div[data-testid="stCheckbox"] div[data-testid="stMarkdownContainer"] p {
        white-space: normal !important;
        overflow-wrap: break-word;
        line-height: 1.5;
    }
    /* Alinha o marcador ao topo do texto e dá respiro entre alternativas */
    div[data-testid="stRadio"] label,
    div[data-testid="stCheckbox"] label {
        align-items: flex-start !important;
        padding: 4px 0;
    }
    .stem {
        line-height: 1.6;
        overflow-wrap: break-word;
    }
    .tag {
        color: #8a94a3;
        font-size: 0.8rem;
        margin-top: 2px;
    }
    /* Alternativas no feedback pós-resposta */
    .opt {
        padding: 10px 14px;
        border-radius: 8px;
        border: 1px solid #d0d0d0;
        background: #fafafa;
        color: #1a1a1a;
        margin: 6px 0;
        line-height: 1.5;
        overflow-wrap: break-word;
    }
    .opt-ok {
        background: #e6f4ea;
        border-color: #34a853;
    }
    .opt-bad {
        background: #fce8e6;
        border-color: #ea4335;
    }
    /* Explicação da correta (verde) e motivo de cada incorreta (vermelho) */
    .exp-ok, .exp-bad {
        padding: 10px 14px;
        border-radius: 8px;
        margin: 10px 0 4px;
        line-height: 1.55;
        overflow-wrap: break-word;
    }
    .exp-ok {
        background: #eef7f0;
        border-left: 4px solid #1e7a3c;
        color: #1e6b35;
    }
    .exp-bad {
        background: #fcf0ef;
        border-left: 4px solid #b3261e;
        color: #a52a1f;
    }
    .exp-ok p, .exp-bad p { margin: 0 0 6px; }
    .exp-ok p:last-child, .exp-bad p:last-child { margin-bottom: 0; }
    .ref {
        color: #8a94a3;
        font-size: 0.78rem;
        word-break: break-all;
        margin-top: 4px;
    }
    .ref a { color: #8a94a3; }
    /* Timer fixo no canto superior direito da janela */
    .timer-box {
        position: fixed;
        top: 8px;
        right: 16px;
        z-index: 9999;
        font-family: Arial, sans-serif;
        font-size: 18px;
        color: black;
        background: #ffffff;
        border: 1px solid #dddddd;
        border-radius: 8px;
        padding: 4px 12px;
    }
    .rodape {
        text-align: center;
        line-height: 1.7;
    }
    .rodape a { text-decoration: none; color: inherit; }
</style>
""", unsafe_allow_html=True)


# — Carrega perguntas —
def parse_list(cell):
    """Lê a célula como JSON; aceita também o formato legado (repr Python)."""
    try:
        return json.loads(cell)
    except (json.JSONDecodeError, TypeError):
        return ast.literal_eval(cell)


def parse_optional(cell, default):
    """Colunas novas (explicação, motivos, referência) são opcionais: célula vazia ou ausente vira default."""
    if not isinstance(cell, str) or not cell.strip():
        return default
    try:
        return parse_list(cell)
    except (ValueError, SyntaxError):
        return cell.strip()  # texto simples também vale


def as_tuple(value):
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(v) for v in value)


def parse_questions(df):
    """Valida o banco inteiro e devolve uma estrutura imutável (compartilhada entre sessões)."""
    faltando = {"question", "options", "answer", "multiple"} - set(df.columns)
    if faltando:
        raise ValueError(f"CSV sem as colunas obrigatórias: {sorted(faltando)}")
    qs = []
    for n, r in enumerate(df.to_dict("records"), start=1):
        opts = tuple(parse_list(r["options"]))
        ans_txt = parse_list(r["answer"])
        if not opts or len(set(opts)) != len(opts) or len(opts) > len(LETTERS):
            raise ValueError(f"Linha {n}: alternativas vazias, repetidas ou acima de {len(LETTERS)}.")
        if not ans_txt or not set(ans_txt) <= set(opts):
            raise ValueError(f"Linha {n}: resposta correta fora das alternativas.")
        mul = str(r["multiple"]).strip().lower() == "true"
        if not mul and len(set(ans_txt)) > 1:
            raise ValueError(f"Linha {n}: mais de uma correta exige multiple=True.")
        wrong = parse_optional(r.get("wrong"), {})
        qs.append({
            "q": str(r["question"]).strip(),
            "opts": opts,
            "ans": frozenset(opts.index(a) for a in ans_txt),   # índices: estado compacto
            "mul": mul,
            "exp": as_tuple(parse_optional(r.get("explanation"), ())),
            "why": {opts.index(k): str(v) for k, v in (wrong.items() if isinstance(wrong, dict) else ())
                    if k in opts},
            "refs": as_tuple(parse_optional(r.get("reference"), ())),
            "tag": str(r.get("domain") or "").strip(),
        })
    if not qs:
        raise ValueError("O CSV não tem perguntas.")
    return tuple(qs)


def read_csv_text(text):
    # keep_default_na=False: alternativas como "None" ou "NA" não podem virar NaN
    return pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)


@st.cache_resource(show_spinner=False)
def http_session():
    """Uma sessão HTTP reaproveitada (pool de conexões) para todo o servidor."""
    s = requests.Session()
    s.headers["User-Agent"] = "aws-simulator5"
    return s


@st.cache_resource(show_spinner=False)
def drive_breaker():
    """Disjuntor simples: guarda até quando o Drive deve ser evitado após falhar."""
    return {"open_until": 0.0}


def fetch_csv(url, attempts=3, base_delay=0.5):
    """Baixa o CSV com retry, backoff exponencial e jitter. Recusa HTML (página de aviso/permissão do Drive)."""
    last = None
    for n in range(attempts):
        try:
            resp = http_session().get(url, timeout=(5, 20))
            resp.raise_for_status()
            resp.encoding = "utf-8"  # Drive envia sem charset; evita mojibake
            text = resp.text
            if text.lstrip()[:1] == "<":
                raise ValueError("o Drive devolveu uma página HTML, não o CSV (confira o compartilhamento do link)")
            return text
        except (requests.RequestException, ValueError) as e:
            last = e
            if n < attempts - 1:
                time.sleep(base_delay * 2 ** n + random.uniform(0, base_delay))
    raise last


# cache_resource devolve o MESMO objeto a todas as sessões (sem cópia/unpickle a cada rerun).
# O banco é imutável (tuplas/frozensets), então compartilhar é seguro. Exceções não são cacheadas.
@st.cache_resource(ttl=CACHE_TTL, show_spinner=False)
def load_remote(url):
    return parse_questions(read_csv_text(fetch_csv(url)))


@st.cache_resource(ttl=CACHE_TTL, show_spinner=False)
def load_local(mtime):  # mtime na chave: editar o CSV local invalida o cache
    return parse_questions(read_csv_text(LOCAL_CSV.read_text(encoding="utf-8")))


def get_questions():
    breaker = drive_breaker()
    if GOOGLE_DRIVE_CSV_URL and time.time() >= breaker["open_until"]:
        try:
            return load_remote(GOOGLE_DRIVE_CSV_URL)
        except Exception:
            breaker["open_until"] = time.time() + DRIVE_COOLDOWN
            if not LOCAL_CSV.exists():
                raise
    if LOCAL_CSV.exists():
        return load_local(LOCAL_CSV.stat().st_mtime)
    raise RuntimeError(
        "Defina GOOGLE_DRIVE_CSV_URL no arquivo .env ou coloque um questions.csv na pasta do app."
    )


# Som de fundo, técnica do aws-ccp-game: o mp3 inteiro em base64 dentro de um <audio autoplay loop>.
# O HTML fica em cache na memória (um único objeto para todas as sessões, junto com o banco de questões);
# como ele é idêntico a cada rerun, o React mantém o mesmo elemento e a música não reinicia.
@st.cache_resource(ttl=CACHE_TTL, show_spinner=False)
def audio_html():
    if not AUDIO_FILE.exists():
        return ""
    b64 = base64.b64encode(AUDIO_FILE.read_bytes()).decode()
    return (f'<audio autoplay loop id="background-music">'
            f'<source src="data:audio/mpeg;base64,{b64}" type="audio/mpeg"></audio>')


# Durante a prova, a sessão usa o banco congelado no início: se o cache do Drive renovar no meio, nada muda.
if "bank" in st.session_state:
    QUESTIONS = st.session_state.bank
else:
    try:
        QUESTIONS = get_questions()
    except Exception as e:
        st.error(f"Erro ao carregar as perguntas: {e}")
        st.stop()

TOTAL = len(QUESTIONS)

# — Callbacks (idempotentes: clique duplo ou repetido não muda o resultado) —
def start_simulado():
    if st.session_state.get("started"):
        return
    st.session_state.bank = QUESTIONS
    st.session_state.duration = MODOS[st.session_state.modo_sel]
    st.session_state.start_ts = time.time()
    st.session_state.finished = False
    st.session_state.started = True


def lock_single(i):
    """Trava a resposta de escolha única assim que o aluno marca uma alternativa."""
    if f"resp_{i}" in st.session_state or st.session_state.finished:
        return
    escolha = st.session_state.get(f"a_{i}")
    if escolha is not None:
        st.session_state[f"resp_{i}"] = (escolha,)


def lock_multiple(i, n_opts):
    """Trava a resposta de múltipla escolha ao confirmar."""
    if f"resp_{i}" in st.session_state or st.session_state.finished:
        return
    st.session_state[f"resp_{i}"] = tuple(j for j in range(n_opts) if st.session_state.get(f"a_{i}_{j}"))


# — Estado padrão —
st.session_state.setdefault("started", False)
st.session_state.setdefault("finished", False)

# — Tela inicial —
if not st.session_state.started:
    st.markdown(
        f"**{TOTAL} perguntas** em inglês, idioma do exame real, cobrindo os domínios 1 a 3 do guia oficial. "
        "Ao responder cada pergunta, aparecem a resposta correta com a explicação (em verde) e o motivo de "
        "cada alternativa incorreta (em vermelho)."
    )
    st.radio("Modo do simulado:", list(MODOS), key="modo_sel")
    st.button("Iniciar Simulado", on_click=start_simulado)
    st.stop()


# — Encerramento automático —
def tempo_restante():
    return max(0, int(st.session_state.duration - (time.time() - st.session_state.start_ts)))


respondidas = sum(1 for i in range(TOTAL) if f"resp_{i}" in st.session_state)

if not st.session_state.finished:
    if st.session_state.duration and tempo_restante() <= 0:
        st.session_state.finished = True
        st.session_state.tempo_esgotado = True
    elif respondidas == TOTAL:
        st.session_state.finished = True

# — Som de fundo —
# Montado só depois do clique em "Iniciar" (o clique libera o autoplay) e sempre na mesma posição da página.
# No fim do simulado o elemento deixa de ser renderizado e o navegador para o som; o F5 também.
if not st.session_state.finished and audio_html():
    st.markdown(audio_html(), unsafe_allow_html=True)


# — Timer ou status —
@st.fragment(run_every=1.0)
def render_timer():
    rem = tempo_restante()
    if rem <= 0:
        if not st.session_state.finished:
            st.session_state.finished = True
            st.session_state.tempo_esgotado = True
        st.rerun(scope="app")
    h, resto = divmod(rem, 3600)
    m, s = divmod(resto, 60)
    st.markdown(f'<div class="timer-box">⏳ Tempo: {h}:{m:02d}:{s:02d}</div>', unsafe_allow_html=True)


if not st.session_state.finished:
    if st.session_state.duration:
        render_timer()
    st.progress(respondidas / TOTAL, text=f"Respondidas: {respondidas} de {TOTAL}")
elif st.session_state.get("tempo_esgotado"):
    st.markdown("#### ⏰ Tempo esgotado! Simulado Finalizado.")
else:
    st.markdown("#### 🚨 Simulado Finalizado!")

# Só o que tem efeito no meio da linha (o rótulo sempre começa com "A. "); menos barras no aria-label.
_MD_SPECIAL = re.compile(r"([\\`*_\[\]|$~<>])")


def md_escape(texto):
    """Rótulos de radio/checkbox interpretam markdown e LaTeX ($); escapa para mostrar o texto literal."""
    return _MD_SPECIAL.sub(r"\\\1", texto)


def e(texto):
    return html.escape(texto, quote=True)


# — Renderização do feedback de uma pergunta já respondida —
def render_feedback(q, resposta):
    user = set(resposta)
    if not resposta:
        st.markdown("**⚠️ Não respondida.** As respostas corretas estão destacadas abaixo:")
    elif user == q["ans"]:
        st.markdown("**✅ Você acertou!**")
    else:
        st.markdown("**❌ Você errou.** As respostas corretas estão destacadas em verde:")

    partes = []
    for j, opt in enumerate(q["opts"]):
        texto = f"<b>{LETTERS[j]}.</b> {e(opt)}"
        if j in q["ans"]:
            sufixo = " ✅" if j in user else " ✅ <em>(resposta correta)</em>"
            partes.append(f'<div class="opt opt-ok">{texto}{sufixo}</div>')
        elif j in user:
            partes.append(f'<div class="opt opt-bad">{texto} ❌ <em>(sua resposta)</em></div>')
        else:
            partes.append(f'<div class="opt">{texto}</div>')

    corretas = ", ".join(LETTERS[j] for j in sorted(q["ans"]))
    rotulo = "Resposta correta" if len(q["ans"]) == 1 else "Respostas corretas"
    paragrafos = "".join(f"<p>{e(p)}</p>" for p in q["exp"])
    partes.append(f'<div class="exp-ok"><p><b>{rotulo}: {corretas}</b></p>{paragrafos}</div>')
    if q["why"]:
        linhas = "".join(f"<p><b>{LETTERS[j]}.</b> {e(w)}</p>" for j, w in sorted(q["why"].items()))
        partes.append(f'<div class="exp-bad">{linhas}</div>')
    if q["refs"]:
        links = " · ".join(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(u)}</a>' for u in q["refs"])
        partes.append(f'<div class="ref">Referência: {links}</div>')
    st.html("".join(partes))


# — Exibe perguntas —
for i, q in enumerate(QUESTIONS):
    with st.container(border=True):
        st.markdown(f"**Pergunta {i+1} de {TOTAL}**")
        tag = f'<div class="tag">{e(q["tag"])}</div>' if q["tag"] else ""
        st.html(f'<div class="stem">{e(q["q"])}</div>{tag}')

        if f"resp_{i}" in st.session_state or st.session_state.finished:
            render_feedback(q, st.session_state.get(f"resp_{i}", ()))
        elif q["mul"]:
            n_ans = len(q["ans"])
            st.caption(f"Escolha {n_ans} alternativas e confirme:")
            for j, opt in enumerate(q["opts"]):
                st.checkbox(f"{LETTERS[j]}. {md_escape(opt)}", key=f"a_{i}_{j}")
            marcadas = sum(bool(st.session_state.get(f"a_{i}_{j}")) for j in range(len(q["opts"])))
            st.button(
                "Confirmar resposta",
                key=f"c_{i}",
                disabled=marcadas != n_ans,
                on_click=lock_multiple,
                args=(i, len(q["opts"])),
            )
        else:
            opts = q["opts"]
            st.radio(
                "Escolha uma alternativa:",
                range(len(opts)),
                format_func=lambda j, opts=opts: f"{LETTERS[j]}. {md_escape(opts[j])}",
                index=None,
                key=f"a_{i}",
                on_change=lock_single,
                args=(i,),
                label_visibility="collapsed",
            )

# — Resultado final —
if st.session_state.finished:
    acertos_q = [set(st.session_state.get(f"resp_{i}", ())) == q["ans"] for i, q in enumerate(QUESTIONS)]
    acertos = sum(acertos_q)
    pct = acertos / TOTAL * 100
    st.metric("Sua Nota (%)", f"{pct:.2f}")
    st.success(f"Acertos: {acertos} de {TOTAL}")
    por_dominio = {}
    for ok, q in zip(acertos_q, QUESTIONS):
        partes = [p.strip() for p in q["tag"].split("·")]
        dominio = f"{partes[0]} · {partes[-1]}" if len(partes) > 1 else (partes[0] or "Sem domínio")
        total_d, ok_d = por_dominio.get(dominio, (0, 0))
        por_dominio[dominio] = (total_d + 1, ok_d + ok)
    if len(por_dominio) > 1:
        st.markdown("**Desempenho por domínio**")
        st.dataframe(
            pd.DataFrame(
                [{"Domínio": d, "Acertos": f"{ok} de {tot}", "%": round(ok / tot * 100, 1)}
                 for d, (tot, ok) in sorted(por_dominio.items())]
            ),
            hide_index=True,
            use_container_width=True,
        )
    st.caption("No exame real, a aprovação exige 750 numa escala de 100 a 1.000 (nota ajustada, não é percentual).")

# Informações de contato
st.markdown("---")
st.markdown("""
<div class="rodape">
    <b>📝 AWS Simulator (AIP-C01)</b><br>
    Desenvolvido por <a href="https://www.linkedin.com/in/aryribeiro" target="_blank" rel="noopener"><b>Ary Ribeiro</b></a><br>
    <small>obs.: recarregue (F5) para reiniciar o simulado.</small>
</div>
""", unsafe_allow_html=True)
