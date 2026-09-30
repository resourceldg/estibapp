"""Lo que se aprende de la historia, solo cuando la historia alcanza.

Nada de esto se activa artificialmente: cada conclusion pasa por el motor
de suficiencia y, si no alcanza, se dice que falta en vez de afirmar algo.

- Segmentos por reglas (urbano corto/medio/largo, Santa Clara, Miramar,
  interurbano, mudanza), cada uno con SUS datos.
- Rentabilidad por canal y LTV por cliente (el CAC solo lo carga el
  primer flete de un cliente nuevo que vino por publicidad).
- Ritmo sostenible: ¿rinden mas por hora las jornadas de 2 fletes o las de 3?
- Costo de oportunidad por franja: solo con 8 semanas o mas de historia.
- Encadenamiento: dos fletes del mismo dia donde el destino de uno queda
  cerca del origen del otro ahorran la vuelta y la ida vacias.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from estiba.economia.mes import FleteDelMes, margen
from estiba.economia.suficiencia import Estado, Obs, Suficiencia, media
from estiba.mapa.ubicar import km_entre

MUDANZA = ("mudanza", "casa", "departamento", "depto", "mudarme", "ambientes")
SEMANAS_PARA_OPORTUNIDAD = 8
AHORRO_MINIMO_KM = 5


def segmento(km: Decimal, origen: str | None, destino: str | None, cosas: list[str] | None) -> str:
    texto = f" {origen or ''} {destino or ''} ".lower()
    if "miramar" in texto:
        return "Miramar"
    if "santa clara" in texto:
        return "Santa Clara"
    if cosas and any(p in " ".join(cosas).lower() for p in MUDANZA):
        return "Mudanza"
    if km >= 100:
        return "Interurbano"
    if km < 35:
        return "Urbano corto"
    return "Urbano medio" if km <= 60 else "Urbano largo"


@dataclass(frozen=True)
class Grupo:
    nombre: str
    n: int
    margen_hora: Suficiencia
    margen: Suficiencia


def _grupos(fletes: list[FleteDelMes], clave, uso_km: Decimal, cliente_de: dict[str, str], hoy: date,
            falta: str) -> list[Grupo]:
    por: dict[str, list[FleteDelMes]] = {}
    for f in fletes:
        por.setdefault(clave(f), []).append(f)
    salida = []
    for nombre, fs in sorted(por.items(), key=lambda x: -len(x[1])):
        calidad = lambda f: "medido" if f.medido else "estimado"
        mh = [Obs(float(margen(f, uso_km) / f.horas), f.dia, cliente_de.get(f.job_id), calidad(f))
              for f in fs if f.horas]
        mg = [Obs(float(margen(f, uso_km)), f.dia, cliente_de.get(f.job_id), calidad(f)) for f in fs]
        salida.append(Grupo(nombre, len(fs), media(f"margen por hora ({nombre})", mh, hoy, falta=falta),
                            media(f"margen ({nombre})", mg, hoy, falta=falta)))
    return salida


def por_segmento(fletes, segmento_de: dict[str, str], uso_km, cliente_de, hoy) -> list[Grupo]:
    return _grupos(fletes, lambda f: segmento_de.get(f.job_id, "Sin datos"), uso_km, cliente_de, hoy,
                   "Más fletes de este tipo, cerrados confirmando km y horas")


def por_canal(fletes, canal_de: dict[str, str], uso_km, cliente_de, hoy) -> list[Grupo]:
    return _grupos(fletes, lambda f: canal_de.get(f.job_id, "otro"), uso_km, cliente_de, hoy,
                   "Anotar cómo llegó cada cliente al cerrar el flete")


def por_anuncio(fletes, anuncio_de: dict[str, str], uso_km, cliente_de, hoy) -> list[Grupo]:
    """Solo los fletes que vinieron de un anuncio: cual trae fletes que dejan plata."""
    con = [f for f in fletes if f.job_id in anuncio_de]
    return _grupos(con, lambda f: anuncio_de[f.job_id], uso_km, cliente_de, hoy,
                   "Más fletes de este anuncio, cerrados confirmando km y horas")


def jornadas(fletes, uso_km, cliente_de, hoy) -> tuple[list[Grupo], str | None]:
    """Agrupa por cuantos fletes hubo ese dia. La conclusion solo si los intervalos no se pisan."""
    por_dia: dict[date, int] = {}
    for f in fletes:
        por_dia[f.dia] = por_dia.get(f.dia, 0) + 1
    grupos = _grupos(fletes, lambda f: f"Días de {min(por_dia[f.dia], 3)}{'+' if por_dia[f.dia] > 3 else ''} "
                                       f"{'flete' if por_dia[f.dia] == 1 else 'fletes'}",
                     uso_km, cliente_de, hoy, "Más jornadas con 2 y con 3 fletes, con horas confirmadas")
    dos = next((g for g in grupos if g.nombre.startswith("Días de 2")), None)
    tres = next((g for g in grupos if g.nombre.startswith("Días de 3")), None)
    conclusion = None
    if dos and tres and dos.margen_hora.fuerte and tres.margen_hora.fuerte:
        a, b = dos.margen_hora, tres.margen_hora
        if a.lo > b.hi:
            conclusion = "Tus jornadas de dos fletes rinden más por hora que las de tres."
        elif b.lo > a.hi:
            conclusion = "Tus jornadas de tres fletes rinden más por hora que las de dos."
        else:
            conclusion = "Entre jornadas de dos y de tres fletes no hay una diferencia clara por hora."
    return grupos, conclusion


@dataclass(frozen=True)
class ValorCliente:
    cliente: str
    fletes: int
    ingresos: Decimal
    margen: Decimal
    cac: Decimal

    @property
    def ltv(self) -> Decimal:
        return self.margen - self.cac

    @property
    def ltv_cac(self) -> float | None:
        return float(self.ltv / self.cac) if self.cac else None


def ltv(fletes, cliente_de: dict[str, str], cac_de_cliente: dict[str, Decimal], uso_km) -> list[ValorCliente]:
    """Por cliente: todo lo que dejo, menos lo que costo conseguirlo (una sola vez)."""
    por: dict[str, list[FleteDelMes]] = {}
    for f in fletes:
        por.setdefault(cliente_de.get(f.job_id, f.job_id), []).append(f)
    salida = [ValorCliente(c, len(fs), sum((f.cobrado for f in fs), Decimal(0)),
                           sum((margen(f, uso_km) for f in fs), Decimal(0)), cac_de_cliente.get(c, Decimal(0)))
              for c, fs in por.items()]
    return sorted(salida, key=lambda v: -v.ltv)


def costo_oportunidad_disponible(fletes, hoy: date) -> tuple[bool, str]:
    """Solo se calcula con historia suficiente. Devuelve (se puede, por que no)."""
    if not fletes:
        return False, "no hay fletes todavía"
    semanas = (hoy - min(f.dia for f in fletes)).days / 7
    if semanas < SEMANAS_PARA_OPORTUNIDAD:
        return False, (f"hacen falta {SEMANAS_PARA_OPORTUNIDAD} semanas de historia para saber qué franjas "
                       f"traen mejores trabajos; hay {semanas:.0f}")
    return True, ""


@dataclass(frozen=True)
class Encadenable:
    primero: str
    segundo: str
    dia: date
    ahorro_km: float


def encadenables(pedidos: list[tuple[str, date, dict, dict]], factor_calles: float) -> list[Encadenable]:
    """pedidos: (job_id, dia, tramos, puntos). Ahorro = vuelta de A + ida de B - de A a B."""
    salida = []
    por_dia: dict[date, list] = {}
    for p in pedidos:
        if p[1] is not None and p[2] and p[3]:
            por_dia.setdefault(p[1], []).append(p)
    for dia, ps in por_dia.items():
        for a in ps:
            for b in ps:
                if a is b:
                    continue
                entre = km_entre(a[3]["destino"], b[3]["origen"]) * factor_calles
                ahorro = float(a[2]["vuelta"]) + float(b[2]["ida"]) - entre
                if ahorro >= AHORRO_MINIMO_KM:
                    salida.append(Encadenable(a[0], b[0], dia, ahorro))
    # Un par por flete: el que mas ahorra.
    salida.sort(key=lambda x: -x.ahorro_km)
    usados, elegidos = set(), []
    for x in salida:
        if x.primero in usados or x.segundo in usados:
            continue
        usados |= {x.primero, x.segundo}
        elegidos.append(x)
    return elegidos


def resumen_estado(grupos: list[Grupo]) -> Estado:
    return max((g.margen_hora.estado for g in grupos), key=lambda e: e.value, default=Estado.EXPLORATORIO)
