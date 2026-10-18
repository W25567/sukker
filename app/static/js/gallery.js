// Stor billedvisning på profilen: klik på et billede i mosaikken, og bladr med pile, tastatur eller swipe.
// Uden JavaScript åbner billedet i en ny fane.
(function () {
  "use strict";

  var mosaic = document.querySelector("[data-gallery]");
  var dialog = document.querySelector("dialog.lightbox");
  if (!mosaic || !dialog || typeof dialog.showModal !== "function") {
    return;
  }
  var items = Array.prototype.map.call(document.querySelectorAll("[data-gallery-items] li"), function (li) {
    return { src: li.getAttribute("data-src"), alt: li.getAttribute("data-alt"), private: li.hasAttribute("data-private") };
  });
  var image = dialog.querySelector(".lightbox-image");
  var caption = dialog.querySelector(".lightbox-caption");
  var prev = dialog.querySelector(".lightbox-prev");
  var next = dialog.querySelector(".lightbox-next");
  var current = 0;
  var touchX = null;

  function show(index) {
    current = (index + items.length) % items.length;
    var item = items[current];
    image.src = item.src;
    image.alt = item.alt;
    caption.textContent = (current + 1) + " / " + items.length + (item.private ? " · Privat" : "");
    prev.hidden = next.hidden = items.length < 2;
  }

  mosaic.addEventListener("click", function (event) {
    var tile = event.target.closest(".tile");
    if (!tile) {
      return;
    }
    event.preventDefault();
    show(parseInt(tile.getAttribute("data-index"), 10) || 0);
    dialog.showModal();
  });
  prev.addEventListener("click", function () { show(current - 1); });
  next.addEventListener("click", function () { show(current + 1); });
  dialog.querySelector(".lightbox-close").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) {
      dialog.close();  // klik uden for billedet
    }
  });
  dialog.addEventListener("keydown", function (event) {
    if (event.key === "ArrowLeft") { show(current - 1); }
    if (event.key === "ArrowRight") { show(current + 1); }
  });
  dialog.addEventListener("touchstart", function (event) { touchX = event.touches[0].clientX; }, { passive: true });
  dialog.addEventListener("touchend", function (event) {
    if (touchX === null) {
      return;
    }
    var delta = event.changedTouches[0].clientX - touchX;
    touchX = null;
    if (Math.abs(delta) > 40) {
      show(current + (delta < 0 ? 1 : -1));
    }
  });
  dialog.addEventListener("close", function () { image.removeAttribute("src"); });
})();
