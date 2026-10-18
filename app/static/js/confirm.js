// Bekræftelse før en handling, der ikke kan fortrydes (formularer med data-confirm, fx sletning af billeder).
// Ved ja sendes bekraeft=1 med. Uden JavaScript viser serveren i stedet en side, hvor handlingen bekræftes.
(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var question = form.getAttribute && form.getAttribute("data-confirm");
    if (!question) {
      return;
    }
    if (!window.confirm(question)) {
      event.preventDefault();
      return;
    }
    if (!form.querySelector('input[name="bekraeft"]')) {
      var field = document.createElement("input");
      field.type = "hidden";
      field.name = "bekraeft";
      field.value = "1";
      form.appendChild(field);
    }
  });
})();
