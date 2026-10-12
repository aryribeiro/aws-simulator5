"""Camada de dados do simulado: dois caches independentes na RAM do servidor, carregados em paralelo.

- CSV (leve): Google Drive -> CSV local, com retry + backoff exponencial + jitter e disjuntor.
- MP3 (pesado): static/som.mp3 ÍNTEGRO (sem recompressão), guardado já em base64.

Cada cache tem o próprio lock e o próprio TTL (24 h); um não espera o outro. Módulo Python é importado uma única
vez por processo, então estes objetos são compartilhados por todas as sessões do servidor (não há cópia por usuário).
Nada aqui chama st.*: as cargas rodam em threads do pool, fora do ciclo de rerun do Streamlit.
"""
import ast
import base64
import hashlib
import json
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from io import StringIO

import pandas as pd
import requests

TTL = 24 * 60 * 60           # 24 horas, para os dois caches
DRIVE_COOLDOWN = 5 * 60      # após falha do Drive, vai direto ao CSV local por 5 min
LETTERS = "ABCDEFGH"
MAX_CSV_BYTES = 2 * 1024 * 1024  # teto para um CSV vindo do dispositivo do aluno

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cache-simulado")  # CSV e MP3 em paralelo
_HTTP = requests.Session()
_HTTP.headers["User-Agent"] = "aws-simulator5"


# ---------------------------------------------------------------- validação do banco
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


def parse_questions(text):
    """Valida o CSV inteiro e devolve uma estrutura imutável (segura para compartilhar entre sessões)."""
    # keep_default_na=False: alternativas como "None" ou "NA" não podem virar NaN
    df = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
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


def version_of(data):
    """Versão = hash do conteúdo. O dispositivo compara a sua cópia com a do servidor por este valor."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


# ---------------------------------------------------------------- cache genérico com TTL
class Entry:
    __slots__ = ("value", "version", "source", "loaded_at")

    def __init__(self, value, version, source):
        self.value, self.version, self.source, self.loaded_at = value, version, source, time.time()

    def fresh(self, ttl):
        return time.time() - self.loaded_at < ttl


class RamCache:
    """Um valor por processo, com TTL. A carga roda UMA vez mesmo com muitas sessões pedindo ao mesmo tempo
    (single-flight): as demais recebem o mesmo Future. Falhas não são guardadas; a próxima chamada tenta de novo."""

    def __init__(self, name, loader, ttl=TTL):
        self.name, self._loader, self.ttl = name, loader, ttl
        self._lock = threading.Lock()
        self._entry = None
        self._future = None

    def peek(self):
        """Entrada válida já na RAM, ou None (sem disparar carga)."""
        e = self._entry
        return e if e is not None and e.fresh(self.ttl) else None

    def future(self):
        """Future da entrada: concluído se já está na RAM; senão dispara (ou reaproveita) a carga em segundo plano."""
        with self._lock:
            e = self.peek()
            if e is not None:
                done = Future()
                done.set_result(e)
                return done
            if self._future is None or self._future.done():
                self._future = _POOL.submit(self._load)
            return self._future

    def get(self, timeout=None):
        return self.future().result(timeout=timeout)

    def _load(self):
        value, version, source = self._loader()
        entry = Entry(value, version, source)
        self._entry = entry
        return entry


# ---------------------------------------------------------------- loaders
_breaker = {"open_until": 0.0}


def fetch_csv(url, attempts=3, base_delay=0.5):
    """Baixa o CSV com retry, backoff exponencial e jitter. Recusa HTML (página de aviso/permissão do Drive)."""
    last = None
    for n in range(attempts):
        try:
            resp = _HTTP.get(url, timeout=(5, 20))
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


def make_csv_loader(drive_url, local_csv):
    def load():
        if drive_url and time.time() >= _breaker["open_until"]:
            try:
                text = fetch_csv(drive_url)
                return {"bank": parse_questions(text), "text": text}, version_of(text), "drive"
            except Exception:
                _breaker["open_until"] = time.time() + DRIVE_COOLDOWN
                if not local_csv.exists():
                    raise
        if local_csv.exists():
            text = local_csv.read_text(encoding="utf-8")
            return {"bank": parse_questions(text), "text": text}, version_of(text), "local"
        raise RuntimeError("Defina GOOGLE_DRIVE_CSV_URL no .env/Secrets ou coloque um questions.csv na pasta do app.")
    return load


def make_mp3_loader(audio_file):
    def load():
        raw = audio_file.read_bytes()  # o arquivo inteiro, byte a byte: nada de recompressão
        return {"b64": base64.b64encode(raw).decode(), "size": len(raw)}, version_of(raw), "disco"
    return load


_CACHES = {}
_CACHES_LOCK = threading.Lock()


def caches(drive_url, local_csv, audio_file):
    """Os dois caches do processo (criados uma única vez, não a cada rerun): ('csv', 'mp3')."""
    key = (drive_url or "", str(local_csv), str(audio_file))
    with _CACHES_LOCK:
        if key not in _CACHES:
            _CACHES[key] = (RamCache("csv", make_csv_loader(drive_url, local_csv)),
                            RamCache("mp3", make_mp3_loader(audio_file)))
        return _CACHES[key]


def bank_from_device(text):
    """Valida o CSV que veio do cache do dispositivo. Serve só àquela sessão: nunca entra no cache compartilhado."""
    if not isinstance(text, str) or not text or len(text.encode("utf-8")) > MAX_CSV_BYTES:
        return None
    try:
        return parse_questions(text)
    except Exception:
        return None
