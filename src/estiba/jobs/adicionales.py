"""Dos trabajos a la vez en la misma charla: el pedido y uno adicional.

`episodios` corta una charla en el tiempo (un flete termina, otro empieza). Pero a veces el cliente, con un flete ya
cotizado, pide OTRO en el mismo momento: «te consulto por si las dudas, que también tendríamos que traer desde
Miramar hasta MdP dos mesas y dos sillas». Desde ahí los mensajes hablan de los dos, a veces en la misma frase
(caso real, 18-nov-2026: «Te confirmo el flete en Mardel por $50.000 […]. Por sumar Miramar […], son $70.000 más.
Total por todo $120.000»). Si todo queda en un solo flete, el adicional le pisa el origen, le suma su carga y el
total de los dos puede pisar el precio del confirmado.

Reglas (antes que modelo, como el resto de Estiba):
  * El adicional empieza con un mensaje del cliente que dice «también / además / aparte / otro flete» y trae un
    recorrido con OTRO origen, cuando el flete original ya está en marcha (el fletero ya respondió).
  * Desde ahí, cada oración va a uno u otro trabajo según sus señales: el lugar de origen, la carga y las
    direcciones de cada uno. Las señales del adicional mandan; sin señales, la oración sigue a la anterior.
  * Una oración con el total de los dos («total por todo», «en total») no es de ninguno: se descarta.
  * El flete original conserva su id; el adicional es `<id>-b`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from estiba.core.models import Conversation, Message, MessageKind
from estiba.extraction.extractor import SlotExtractor
from estiba.extraction.texto import plano
from estiba.mapa.ubicar import ALIAS

_ADICIONAL = re.compile(r"\b(?:tambien|ademas|aparte|otra\s+cosa|otro\s+(?:flete|viaje|tramo|trabajo))\b")
_TOTAL = re.compile(r"\b(?:total|por\s+todo|en\s+total|todo\s+junto|los\s+dos\s+(?:fletes|viajes))\b")
_DIRECCION = re.compile(r"\b(?:calle\s+\d{1,3}|([a-z]{3,})\s+(?:n\s*°?\s*)?\d{3,5})\b")
_NO_CALLE = {"los", "las", "por", "son", "total", "mas", "con", "que", "del", "una", "uno", "unos", "sale", "cuesta",
             "seria", "serian", "precio", "pesos", "tenes", "tengo", "todo", "hasta", "desde", "entre"}
_CIUDAD_PROPIA = re.compile(r"\baca\s+en\b")
_SINGULAR = re.compile(r"(?:es|s)$")

ID_ADICIONAL = "-b"


@dataclass(frozen=True)
class Separacion:
    principal: Conversation
    adicional: Conversation | None
    #: Índice del mensaje donde el cliente pidió el adicional (None si no hubo).
    desde: int | None = None


def _texto(conv: Conversation, m: Message) -> str:
    return conv.text_of(m) or ""


def _oraciones(texto: str) -> list[str]:
    """Una oración termina en . ! ? seguido de espacio o fin (no en el punto de miles de «$50.000»), o en un salto
    de línea."""
    salida, actual, n = [], [], len(texto)
    for k, c in enumerate(texto):
        if c == "\n":
            salida.append("".join(actual)); actual = []
            continue
        actual.append(c)
        if c in ".!?" and (k + 1 == n or texto[k + 1].isspace()):
            salida.append("".join(actual)); actual = []
    salida.append("".join(actual))
    return [o.strip() for o in salida if o.strip()]


def _palabras_de_carga(items) -> set[str]:
    """«2 mesas» → {"mesa"}: el sustantivo, para reconocerlo en singular o plural."""
    salida: set[str] = set()
    for it in items or []:
        for w in plano(str(it)).split():
            if w.isalpha() and len(w) > 2:
                salida.add(_SINGULAR.sub("", w) if len(w) > 4 else w)
    return salida


def _direcciones(texto: str) -> set[str]:
    halladas = set()
    for m in _DIRECCION.finditer(texto):
        if m.group(1) and m.group(1) in _NO_CALLE:
            continue
        halladas.add(" ".join(m.group(0).split()))
    return halladas


def _patron(palabras: set[str], frases: set[str]) -> re.Pattern | None:
    partes = [re.escape(f) for f in sorted(frases, key=len, reverse=True)]
    partes += [re.escape(p) + r"(?:s|es)?" for p in sorted(palabras)]
    return re.compile(r"\b(?:" + "|".join(partes) + r")\b") if partes else None


class _Senales:
    def __init__(self, palabras: set[str], frases: set[str]):
        self.palabras, self.frases = set(palabras), set(frases)
        self._re = _patron(self.palabras, self.frases)

    def sumar_direcciones(self, texto: str, del_otro: "_Senales | None" = None) -> None:
        nuevas = _direcciones(texto) - self.frases - (del_otro.frases if del_otro else set())
        if nuevas:
            self.frases |= nuevas
            self._re = _patron(self.palabras, self.frases)

    def en(self, texto: str) -> bool:
        return bool(self._re and self._re.search(texto))


def _inicio(conv: Conversation, extractor: SlotExtractor) -> tuple[int, object, object] | None:
    """El mensaje donde el cliente pide un trabajo adicional, con lo extraído de él y del flete hasta ahí."""
    for i, m in enumerate(conv.messages):
        if i == 0 or not m.from_customer:
            continue
        texto = plano(_texto(conv, m))
        if not _ADICIONAL.search(texto):
            continue
        # El flete original ya está en marcha: el fletero respondió y hay algo que mover.
        if not any(not x.from_customer for x in conv.messages[:i]):
            continue
        antes = extractor.extract(Conversation(conv.chat_id, conv.messages[:i], conv.transcripts)).job
        if antes.items is None and antes.origin is None and antes.quoted_price is None:
            continue
        suyo = extractor.extract(Conversation(conv.chat_id, [m], conv.transcripts)).job
        if suyo.origin is None:
            continue
        origen = plano(str(suyo.origin.value))
        previo = plano(str(antes.origin.value)) if antes.origin else None
        if origen == previo or origen in ALIAS or (previo and origen in previo):
            continue
        return i, antes, suyo
    return None


_DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


def _debiles(job) -> re.Pattern | None:
    """El día y la hora del flete original («el domingo», «a las 11»): señal floja, solo abre un mensaje."""
    partes = []
    if job.when:
        partes.append(_DIAS[job.when.value.weekday()])
    if job.hour:
        partes.append(rf"(?:a\s+las\s+)?{job.hour.value.hour}\s*(?:hs?|horas)\b|a\s+las\s+{job.hour.value.hour}")
    return re.compile(r"\b(?:" + "|".join(partes) + r")") if partes else None


def separar(conv: Conversation, extractor: SlotExtractor | None = None, traza: list | None = None) -> Separacion:
    """Parte la charla en el flete original y, si lo hay, el adicional pedido en el medio."""
    extractor = extractor or SlotExtractor()
    hallado = _inicio(conv, extractor)
    if hallado is None:
        return Separacion(conv, None)
    i, antes, suyo = hallado
    previos = " ".join(plano(_texto(conv, m)) for m in conv.messages[:i])
    origen = plano(str(suyo.origin.value))
    adicional = _Senales(_palabras_de_carga(suyo.items.value if suyo.items else []), {origen})
    # La ciudad del flete original, con todos sus nombres («en mar del plata» → también «mardel», «mdp»).
    nombres = {**ALIAS, **{plano(v): v for v in ALIAS.values()}}
    nombradas = {v for k, v in nombres.items() if re.search(r"\b" + re.escape(k) + r"\b", previos)}
    ciudades = {k for k, v in nombres.items() if v in nombradas} - {origen}
    principal = _Senales(_palabras_de_carga(antes.items.value if antes.items else []), ciudades)
    principal.sumar_direcciones(previos)
    debil = _debiles(antes)

    mios: list[Message] = list(conv.messages[:i])
    suyos: list[Message] = []
    ultimo = "b"  # el mensaje que abre el adicional es del adicional
    for k, m in enumerate(conv.messages[i:], start=i):
        texto = _texto(conv, m)
        if k == i:
            suyos.append(m)
            adicional.sumar_direcciones(plano(texto))
            continue
        if not texto:
            (suyos if ultimo == "b" else mios).append(m)
            continue
        destino: list[str | None] = []
        for o in _oraciones(texto):
            p = plano(o)
            if _TOTAL.search(p) and re.search(r"\d", p):
                destino.append(None)
            elif adicional.en(p):
                destino.append("b")
                adicional.sumar_direcciones(p, principal)
            elif principal.en(p) or _CIUDAD_PROPIA.search(p):
                destino.append("a")
                principal.sumar_direcciones(p, adicional)
            elif previa := next((d for d in reversed(destino) if d in ("a", "b")), None):
                destino.append(previa)
            elif debil and debil.search(p):
                destino.append("a")
            else:
                destino.append("?")
        # Lo que abre el mensaje sin señales sigue a la primera oración con señales; si no hay ninguna, al
        # trabajo del que se venía hablando.
        primera = next((d for d in destino if d in ("a", "b")), ultimo)
        destino = [primera if d == "?" else d for d in destino]
        oraciones = _oraciones(texto)
        if traza is not None:
            traza.extend((m.id, o, d) for o, d in zip(oraciones, destino))
        for q, lista in (("a", mios), ("b", suyos)):
            propias = [o for o, d in zip(oraciones, destino) if d == q]
            if not propias:
                continue
            if len(propias) == len(oraciones):  # entero para este trabajo: el mensaje tal cual (audio incluido)
                lista.append(m)
            else:
                lista.append(Message(m.id, m.chat_id, m.from_customer, m.sent_at, MessageKind.TEXT, text=" ".join(propias), source=m.source))
        ultimo = next((d for d in reversed(destino) if d in ("a", "b")), ultimo)
    return Separacion(Conversation(conv.chat_id, mios, conv.transcripts), Conversation(conv.chat_id, suyos, conv.transcripts), i)



def con_adicional(ep, extractor: SlotExtractor | None = None) -> list:
    """El episodio tal cual, o el original y su adicional (`<id>-b`) si en el medio se pidió otro trabajo."""
    from estiba.jobs.episodios import Episodio

    s = separar(ep.conversacion, extractor)
    if s.adicional is None:
        return [ep]
    return [Episodio(ep.job_id, s.principal), Episodio(ep.job_id + ID_ADICIONAL, s.adicional)]
