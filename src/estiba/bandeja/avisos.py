"""Cuando un mensaje merece un aviso en el escritorio, y cuando es ruido.

Antes avisaba cada mensaje de un cliente, con sonido. Medido sobre una
semana real (97 mensajes): 44 llegaron en rafaga, a menos de 3 minutos del
anterior del mismo chat, y 41 mientras el fletero ya estaba en esa charla
(le habia escrito hace menos de 10 minutos). Un cliente que manda "hola",
"necesito un flete", "de alberti a constitucion" y un audio son cuatro
sonidos por una sola cosa que hacer: contestarle.

La regla: se avisa el primer mensaje de cada turno del cliente. No se
avisa si ya se aviso ese turno (el cliente sigue escribiendo sin que le
hayan contestado y no paso un rato largo) ni si el fletero le escribio
recien: ya esta en esa conversacion, en el celular o en Estiba.
"""

from __future__ import annotations

from datetime import timedelta

#: Mensajes del cliente seguidos, sin respuesta en el medio, cuentan como uno
#: mientras no pase este rato entre uno y otro.
RAFAGA = timedelta(minutes=10)
#: Si le escribiste hace menos que esto, estas en la charla: no hace falta sonar.
CHARLA_ACTIVA = timedelta(minutes=10)


def merece_aviso(mensaje, anteriores) -> bool:
    """`anteriores`: los mensajes del mismo chat (pueden incluir a `mensaje`)."""
    if not mensaje.from_customer:
        return False
    previos = [m for m in anteriores if m.id != mensaje.id and m.sent_at <= mensaje.sent_at]
    nuestro = max((m.sent_at for m in previos if not m.from_customer), default=None)
    if nuestro is not None and mensaje.sent_at - nuestro < CHARLA_ACTIVA:
        return False
    suyo = max((m.sent_at for m in previos if m.from_customer), default=None)
    sin_contestar = suyo is not None and (nuestro is None or suyo > nuestro)
    if sin_contestar and mensaje.sent_at - suyo < RAFAGA:
        return False
    return True
