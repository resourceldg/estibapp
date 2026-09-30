"""El precio de un flete, a partir de lo que cuesta y de la meta del mes.

La tarifa fija de antes sumaba montos por barrio y por mueble, pensados para
otra ciudad. Esta cuenta lo que ese flete en particular le cuesta al
fletero y le suma lo que tiene que dejar para llegar a la meta:

- La camioneta: los km de verdad. Sale de la base, va al origen, al
  destino y vuelve. Desde Santa Clara del Mar eso pesa: un flete corto en
  Mar del Plata igual son 40 km de ida y vuelta.
- El trabajo: las horas que lleva (manejar, cargar, subir pisos) por lo que
  tiene que valer cada hora para cubrir los fijos y llegar a la meta.
- Conseguir el cliente: la publicidad promedio de un flete hecho.
- Los ayudantes, a lo que se cobran.

Cada linea del desglose dice de donde sale. Lo que se supuso (una direccion
que el mapa no encontro, una cosa de tamano desconocido) queda escrito.
"""

from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from estiba.core.models import FreightJob
from estiba.finanzas.costos import Costos
from estiba.jobs.pricing import CATEGORIAS, Presupuesto
from estiba.mapa.caracteristicas import ciudad_del_telefono
from estiba.mapa.ubicar import CALLE, Mapa, Ubicacion, km_entre
from estiba.extraction.reglas import leer_item

#: Horas de mas por cada cosa, segun su tamano. Una heladera se ata, se
#: sube y se baja con cuidado; una caja se apila.
HORAS_POR = {
    "grande": Decimal("0.25"), "juego_comedor": Decimal("0.4"), "mediano": Decimal("0.1"),
    "chico": Decimal("0.05"), "caja": Decimal("0.02"),
}
#: Horas de mas por cada piso por escalera, con lo que haya que subir.
HORAS_POR_PISO = Decimal("0.25")

#: Donde sale la C10 si no se configuro otra cosa: Sebastian Gaboto 423,
#: Santa Elena, Santa Clara del Mar.
BASE_DEFAULT = {"texto": "Sebastián Gaboto 423, Santa Clara del Mar", "lat": -37.866678, "lon": -57.518770}


class PrecioPorObjetivos:
    """Un PricingEngine: misma interfaz, `estimate(job) -> Presupuesto`."""

    def __init__(self, mapa: Path | str | None = None) -> None:
        self.ruta_mapa = Path(mapa) if mapa else None
        self.costos = Costos()
        self.base = BASE_DEFAULT
        self._mapa: Mapa | None = None
        self._conn_mapa: sqlite3.Connection | None = None

    def preparar(self, conn: sqlite3.Connection) -> None:
        """Lee costos y base antes de cada sincronizacion: se editan en la app."""
        from estiba.storage.repositories import SettingsRepository

        ajustes = SettingsRepository(conn)
        self.costos = Costos.desde_dict(ajustes.json("costos"))
        self.base = ajustes.json("base") or BASE_DEFAULT
        mapa = self.mapa
        if mapa is not None:
            mapa.base = self.punto_base

    @property
    def punto_base(self) -> tuple[float, float]:
        return float(self.base["lat"]), float(self.base["lon"])

    @property
    def mapa(self) -> Mapa | None:
        """El mapa se abre una vez: cargar los nombres de la provincia tarda."""
        if self._mapa is None and self.ruta_mapa and self.ruta_mapa.is_file():
            self._conn_mapa = sqlite3.connect(self.ruta_mapa, check_same_thread=False)
            self._conn_mapa.row_factory = sqlite3.Row
            self._mapa = Mapa(self._conn_mapa, self.punto_base)
        return self._mapa

    # ------------------------------------------------------------------

    def ubicar(self, texto: str, chat_id: str) -> Ubicacion | None:
        mapa = self.mapa
        return mapa.ubicar(texto, ciudad_del_telefono(chat_id)) if mapa else None

    def estimate(self, job: FreightJob) -> Presupuesto | None:
        """None solo si no se sabe nada del flete. Con algo, se estima y se
        dice lo que falta: "¿cuanto me sale?" merece un numero aunque sea
        aproximado, y el fletero ve que se supuso antes de mandarlo."""
        if job.origin is None and job.destination is None and job.items is None:
            return None
        c = self.costos
        p = Presupuesto(total=Decimal(0))
        if job.origin is None or job.destination is None:
            falta = " ni ".join(x for x, s in (("de dónde", job.origin), ("a dónde", job.destination)) if s is None)
            p.sin_contar.append(f"no se sabe {falta}: la distancia es tu promedio")

        # La camioneta: el recorrido entero, no solo de A a B.
        km, recorrido, paradas = self._km(job, p)
        camioneta = c.costo_km * km
        p.desglose.append((f"Camioneta: {_num(km)} km ({recorrido})", camioneta))

        # El trabajo: manejar y cargar, a lo que vale la hora para la meta.
        manejo = km / c.velocidad_kmh if c.velocidad_kmh > 0 else Decimal(0)
        carga = c.horas_carga + self._horas_de_carga(job, p)
        horas = manejo + carga
        trabajo = Decimal(0)
        if (valor := c.valor_hora) is not None:
            trabajo = horas * valor
            p.desglose.append((
                f"Tu trabajo: {_num(horas, 1)} h (manejo {_num(manejo, 1)} + carga {_num(carga, 1)}) "
                f"a {_pesos(valor)} la hora",
                trabajo,
            ))

        pub = c.publicidad_por_flete or Decimal(0)
        if pub:
            p.desglose.append(("Conseguir el cliente: publicidad promedio por flete", pub))

        ayudantes = Decimal(0)
        if job.helpers is not None and job.helpers.value:
            if job.helpers.confidence < 0.8:
                p.sin_contar.append(f"ayudantes: '{job.helpers.evidence.quote}' no es seguro")
            else:
                n = job.helpers.value
                ayudantes = c.ayudante * n
                p.desglose.append((f"{n} {'ayudante' if n == 1 else 'ayudantes'}", ayudantes))

        # El minimo: lo mismo, pero la hora solo paga los fijos, sin la meta.
        # Debajo de esto el flete pierde plata; entre el minimo y el sugerido
        # se gana, pero menos de lo que pide la meta.
        horas_mes = c.fletes_por_mes_max * c.horas_flete
        fijos_hora = c.fijos_mes / horas_mes if horas_mes > 0 else Decimal(0)
        minimo = camioneta + horas * fijos_hora + pub + ayudantes
        p.detalle = {
            "km": km, "horas": horas, "manejo": manejo, "carga": carga,
            "paradas": paradas,
            "partes": [("Camioneta", camioneta), ("Tu trabajo", trabajo),
                       ("Publicidad", pub), ("Ayudantes", ayudantes)],
            "minimo": _mil(minimo),
            "valor_hora": valor,
        }

        suma = sum((m for _, m in p.desglose), Decimal(0))
        p.total = (suma / 1000).quantize(Decimal(1), ROUND_HALF_UP) * 1000
        p.desglose = [(concepto, monto.quantize(Decimal(1), ROUND_HALF_UP)) for concepto, monto in p.desglose]
        return p

    def _km(self, job: FreightJob, p: Presupuesto) -> tuple[Decimal, str]:
        c = self.costos
        chat = job.conversation_id
        if job.origin is None or job.destination is None:
            return c.km_por_flete, "promedio", []
        desde = self.ubicar(job.origin.value, chat)
        hasta = self.ubicar(job.destination.value, chat)
        faltan = [t for t, u in ((job.origin.value, desde), (job.destination.value, hasta)) if u is None]
        if faltan:
            motivo = "no está el mapa de calles" if self.mapa is None else \
                f"no encontré {' ni '.join(repr(f) for f in faltan)} en el mapa"
            p.supuestos.append(f"Distancia: {motivo}; usé tu promedio de {_num(c.km_por_flete)} km.")
            return c.km_por_flete, "promedio", []
        for texto, u in ((job.origin.value, desde), (job.destination.value, hasta)):
            if u.precision == CALLE:
                p.supuestos.append(f"'{texto}' no tiene altura: tomé {u.como}.")
        base = self.punto_base
        linea_recta = km_entre(base, desde.punto) + km_entre(desde.punto, hasta.punto) + km_entre(hasta.punto, base)
        km = (Decimal(str(linea_recta)) * c.factor_calles).quantize(Decimal(1), ROUND_HALF_UP)
        paradas = [
            ("Casa", True),
            (desde.como.split(",")[0], desde.precision != CALLE),
            (hasta.como.split(",")[0], hasta.precision != CALLE),
            ("Casa", True),
        ]
        return km, f"de casa a {desde.como.split(',')[0]}, a {hasta.como.split(',')[0]} y vuelta", paradas

    def _horas_de_carga(self, job: FreightJob, p: Presupuesto) -> Decimal:
        horas = Decimal(0)
        grandes = 0
        if job.items is None:
            p.supuestos.append("No dijo qué lleva: conté una carga mediana.")
            return HORAS_POR["mediano"]
        for texto in job.items.value:
            cosa = leer_item(texto)
            if cosa is None or cosa.nombre not in CATEGORIAS:
                p.supuestos.append(f"'{texto}': no sé qué tamaño tiene, lo conté como mediano.")
                horas += HORAS_POR["mediano"]
                continue
            cat = CATEGORIAS[cosa.nombre]
            cantidad = cosa.cantidad or 1
            horas += HORAS_POR[cat] * cantidad
            grandes += cantidad if cat in ("grande", "juego_comedor") else 0
        if job.floors_no_lift is None:
            p.sin_contar.append("pisos por escalera: no se sabe si hay ascensor")
        elif job.floors_no_lift.value:
            # Cada piso cuesta por viaje: con mas cosas grandes, mas viajes.
            horas += HORAS_POR_PISO * job.floors_no_lift.value * max(1, grandes)
        return horas


def _mil(d: Decimal) -> Decimal:
    return (d / 1000).quantize(Decimal(1), ROUND_HALF_UP) * 1000


def _num(d: Decimal, decimales: int = 0) -> str:
    return f"{d:.{decimales}f}".replace(".", ",")


def _pesos(d: Decimal) -> str:
    return f"${int(d.quantize(Decimal(1), ROUND_HALF_UP)):,}".replace(",", ".")
