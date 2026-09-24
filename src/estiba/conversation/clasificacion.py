"""Que es esta conversacion: negocio, otra cosa, o no se sabe.

La regla de oro es no preguntar lo que se puede inferir. Estiba mira la
conversacion y decide sola cuando tiene con que; solo cuando no puede
determinarlo aparece una decision para una persona.

No confunde "no es cliente" con "no se". Marcar a alguien como personal por
las dudas seria peor que preguntar: se pierde un cliente en silencio. Ante
la duda, `DUDOSA`, que es la unica clase que pide intervencion.

Las reglas son explicitas a proposito: se puede decir por que quedo en cada
clase, y eso se ve en la pantalla.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from estiba.core.models import Conversation
from estiba.extraction.extractor import Extraccion
from estiba.extraction.intencion import Intencion
from estiba.extraction.texto import plano

#: Senales fuertes: esto no aparece por casualidad en una publicidad.
_CAMPOS_FUERTES = ("origin", "destination", "items")

#: Senales debiles: solas no dicen nada. Una promo con "VALIDO SOLO POR HOY"
#: deja una fecha, y "el precio del plan" parece una consulta de precio.
_CAMPOS_DEBILES = ("when",)

#: El oficio nombrado de frente. Alcanza con uno para saber de que se habla.
_OFICIO = re.compile(
    r"\b(?:flete|fletes|fletero|mudanza|mudanzas|mudar|acarreo|camioneta|"
    r"trafic|utilitario|ayudante|ayudantes|carga|descarga|embalar|"
    r"trasladar|transportar)\b"
)

#: Promociones y cadenas: casi siempre llegan de numeros que nunca contestan.
_PROMO = re.compile(
    r"\b(?:promo|promocion|descuento|descuentos|gratis|sorteo|sorteamos|"
    r"oferta|ofertas|cupon|codigo\s+de\s+descuento|black\s+friday|hot\s+sale|"
    r"suscribite|registrate|valido\s+(?:solo\s+)?(?:hasta|por))\b"
)
_ENLACE = re.compile(r"https?://|\bwa\.me/|\bbit\.ly\b")
_ENLACE_COMPLETO = re.compile(r"\S*(?:https?://|wa\.me/|bit\.ly)\S*")

#: Como se llama el contacto en WhatsApp. Un nombre puede cantar solo lo que
#: es, sin leer un mensaje: "ideaspublicom518" o "Promos Movistar".
_NOMBRE_COMERCIAL = re.compile(
    r"\b(?:promo|promos|promocion|ofertas?|descuentos?|publicidad|marketing|"
    r"ventas|tienda|shop|store|canal|emprendimiento|delivery|inmobiliaria)\b"
)
#: Un usuario, no una persona: todo junto, sin espacios, con numeros al
#: final. El espacio es lo que separa "ideaspublicom518" de "Natalia
#: Parece 462", que es una persona de carne y hueso.
_ES_HANDLE = re.compile(r"^[a-z][a-z._-]{5,}\d{2,}$")

#: Alguien que ofrece su servicio en vez de pedir el tuyo. No dice "promo"
#: ni "descuento": es una propuesta escrita a mano, y entra igual como lead.
_OFRECE = re.compile(
    r"\b(?:te\s+(?:ofrezco|cuento\s+como)|le\s+ofrecemos|ofrecemos\s+"
    r"|sumate|seguinos|segui\s+nuestro|canal\s+de\s+whatsapp|lista\s+de\s+difusion"
    r"|me\s+dedico\s+a|brindo\s+(?:el\s+)?servicio|mi\s+emprendimiento"
    r"|contactame\s+si\s+(?:necesitas|queres))\b"
)

#: Afecto y casa: una charla que no es del negocio.
_PERSONAL = re.compile(
    r"\b(?:te\s+amo|te\s+quiero|mi\s+amor|amor\b|besos?|beso\b|cari[nñ]o|"
    r"mam[aá]\b|pap[aá]\b|abuela|abuelo|hermano|hermana|t[ií]a\b|t[ií]o\b|"
    r"hij[oa]\b|nen[ae]\b|cu[nñ]ad[oa]|suegr[ao]|feliz\s+cumple|te\s+extra[nñ]o)\b"
)


class Clase(Enum):
    """Suma cero: una conversacion es una sola de estas, nunca dos.

    Lead y cliente no son lo mismo. El que pregunta es un lead; el que ya
    hizo un flete es un cliente, y se trata distinto. La diferencia la
    deduce Estiba del historial, no hace falta marcarla a mano.
    """

    #: Pregunto por un flete y todavia no hizo ninguno.
    LEAD = "lead"
    #: Ya hizo al menos un flete: no es un desconocido.
    CLIENTE = "cliente"
    #: Charla que no es del negocio: familia, amigos, la pareja.
    PRIVADO = "privado"
    #: Publicidad, cadenas, promociones.
    SPAM = "spam"
    #: No se puede determinar: la unica que pide una decision humana.
    DUDOSA = "dudosa"

    @property
    def es_negocio(self) -> bool:
        return self in (Clase.LEAD, Clase.CLIENTE)


#: Menos que esto dicho por el cliente es "todavia no hablaron", no una duda.
MINIMO_PARA_JUZGAR = 25


@dataclass(frozen=True)
class Clasificacion:
    clase: Clase
    #: Por que quedo asi, en criollo, para mostrarlo en la pantalla.
    porque: str
    #: Si dijo lo bastante como para que la duda sea una duda de verdad.
    hay_con_que: bool = True

    @property
    def es_negocio(self) -> bool:
        return self.clase.es_negocio

    @property
    def pide_decision(self) -> bool:
        """La unica situacion que gasta atencion humana.

        "Hola" suelto no es una duda: es que todavia no hablaron. Preguntar
        ahi seria pedir una decision que ni una persona puede tomar.
        """
        return self.clase is Clase.DUDOSA and self.hay_con_que


def _sin_enlaces(texto: str) -> str:
    """Las URLs no se leen: traen basura que parece vocabulario de promo.

    Un link de MercadoLibre con "...&matt_tool=...clic..." adentro hacia que
    una charla real de un cliente pasara por publicidad.
    """
    return _ENLACE_COMPLETO.sub(" ", texto)


def _texto_del_cliente(conv: Conversation) -> str:
    """Todo lo que dijo el cliente, con los audios ya transcriptos.

    `text_of` es lo que hay que usar: la mitad de la gente manda audios y
    mirar solo `m.text` deja esas conversaciones mudas.
    """
    dichos = (conv.text_of(m) for m in conv.messages if m.from_customer)
    return plano(" ".join(t for t in dichos if t))


def _texto_del_fletero(conv: Conversation) -> str:
    """Lo que escribe el fletero. Como le habla a alguien dice quien es.

    Si el le manda "un abrazo" o "gracias Sabi", eso pesa tanto como lo que
    diga el otro: a un cliente no se le escribe asi.
    """
    dichos = (conv.text_of(m) for m in conv.messages if not m.from_customer)
    return plano(" ".join(t for t in dichos if t))


def clasificar(conv: Conversation, ex: Extraccion, nombre: str | None = None) -> Clasificacion:
    """Que es esta conversacion, y por que.

    El orden no es casual. Primero lo que solo pasa en un pedido de flete
    (de donde, a donde, que lleva). Despues la publicidad, porque las promos
    dejan rastros que parecen datos: "VALIDO SOLO POR HOY" deja una fecha y
    "el precio del plan" parece una consulta de precio. Recien al final las
    senales debiles, ya descartado que sea una promo.
    """
    texto = _texto_del_cliente(conv)
    nuestros = [m for m in conv.messages if not m.from_customer]
    de_ellos = [m for m in conv.messages if m.from_customer]

    fuertes = [c for c in _CAMPOS_FUERTES if c in ex.ganadores]
    if fuertes:
        return Clasificacion(Clase.LEAD, f"se entendió {', '.join(fuertes)} del chat")
    # Como se llama ya lo dice, sin leer nada. Va antes que el oficio porque
    # una publicidad de transporte tambien lo nombra, y despues de los datos
    # concretos porque si dijo de donde a donde, es un pedido igual.
    liso = plano(nombre or "")
    if liso and (_NOMBRE_COMERCIAL.search(liso) or _ES_HANDLE.match(liso)):
        return Clasificacion(Clase.SPAM, f"el contacto se llama “{nombre}”")

    if _OFICIO.search(texto):
        return Clasificacion(Clase.LEAD, "habla del oficio")

    # Publicidad antes que las senales debiles: si no, una promo con fecha
    # entra al embudo como si fuera un cliente. Pero un link solo no alcanza,
    # y si ya hubo ida y vuelta con esta persona, tampoco: perder un cliente
    # en silencio es peor que una decision de mas.
    promos = len(_PROMO.findall(_sin_enlaces(texto)))
    if promos and (not nuestros or (promos >= 2 and len(nuestros) < 3)):
        return Clasificacion(Clase.SPAM, "parece publicidad, no una consulta")

    # Ofrece su servicio en vez de pedir el tuyo.
    if _OFRECE.search(_sin_enlaces(texto)) and len(nuestros) < 3:
        return Clasificacion(Clase.SPAM, "te está ofreciendo algo, no pidiendo un flete")

    # Lo personal tambien va antes que lo debil, por lo mismo que la
    # publicidad: "manana paso por lo de la abuela" deja una fecha, y una
    # fecha suelta no es un pedido de flete.
    if _PERSONAL.search(texto) and nuestros:
        return Clasificacion(Clase.PRIVADO, "charla personal, no del trabajo")
    # Como le hablas vos vale tanto como lo que diga el otro.
    if _PERSONAL.search(_texto_del_fletero(conv)):
        return Clasificacion(Clase.PRIVADO, "le hablás como a alguien de confianza")

    debiles = [c for c in _CAMPOS_DEBILES if c in ex.ganadores]
    if debiles or any(i.intencion is Intencion.CONSULTA for i in ex.intenciones):
        return Clasificacion(Clase.LEAD, "parece una consulta, pero dijo poco")

    # Una charla larga y de ida y vuelta sin una sola palabra del oficio no
    # parece un cliente, pero tampoco hay con que afirmarlo.
    if len(de_ellos) >= 5 and nuestros:
        return Clasificacion(Clase.DUDOSA, "hablan seguido pero nunca de fletes")
    if len(texto.strip()) < MINIMO_PARA_JUZGAR:
        return Clasificacion(Clase.DUDOSA, "todavía no dijo de qué se trata", hay_con_que=False)
    return Clasificacion(Clase.DUDOSA, "escribió, pero no se entiende para qué")
