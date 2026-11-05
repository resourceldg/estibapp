"""Compara la transcripcion original (small) con la de la auditoria (medium + vocabulario).

Sin una transcripcion humana no hay "verdad": se mide cuanto difieren y,
sobre todo, si la diferencia cambia lo que Estiba entiende (lugares, cosas,
numeros, intencion). Escribe audit/stt_comparacion.json.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

A = Path("audit")


def main() -> None:
    from estiba.audio.wer import distancia, normalizar
    from estiba.extraction import fechas, reglas
    from estiba.extraction.intencion import intencion

    clases = {}
    for l in (A / "messages_export.jsonl").open():
        m = json.loads(l)
        clases[m["message_id"]] = (m["chat_class"], m["direction"])
    filas, cambios = [], []
    total_dist = total_pal = 0
    for l in (A / "audios.jsonl").open():
        r = json.loads(l)
        a, b = r["transcripcion_original"] or "", r["transcripcion_auditoria"] or ""
        ra, rb = normalizar(a), normalizar(b)
        d = distancia(rb, ra) if rb else None
        clase, direccion = clases.get(r["message_id"], (None, None))
        negocio = clase in ("lead", "cliente", "dudosa")
        if d is not None:
            total_dist += d
            total_pal += max(len(rb), 1)

        def leer(t: str) -> dict:
            if not t:
                return {}
            ahora = datetime.fromisoformat(r["sent_at"])
            hs = reglas.lugares(t) + reglas.items(t) + reglas.pisos(t) + reglas.ayudantes(t) + \
                fechas.horarios(t) + fechas.fechas(t, ahora)
            if direccion == "saliente":
                hs = reglas.precio(t) + fechas.horarios(t) + fechas.fechas(t, ahora)
            salida = {h.campo: (sorted(str(x) for x in h.valor) if isinstance(h.valor, list) else str(h.valor))
                      for h in hs}
            i = intencion(t) if direccion == "entrante" else None
            if i:
                salida["intencion"] = i[0].value
            return salida

        ia, ib = leer(a), leer(b)
        fila = {"message_id": r["message_id"], "negocio": negocio, "direccion": direccion,
                "duracion": r["duration_s"], "conf_small": r["confianza_original"],
                "conf_medium": r.get("confianza_auditoria"), "diferencia_palabras": d,
                "palabras": len(rb), "wer_relativo": round(d / max(len(rb), 1), 3) if d is not None else None,
                "interpretacion_small": ia, "interpretacion_medium": ib, "cambia_interpretacion": ia != ib,
                "small": a, "medium": b, "error": r.get("error")}
        filas.append(fila)
        if negocio and ia != ib:
            cambios.append(fila)
    neg = [f for f in filas if f["negocio"]]
    resumen = {
        "audios": len(filas), "de_negocio": len(neg), "errores": sum(1 for f in filas if f["error"]),
        "diferencia_media_entre_modelos": round(total_dist / max(total_pal, 1), 3),
        "negocio_diferencia_media": round(sum(f["diferencia_palabras"] or 0 for f in neg) /
                                          max(sum(f["palabras"] for f in neg), 1), 3),
        "negocio_confianza_small": round(sum(f["conf_small"] or 0 for f in neg) / max(len(neg), 1), 3),
        "negocio_confianza_medium": round(sum(f["conf_medium"] or 0 for f in neg) / max(len(neg), 1), 3),
        "negocio_que_cambian_la_interpretacion": len(cambios),
    }
    (A / "stt_comparacion.json").write_text(json.dumps({"resumen": resumen, "cambios": cambios, "todos": filas},
                                                      ensure_ascii=False, indent=1))
    print(json.dumps(resumen, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
