"""Volver a escribirle a quien no contrato, cuando tiene sentido y sin spam.

No se persigue a todos: una consulta que nunca llego a presupuesto no
entra, y lo que no se puede resolver (distancia, vehiculo) tampoco. Lo que
entra sigue una secuencia segun por que se perdio, con un limite de
mensajes, un intervalo minimo y un horario, y se corta en cuanto la persona
responde, contrata o pide que no le escriban.

Nada sale solo: Estiba prepara el mensaje, la persona lo revisa y lo manda
(o lo copia). La cola no se guarda: se calcula de las personas, sus
oportunidades y sus recontactos, asi nunca queda desincronizada.

Ver docs/recuperacion.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from estiba.crm.personas import Oportunidad, Persona, reputacion

# --------------------------------------------------------------------------
# Plantillas y secuencias (datos, editables desde la app)
# --------------------------------------------------------------------------

PLANTILLAS: dict[str, dict[str, str]] = {
    "seguimiento": {
        "nombre": "Seguimiento suave",
        "texto": "Hola {{nombre}}, ¿cómo estás? Te escribo por el flete que habías consultado. ¿Pudiste "
                 "resolverlo o todavía necesitás traslado? Si querés, revisamos nuevamente disponibilidad y "
                 "presupuesto.",
    },
    "precio": {
        "nombre": "Perdido por precio",
        "texto": "Hola {{nombre}}, ¿cómo estás? Hace unos días nos habías consultado por un flete. Te escribo "
                 "porque estoy organizando los próximos viajes y quizás pueda ofrecerte una alternativa "
                 "distinta si todavía necesitás hacerlo.",
    },
    "no-respondio": {
        "nombre": "No respondió",
        "texto": "Hola {{nombre}}, ¿cómo estás? Habíamos quedado con la consulta del flete y no quería dejarla "
                 "colgada. Si todavía lo necesitás, decime y vemos disponibilidad.",
    },
    "sin-lugar": {
        "nombre": "No teníamos lugar",
        "texto": "Hola {{nombre}}, ¿cómo estás? La otra vez no pude hacerte el flete porque no tenía lugar. "
                 "Ahora tengo disponibilidad: si todavía lo necesitás, decime y lo coordinamos.",
    },
    "consulta-antigua": {
        "nombre": "Consulta antigua",
        "texto": "Hola {{nombre}}, ¿cómo estás? Hace un tiempo nos habías consultado por un traslado. Te "
                 "escribo porque seguimos trabajando con fletes y mini fletes por la zona. Si volvés a "
                 "necesitar uno, podés escribirme por acá.",
    },
    "cliente-anterior": {
        "nombre": "Cliente anterior",
        "texto": "Hola {{nombre}}, ¿cómo estás? ¿Todo bien? Te escribo porque ya habíamos hecho un flete "
                 "juntos. Si en estos días necesitás algún traslado, mudanza chica o retiro de muebles, "
                 "avisame y coordinamos.",
    },
}

#: Secuencia -> (motivos que la disparan, pasos (dias, plantilla)). Los dias
#: se cuentan desde la perdida (o desde el ultimo flete, para el cliente
#: anterior). Sin descuentos automaticos en ningun texto.
SECUENCIAS: dict[str, dict] = {
    "sin-respuesta": {"nombre": "Lead sin respuesta", "motivos": ["no-respondio"],
                      "pasos": [[7, "no-respondio"], [30, "consulta-antigua"]]},
    "precio": {"nombre": "Perdido por precio", "motivos": ["precio", "otro-flete", "tarde"],
               "pasos": [[7, "precio"], [30, "precio"], [90, "consulta-antigua"]]},
    "sin-lugar": {"nombre": "No teníamos lugar", "motivos": ["sin-lugar", "horario", "fecha"],
                  "pasos": [[30, "sin-lugar"], [90, "consulta-antigua"]]},
    "consulta-antigua": {"nombre": "Consulta antigua", "motivos": ["cancelo", "desistio", "consultaba", "otro"],
                         "pasos": [[60, "consulta-antigua"]]},
    "cliente-anterior": {"nombre": "Cliente anterior", "motivos": [], "pasos": [[90, "cliente-anterior"]]},
}
#: Lo que no se arregla volviendo a escribir.
NO_RECUPERABLES = {"zona", "vehiculo"}


@dataclass(frozen=True)
class Reglas:
    """Contra el spam. Valen para todas las secuencias."""

    max_por_persona: int = 3          # recontactos en la ventana
    ventana: timedelta = timedelta(days=180)
    intervalo: timedelta = timedelta(days=7)
    desde_hora: int = 9
    hasta_hora: int = 20

    def en_horario(self, ahora: datetime) -> bool:
        return self.desde_hora <= ahora.hour < self.hasta_hora


REGLAS = Reglas()


class Mensajero(Protocol):
    """Lo unico que la recuperacion necesita de un proveedor de mensajes.

    Hoy lo cumple el cliente de WAHA; mañana la API de WhatsApp Business o
    cualquier otro, sin tocar la logica comercial.
    """

    def enviar_texto(self, chat_id: str, texto: str) -> dict: ...


# --------------------------------------------------------------------------
# Texto de un mensaje
# --------------------------------------------------------------------------

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def variables(p: Persona, o: Oportunidad | None, hoy: date) -> dict[str, str]:
    origen, _, destino = (o.ruta or "").partition(" → ") if o else ("", "", "")
    limpio = lambda t: "" if not t or "¿?" in t else t
    nombre = (p.nombre or "").split()[0] if p.nombre else ""
    return {
        "nombre": nombre,
        "origen": limpio(origen),
        "destino": limpio(destino),
        "fecha_consulta": o.creado.strftime("%d/%m") if o else "",
        "precio_anterior": f"${int(o.cotizado):,}".replace(",", ".") if o and o.cotizado else "",
        "dias_desde_consulta": str((hoy - o.creado.date()).days) if o else "",
        "tipo_flete": o.cosas or "" if o else "",
    }


def redactar(texto: str, valores: dict[str, str]) -> str:
    """Completa las variables. Lo que no se sabe se saca sin dejar huecos raros:
    "Hola {{nombre}}, ..." sin nombre queda "Hola, ..."."""
    salida = _VAR.sub(lambda m: valores.get(m.group(1), ""), texto)
    salida = re.sub(r"[ \t]+([,.;:!?])", r"\1", salida)
    return re.sub(r"[ \t]{2,}", " ", salida).strip()


# --------------------------------------------------------------------------
# La cola
# --------------------------------------------------------------------------

PENDIENTE, PROGRAMADO, PREPARADO, ENVIADO, RESPONDIO = (
    "Pendiente", "Programado", "Mensaje preparado", "Enviado", "Respondió")
RECUPERADO, NO_INTERESADO, NO_CONTACTAR, CADUCADO = "Recuperado", "No interesado", "No contactar", "Caducado"
#: Las que piden hacer algo hoy, en este orden.
ACCIONABLES = (RESPONDIO, PREPARADO, PENDIENTE)


@dataclass
class Item:
    persona: Persona
    oportunidad: Oportunidad | None
    secuencia: str
    estado: str
    #: Desde cuando se cuenta (la perdida o el ultimo flete).
    base: datetime
    #: Proximo paso: cuando toca y con que plantilla. None si no hay mas.
    debido: date | None = None
    plantilla: str | None = None
    paso: int | None = None
    pasos: int = 0
    intentos: int = 0
    ultimo_enviado: object | None = None   # Recontacto
    preparado: object | None = None        # Recontacto
    puntaje: int = 0
    motivos: list[tuple[str, str]] = field(default_factory=list)

    @property
    def motivo(self) -> str:
        if self.secuencia == "cliente-anterior":
            return "Cliente anterior"
        o = self.oportunidad
        return o.perdida.etiqueta if o and o.perdida else "—"


def _secuencia_de(o: Oportunidad, secuencias: dict) -> str | None:
    if o.perdida is None or o.perdida.codigo in NO_RECUPERABLES:
        return None
    # Una consulta que nunca llego a presupuesto, perdida por silencio: un
    # "hola" que no siguio. No se persigue.
    if not o.presupuestada and not o.perdida.decidida:
        return None
    for clave, s in secuencias.items():
        if o.perdida.codigo in s.get("motivos", []):
            return clave
    return None


def _recuperable(p: Persona, secuencias: dict) -> tuple[Oportunidad | None, str | None, datetime | None]:
    """La oportunidad por la que tiene sentido volver a escribir, y desde cuando."""
    ultima = p.oportunidades[-1] if p.oportunidades else None
    if ultima is None:
        return None, None, None
    if ultima.perdida_ok:
        clave = _secuencia_de(ultima, secuencias)
        if clave:
            return ultima, clave, ultima.perdida.at or ultima.creado
    if ultima.hecha and p.ultimo_flete:
        return ultima, "cliente-anterior", p.ultimo_flete
    return None, None, None


def primer_recontacto(p: Persona) -> datetime | None:
    salidos = [r.decidido for r in p.recontactos if r.salio and r.decidido]
    return min(salidos) if salidos else None


def recuperado_por_recontacto(p: Persona) -> bool:
    """Perdio una oportunidad, le volviste a escribir y despues contrato."""
    primero = primer_recontacto(p)
    if primero is None:
        return False
    perdio_antes = any(o.perdida_ok and o.creado < primero for o in p.oportunidades)
    contrato_despues = any(o.hecha and o.creado > primero for o in p.oportunidades)
    return perdio_antes and contrato_despues


#: Cuanto se lo muestra como recuperado antes de que entre a "cliente anterior".
RECUPERADO_DIAS = 90


def puntaje(p: Persona, item: Item, hoy: date) -> tuple[int, list[tuple[str, str]]]:
    """Recovery score: que tan probable y que tan valioso es recuperarla. 0 a 100, con sus porques."""
    v, m = 40, []

    def sumar(delta: int, texto: str) -> None:
        nonlocal v
        v += delta
        m.append(("+" if delta > 0 else "−", texto))

    if p.completados:
        sumar(25, "cliente anterior")
    if p.completados >= 2:
        sumar(10, f"ya hizo {p.completados} fletes")
    o = item.oportunidad
    if o and o.perdida:
        codigo = o.perdida.codigo
        if codigo in ("sin-lugar", "horario", "fecha", "tarde"):
            sumar(20, f"se perdió por {o.perdida.etiqueta.lower()}: lo quería hacer")
        elif codigo == "consultaba":
            sumar(-15, "solo consultaba precios")
        elif codigo == "otro-flete":
            sumar(-5, "consiguió otro fletero")
        if o.alcanzo.value in ("confirmado",):
            sumar(10, "había confirmado")
        elif o.presupuestada:
            sumar(8, "llegó a presupuesto")
    dias = (hoy - item.base.date()).days
    if dias <= 30:
        sumar(10, f"hace {dias} días")
    elif dias > 180:
        sumar(-15, f"hace {dias} días")
    if p.mensajes_suyos >= 5:
        sumar(5, "conversó bastante")
    sin_respuesta = item.intentos if item.estado != RESPONDIO else 0
    if sin_respuesta:
        sumar(-10 * sin_respuesta, f"{sin_respuesta} {'mensaje' if sin_respuesta == 1 else 'mensajes'} sin respuesta")
    if not p.completados and p.perdidos >= 3:
        sumar(-20, f"{p.perdidos} consultas sin contratar")
    rep = reputacion(p)
    if rep.valor < 50:
        sumar(-30, f"reputación {rep.nivel.lower()}")
    return max(0, min(100, v)), m


def item_de(p: Persona, hoy: datetime, reglas: Reglas = REGLAS, secuencias: dict | None = None) -> Item | None:
    """Donde esta esta persona en su recuperacion, o None si no corresponde."""
    secuencias = secuencias or SECUENCIAS
    if p.fuera:
        return None
    if recuperado_por_recontacto(p) and p.ultimo_flete and (hoy - p.ultimo_flete).days < RECUPERADO_DIAS:
        hecha = next(o for o in reversed(p.oportunidades) if o.hecha)
        salidos = [r for r in p.recontactos if r.salio and r.decidido]
        item = Item(p, hecha, "recuperado", RECUPERADO, primer_recontacto(p), intentos=len(salidos),
                    ultimo_enviado=max(salidos, key=lambda r: r.decidido))
        item.puntaje, item.motivos = puntaje(p, item, hoy.date())
        return item
    o, clave, base = _recuperable(p, secuencias)
    if clave is None:
        return None
    pasos = secuencias[clave]["pasos"]
    enviados = [r for r in p.recontactos if r.salio and r.decidido and r.decidido >= base]
    preparado = next((r for r in p.recontactos if r.estado == "preparado"), None)
    ultimo = max(enviados, key=lambda r: r.decidido) if enviados else None
    item = Item(p, o, clave, PENDIENTE, base, pasos=len(pasos), intentos=len(enviados), ultimo_enviado=ultimo,
                preparado=preparado)

    def cerrar(estado: str) -> Item:
        item.estado = estado
        item.puntaje, item.motivos = puntaje(p, item, hoy.date())
        return item

    if p.no_contactar:
        return cerrar(NO_CONTACTAR)
    if p.no_interesado:
        return cerrar(NO_INTERESADO)
    # Contrato despues de la perdida: ya esta recuperado (o era cliente y volvio).
    if any(x.hecha and x.creado > base for x in p.oportunidades) or p._ultimo("recuperado"):
        return cerrar(RECUPERADO)
    # Contesto despues del ultimo mensaje: la charla sigue por la bandeja.
    if ultimo and p.ultimo_suyo and p.ultimo_suyo > ultimo.decidido:
        return cerrar(RESPONDIO)
    if preparado:
        return cerrar(PREPARADO)
    k = len(enviados)
    if k >= len(pasos):
        return cerrar(CADUCADO)
    # Contra el spam: tope en la ventana e intervalo minimo con el anterior.
    recientes = [r for r in p.recontactos if r.salio and r.decidido and r.decidido >= hoy - reglas.ventana]
    if len(recientes) >= reglas.max_por_persona:
        return cerrar(CADUCADO)
    dias, plantilla = pasos[k]
    debido = (base + timedelta(days=dias)).date()
    if ultimo:
        debido = max(debido, (ultimo.decidido + reglas.intervalo).date())
    if p.programado:
        debido = max(debido, p.programado) if p.programado > hoy.date() else debido
    item.debido, item.plantilla, item.paso = debido, plantilla, k + 1
    if ultimo and debido > hoy.date():
        return cerrar(ENVIADO)
    return cerrar(PENDIENTE if debido <= hoy.date() else PROGRAMADO)


def cola(gente, hoy: datetime, reglas: Reglas = REGLAS, secuencias: dict | None = None) -> list[Item]:
    """Las personas recuperables, primero lo que pide hacer algo hoy y por puntaje."""
    items = [i for p in gente if (i := item_de(p, hoy, reglas, secuencias)) is not None]
    orden = {e: n for n, e in enumerate(ACCIONABLES)}
    return sorted(items, key=lambda i: (orden.get(i.estado, 9), -i.puntaje, i.debido or date.max))


# --------------------------------------------------------------------------
# Numeros de la recuperacion
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Resumen:
    perdidos_mes: int
    recuperables: int
    pendientes: int
    preparados: int
    enviados: int
    respondieron: int
    recuperados: int
    facturado_recuperado: Decimal
    motivos: list[tuple[str, int]]

    @property
    def tasa(self) -> float | None:
        return self.recuperados / self.enviados if self.enviados else None


def resumen(gente, items: list[Item], hoy: datetime) -> Resumen:
    """Los numeros salen de la historia de cada persona, no del estado de la cola:
    alguien recuperado hace un año sigue contando como recuperado."""
    from collections import Counter

    gente = list(gente)
    mes = hoy.date().replace(day=1)
    perdidas = [o for p in gente for o in p.oportunidades if o.perdida_ok and o.perdida]
    del_mes = [o for o in perdidas if (o.perdida.at or o.creado).date() >= mes]
    contactados = [p for p in gente if primer_recontacto(p) is not None]
    recuperados = [p for p in contactados if recuperado_por_recontacto(p)]
    respondieron = [p for p in contactados
                    if p in recuperados or (p.ultimo_suyo and p.ultimo_suyo > primer_recontacto(p))]
    facturado = Decimal(0)
    for p in recuperados:
        primero = primer_recontacto(p)
        facturado += sum(((o.cobrado or o.cotizado or Decimal(0)) for o in p.oportunidades
                          if o.hecha and o.creado > primero), Decimal(0))
    return Resumen(
        perdidos_mes=len(del_mes),
        recuperables=sum(1 for i in items if i.estado in (PENDIENTE, PROGRAMADO, PREPARADO, ENVIADO, RESPONDIO)),
        pendientes=sum(1 for i in items if i.estado == PENDIENTE),
        preparados=sum(1 for i in items if i.estado == PREPARADO),
        enviados=len(contactados),
        respondieron=len(respondieron),
        recuperados=len(recuperados),
        facturado_recuperado=facturado,
        # "No respondio" anotado a mano y "Dejo de responder" por silencio son lo mismo.
        motivos=Counter("Dejó de responder" if o.perdida.codigo == "no-respondio" else o.perdida.etiqueta
                        for o in perdidas).most_common(),
    )


# --------------------------------------------------------------------------
# Tablero: lo que hace falta para responder las preguntas del negocio
# --------------------------------------------------------------------------

#: Localidades para agrupar por zona: la primera que nombre el origen.
ZONAS = ("Ostende", "Pinamar", "Villa Gesell", "Mar Azul", "San Clemente", "Santa Clara", "Mar de Cobo",
         "Miramar", "Batán", "Camet", "Mar Chiquita", "Sierra de los Padres", "Chapadmalal")


def zona_de(ruta: str | None) -> str:
    import unicodedata

    plano_ = lambda t: "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                               if unicodedata.category(c) != "Mn")
    origen = plano_((ruta or "").split("→")[0])
    if not origen.strip() or "¿?" in origen:
        return "Sin dato"
    for z in ZONAS:
        if plano_(z) in origen:
            return z
    # Una calle sin ciudad es de Mar del Plata: es lo que resuelve el mapa
    # para la zona de trabajo.
    return "Mar del Plata"


@dataclass(frozen=True)
class FilaGrupo:
    nombre: str
    consultas: int
    presupuestos: int
    hechos: int
    perdidos_precio: int

    @property
    def conversion(self) -> float | None:
        return self.hechos / self.consultas if self.consultas else None


@dataclass(frozen=True)
class Tablero:
    resumen: Resumen
    embudo: list[tuple[str, int]]
    canales: list[FilaGrupo]
    zonas: list[FilaGrupo]
    rechazo_precio: float | None      # de los perdidos con motivo, cuantos por precio
    clientes: int                     # personas con al menos un flete hecho
    vuelven: int                      # con dos o mas
    costo_por_cliente: Decimal | None  # publicidad pagada / clientes que llegaron por anuncio


def tablero(gente, items: list[Item], hoy: datetime, publicidad_pagada: Decimal | None = None) -> Tablero:
    """Recuperacion y origen de los clientes, en numeros. Ver docs/recuperacion.md."""
    gente = list(gente)
    r = resumen(gente, items, hoy)
    ops = [o for p in gente for o in p.oportunidades]
    # Por personas, y cada paso adentro del anterior: quien no perdio nada no
    # puede estar en "recuperables", ni quien no fue contactado en "respondieron".
    por_persona = {i.persona.chat_id: i for i in items}
    presupuestaron = [p for p in gente if any(o.presupuestada for o in p.oportunidades)]
    perdieron = [p for p in presupuestaron if any(o.presupuestada and o.perdida_ok for o in p.oportunidades)]
    activos = (PENDIENTE, PROGRAMADO, PREPARADO, ENVIADO, RESPONDIO, RECUPERADO)
    recuperables = [p for p in perdieron if primer_recontacto(p) is not None
                    or (p.chat_id in por_persona and por_persona[p.chat_id].estado in activos)]
    contactados = [p for p in recuperables if primer_recontacto(p) is not None]
    respondieron = [p for p in contactados if recuperado_por_recontacto(p)
                    or (p.ultimo_suyo and p.ultimo_suyo > primer_recontacto(p))]
    contrataron = [p for p in respondieron if recuperado_por_recontacto(p)]
    embudo = [
        ("Presupuestaron", len(presupuestaron)),
        ("Perdieron uno", len(perdieron)),
        ("Recuperables", len(recuperables)),
        ("Contactados", len(contactados)),
        ("Respondieron", len(respondieron)),
        ("Contrataron", len(contrataron)),
    ]

    def agrupar(clave) -> list[FilaGrupo]:
        grupos: dict[str, list] = {}
        for p in gente:
            for o in p.oportunidades:
                grupos.setdefault(clave(p, o), []).append(o)
        filas = [FilaGrupo(n, len(os_), sum(1 for o in os_ if o.presupuestada), sum(1 for o in os_ if o.hecha),
                           sum(1 for o in os_ if o.perdida and o.perdida.codigo == "precio"))
                 for n, os_ in grupos.items()]
        return sorted(filas, key=lambda f: -f.consultas)

    from estiba.economia.registro import CANALES

    nombre_canal = dict(CANALES)
    canales = agrupar(lambda p, o: nombre_canal.get("meta" if o.por_anuncio else p.origen, p.origen))
    zonas = agrupar(lambda p, o: zona_de(o.ruta))
    con_motivo = [o for o in ops if o.perdida and o.perdida.decidida]
    precio = sum(1 for o in con_motivo if o.perdida.codigo == "precio")
    clientes = [p for p in gente if p.completados]
    por_anuncio = [p for p in clientes if any(o.por_anuncio and o.hecha for o in p.oportunidades)]
    cac = (publicidad_pagada / len(por_anuncio)) if publicidad_pagada and por_anuncio else None
    return Tablero(r, embudo, canales, zonas, precio / len(con_motivo) if con_motivo else None,
                   len(clientes), sum(1 for p in clientes if p.completados >= 2), cac)
