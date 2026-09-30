"""Lo unico que Estiba manda por WhatsApp sin preguntar.

Toda la app se apoya en una regla: nada sale sin que el fletero lo apruebe.
Esto es la unica excepcion, y esta escrita aparte para que se vea entera de
un vistazo y se pueda apagar sin tocar nada mas.

Por que esta: el que toca un anuncio de Meta llega con un texto que no
escribio —"Quiero mas informacion"— y se queda esperando. Contestarle no
necesita criterio: siempre es la misma frase, siempre dice lo mismo, y lo
unico que cambia es cuanto tarda. De las 58 conversaciones reales, 15
empezaron asi. Aprobar quince veces el mismo saludo no es decidir nada: es
demorar la respuesta hasta que el fletero mira el telefono.

Hasta ahi, y ni un paso mas. En cuanto la persona contesta algo, vuelve el
circuito de siempre: Estiba propone y el fletero decide.

Las seis condiciones son todas necesarias. Cualquiera que falte, no sale
nada y el borrador queda esperando aprobacion como cualquier otro.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from estiba.bandeja.redactor import APERTURA
from estiba.conversation.clasificacion import _DESDE_PUBLI
from estiba.core.models import Conversation
from estiba.extraction.texto import plano

#: Apagarlo sin tocar codigo: ESTIBA_APERTURA_AUTOMATICA=0
VARIABLE = "ESTIBA_APERTURA_AUTOMATICA"


#: Pasado esto, un saludo automatico ya no es "al toque": es tarde, y tarde
#: el que decide es el fletero.
RECIEN = timedelta(hours=3)


def encendido() -> bool:
    return os.environ.get(VARIABLE, "1").strip().lower() not in ("0", "no", "off", "false")


def sale_sola(conv: Conversation, texto: str, ahora: datetime, ya_mando: bool) -> bool:
    """Si esta apertura se puede mandar sin aprobacion.

    `ya_mando` dice si a esta conversacion ya le salio algo alguna vez: se
    consulta afuera porque vive en la tabla de borradores.
    """
    if not encendido():
        return False

    # 1. Es la apertura, palabra por palabra. Si el redactor escribio
    #    cualquier otra cosa es porque entendio algo del pedido, y eso ya
    #    es una respuesta con contenido: la decide una persona.
    if texto.strip() != APERTURA:
        return False

    mensajes = [m for m in conv.messages if m.sent_at <= ahora]
    if not mensajes:
        return False

    # 2. El fletero nunca escribio en este chat. No se mete en el medio de
    #    una conversacion empezada, ni le "vuelve a saludar" a nadie.
    if any(not m.from_customer for m in mensajes):
        return False

    # 3. Nunca salio nada antes por esta via. El redactor podria volver a
    #    proponer la apertura si el cliente insiste sin decir nada; no se
    #    saluda dos veces.
    if ya_mando:
        return False

    # 4. Llego por la publicidad. Es lo que hace que la respuesta sea
    #    obvia: el texto lo puso el boton del anuncio, no la persona. Un
    #    "hola" escrito a mano puede ser cualquier cosa y espera.
    primero = conv.text_of(mensajes[0]) or ""
    if not _DESDE_PUBLI.search(plano(primero)):
        return False

    # 5. Y no dijo nada mas. Si ademas del texto del anuncio escribio algo
    #    suyo, ya hay con que contestarle mejor que con un saludo.
    if len(mensajes) != 1:
        return False

    # 6. Y llego hace poco. Lo que se recupera despues de tener la app
    #    cerrada puede tener horas: un "¿que queres llevar?" automatico a la
    #    noche por un mensaje de la mañana no es rapido, es raro. Queda en
    #    la bandeja y lo manda el fletero, con las palabras que quiera.
    return ahora - mensajes[0].sent_at <= RECIEN
