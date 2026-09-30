"""Lo que paso en un flete, contra lo que Estiba estimo.

La unica forma de aprender es cerrar el ciclo estimacion -> realidad. Para
que eso no cueste trabajo, el cierre arranca con la estimacion puesta: si
fue asi, un toque; si fue distinto, se corrige solo lo que cambio.

Solo lo confirmado o corregido cuenta como medido. Lo cerrado sin mirar
(desde la agenda compacta) sigue siendo una estimacion.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from estiba.economia.modelo import CERO

CANALES = (
    ("meta", "Meta (anuncio)"),
    ("recurrente", "Cliente que vuelve"),
    ("referido", "Recomendado"),
    ("organico", "Me encontró solo"),
    ("google", "Google"),
    ("marketplace", "Marketplace"),
    ("whatsapp", "WhatsApp directo"),
    ("convenio", "Empresa o convenio"),
    ("otro", "Otro"),
)
CLAVES_CANAL = {c for c, _ in CANALES}

ESTIMADO, CONFIRMADO, CORREGIDO = "estimado", "confirmado", "corregido"


def canal_inferido(recurrente: bool, por_publicidad: bool) -> str:
    """Lo unico que se sabe con seguridad: el que vuelve y el que toco un anuncio."""
    if recurrente:
        return "recurrente"
    if por_publicidad:
        return "meta"
    return "otro"


@dataclass(frozen=True)
class Registro:
    km: Decimal | None = None
    km_vacios: Decimal | None = None
    horas: Decimal | None = None
    espera_h: Decimal = CERO
    ayudante_pagado: Decimal = CERO
    otros_gastos: Decimal = CERO
    canal: str = "otro"
    fuente: str = ESTIMADO

    @property
    def medido(self) -> bool:
        return self.fuente in (CONFIRMADO, CORREGIDO)

    @property
    def directos(self) -> Decimal:
        return self.ayudante_pagado + self.otros_gastos


def desde_evaluacion(ev) -> Registro:
    """La estimacion de Estiba, lista para confirmar o corregir."""
    if ev is None:
        return Registro()
    return Registro(km=ev.km, km_vacios=ev.km_vacios, horas=ev.horas,
                    ayudante_pagado=sum((p.monto for p in ev.partes if p.nombre.startswith("Ayudantes")), CERO),
                    canal=canal_inferido(ev.cliente.recurrente, ev.cliente.por_publicidad))


def cerrar(estimado: Registro, *, visto: bool, cambios: dict) -> Registro:
    """El registro final. `cambios` trae solo los campos que el fletero toco."""
    limpios = {k: v for k, v in cambios.items() if v is not None}
    if "canal" in limpios and limpios["canal"] not in CLAVES_CANAL:
        limpios.pop("canal")
    # Cambiar el canal no corrige una medicion: sigue siendo "asi fue".
    corrigio_numeros = any(k != "canal" and limpios[k] != getattr(estimado, k) for k in limpios)
    fuente = CORREGIDO if corrigio_numeros else (CONFIRMADO if visto else ESTIMADO)
    return replace(estimado, **limpios, fuente=fuente)


@dataclass(frozen=True)
class Desvio:
    km: Decimal | None
    horas: Decimal | None


def desvio(estimado: Registro, real: Registro) -> Desvio:
    """Cuanto se equivoco la estimacion (real - estimado), para aprender."""
    def d(a, b):
        return b - a if a is not None and b is not None else None
    return Desvio(d(estimado.km, real.km), d(estimado.horas, real.horas))
