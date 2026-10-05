"""Una persona, con todas sus oportunidades: memoria comercial.

La persona es el contacto (un chat). Cada consulta es una oportunidad
aparte (un episodio de la charla), con su propio estado y su propio motivo
si se perdio. Perder un flete no hace mala a nadie: la reputacion mira lo
que la persona hizo (pago, cumplio, cancelo), no lo que no se concreto.

Nada de esto se guarda ya calculado. Se deduce de las oportunidades, de lo
que se anoto sobre la persona (`contacto_eventos`) y de los recontactos, y
cada numero dice de donde sale. Ver docs/recuperacion.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from estiba.core.models import Stage
from estiba.crm.leads import AVANCE, Paso
from estiba.crm.perdida import Perdida

LEAD, CLIENTE, RECURRENTE = "Lead", "Cliente", "Cliente recurrente"

#: Etiquetas que se ofrecen al principio. Se pueden agregar otras.
ETIQUETAS = (
    "cliente_recurrente", "precio_sensible", "mudanzas", "mini_flete", "muebles", "larga_distancia",
    "costa_atlantica", "empresa", "particular", "buen_cliente", "requiere_ayuda_carga",
    "contactar_mas_adelante",
)

#: Lo que se puede anotar sobre una persona. Todo queda: nada se borra.
EVENTOS = {
    "no_contactar": "No contactar",
    "contactar": "Se puede volver a contactar",
    "no_interesado": "No le interesa",
    "etiqueta+": "Etiqueta",
    "etiqueta-": "Sin etiqueta",
    "nota": "Nota",
    "reputacion": "Reputación corregida",
    "pago_ok": "Pagó bien",
    "pago_problema": "Problema de pago",
    "puntual": "Puntual",
    "impuntual": "Impuntual",
    "facil": "Fácil de coordinar",
    "dificil": "Difícil de coordinar",
    "origen": "Origen",
    "programado": "Seguimiento programado",
    "recuperado": "Recuperado (contrató por otro lado)",
}


@dataclass(frozen=True)
class Oportunidad:
    job_id: str
    creado: datetime
    etapa: Stage
    alcanzo: Stage
    historia: tuple[Paso, ...]
    ruta: str | None = None
    cosas: str | None = None
    cotizado: Decimal | None = None
    cobrado: Decimal | None = None
    perdida: Perdida | None = None
    por_anuncio: bool = False

    @property
    def hecha(self) -> bool:
        return self.etapa is Stage.DONE

    @property
    def perdida_ok(self) -> bool:
        return self.etapa is Stage.LOST

    @property
    def presupuestada(self) -> bool:
        return AVANCE.index(self.alcanzo) >= AVANCE.index(Stage.QUOTED)


@dataclass(frozen=True)
class Evento:
    tipo: str
    at: datetime
    valor: str | None = None
    nota: str | None = None


@dataclass(frozen=True)
class Recontacto:
    id: int
    plantilla: str
    secuencia: str | None
    paso: int | None
    texto: str
    estado: str           # preparado | enviado | copiado | descartado
    creado: datetime
    decidido: datetime | None
    job_id: str | None = None

    @property
    def salio(self) -> bool:
        return self.estado in ("enviado", "copiado")

    @property
    def cuando_salio(self) -> datetime | None:
        return self.decidido if self.salio else None


@dataclass
class Persona:
    chat_id: str
    nombre: str | None
    telefono: str
    oportunidades: list[Oportunidad]
    eventos: list[Evento] = field(default_factory=list)
    recontactos: list[Recontacto] = field(default_factory=list)
    #: Cuando escribio ella por ultima vez, y cualquiera de los dos.
    ultimo_suyo: datetime | None = None
    ultimo_contacto: datetime | None = None
    primer_contacto: datetime | None = None
    mensajes_suyos: int = 0
    #: privado / spam: fuera del circuito comercial.
    fuera: bool = False
    #: El canal deducido (registro.CANALES): "meta", "recurrente", "otro"...
    canal_inferido: str = "otro"

    # -- conteos -----------------------------------------------------------

    @property
    def consultas(self) -> int:
        return len(self.oportunidades)

    @property
    def presupuestados(self) -> int:
        return sum(1 for o in self.oportunidades if o.presupuestada)

    @property
    def completados(self) -> int:
        return sum(1 for o in self.oportunidades if o.hecha)

    @property
    def perdidos(self) -> int:
        return sum(1 for o in self.oportunidades if o.perdida_ok)

    def _perdidas(self, *codigos: str) -> int:
        return sum(1 for o in self.oportunidades if o.perdida and o.perdida.codigo in codigos)

    @property
    def cancelaciones(self) -> int:
        return self._perdidas("cancelo", "desistio")

    @property
    def rechazos_precio(self) -> int:
        return self._perdidas("precio")

    @property
    def silencios(self) -> int:
        """Presupuestos sin respuesta: un "hola" sin cotizar no cuenta."""
        return sum(1 for o in self.oportunidades
                   if o.perdida and o.perdida.por_silencio and not o.perdida.decidida and o.presupuestada)

    @property
    def facturado(self) -> Decimal:
        return sum(((o.cobrado or o.cotizado or Decimal(0)) for o in self.oportunidades if o.hecha), Decimal(0))

    @property
    def ultimo_flete(self) -> datetime | None:
        hechos = [o.historia[-1].at for o in self.oportunidades if o.hecha]
        return max(hechos) if hechos else None

    # -- situacion ---------------------------------------------------------

    @property
    def situacion(self) -> str:
        if self.completados >= 2:
            return RECURRENTE
        return CLIENTE if self.completados else LEAD

    @property
    def recuperado(self) -> bool:
        """Perdio una oportunidad y despues contrato otra."""
        perdidas = [o.creado for o in self.oportunidades if o.perdida_ok]
        return bool(perdidas) and any(o.hecha and o.creado > min(perdidas) for o in self.oportunidades)

    # -- lo anotado (se recorre en orden; vale el ultimo) -----------------

    def _ultimo(self, *tipos: str) -> Evento | None:
        return next((e for e in reversed(self.eventos) if e.tipo in tipos), None)

    @property
    def no_contactar(self) -> bool:
        e = self._ultimo("no_contactar", "contactar")
        return e is not None and e.tipo == "no_contactar"

    @property
    def no_interesado(self) -> bool:
        e = self._ultimo("no_interesado", "contactar")
        return e is not None and e.tipo == "no_interesado"

    @property
    def etiquetas(self) -> list[str]:
        puestas: list[str] = []
        for e in self.eventos:
            if e.tipo == "etiqueta+" and e.valor and e.valor not in puestas:
                puestas.append(e.valor)
            elif e.tipo == "etiqueta-" and e.valor in puestas:
                puestas.remove(e.valor)
        # Las que se deducen solas, sin pisar lo que saco una persona.
        sacadas = {e.valor for e in self.eventos if e.tipo == "etiqueta-"}
        auto = []
        if self.completados >= 2:
            auto.append("cliente_recurrente")
        if self.rechazos_precio:
            auto.append("precio_sensible")
        if any(o.cosas and any(p in o.cosas.lower() for p in ("mudanza", "ambientes", "depto")) for o in self.oportunidades):
            auto.append("mudanzas")
        return puestas + [a for a in auto if a not in puestas and a not in sacadas]

    @property
    def notas(self) -> list[Evento]:
        return [e for e in self.eventos if e.tipo == "nota"]

    @property
    def origen(self) -> str:
        e = self._ultimo("origen")
        return e.valor if e and e.valor else self.canal_inferido

    @property
    def programado(self) -> date | None:
        """Volver a escribirle desde esta fecha (vale el ultimo)."""
        e = self._ultimo("programado")
        if e is None or not e.valor:
            return None
        try:
            return date.fromisoformat(e.valor)
        except ValueError:
            return None

    def senales(self, *tipos: str) -> int:
        return sum(1 for e in self.eventos if e.tipo in tipos)


# --------------------------------------------------------------------------
# Reputacion
# --------------------------------------------------------------------------

NIVELES = ((85, "Excelente", "⭐"), (70, "Bueno", "🟢"), (50, "Normal", "🟡"), (30, "Atención", "🟠"),
           (0, "Problemático", "🔴"))
BASE = 60


@dataclass(frozen=True)
class Puntaje:
    valor: int
    motivos: list[tuple[str, str]]   # ("+", "3 fletes completados")
    #: La cuenta automatica, si una persona la corrigio a mano.
    calculado: int | None = None
    nota_manual: str | None = None

    @property
    def nivel(self) -> str:
        return next(n for minimo, n, _ in NIVELES if self.valor >= minimo)

    @property
    def icono(self) -> str:
        return next(i for minimo, _, i in NIVELES if self.valor >= minimo)


def _sumar(motivos, valor, delta, signo_texto):
    if delta:
        motivos.append(("+" if delta > 0 else "−", signo_texto))
    return valor + delta


def reputacion(p: Persona) -> Puntaje:
    """De 0 a 100, empezando en 60. Cada regla dice por que suma o resta.

    Mide como es trabajar con la persona, no si compro: un lead que consulto
    y no contrato queda en Normal, no en Atencion.
    """
    v, m = BASE, []
    if p.completados:
        v = _sumar(m, v, min(30, 10 * p.completados),
                   f"{p.completados} {'flete completado' if p.completados == 1 else 'fletes completados'}")
    if p.completados >= 2:
        v = _sumar(m, v, 10, "cliente recurrente")
    if (ok := p.senales("pago_ok")):
        v = _sumar(m, v, min(16, 8 * ok), "pagos correctos")
    if (mal := p.senales("pago_problema")):
        v = _sumar(m, v, -25 * mal, f"{mal} {'problema' if mal == 1 else 'problemas'} de pago")
    if (pu := p.senales("puntual")):
        v = _sumar(m, v, min(10, 5 * pu), "puntual")
    if (im := p.senales("impuntual")):
        v = _sumar(m, v, -10 * im, "impuntual")
    if (fa := p.senales("facil")):
        v = _sumar(m, v, min(10, 5 * fa), "fácil de coordinar")
    if (di := p.senales("dificil")):
        v = _sumar(m, v, -10 * di, "difícil de coordinar")
    if p.cancelaciones:
        v = _sumar(m, v, -min(24, 8 * p.cancelaciones),
                   f"{p.cancelaciones} {'cancelación' if p.cancelaciones == 1 else 'cancelaciones'}")
    if p.silencios:
        v = _sumar(m, v, -min(12, 3 * p.silencios),
                   f"dejó de responder {p.silencios} {'vez' if p.silencios == 1 else 'veces'} después del presupuesto")
    if not p.completados and p.consultas > 2:
        v = _sumar(m, v, -min(10, 2 * (p.consultas - 2)), f"{p.consultas} consultas sin contratar")
    if not m:
        m.append(("·", "sin historia todavía"))
    calculado = max(0, min(100, v))
    manual = p._ultimo("reputacion")
    if manual is not None and manual.valor and manual.valor.isdigit():
        return Puntaje(max(0, min(100, int(manual.valor))), m, calculado, manual.nota)
    return Puntaje(calculado, m)


# --------------------------------------------------------------------------
# Historial cronologico
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Hito:
    at: datetime
    texto: str
    tipo: str        # consulta | presupuesto | confirmado | realizado | perdido | evento | mensaje
    job_id: str | None = None


def _pesos(v: Decimal | None) -> str:
    return f"${int(v):,}".replace(",", ".") if v else ""


def historial(p: Persona) -> list[Hito]:
    """Todo lo que paso con la persona, en orden: cada oportunidad y lo anotado."""
    hitos: list[Hito] = []
    for o in p.oportunidades:
        for paso in o.historia:
            if paso.etapa is Stage.NEW:
                hitos.append(Hito(paso.at, f"Consultó{' ' + o.ruta if o.ruta else ''}", "consulta", o.job_id))
            elif paso.etapa is Stage.QUOTED:
                hitos.append(Hito(paso.at, f"Presupuesto{' ' + _pesos(o.cotizado) if o.cotizado else ''}",
                                  "presupuesto", o.job_id))
            elif paso.etapa is Stage.CONFIRMED:
                hitos.append(Hito(paso.at, "Flete confirmado", "confirmado", o.job_id))
            elif paso.etapa is Stage.DONE:
                monto = _pesos(o.cobrado or o.cotizado)
                hitos.append(Hito(paso.at, f"Flete completado{' · ' + monto if monto else ''}", "realizado", o.job_id))
            elif paso.etapa is Stage.LOST:
                motivo = o.perdida.etiqueta if o.perdida else paso.motivo
                nota = f" · {o.perdida.nota}" if o.perdida and o.perdida.nota else ""
                hitos.append(Hito(paso.at, f"Perdido: {motivo}{nota}", "perdido", o.job_id))
    for e in p.eventos:
        nombre = EVENTOS.get(e.tipo, e.tipo)
        detalle = " ".join(x for x in (e.valor if e.tipo != "reputacion" else f"{e.valor}/100", e.nota) if x)
        hitos.append(Hito(e.at, f"{nombre}{': ' + detalle if detalle else ''}", "evento"))
    for r in p.recontactos:
        if r.salio:
            hitos.append(Hito(r.decidido, f"Mensaje de recuperación ({r.plantilla})", "mensaje", r.job_id))
    return sorted(hitos, key=lambda h: h.at)
