// Kommunevælgeren på tilmeldingen (auth/register.html). Dækker postnummeret flere kommuner, vises vælgeren, så snart
// postnummeret er skrevet, og ikke først efter "Opret profil" (hvor passwordet så skulle skrives igen). Uden
// JavaScript klarer serveren det stadig: den beder om kommunen, når formularen sendes (auth/routes.py).
(function () {
  "use strict";
  var box = document.querySelector("[data-kommune-field]");
  var postcode = document.getElementById("postcode");
  if (!box || !postcode) return;
  var select = box.querySelector("select");
  var url = box.getAttribute("data-url");
  var asked = null;

  function show(options) {
    var chosen = select.value || box.getAttribute("data-selected") || "";
    select.innerHTML = "";
    select.appendChild(new Option("Vælg kommune", ""));
    options.forEach(function (kommune) {
      select.appendChild(new Option(kommune.name, kommune.code, false, kommune.code === chosen));
    });
    box.hidden = false;
  }

  function hide() {
    box.hidden = true;
    select.value = "";
  }

  function update() {
    var value = postcode.value.trim();
    if (!/^\d{4}$/.test(value)) {
      asked = null;
      hide();
      return;
    }
    if (value === asked) return;
    asked = value;
    fetch(url + "?postnummer=" + encodeURIComponent(value), { headers: { Accept: "application/json" } })
      .then(function (response) { return response.ok ? response.json() : []; })
      .then(function (options) {
        if (postcode.value.trim() !== value) return;  // der er skrevet videre imens
        if (Array.isArray(options) && options.length > 1) show(options); else hide();
      })
      .catch(function () { asked = null; });  // serveren spørger, når formularen sendes
  }

  postcode.addEventListener("input", update);
  postcode.addEventListener("change", update);
  if (box.hidden) update();  // fx udfyldt af browseren
})();
