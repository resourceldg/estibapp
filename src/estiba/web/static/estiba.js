// Estiba: lo poco que hace falta ademas de htmx.

// Aprobar un mensaje vacio no se puede. Sin WAHA, aprobar ademas abre
// WhatsApp con el texto listo: tiene que pasar en el mismo click para que el
// navegador no lo bloquee como popup.
document.addEventListener("click", (e) => {
  const boton = e.target.closest("[data-aprobar]");
  if (!boton) return;
  const area = boton.form.querySelector("textarea");
  const texto = area.value.trim();
  // Un mensaje con el hueco del precio sin completar no puede salir.
  if (/\$_+/.test(texto)) {
    e.preventDefault();
    e.stopImmediatePropagation();
    const campo = boton.form.querySelector("[data-monto]");
    if (campo) { campo.classList.add("vacio-error"); campo.focus(); }
    return;
  }
  if (!texto) {
    e.preventDefault();
    e.stopImmediatePropagation();
    area.classList.remove("vacio-error");
    void area.offsetWidth;
    area.classList.add("vacio-error");
    area.focus();
    return;
  }
  if (!boton.hasAttribute("data-abre-whatsapp")) return;
  window.open(`https://wa.me/${area.dataset.chat}?text=${encodeURIComponent(texto)}`, "_blank", "noopener");
}, true);

// Ctrl/Cmd + Enter aprueba desde el textarea.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !(e.ctrlKey || e.metaKey)) return;
  const area = e.target.closest(".redactar textarea");
  if (!area) return;
  e.preventDefault();
  area.form.querySelector("[data-aprobar]").click();
});

// La propuesta se muestra bloqueada a proposito: primero se lee, y recien
// si hace falta se modifica. Asi "validar" no compite con "editar".
document.addEventListener("click", (e) => {
  const boton = e.target.closest("[data-modificar]");
  if (!boton) return;
  const area = document.getElementById(boton.dataset.modificar);
  if (!area) return;
  area.removeAttribute("readonly");
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
  boton.closest(".decision-menores")?.classList.add("modificando");
});

// Copiar el texto aprobado.
document.addEventListener("click", async (e) => {
  const boton = e.target.closest("[data-copiar]");
  if (!boton) return;
  try {
    await navigator.clipboard.writeText(boton.dataset.copiar);
    const etiqueta = boton.querySelector("span");
    const antes = etiqueta.textContent;
    etiqueta.textContent = "Copiado";
    setTimeout(() => { etiqueta.textContent = antes; }, 1600);
  } catch { /* sin permiso de portapapeles: no pasa nada */ }
});

// Tocar un dato lleva al mensaje del que salio y lo resalta, aunque ya
// estuviera en el hash (el :target solo anima la primera vez).
document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-ir]");
  if (!link) return;
  const msj = document.getElementById(link.dataset.ir);
  if (!msj) return;
  e.preventDefault();
  msj.scrollIntoView({ behavior: "smooth", block: "center" });
  msj.classList.remove("resaltado");
  void msj.offsetWidth;
  msj.classList.add("resaltado");
  history.replaceState(null, "", `#${link.dataset.ir}`);
});

// Textareas que crecen con el texto donde no hay `field-sizing`.
if (!CSS.supports("field-sizing", "content")) {
  const ajustar = (t) => { t.style.height = "auto"; t.style.height = `${t.scrollHeight + 2}px`; };
  document.addEventListener("input", (e) => { if (e.target.matches(".redactar textarea")) ajustar(e.target); });
  const todas = () => document.querySelectorAll(".redactar textarea").forEach(ajustar);
  document.addEventListener("DOMContentLoaded", todas);
  document.addEventListener("htmx:afterSwap", todas);
}

// El hilo abre en el ultimo mensaje, como WhatsApp: lo que acaba de llegar
// esta a la vista y lo viejo queda arriba, a un scroll de distancia.
//
// Se hace varias veces a proposito: el alto cambia despues del primer
// pintado (fuentes, audios, un "Leer mas" que se abre) y una sola pasada
// dejaba el hilo parado en mensajes viejos.
const alFinal = () => {
  document.querySelectorAll("[data-al-final]").forEach((hilo) => {
    hilo.scrollTop = hilo.scrollHeight;
  });
};
const alFinalInsistiendo = () => {
  alFinal();
  requestAnimationFrame(alFinal);
  setTimeout(alFinal, 120);
  setTimeout(alFinal, 500);
};
document.addEventListener("DOMContentLoaded", alFinalInsistiendo);
window.addEventListener("load", alFinalInsistiendo);
document.addEventListener("htmx:afterSwap", alFinalInsistiendo);
// Abrir un mensaje largo cambia el alto: vuelve al final.
document.addEventListener("toggle", (e) => {
  if (e.target.classList?.contains("msj-largo")) alFinal();
}, true);

// El chat ocupa poco y se agranda cuando hace falta: con el boton o con
// doble toque en el hilo, que es el gesto de siempre para "mostrame mas".
const agrandar = (panel) => {
  const grande = panel.classList.toggle("grande");
  const boton = panel.querySelector("[data-agrandar]");
  if (boton) boton.textContent = grande ? "Achicar" : "Agrandar";
  alFinal();
};
document.addEventListener("click", (e) => {
  const boton = e.target.closest("[data-agrandar]");
  if (boton) agrandar(boton.closest(".chat"));
});
document.addEventListener("dblclick", (e) => {
  const hilo = e.target.closest(".chat .mensajes");
  if (hilo) agrandar(hilo.closest(".chat"));
});

// El precio se toca con + y - o escribiendolo, y el mensaje se reescribe
// solo. Editar el texto a mano para cambiar un numero es pedir que se
// escape un cero.
const PASO_PRECIO = 1000;

const conPuntos = (n) => n.toLocaleString("es-AR");

// El precio del mensaje es el primer "$..." que aparece, sea un numero o el
// hueco "$___" que deja Estiba cuando no sabe cuanto cobrar.
const PRECIO_EN_TEXTO = /\$\s?[\d.]+|\$_+/;

const escribirPrecio = (campo) => {
  const area = document.getElementById(campo.dataset.destino);
  if (!area) return;
  const valor = parseInt(campo.value.replace(/\D/g, ""), 10);
  if (!valor) return;
  area.value = area.value.replace(PRECIO_EN_TEXTO, `$${conPuntos(valor)}`);
};

document.addEventListener("click", (e) => {
  const boton = e.target.closest("[data-precio]");
  if (!boton) return;
  const campo = boton.parentElement.querySelector("[data-monto]");
  const valor = parseInt(campo.value.replace(/\D/g, ""), 10) || 0;
  const paso = boton.dataset.precio === "+" ? PASO_PRECIO : -PASO_PRECIO;
  campo.value = conPuntos(Math.max(valor + paso, 0));
  escribirPrecio(campo);
});

document.addEventListener("input", (e) => {
  if (e.target.matches("[data-monto]")) escribirPrecio(e.target);
});

// "Usar" lo que se cobro antes: llena el campo y reescribe el mensaje, sin
// que haya que copiar un numero a mano.
document.addEventListener("click", (e) => {
  const boton = e.target.closest("[data-usar]");
  if (!boton) return;
  const campo = document.querySelector("[data-monto]");
  if (!campo) return;
  campo.value = conPuntos(parseInt(boton.dataset.usar, 10));
  campo.classList.remove("vacio-error");
  escribirPrecio(campo);
});
