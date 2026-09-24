// Estiba: lo poco que hace falta ademas de htmx.

// Aprobar un mensaje vacio no se puede. Sin WAHA, aprobar ademas abre
// WhatsApp con el texto listo: tiene que pasar en el mismo click para que el
// navegador no lo bloquee como popup.
document.addEventListener("click", (e) => {
  const boton = e.target.closest("[data-aprobar]");
  if (!boton) return;
  const area = boton.form.querySelector("textarea");
  const texto = area.value.trim();
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
const alFinal = () => {
  document.querySelectorAll("[data-al-final]").forEach((hilo) => {
    hilo.scrollTop = hilo.scrollHeight;
  });
};
document.addEventListener("DOMContentLoaded", alFinal);
document.addEventListener("htmx:afterSwap", alFinal);
