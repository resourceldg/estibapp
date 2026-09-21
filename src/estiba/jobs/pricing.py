"""PricingEngine: un precio sugerido con su desglose.

Es una sugerencia para el fletero, nunca algo que se le manda al cliente sin
mirar. Si falta un dato que cambia el precio, se calcula sin ese concepto y
se avisa: un presupuesto que esconde lo que no sabe es peor que ninguno.

Los montos por defecto salen de las tres cotizaciones de fixtures/ y sirven
para arrancar. Los reales van en `tarifa.toml` (ver `Tarifa.desde_toml`).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from estiba.core.models import FreightJob
from estiba.extraction.reglas import leer_item, zona

TARIFA_DEFAULT = Path("tarifa.toml")

#: Categoria de cada cosa, por su nombre en singular.
CATEGORIAS = {
    "heladera": "grande", "lavarropas": "grande", "secarropas": "grande",
    "freezer": "grande", "sillon": "grande", "cama": "grande",
    "colchon": "grande", "sommier": "grande", "placard": "grande",
    "biblioteca": "grande", "juego de comedor": "juego_comedor",
    "mesa": "mediano", "escritorio": "mediano", "televisor": "mediano",
    "cajonera": "mediano", "comoda": "mediano", "rack": "mediano",
    "bicicleta": "mediano", "microondas": "chico", "silla": "chico",
    "caja": "caja",
}
#: Si se lleva un juego de comedor, la mesa y las sillas son parte del juego.
_INCLUIDO_EN_JUEGO = {"mesa", "silla"}


@dataclass(frozen=True)
class Tarifa:
    base: Decimal = Decimal(30000)
    conurbano: Decimal = Decimal(15000)  # si origen o destino es del conurbano
    grande: Decimal = Decimal(8000)
    mediano: Decimal = Decimal(3000)
    chico: Decimal = Decimal(1000)
    caja: Decimal = Decimal(700)
    juego_comedor: Decimal = Decimal(12000)
    piso_sin_ascensor: Decimal = Decimal(7000)  # por piso
    ayudante: Decimal = Decimal(10000)
    redondeo: Decimal = Decimal(1000)

    @classmethod
    def desde_toml(cls, ruta: Path | str = TARIFA_DEFAULT) -> Tarifa:
        """Lee los montos de un TOML plano. Lo que no esta queda por defecto."""
        ruta = Path(ruta)
        if not ruta.is_file():
            return cls()
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
        validos = {f.name for f in fields(cls)}
        desconocidos = set(datos) - validos
        if desconocidos:
            raise ValueError(f"{ruta}: claves desconocidas {sorted(desconocidos)}")
        return cls(**{k: Decimal(str(v)) for k, v in datos.items()})


@dataclass
class Presupuesto:
    total: Decimal
    desglose: list[tuple[str, Decimal]] = field(default_factory=list)
    #: Lo que no se pudo contar. Si no esta vacio, el total es un piso.
    sin_contar: list[str] = field(default_factory=list)

    @property
    def completo(self) -> bool:
        return not self.sin_contar


class PricingEngine:
    def __init__(self, tarifa: Tarifa | None = None) -> None:
        self.tarifa = tarifa or Tarifa()

    def estimate(self, job: FreightJob) -> Presupuesto | None:
        """None si falta lo minimo (lugares o que se lleva) para estimar algo."""
        if job.origin is None or job.destination is None or job.items is None:
            return None
        t = self.tarifa
        p = Presupuesto(total=Decimal(0))
        p.desglose.append(("base", t.base))

        zonas = {zona(job.origin.value), zona(job.destination.value)}
        if "conurbano" in zonas:
            p.desglose.append(("conurbano", t.conurbano))
        if None in zonas:
            p.sin_contar.append("zona: un lugar no esta en la lista de barrios")

        cosas = [leer_item(s) for s in job.items.value]
        hay_juego = any(c is not None and c.nombre == "juego de comedor" for c in cosas)
        for texto, cosa in zip(job.items.value, cosas):
            if cosa is None or cosa.nombre not in CATEGORIAS:
                p.sin_contar.append(f"'{texto}': no se que tamano tiene")
                continue
            if hay_juego and cosa.nombre in _INCLUIDO_EN_JUEGO:
                p.desglose.append((f"{texto} (parte del juego)", Decimal(0)))
                continue
            if cosa.cantidad is None and cosa.en_plural:
                p.sin_contar.append(f"'{texto}': cuantas?")
            cantidad = cosa.cantidad or 1
            p.desglose.append((texto, getattr(t, CATEGORIAS[cosa.nombre]) * cantidad))

        if job.floors_no_lift is None:
            p.sin_contar.append("pisos por escalera: no se sabe si hay ascensor")
        elif job.floors_no_lift.value:
            pisos = job.floors_no_lift.value
            p.desglose.append((f"{pisos} pisos por escalera", t.piso_sin_ascensor * pisos))

        if job.helpers is not None and job.helpers.value:
            if job.helpers.confidence < 0.8:
                p.sin_contar.append(f"ayudantes: '{job.helpers.evidence.quote}' no es seguro")
            else:
                n = job.helpers.value
                p.desglose.append((f"{n} ayudantes", t.ayudante * n))

        suma = sum((monto for _, monto in p.desglose), Decimal(0))
        p.total = (suma / t.redondeo).quantize(Decimal(1), ROUND_HALF_UP) * t.redondeo
        return p
