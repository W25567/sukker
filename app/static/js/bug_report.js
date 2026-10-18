// Fejlrapport: højst tre skærmbilleder. Tjekkes i browseren, så man ikke først får besked efter upload.
(function () {
  "use strict";

  var input = document.getElementById("screenshots");
  if (!input) {
    return;
  }
  input.addEventListener("change", function () {
    var max = parseInt(input.getAttribute("data-max-files"), 10);
    input.setCustomValidity(input.files.length > max ? "Vælg højst " + max + " billeder." : "");
    input.reportValidity();
  });
})();
