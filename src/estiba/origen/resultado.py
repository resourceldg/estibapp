"""Que paso con lo que trajo cada pieza: consultas, fletes, plata y costo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from estiba.core.models import Stage
from estiba.crm.leads import AVANCE
from estiba.origen.atribucion import Atribucion
from estiba.origen.piezas import Pieza

CERO = Decimal(0)
SIN_ATRIBUIR = "Sin atribuir"


@dataclass(frozen=True)
class Fila:
    nombre: str
    medio: str            # pago | organico | "" (sin atribuir)
    canal: str
    consultas: int
    cotizados: int
    agendados: int
    realizados: int
    facturado: Decimal
    pagado: Decimal       # con impuestos; cero si es organico
    conversaciones_meta: int | None

    @property
    def costo_por_consulta(self) -> Decimal | None:
        return self.pagado / self.consultas if self.pagado and self.consultas else None

    @property
    def costo_por_flete(self) -> Decimal | None:
        return self.pagado / self.realizados if self.pagado and self.realizados else None

    @property
    def cierre(self) -> float | None:
        return self.realizados / self.consultas if self.consultas else None

    @property
    def retorno(self) -> Decimal | None:
        """Pesos facturados por cada peso pagado."""
        return self.facturado / self.pagado if self.pagado else None


def _llego(o, etapa: Stage) -> bool:
    alcanzo = getattr(o, "alcanzo", None)
    return alcanzo in AVANCE and AVANCE.index(alcanzo) >= AVANCE.index(etapa)


def por_pieza(oportunidades, piezas: list[Pieza], atribuciones: dict[str, Atribucion],
              gasto_por_anuncio: dict[str, tuple[Decimal, int]], tasa_impuestos: Decimal,
              desde: date | None = None, hasta: date | None = None) -> list[Fila]:
    """Una fila por pieza con movimiento y una de "Sin atribuir" al final.

    `oportunidades`: las de personas (job_id, creado, alcanzo, hecha, cobrado).
    `gasto_por_anuncio`: el de la pauta ya filtrado al periodo.
    `tasa_impuestos` en %: la misma que usa Numeros para la publicidad.
    """
    dentro = [o for o in oportunidades
              if (desde is None or o.creado.date() >= desde) and (hasta is None or o.creado.date() <= hasta)]
    de_pieza: dict[int | None, list] = {}
    for o in dentro:
        a = atribuciones.get(o.job_id)
        de_pieza.setdefault(a.pieza_id if a else None, []).append(o)

    def fila(nombre, medio, canal, ops, pagado, conv_meta) -> Fila:
        hechas = [o for o in ops if o.hecha]
        return Fila(nombre, medio, canal, len(ops),
                    sum(1 for o in ops if _llego(o, Stage.QUOTED)),
                    sum(1 for o in ops if _llego(o, Stage.CONFIRMED)),
                    len(hechas), sum((o.cobrado or CERO for o in hechas), CERO), pagado, conv_meta)

    filas = []
    for p in piezas:
        ops = de_pieza.get(p.id, [])
        gasto, conv = gasto_por_anuncio.get(p.anuncio_id, (CERO, 0)) if p.anuncio_id else (CERO, None)
        pagado = gasto * (1 + tasa_impuestos / 100) + p.costo_fijo
        if not ops and not pagado:
            continue
        filas.append(fila(p.nombre, p.medio, p.canal, ops, pagado, conv))
    filas.sort(key=lambda f: (-f.realizados, -f.consultas, f.nombre))
    sueltas = de_pieza.get(None, [])
    if sueltas:
        filas.append(fila(SIN_ATRIBUIR, "", "", sueltas, CERO, None))
    return filas
