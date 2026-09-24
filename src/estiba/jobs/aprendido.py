"""Lo que Estiba aprende de lo que cobra el fletero.

La tarifa de fabrica no sirve para todos los negocios: supone geografia,
precios y costumbres que pueden no ser las de este. En vez de adivinarlas,
Estiba mira lo que se cotizo antes y propone a partir de eso.

Tres reglas para que la sugerencia sea honesta:

- Con menos de `MINIMO` trabajos parecidos no se sugiere nada. Un promedio
  de dos numeros no es un precio, es una coincidencia.
- Se usa la mediana, no el promedio: un flete carisimo o regalado no
  arrastra a todos los demas.
- Siempre se dice de donde sale. "Por trabajos parecidos cobraste X" se
  puede discutir; un numero solo, no.

No hay modelo entrenado aca: son los numeros del fletero, ordenados. Eso
alcanza hasta que haya suficiente historia como para algo mas.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal

from estiba.core.models import FreightJob

#: Menos que esto no alcanza para sugerir: seria inventar con cara de dato.
MINIMO = 3

#: Categorias de carga. Lo que cambia el precio no es la palabra exacta sino
#: el tipo de mudanza: no es lo mismo una heladera que una casa entera.
_CATEGORIAS = {
    "mudanza": ("mudanza", "casa", "departamento", "depto", "mudarme"),
    "electrodomestico": ("heladera", "lavarropas", "freezer", "cocina", "secarropas", "estufa"),
    "mueble": ("placard", "ropero", "sillon", "sofa", "cama", "colchon", "sommier",
               "mesa", "escritorio", "comoda", "biblioteca", "modular", "mueble"),
    "cajas": ("caja", "cajas", "bolso", "bolsos", "valija"),
}


def categoria(job: FreightJob) -> str:
    """En que familia cae esta carga. "otro" si no se reconoce."""
    if not job.items:
        return "otro"
    texto = " ".join(job.items.value).lower()
    for nombre, palabras in _CATEGORIAS.items():
        if any(p in texto for p in palabras):
            return nombre
    return "otro"


@dataclass(frozen=True)
class Sugerencia:
    valor: Decimal
    #: Cuantos trabajos la respaldan. Con pocos, hay que mirarla con pinzas.
    casos: int
    #: De donde sale, en criollo, para poder discutirla.
    porque: str
    #: El rango de lo cobrado: dice cuanto varia este tipo de trabajo.
    minimo: Decimal
    maximo: Decimal

    @property
    def firme(self) -> bool:
        """Si el rango es angosto, el numero significa algo."""
        return self.casos >= 5 and self.maximo <= self.minimo * Decimal("1.6")


def sugerir(job: FreightJob, historial: list[tuple[str, Decimal]]) -> Sugerencia | None:
    """Que cobrar, segun lo que se cobro antes.

    `historial` son pares (categoria, monto) de cotizaciones ya mandadas,
    de la mas nueva a la mas vieja. Primero se buscan las del mismo tipo de
    carga; si no alcanzan, se mira todo junto y se dice que es asi.
    """
    if not historial:
        return None
    cat = categoria(job)
    mismos = [monto for c, monto in historial if c == cat]
    if len(mismos) >= MINIMO:
        cuales, detalle = mismos, f"por {len(mismos)} trabajos de {cat}"
    elif len(historial) >= MINIMO:
        cuales = [monto for _, monto in historial]
        detalle = f"por los últimos {len(cuales)} trabajos, de cualquier tipo"
    else:
        return None
    return Sugerencia(
        valor=Decimal(int(statistics.median(cuales))),
        casos=len(cuales),
        porque=detalle,
        minimo=min(cuales),
        maximo=max(cuales),
    )
