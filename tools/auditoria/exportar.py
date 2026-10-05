"""Exporta el corpus de la auditoria: cada mensaje y lo que Estiba hizo con el.

Corre la misma sincronizacion que la app sobre una COPIA de la base (la
original no se toca) y deja, sin inventar nada (null si no existe):

- audit/messages_export.jsonl / .csv: un registro por mensaje.
- audit/fletes_export.jsonl: un registro por oportunidad (pedido), con los
  campos finales, de que mensaje salio cada uno, los candidatos descartados
  y cada cambio de estado con su causa.
- audit/conversaciones/<id>.md: cada conversacion legible, en orden, con lo
  que interpreto Estiba al lado de cada mensaje.

Uso: uv run python tools/auditoria/exportar.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

SALIDA = Path("audit")


def _copiar_base() -> Path:
    destino = SALIDA / "estiba-copia.db"
    origen = sqlite3.connect("file:estiba.db?mode=ro", uri=True)
    copia = sqlite3.connect(destino)
    origen.backup(copia)
    origen.close()
    copia.close()
    return destino


def _slot(s) -> dict | None:
    if s is None:
        return None
    v = s.value
    if hasattr(v, "isoformat"):
        v = v.isoformat()
    elif isinstance(v, list):
        v = [str(x) for x in v]
    elif v is not None and not isinstance(v, (int, float, str, bool)):
        v = str(v)
    return {"value": v, "confidence": s.confidence, "source_message_id": s.evidence.message_id,
            "source_text": s.evidence.quote, "method": s.evidence.method.value}


def _hallazgos(texto: str, m, desde_cliente: bool) -> list[dict]:
    from estiba.extraction import fechas, reglas

    if not texto:
        return []
    if desde_cliente:
        hs = [h for regla in (reglas.lugares, reglas.items, reglas.pisos, reglas.ayudantes, fechas.horarios)
              for h in regla(texto)]
        hs += fechas.fechas(texto, m.sent_at)
    else:
        hs = reglas.precio(texto) + fechas.horarios(texto) + fechas.fechas(texto, m.sent_at)
    salida = []
    for h in hs:
        v = h.valor
        if isinstance(v, list):
            v = [str(x) for x in v]
        elif hasattr(v, "isoformat"):
            v = v.isoformat()
        salida.append({"campo": h.campo, "valor": v, "confianza": h.confianza, "cita": h.cita, "duda": h.duda})
    return salida


def main() -> None:
    import os

    os.environ.setdefault("ESTIBA_APERTURA_AUTOMATICA", "0")
    from estiba.cli import MAPA_DEFAULT, PrecioPorObjetivos
    from estiba.conversation.store import ConversationStore
    from estiba.extraction.intencion import intencion
    from estiba.extraction.texto import plano
    from estiba.jobs.engine import JobEngine
    from estiba.web.app import Estado

    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "conversaciones").mkdir(exist_ok=True)
    db = _copiar_base()
    e = Estado(str(db), JobEngine(pricing=PrecioPorObjetivos(str(MAPA_DEFAULT))), datetime.now, None, None)
    e.sincronizar()
    r = e.resultado
    conn = e.conectar()
    store = ConversationStore(conn)
    transcripciones = {f["message_id"]: f for f in conn.execute("SELECT * FROM transcripts")}

    # De que pedido es cada mensaje.
    pedido_de: dict[str, str] = {}
    for job_id, (_, conv) in r.fletes.items():
        for m in conv.messages:
            pedido_de[m.id] = job_id
    # Que mensajes son evidencia de un dato final, y de cual.
    evidencia: dict[str, list[str]] = {}
    for job_id, (ev, _) in r.fletes.items():
        for campo, c in ev.extraccion.ganadores.items():
            evidencia.setdefault(c.slot.evidence.message_id, []).append(f"{job_id}:{campo}")

    registros = []
    for chat_id in store.chat_ids():
        conv = store.get(chat_id)
        clase = r.clases.get(chat_id)
        lineas = [f"# Conversación {chat_id}", "",
                  f"Clase: {clase.clase.value if clase else (r.fuera.get(chat_id) or '—')}"
                  f"{' · ' + clase.porque if clase else ''}", ""]
        for m in conv.messages:
            t = transcripciones.get(m.id)
            texto = conv.text_of(m)
            hallados = _hallazgos(texto, m, m.from_customer)
            intent = intencion(texto) if (texto and m.from_customer) else None
            job_id = pedido_de.get(m.id)
            estado_proc = ("transcripto" if t else "audio_sin_transcribir") if m.kind.value == "audio" else \
                ("sin_contenido" if m.kind.value == "other" else "texto")
            reg = {
                "conversation_id": chat_id, "message_id": m.id, "timestamp": m.sent_at.isoformat(),
                "direction": "entrante" if m.from_customer else "saliente", "contact_id": chat_id,
                "lead_id": job_id, "freight_id": job_id, "message_type": m.kind.value,
                "raw_text": m.text, "media_type": "audio" if m.kind.value == "audio" else None,
                "media_reference": m.media_path, "audio_transcription": t["text"] if t else None,
                "transcription_confidence": t["confidence"] if t else None,
                "source_text": texto, "normalized_text_interno": plano(texto) if texto else None,
                "detected_intent": intent[0].value if intent else None,
                "intent_quote": intent[1] if intent else None,
                "extracted_entities": hallados or None, "parser_output": hallados or None,
                "llm_output": None, "final_saved_data": evidencia.get(m.id),
                "chat_class": clase.clase.value if clase else r.fuera.get(chat_id),
                "processing_status": estado_proc, "error": None, "source": m.source,
            }
            registros.append(reg)
            quien = "CLIENTE" if m.from_customer else "FLETERO"
            tipo = " (AUDIO)" if m.kind.value == "audio" else (" (OTRO, sin contenido)" if m.kind.value == "other" else "")
            notas = []
            if intent:
                notas.append(f"intención={intent[0].value}")
            for h in hallados:
                notas.append(f"{h['campo']}={h['valor']}")
            if evidencia.get(m.id):
                notas.append("GANÓ: " + ", ".join(x.split(":")[-1] for x in evidencia[m.id]))
            lineas.append(f"- `{m.sent_at:%d/%m %H:%M}` **{quien}{tipo}** [{job_id.split(':')[-1] if job_id else '-'}]: "
                          f"{(texto or '').replace(chr(10), ' ')}" + (f"  \n  ↳ {' · '.join(notas)}" if notas else ""))
        # Resultado de cada pedido de esta charla.
        for job_id, (ev, _) in sorted((k, v) for k, v in r.fletes.items() if k.split(":job")[0] == chat_id):
            j = ev.job
            lineas += ["", f"## {job_id.split(':')[-1]} → estado final: {j.state.value}"]
            for campo in ("origin", "destination", "items", "when", "hour", "daypart", "floors_no_lift",
                          "helpers", "quoted_price"):
                s = getattr(j, campo, None)
                if s is not None:
                    lineas.append(f"- {campo}: {_slot(s)['value']}  (de: \"{s.evidence.quote}\", conf {s.confidence})")
            for x in ev.eventos:
                lineas.append(f"- estado {x.desde.value if x.desde else '·'} → {x.hacia.value}: {x.causa}")
            if ev.presupuesto is not None:
                lineas.append(f"- presupuesto Estiba: {ev.presupuesto.total} · km {ev.presupuesto.detalle.get('km')}"
                              f" · supuestos: {ev.presupuesto.supuestos}")
        (SALIDA / "conversaciones" / f"{chat_id.replace('@', '_')}.md").write_text("\n".join(lineas) + "\n")

    with (SALIDA / "messages_export.jsonl").open("w") as f:
        for reg in registros:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
    campos = list(registros[0])
    with (SALIDA / "messages_export.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        for reg in registros:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                        for k, v in reg.items()})

    with (SALIDA / "fletes_export.jsonl").open("w") as f:
        for job_id, (ev, conv) in r.fletes.items():
            lead = next((l for l in r.leads if l.job_id == job_id), None)
            f.write(json.dumps({
                "freight_id": job_id, "conversation_id": conv.chat_id, "state": ev.job.state.value,
                "lead_stage": lead.etapa.value if lead else None,
                "lost_reason": lead.motivo_perdida if lead else None,
                "fields": {c: _slot(getattr(ev.job, c, None)) for c in
                           ("origin", "destination", "items", "when", "hour", "daypart", "floors_no_lift",
                            "helpers", "quoted_price")},
                "missing": [str(x) for x in ev.job.missing],
                "discarded_candidates": [{"campo": c.campo, **_slot(c.slot)} for c in ev.extraccion.candidatos
                                         if ev.extraccion.ganadores.get(c.campo) is not c],
                "unresolved": [{"campo": c.campo, "duda": c.duda, "cita": c.slot.evidence.quote}
                               for c in ev.extraccion.sin_resolver],
                "intents": [{"message_id": i.message_id, "intent": i.intencion.value, "quote": i.cita}
                            for i in ev.extraccion.intenciones],
                "state_events": [{"from": x.desde.value if x.desde else None, "to": x.hacia.value,
                                  "at": x.at.isoformat(), "cause": x.causa, "message_id": x.message_id}
                                 for x in ev.eventos],
                "alerts": ev.alertas,
                "quote_estimate": {"total": str(ev.presupuesto.total), "km": str(ev.presupuesto.detalle.get("km")),
                                   "assumptions": ev.presupuesto.supuestos,
                                   "not_counted": ev.presupuesto.sin_contar} if ev.presupuesto else None,
                "messages": len(conv.messages),
            }, ensure_ascii=False) + "\n")
    charlas = len(store.chat_ids())
    conn.close()
    print(f"{len(registros)} mensajes, {len(r.fletes)} pedidos, {charlas} conversaciones")


if __name__ == "__main__":
    main()
