import html
import os
import re
import time
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

import cache_simulado as cs

# Carrega as variáveis do arquivo .env (no Streamlit Cloud, os Secrets viram variáveis de ambiente)
load_dotenv()
GOOGLE_DRIVE_CSV_URL = os.getenv("GOOGLE_DRIVE_CSV_URL")
BASE_DIR = Path(__file__).parent
LOCAL_CSV = BASE_DIR / "questions.csv"
AUDIO_FILE = BASE_DIR / "static" / "som.mp3"  # som íntegro (432 Hz), sem recompressão

EXAM_MINUTES = 180          # duração oficial do AIP-C01
ESL_EXTRA_MINUTES = 30      # acomodação ESL +30 da AWS para quem não é nativo em inglês
LETTERS = cs.LETTERS
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
    /* Blocos invisíveis (ponte de cache e timer fixo) saem do fluxo: sem faixa vazia entre os elementos */
    div[data-testid="stElementContainer"]:has(iframe[title*="cache_bridge"]),
    div[data-testid="stElementContainer"]:has(.timer-box) {
        position: absolute !important;
        width: 0 !important;
        height: 0 !important;
        overflow: visible;
    }
    iframe[title*="cache_bridge"] { width: 0 !important; height: 0 !important; border: 0; }
    /* Carregando as perguntas (primeiro acesso com o servidor frio) */
    @keyframes simgira { to { transform: rotate(360deg); } }
    .carregando { display: flex; align-items: center; gap: 10px; color: #31333f; margin: 1rem 0; }
    .carregando i {
        width: 22px; height: 22px; display: inline-block; border-radius: 50%;
        border: 3px solid #d0d4dc; border-top-color: #ff4b4b; animation: simgira .8s linear infinite;
    }
    /* Celular: o timer vira uma faixa fina no topo (não cobre o texto) e o fim da página ganha respiro
       para os ícones do Streamlit Cloud (canto inferior direito) não taparem o último conteúdo */
    @media (max-width: 640px) {
        .block-container { padding-top: 2.6rem; padding-bottom: 7rem; }
        .timer-box {
            top: 0; left: 0; right: 0;
            text-align: center;
            font-size: 15px;
            padding: 5px 8px;
            border-width: 0 0 1px 0;
            border-radius: 0;
            background: #ffffff;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
        }
    }
</style>
""", unsafe_allow_html=True)


# — Caches —
# Servidor: dois caches independentes na RAM (CSV leve e MP3 pesado), carregados em paralelo, TTL de 24 h.
# Dispositivo: IndexedDB do navegador (cache_bridge/index.html), também com TTL de 24 h.
# Ordem do CSV: dispositivo -> RAM do servidor -> Google Drive (-> CSV local). MP3: dispositivo -> RAM -> arquivo.
CSV_CACHE, MP3_CACHE = cs.caches(GOOGLE_DRIVE_CSV_URL, LOCAL_CSV, AUDIO_FILE)
DEVICE_GRACE = 3.0  # segundos que o servidor espera o relatório do dispositivo antes de seguir sem ele
_ponte = components.declare_component("cache_bridge", path=str(BASE_DIR / "cache_bridge"))

csv_futuro = CSV_CACHE.future()   # não bloqueia: dispara a carga do CSV se a RAM estiver vazia
MP3_CACHE.future()                # e, em paralelo, a do MP3 (só a RAM do servidor; o aluno recebe depois)
st.session_state.setdefault("t_abertura", time.time())


def dispositivo():
    return st.session_state.get("cache_bridge") or {}


def resolver_banco():
    """Banco desta sessão + de onde veio. (None, None) enquanto ainda espera o dispositivo ou o Drive."""
    if "bank" in st.session_state:
        return st.session_state.bank, st.session_state.fonte
    dev = dispositivo()
    srv = CSV_CACHE.peek()
    dcsv = dev.get("csv") or {}
    # 1ª camada: a cópia do dispositivo (24 h). Se o servidor tem a MESMA versão, usa o objeto já na RAM
    # (zero cópia); se o servidor está frio, valida a cópia do aluno e serve só a esta sessão.
    if dcsv.get("version") and (srv is None or dcsv["version"] == srv.version):
        if srv is not None:
            return srv.value["bank"], "dispositivo"
        banco = cs.bank_from_device(dcsv.get("text"))
        if banco:
            return banco, "dispositivo"
    # 2ª camada: a RAM do servidor (se a versão do dispositivo for outra, a do servidor é a mais nova).
    # Se a RAM foi preenchida durante esta visita, a origem real foi o Drive (ou o CSV local).
    if srv is not None:
        if srv.loaded_at >= st.session_state.t_abertura:
            return srv.value["bank"], ("drive" if srv.source == "drive" else "csv local")
        return srv.value["bank"], "servidor"
    if not dev.get("reported") and time.time() - st.session_state.t_abertura < DEVICE_GRACE:
        return None, None
    # 3ª camada: Google Drive (com retry/backoff/disjuntor e, por último, o CSV local)
    if not csv_futuro.done():
        return None, None
    entrada = csv_futuro.result()  # se a carga falhou, a exceção sobe e vira a mensagem de erro abaixo
    return entrada.value["bank"], ("drive" if entrada.source == "drive" else "csv local")


def args_ponte(fase, total):
    dev = dispositivo()
    srv = CSV_CACHE.peek()
    csv_arg = {"version": srv.version if srv else None, "text": None}
    # Depois que a RAM do servidor tem o CSV, ele vai para o dispositivo (uma vez por versão)
    if srv is not None and dev.get("reported") and dev.get("idb") and dev.get("csv_have") != srv.version:
        csv_arg["text"] = srv.value["text"]
    mp3_arg = {"version": None, "b64": None}
    if fase == "exam":
        try:
            m = MP3_CACHE.get(timeout=30)
            mp3_arg["version"] = m.version
            # Só quando o dispositivo pede (já com a prova na tela) e não tem esta versão: lazy loading
            if dev.get("mp3_need") and dev.get("mp3_have") != m.version:
                mp3_arg["b64"] = m.value["b64"]
        except Exception:
            pass  # sem som não impede a prova
    return {"phase": fase, "total": total, "ttl_ms": int(cs.TTL * 1000), "csv": csv_arg, "mp3": mp3_arg}


try:
    QUESTIONS, FONTE = resolver_banco()
except Exception as e:
    st.error(f"Erro ao carregar as perguntas: {e}")
    st.stop()

if QUESTIONS is None:
    # Primeiro acesso com o servidor frio: aguarda o dispositivo ou o Drive sem travar a tela
    _ponte(**args_ponte("loading", 0), key="cache_bridge", default=None)
    st.markdown('<div class="carregando"><i></i><span>Carregando as perguntas…</span></div>', unsafe_allow_html=True)

    @st.fragment(run_every=0.4)
    def aguardar_banco():
        try:
            pronto = resolver_banco()[0] is not None
        except Exception:
            pronto = True  # deixa o run completo mostrar o erro
        if pronto:
            st.rerun(scope="app")

    aguardar_banco()
    st.stop()

TOTAL = len(QUESTIONS)

# — Callbacks (idempotentes: clique duplo ou repetido não muda o resultado) —
def start_simulado():
    if st.session_state.get("started"):
        return
    # A sessão congela o banco no início: se o cache renovar no meio da prova, nada muda para o aluno
    st.session_state.bank = QUESTIONS
    st.session_state.fonte = FONTE
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


# — Encerramento automático (antes da ponte, para o som parar no mesmo run em que a prova acaba) —
def tempo_restante():
    return max(0, int(st.session_state.duration - (time.time() - st.session_state.start_ts)))


respondidas = sum(1 for i in range(TOTAL) if f"resp_{i}" in st.session_state)

if st.session_state.started and not st.session_state.finished:
    if st.session_state.duration and tempo_restante() <= 0:
        st.session_state.finished = True
        st.session_state.tempo_esgotado = True
    elif respondidas == TOTAL:
        st.session_state.finished = True

# — Ponte com o dispositivo (sempre nesta mesma posição da página: o iframe nunca remonta) —
FASE = "start" if not st.session_state.started else ("done" if st.session_state.finished else "exam")
_ponte(**args_ponte(FASE, TOTAL), key="cache_bridge", default=None)

# — Tela inicial —
if not st.session_state.started:
    st.markdown(
        f"**{TOTAL} perguntas** em inglês, idioma do exame real, cobrindo os domínios 1 a 3 do guia oficial. "
        "Ao responder cada pergunta, aparecem a resposta correta com a explicação (em verde) e o motivo de "
        "cada alternativa incorreta (em vermelho)."
    )
    st.radio("Modo do simulado:", list(MODOS), key="modo_sel")
    st.button("Iniciar Simulado", on_click=start_simulado)
    ORIGENS = {"dispositivo": "do cache do seu dispositivo", "servidor": "do cache do servidor",
               "drive": "do Google Drive", "csv local": "do CSV local"}
    st.caption(f"⚡ Perguntas carregadas {ORIGENS.get(FONTE, '')}.")
    st.stop()


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
