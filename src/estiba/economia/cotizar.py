"""El puente entre la cotizacion de siempre y la evaluacion economica.

`PrecioPorObjetivos` sigue sabiendo lo que sabe (km del mapa, horas de
carga por lo que se lleva, ayudantes); `pedido.evaluar` decide con el
contexto que la cotizacion no ve (si el cliente vuelve, si vino por un
anuncio, como esta el dia). `aplicar` deja el precio recomendado como el
precio de la cotizacion, con su desglose: asi el borrador para el cliente
ya lleva ese numero, listo para aprobar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from estiba.economia.config import Economia
from estiba.economia.modelo import CERO, Tipo
from estiba.economia.pedido import Evaluacion, Pedido


def pedido_desde(presupuesto, job, e: Economia, fletes_ese_dia: int = 0) -> Pedido | None:
    """None si la cotizacion no tiene km (la tarifa fija de tarifa.toml)."""
    d = presupuesto.detalle or {}
    if d.get("km") is None:
        return None
    if not d.get("paradas"):
        km_tipo = Tipo.SUPUESTO
    elif presupuesto.supuestos:
        km_tipo = Tipo.ESTIMADO
    else:
        km_tipo = Tipo.CALCULADO
    carga = Decimal(d.get("carga") or e.costos.horas_carga)
    ayudantes = dict(d.get("partes", [])).get("Ayudantes", CERO)
    return Pedido(
        km=Decimal(d["km"]),
        km_tipo=km_tipo,
        tramos=d.get("tramos"),
        horas_carga_extra=max(CERO, carga - e.costos.horas_carga),
        directos=Decimal(ayudantes),
        precio_hablado=job.quoted_price.value if job.quoted_price is not None else None,
        dia=job.when.value if job.when is not None else None,
        fletes_ese_dia=fletes_ese_dia,
        supuestos=tuple(presupuesto.supuestos),
    )


#: Los nombres que usa la barra de colores de la cotizacion.
_NOMBRE_BARRA = (("Camioneta", "Camioneta"), ("Tus horas", "Tu trabajo"),
                 ("Conseguir", "Publicidad"), ("Ayudantes", "Ayudantes"))


def aplicar(presupuesto, ev: Evaluacion) -> None:
    """El precio de la cotizacion pasa a ser el recomendado, con su desglose."""
    if ev.recomendado is None:
        presupuesto.detalle["evaluacion"] = ev
        return
    presupuesto.total = ev.recomendado
    presupuesto.desglose = [(p.nombre, p.monto.quantize(Decimal(1))) for p in ev.partes]
    # Las lineas suman exactamente el precio: lo que falta hasta el base (o el
    # redondeo a mil del recomendado) va en su propio renglon.
    resto = ev.recomendado - sum((m for _, m in presupuesto.desglose), CERO)
    if resto > 0:
        etiqueta = "Hasta el precio base" if ev.recomendado == ev.base else "Redondeo"
        presupuesto.desglose.append((etiqueta, resto))
    partes = []
    for p in ev.partes:
        nombre = next((barra for pre, barra in _NOMBRE_BARRA if p.nombre.startswith(pre)), p.nombre)
        partes.append((nombre, p.monto))
    presupuesto.detalle["partes"] = partes
    presupuesto.detalle["minimo"] = ev.minimo
    presupuesto.detalle["horas"] = ev.horas
    presupuesto.detalle["evaluacion"] = ev


def fletes_por_dia(eventos, dia: date | None, job_id: str) -> int:
    """Cuantos otros fletes activos hay ese dia en la agenda."""
    if dia is None:
        return 0
    n = 0
    for ev, cancelado in eventos:
        if cancelado or ev.job_id == job_id:
            continue
        inicio = ev.inicio.date() if hasattr(ev.inicio, "date") and callable(ev.inicio.date) else ev.inicio
        if inicio == dia:
            n += 1
    return n
