// Adgang til private billeder: krydser man et billede af, gælder adgangen kun de valgte billeder.
(function () {
  "use strict";

  var form = document.querySelector("form.access-form");
  if (!form) {
    return;
  }
  var selected = form.querySelector('input[name="scope"][value="selected"]');
  form.addEventListener("change", function (event) {
    if (event.target.name === "image" && event.target.checked && selected) {
      selected.checked = true;
    }
  });
})();
