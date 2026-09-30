"""Lo que el fletero configura para el motor economico.

Los costos por km, los fijos y la cuota del credito ya estaban en `Costos`
(settings "costos") y ahi se quedan: una sola fuente. Aca va lo nuevo:

- `Camioneta`: el activo. Valor de mercado y de reposicion, odometro, y una
  depreciacion *opcional*: la C10 es un activo viejo y no se sabe como se
  comporta su valor, asi que no se inventa una curva lineal. Si no se carga,
  el resultado economico lo dice en vez de suponerla.
- `Credito`: la deuda, separada del activo. La cuota es caja; solo su
  parte de interes es costo economico, y solo si se sabe.
- `Disponibilidad`: capacidad *deseada*, no la tecnica. Que entren 48 fletes
  en un mes no quiere decir que se quieran hacer.
- `Fondo`: plata que se aparta cada mes. Los de conservacion (mantenimiento,
  reparaciones, reposicion) existen aunque se este pagando el credito:
  pagar capital no deja plata disponible para arreglar la camioneta.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date
from decimal import Decimal

from estiba.finanzas.costos import Costos

#: Semanas por mes, para pasar de "dias por semana" a un mes.
SEMANAS_MES = Decimal(52) / Decimal(12)


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except ArithmeticError:
        return None


@dataclass(frozen=True)
class Camioneta:
    valor_mercado: Decimal | None = None
    valor_reposicion: Decimal | None = None
    odometro: Decimal | None = None
    #: Supuesto opcional. None: no se descuenta ninguna depreciacion.
    depreciacion_mes: Decimal | None = None

    @classmethod
    def desde_dict(cls, d: dict | None) -> Camioneta:
        d = d or {}
        return cls(**{f.name: _dec(d.get(f.name)) for f in fields(cls)})


@dataclass(frozen=True)
class Credito:
    cuotas: int = 72
    #: Mes de la primera cuota. None: no se sabe cuando termina.
    primera: date | None = None
    #: Cuanto de la cuota de este mes es interes. None: no se sabe.
    interes_mes: Decimal | None = None

    @classmethod
    def desde_dict(cls, d: dict | None) -> Credito:
        d = d or {}
        primera = None
        if d.get("primera"):
            try:
                primera = date.fromisoformat(str(d["primera"]))
            except ValueError:
                primera = None
        try:
            cuotas = int(d.get("cuotas", 72))
        except (TypeError, ValueError):
            cuotas = 72
        return cls(cuotas=max(0, cuotas), primera=primera, interes_mes=_dec(d.get("interes_mes")))

    def pagadas(self, hoy: date) -> int | None:
        if self.primera is None:
            return None
        meses = (hoy.year - self.primera.year) * 12 + hoy.month - self.primera.month + 1
        return max(0, min(self.cuotas, meses))

    def restantes(self, hoy: date) -> int | None:
        p = self.pagadas(hoy)
        return None if p is None else self.cuotas - p

    def ultima(self) -> date | None:
        if self.primera is None or self.cuotas <= 0:
            return None
        meses = self.primera.month - 1 + self.cuotas - 1
        return date(self.primera.year + meses // 12, meses % 12 + 1, 1)

    def vigente(self, mes: date) -> bool:
        """Si en ese mes se paga cuota. Sin fechas, se supone que si: es lo
        conservador (no hacer desaparecer una obligacion que se desconoce)."""
        if self.primera is None:
            return True
        ultima = self.ultima()
        inicio = self.primera.replace(day=1)
        return inicio <= mes.replace(day=1) and (ultima is None or mes.replace(day=1) <= ultima)


@dataclass(frozen=True)
class Disponibilidad:
    dias_semana: Decimal = Decimal(4)
    fletes_dia_comodo: Decimal = Decimal(2)
    fletes_dia_pico: Decimal = Decimal(3)
    horas_dia: Decimal = Decimal(8)
    #: Tope explicito de horas por mes. None: sale de lo de arriba.
    tope_horas_mes: Decimal | None = None
    #: Coordinar por WhatsApp y cobrar: tiempo real que no es manejar ni cargar.
    h_coordinacion: Decimal = Decimal("0.25")
    h_cobro: Decimal = Decimal("0.1")
    dias_no: tuple[str, ...] = ()
    franjas: tuple[str, ...] = ()
    #: Cuanto vale para vos una hora libre. Opcional: sin esto Estiba no
    #: decide por vos cuando dejar de agregar trabajo.
    valor_hora_libre: Decimal | None = None

    @classmethod
    def desde_dict(cls, d: dict | None) -> Disponibilidad:
        d = d or {}
        base = cls()
        cambios = {}
        for f in fields(cls):
            if f.name not in d:
                continue
            if f.name in ("dias_no", "franjas"):
                cambios[f.name] = tuple(str(x) for x in (d[f.name] or []))
            elif f.name in ("tope_horas_mes", "valor_hora_libre"):
                cambios[f.name] = _dec(d[f.name])
            elif (v := _dec(d[f.name])) is not None and v >= 0:
                cambios[f.name] = v
        return replace(base, **cambios)

    def fletes_mes(self, pico: bool = False) -> Decimal:
        por_dia = self.fletes_dia_pico if pico else self.fletes_dia_comodo
        return self.dias_semana * SEMANAS_MES * por_dia

    def horas_mes(self, horas_flete: Decimal) -> Decimal:
        """Las horas que se *quieren* trabajar: el ritmo comodo, nunca el pico."""
        comodas = self.fletes_mes() * horas_flete
        jornada = self.dias_semana * SEMANAS_MES * self.horas_dia
        horas = min(comodas, jornada)
        if self.tope_horas_mes is not None:
            horas = min(horas, self.tope_horas_mes)
        return horas


CONSERVACION = "conservacion"
PERSONAL = "personal"


@dataclass(frozen=True)
class Fondo:
    clave: str
    nombre: str
    tipo: str  # conservacion | personal
    aporte_mes: Decimal = Decimal(0)
    objetivo: Decimal | None = None


FONDOS_INICIALES = (
    Fondo("mantenimiento", "Mantenimiento y reparaciones", CONSERVACION),
    Fondo("reposicion", "Conservación y reposición de la camioneta", CONSERVACION),
    Fondo("viajes", "Viajes", PERSONAL),
)


def fondos_desde(lista: list | None) -> tuple[Fondo, ...]:
    if not lista:
        return FONDOS_INICIALES
    salida = []
    for d in lista:
        tipo = d.get("tipo") if d.get("tipo") in (CONSERVACION, PERSONAL) else CONSERVACION
        salida.append(Fondo(str(d.get("clave") or d.get("nombre")), str(d.get("nombre") or d.get("clave")),
                            tipo, _dec(d.get("aporte_mes")) or Decimal(0), _dec(d.get("objetivo"))))
    return tuple(salida)


def a_json(obj) -> dict | list:
    """Para guardar: Decimal y date como texto."""
    if isinstance(obj, tuple) and obj and isinstance(obj[0], Fondo):
        return [a_json(f) for f in obj]
    d = asdict(obj)
    for k, v in d.items():
        if isinstance(v, (Decimal, date)):
            d[k] = str(v) if isinstance(v, Decimal) else v.isoformat()
        elif isinstance(v, tuple):
            d[k] = list(v)
    return d


@dataclass(frozen=True)
class Economia:
    """Todo lo que el motor necesita saber de la configuracion."""

    costos: Costos
    precio_base: Decimal
    camioneta: Camioneta = field(default_factory=Camioneta)
    credito: Credito = field(default_factory=Credito)
    disponibilidad: Disponibilidad = field(default_factory=Disponibilidad)
    fondos: tuple[Fondo, ...] = FONDOS_INICIALES
    #: Los campos de `Costos` que el fletero guardo. Los demas son supuestos.
    declarados: frozenset[str] = frozenset()
