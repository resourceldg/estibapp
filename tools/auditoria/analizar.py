"""Mide sobre el corpus exportado cada patron de error, para no generalizar desde ejemplos.

Lee audit/messages_export.jsonl y audit/fletes_export.jsonl (ver exportar.py)
y escribe audit/analisis.json con conteos y la lista de casos de cada uno.
Solo mira conversaciones de negocio (lead, cliente, dudosa).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

A = Path("audit")
NEGOCIO = {"lead", "cliente", "dudosa"}


def plano(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


# Un precio como lo dice el fletero: un numero con "mil/k/lucas", con $, con punto de miles,
# o un numero de 2-3 cifras pegado a "sale/salen/esta/son/queda/te sale/le sale/precio/valor".
PRECIO_REAL = re.compile(
    r"(\$\s?\d[\d.]*|\b\d{1,3}(?:\.\d{3})+\b|\b\d{2,3}\s*(?:mil|k|lucas|luca)\b"
    r"|\b(?:sale|salen|esta|son|queda|cuesta|precio|valor|cobro)\s+(?:de\s+|en\s+)?\d{2,3}\b"
    r"|\b\d{2,3}\s+(?:le|te)?\s*sale\b)")
LUGARES_COSTA = ["mar del plata", "mardel", "mdp", "ostende", "pinamar", "gesell", "mar azul", "san clemente",
                 "santa clara", "mar de cobo", "miramar", "camet", "batan", "chapadmalal", "mar chiquita",
                 "san bernardo", "mar de ajo", "carilo", "las gaviotas", "mar de las pampas", "sierra de los padres"]
ALTURA_SIN_AL = re.compile(r"\b([a-záéíóúñ][a-záéíóúñ.]+(?:\s+[a-záéíóúñ]+){0,2})\s+(\d{3,5})\b", re.I)
PISO = re.compile(r"\b(planta baja|pb|\d\s*(?:er|ro|do|°)?\s*piso|primer piso|segundo piso|tercer piso|escalera|ascensor)\b")
COSAS = re.compile(r"\b(?:un|una|unos|unas|dos|tres|cuatro|cinco|seis|\d+)\s+([a-záéíóúñ]{4,})", re.I)
NO_COSA = {"flete", "fletes", "consulta", "pregunta", "cuadra", "cuadras", "hora", "horas", "semana", "dias",
           "momento", "rato", "poco", "vez", "veces", "persona", "personas", "mudanza", "direccion", "foto",
           "fotos", "ubicacion", "audio", "precio", "valor", "metros", "metro", "plazas", "piso", "pisos",
           "ayudante", "viaje", "vuelta", "camioneta", "casa", "departamento", "depto", "mano", "chico", "chica",
           "grande", "mueble"}


def main() -> None:
    from estiba.extraction import reglas

    msgs = [json.loads(l) for l in (A / "messages_export.jsonl").open()]
    fletes = {f["freight_id"]: f for f in (json.loads(l) for l in (A / "fletes_export.jsonl").open())}
    neg = [m for m in msgs if m["chat_class"] in NEGOCIO]
    out: dict = {"mensajes_negocio": len(neg), "conversaciones_negocio": len({m["conversation_id"] for m in neg})}

    # 1. Precios del fletero no detectados
    perdidos, detectados = [], 0
    for m in neg:
        t = m["source_text"]
        if m["direction"] != "saliente" or not t:
            continue
        if PRECIO_REAL.search(plano(t)):
            if any(h["campo"] == "quoted_price" for h in (m["parser_output"] or [])):
                detectados += 1
            else:
                perdidos.append({"chat": m["conversation_id"], "id": m["message_id"], "tipo": m["message_type"],
                                 "texto": t[:160]})
    out["precio"] = {"con_precio": detectados + len(perdidos), "detectados": detectados,
                     "no_detectados": len(perdidos), "casos": perdidos}

    # 2. Pedidos sin precio aunque el fletero lo dijo
    sin = []
    for fid, f in fletes.items():
        if f["fields"]["quoted_price"] is None:
            dichos = [p for p in perdidos if p["chat"] == f["conversation_id"]]
            if dichos:
                sin.append({"freight_id": fid, "state": f["state"], "precio_dicho": dichos[0]["texto"][:80]})
    out["pedidos_sin_precio_pero_cotizados"] = sin

    # 3. Lugares de la costa nombrados por el cliente y como quedaron
    lugares = []
    for m in neg:
        t = m["source_text"]
        if m["direction"] != "entrante" or not t:
            continue
        nombrados = [l for l in LUGARES_COSTA if re.search(rf"\b{l}\b", plano(t))]
        if nombrados:
            ext = {h["campo"]: h["valor"] for h in (m["parser_output"] or []) if h["campo"] in ("origin", "destination")}
            lugares.append({"chat": m["conversation_id"], "nombrados": nombrados, "extraido": ext, "texto": t[:140]})
    out["lugares_costa"] = lugares

    # 4. Direcciones con altura sin "al" (que la regla de direcciones sueltas no toma)
    alturas = []
    for m in neg:
        t = m["source_text"]
        if m["direction"] != "entrante" or not t or len(t) > 400:
            continue
        ext = [h for h in (m["parser_output"] or []) if h["campo"] in ("origin", "destination")]
        for x in ALTURA_SIN_AL.finditer(t):
            calle = plano(x.group(1))
            if calle.split()[-1] in ("al", "los", "las", "de", "en", "unos", "son", "x") or re.search(r"\d", calle):
                continue
            if not any(x.group(2) in str(h["valor"]) for h in ext):
                alturas.append({"chat": m["conversation_id"], "direccion": x.group(0), "texto": t[:120]})
    out["direcciones_con_altura_no_tomadas"] = alturas

    # 5. Pisos / planta baja / escalera mencionados y no tomados
    pisos = []
    for m in neg:
        t = m["source_text"]
        if m["direction"] != "entrante" or not t:
            continue
        if PISO.search(plano(t)) and not any(h["campo"] == "floors_no_lift" for h in (m["parser_output"] or [])):
            pisos.append({"chat": m["conversation_id"], "texto": t[:140]})
    out["pisos_no_tomados"] = pisos

    # 6. Cosas nombradas que no estan en el vocabulario
    vocab = set(reglas._FORMA)
    faltan = Counter()
    ejemplos = defaultdict(list)
    for m in neg:
        t = m["source_text"]
        if m["direction"] != "entrante" or not t:
            continue
        for x in COSAS.finditer(plano(t)):
            w = x.group(1)
            if w not in vocab and w not in NO_COSA and w.rstrip("s") not in vocab:
                faltan[w] += 1
                if len(ejemplos[w]) < 2:
                    ejemplos[w].append(t[:100])
    out["palabras_tras_cantidad_fuera_del_vocabulario"] = [{"palabra": w, "veces": n, "ejemplos": ejemplos[w]}
                                                           for w, n in faltan.most_common(60)]

    # 7. Fechas sacadas de nombres de calle ("11 de setiembre", "14 de julio") o lejos en el futuro
    fechas = []
    for m in neg:
        for h in m["parser_output"] or []:
            if h["campo"] == "when" and str(h["valor"]).startswith("2027"):
                fechas.append({"chat": m["conversation_id"], "valor": h["valor"], "cita": h["cita"],
                               "texto": (m["source_text"] or "")[:120]})
    out["fechas_sospechosas"] = fechas

    # 8. Intenciones detectadas, para revisarlas una por una
    out["intenciones"] = [{"chat": m["conversation_id"], "intent": m["detected_intent"], "cita": m["intent_quote"],
                           "texto": (m["source_text"] or "")[:160]}
                          for m in neg if m["detected_intent"]]

    # 9. Pedidos perdidos por silencio con el ultimo mensaje del cliente prometiendo volver
    vuelve = re.compile(r"\b(te\s+aviso|te\s+confirmo|te\s+contacto|te\s+escribo|te\s+mantengo|le\s+digo|lo\s+coordino|"
                        r"te\s+llamo|averiguando)\b")
    silencio = []
    por_chat = defaultdict(list)
    for m in msgs:
        por_chat[m["freight_id"]].append(m)
    for fid, f in fletes.items():
        causa = (f["state_events"][-1]["cause"] if f["state_events"] else "") or ""
        if f["state"] == "cancelado" and "sin respuesta" in causa:
            suyos = [m for m in por_chat[fid] if m["direction"] == "entrante" and m["source_text"]]
            ult = suyos[-1]["source_text"] if suyos else ""
            if vuelve.search(plano(ult)):
                silencio.append({"freight_id": fid, "ultimo_del_cliente": ult[:140], "causa": causa})
    out["perdidos_por_silencio_que_dijeron_te_aviso"] = silencio

    # 10. Estados finales y causas
    out["estados"] = Counter(f["state"] for f in fletes.values())
    out["causas_perdida"] = Counter(re.sub(r"\d+ días", "N días", (f["state_events"][-1]["cause"] if f["state_events"] else "—"))
                                    for f in fletes.values() if f["state"] == "cancelado")
    out["mensajes_otro_sin_contenido"] = sum(1 for m in neg if m["message_type"] == "other")
    (A / "analisis.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    resumen = {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}
    resumen["precio"] = {k: v for k, v in out["precio"].items() if k != "casos"}
    print(json.dumps(resumen, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
