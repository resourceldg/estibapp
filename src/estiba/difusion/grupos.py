"""El catalogo de grupos, la cola del dia y lo publicado.

La cola respeta lo que haria una persona cuidadosa: solo grupos que aceptan
publicidad, en los dias que el grupo permite, con dias de por medio entre un
posteo y el siguiente en el mismo grupo y pocos posteos por dia. Cada texto
sale distinto del ultimo que se uso en ese grupo, y lleva un link de WhatsApp
con un codigo propio para saber de donde vino la consulta.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote

from estiba.origen import piezas as P

TEMAS = ("general", "estudiantes", "alquileres", "compraventa")
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

#: Cuantos dias tienen que pasar entre dos posteos en el mismo grupo, y
#: cuantos grupos por dia como mucho. Pocos y espaciados: lo que hace una
#: persona, no un robot.
ESPACIADO_DIAS = 7
POR_DIA = 4
TELEFONO = "5492235128209"

#: Textos por tema. {zona} y {link} se completan; ninguno promete algo que
#: no se hace hoy (precio por foto, C10, zonas y destinos confirmados).
TEXTOS: dict[str, dict[str, str]] = {
    "general": {
        "general-1": "🚚 ¡Hola grupo! Hago fletes y mudanzas chicas en {zona} con mi C10. Heladeras, camas, "
                     "sillones, cajas y compras de Marketplace. Mandame una foto de lo que hay que mover y te paso "
                     "el precio al toque 👉 {link}",
        "general-2": "Buenas! Si alguien necesita mover un mueble, un electro o hacer una mudanza chica en {zona}, "
                     "tengo flete con camioneta. Precio claro antes de salir. Escribime: {link}",
        "general-3": "📦 Flete en {zona}: muebles, electrodomésticos, cajas y bultos. Me mandás una foto y las "
                     "direcciones por WhatsApp y te digo cuánto sale, sin vueltas: {link}",
    },
    "estudiantes": {
        "estudiantes-1": "🎓 ¿Terminaste de cursar y dejás el depto? Llevo tus cosas a Tandil, Balcarce, Necochea "
                         "o Miramar. Armo viajes compartidos entre estudiantes para que salga más barato. "
                         "Escribime con tu ciudad y la fecha: {link}",
        "estudiantes-2": "Fin de cursada 📚 Si volvés a tu ciudad y no sabés cómo llevar colchón, cajas y la "
                         "bici: hago viajes compartidos a Tandil, Balcarce, Necochea y Miramar. Precio por foto: {link}",
    },
    "alquileres": {
        "alquileres-1": "☀️ Para quienes preparan alquileres para la temporada: saco los muebles que sobran y "
                        "llevo los nuevos (camas, colchones, heladeras) en {zona}. Fotos por WhatsApp y te paso "
                        "precio: {link}",
        "alquileres-2": "¿Dejás el depto listo para la temporada? Vacío y amueblo con flete en {zona}. "
                        "Mandame lo que hay que mover y te cotizo en el momento: {link}",
    },
    "compraventa": {
        "compraventa-1": "🛋️ ¿Compraste algo acá y no entra en el auto? Lo busco en lo del vendedor y te lo llevo "
                         "a tu casa en {zona}. Pasame el link de la publicación: {link}",
        "compraventa-2": "Para compradores y vendedores del grupo: si necesitan flete para entregar o retirar lo "
                         "que vendieron en {zona}, escribime con una foto y las dos direcciones: {link}",
    },
}


@dataclass(frozen=True)
class Grupo:
    id: int
    nombre: str
    url: str | None
    zona: str
    tema: str
    dias: tuple[int, ...]
    acepta_publicidad: bool
    reglas: str | None
    miembros: int | None
    vigente: bool

    def deja_publicar(self, dia: date) -> bool:
        return not self.dias or dia.weekday() in self.dias

    @property
    def dias_texto(self) -> str:
        return ", ".join(DIAS[d] for d in self.dias) if self.dias else "cualquier día"


@dataclass(frozen=True)
class Posteo:
    """Uno para hacer hoy: el grupo, el texto listo y el codigo."""
    grupo: Grupo
    plantilla: str
    codigo: str
    texto: str
    ultimo: date | None


def _dias(v: str) -> tuple[int, ...]:
    return tuple(sorted({int(x) for x in v.split(",") if x.strip().isdigit() and 0 <= int(x) <= 6}))


def _grupo(r: sqlite3.Row) -> Grupo:
    return Grupo(r["id"], r["nombre"], r["url"], r["zona"], r["tema"], _dias(r["dias"]),
                 bool(r["acepta_publicidad"]), r["reglas"], r["miembros"], bool(r["vigente"]))


def todos(conn: sqlite3.Connection) -> list[Grupo]:
    return [_grupo(r) for r in conn.execute("SELECT * FROM grupos ORDER BY vigente DESC, nombre")]


def agregar(conn: sqlite3.Connection, nombre: str, *, url: str | None = None, zona: str = "Mar del Plata",
            tema: str = "general", dias: tuple[int, ...] = (), acepta_publicidad: bool = True,
            reglas: str | None = None, miembros: int | None = None) -> Grupo:
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("el grupo necesita un nombre")
    if tema not in TEMAS:
        raise ValueError(f"tema '{tema}' no es uno de {', '.join(TEMAS)}")
    with conn:
        cur = conn.execute(
            "INSERT INTO grupos (nombre, url, zona, tema, dias, acepta_publicidad, reglas, miembros)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (nombre, url, zona, tema, ",".join(str(d) for d in sorted(set(dias))), int(acepta_publicidad),
             reglas, miembros))
    return _grupo(conn.execute("SELECT * FROM grupos WHERE id = ?", (cur.lastrowid,)).fetchone())


def pausar(conn: sqlite3.Connection, grupo_id: int, vigente: bool = False) -> None:
    with conn:
        conn.execute("UPDATE grupos SET vigente = ? WHERE id = ?", (int(vigente), grupo_id))


def codigo(grupo_id: int, dia: date) -> str:
    """Corto, legible y unico por grupo y dia: G3-1108."""
    return f"G{grupo_id}-{dia:%d%m}"


def link(grupo: Grupo, cod: str, telefono: str = TELEFONO) -> str:
    # No "vi tu publicacion": eso lo reconoce clasificacion como anuncio de
    # Meta y la consulta contaria como publicidad paga.
    texto = f"¡Hola! Te escribo por el grupo {grupo.nombre} ({cod})"
    return f"https://wa.me/{telefono}?text={quote(texto)}"


def _posteados(conn: sqlite3.Connection) -> dict[str, list[tuple[date, str | None]]]:
    """lugar (nombre del grupo) -> [(dia, plantilla)] de lo publicado, del mas nuevo al mas viejo."""
    salida: dict[str, list[tuple[date, str | None]]] = {}
    for r in conn.execute("SELECT lugar, desde, notas FROM piezas WHERE tipo = 'posteo_grupo' AND desde IS NOT NULL"
                          " ORDER BY desde DESC, id DESC"):
        plantilla = (r["notas"] or "").removeprefix("plantilla:") or None
        salida.setdefault(r["lugar"], []).append((date.fromisoformat(r["desde"]), plantilla))
    return salida


def _plantilla(tema: str, usadas: list[str | None]) -> str:
    """La del tema que hace mas tiempo que no se usa en este grupo."""
    opciones = list(TEXTOS.get(tema) or TEXTOS["general"])
    for clave in opciones:
        if clave not in usadas:
            return clave
    return max(opciones, key=lambda c: usadas.index(c))


def cola(conn: sqlite3.Connection, hoy: date, por_dia: int = POR_DIA, espaciado: int = ESPACIADO_DIAS,
         telefono: str = TELEFONO) -> list[Posteo]:
    """Los grupos que tocan hoy, del que hace mas tiempo que no se publica."""
    hechos = _posteados(conn)
    candidatos = []
    for g in todos(conn):
        if not (g.vigente and g.acepta_publicidad and g.deja_publicar(hoy)):
            continue
        historia = hechos.get(g.nombre, [])
        ultimo = historia[0][0] if historia else None
        if ultimo is not None and (hoy - ultimo).days < espaciado:
            continue
        candidatos.append((ultimo or date.min, g, historia))
    candidatos.sort(key=lambda t: (t[0], t[1].nombre))
    salida = []
    for ultimo, g, historia in candidatos[:por_dia]:
        clave = _plantilla(g.tema, [p for _, p in historia])
        cod = codigo(g.id, hoy)
        tema = g.tema if g.tema in TEXTOS else "general"
        texto = TEXTOS[tema][clave].format(zona=g.zona, link=link(g, cod, telefono))
        salida.append(Posteo(g, clave, cod, texto, None if ultimo == date.min else ultimo))
    return salida


def publicado(conn: sqlite3.Connection, posteo: Posteo, hoy: date) -> P.Pieza:
    """Marcar como publicado: queda como pieza, con su codigo, para Origen."""
    existente = P.por_clave(conn, f"grupo-{posteo.grupo.id}-{hoy:%Y%m%d}")
    if existente:
        return existente
    pieza = P.agregar(conn, f"grupo-{posteo.grupo.id}-{hoy:%Y%m%d}", f"Grupo · {posteo.grupo.nombre}",
                      "posteo_grupo", "organico", "organico", lugar=posteo.grupo.nombre,
                      codigo=posteo.codigo, url=posteo.grupo.url, desde=hoy, texto=posteo.texto)
    with conn:
        conn.execute("UPDATE piezas SET notas = ? WHERE id = ?", (f"plantilla:{posteo.plantilla}", pieza.id))
    return pieza


# --- Pedidos vistos en grupos -------------------------------------------------

@dataclass(frozen=True)
class PedidoVisto:
    id: int
    grupo: str | None
    texto: str
    contacto: str | None
    visto_at: datetime
    estado: str


def anotar_pedido(conn: sqlite3.Connection, texto: str, ahora: datetime, grupo_id: int | None = None,
                  contacto: str | None = None) -> int:
    texto = texto.strip()
    if not texto:
        raise ValueError("pegá el texto del pedido")
    with conn:
        return conn.execute("INSERT INTO pedidos_vistos (grupo_id, texto, contacto, visto_at) VALUES (?,?,?,?)",
                            (grupo_id, texto, contacto, ahora.isoformat())).lastrowid


def pedidos(conn: sqlite3.Connection, solo_pendientes: bool = True) -> list[PedidoVisto]:
    sql = ("SELECT p.*, g.nombre AS grupo FROM pedidos_vistos p LEFT JOIN grupos g ON g.id = p.grupo_id"
           + (" WHERE p.estado = 'pendiente'" if solo_pendientes else "") + " ORDER BY p.visto_at DESC")
    return [PedidoVisto(r["id"], r["grupo"], r["texto"], r["contacto"], datetime.fromisoformat(r["visto_at"]),
                        r["estado"]) for r in conn.execute(sql)]


def resolver_pedido(conn: sqlite3.Connection, pedido_id: int, estado: str, ahora: datetime) -> None:
    if estado not in ("contactado", "descartado"):
        raise ValueError("estado: contactado o descartado")
    with conn:
        conn.execute("UPDATE pedidos_vistos SET estado = ?, resuelto_at = ? WHERE id = ?",
                     (estado, ahora.isoformat(), pedido_id))
