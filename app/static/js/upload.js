// Uploader billeder, så snart de er valgt. Flere billeder sendes ét ad gangen, så ingen forespørgsel bliver
// for stor, og man kan vælge flere på én gang fra kamerarullen. Bagefter genindlæses siden med beskederne.
(function () {
  "use strict";

  var form = document.querySelector("form.upload");
  if (!form || !window.fetch || !window.FormData) {
    return;  // uden JavaScript bruges knappen "Upload valgte billeder"
  }
  var input = form.querySelector(".upload-input");
  var pick = form.querySelector(".upload-pick");
  var submit = form.querySelector(".upload-submit");
  var status = form.querySelector(".upload-status");
  var csrf = form.querySelector('input[name="csrf_token"]').value;
  var maxBytes = parseInt(form.getAttribute("data-max-bytes"), 10);
  var doneUrl = form.getAttribute("data-done-url");
  submit.hidden = true;

  var KEY = "sukker-upload-result";

  function say(text) {
    status.textContent = text;
  }

  function remember(result) {
    try { window.sessionStorage.setItem(KEY, JSON.stringify(result)); } catch (e) { /* privat vindue */ }
  }

  // Vis resultatet af en upload, der lige er afsluttet (sat før genindlæsningen).
  (function showLastResult() {
    var raw = null;
    try {
      raw = window.sessionStorage.getItem(KEY);
      window.sessionStorage.removeItem(KEY);
    } catch (e) { /* privat vindue */ }
    if (!raw) {
      return;
    }
    var result = JSON.parse(raw);
    var lines = [];
    if (result.uploaded) {
      lines.push(result.uploaded === 1 ? "Billedet er uploadet." : result.uploaded + " billeder er uploadet.");
    }
    lines = lines.concat(result.errors || []);
    say(lines.join(" "));
  })();

  function uploadOne(file) {
    var data = new FormData();
    data.append("csrf_token", csrf);
    data.append("image", file, file.name);
    return fetch(form.action, {
      method: "POST", body: data, credentials: "same-origin", headers: { "X-Upload": "1", "Accept": "application/json" }
    }).then(function (response) {
      if (response.status === 413) {
        return { uploaded: 0, error: file.name + ": Billedet er for stort." };
      }
      if (!response.ok) {
        return { uploaded: 0, error: file.name + ": Billedet kunne ikke uploades. Prøv igen." };
      }
      return response.json();
    }).catch(function () {
      return { uploaded: 0, error: file.name + ": Forbindelsen svigtede. Prøv igen." };
    });
  }

  input.addEventListener("change", function () {
    var files = Array.prototype.slice.call(input.files || []);
    if (!files.length) {
      return;
    }
    pick.setAttribute("aria-disabled", "true");
    pick.classList.add("busy");
    input.disabled = true;
    var uploaded = 0;
    var errors = [];
    var index = 0;

    function next() {
      if (index >= files.length) {
        // Resultatet vises efter genindlæsning. Fejl fra serveren kommer som almindelige beskeder på siden.
        remember({ uploaded: uploaded, errors: errors });
        if (window.location.pathname === doneUrl) {
          window.location.hash = "billeder";
          window.location.reload();  // samme adresse: kun en ændret # ville ikke genindlæse siden
        } else {
          window.location.assign(doneUrl + "#billeder");
        }
        return;
      }
      var file = files[index];
      index += 1;
      if (file.size > maxBytes) {
        errors.push(file.name + ": Billedet fylder over 15 MB.");
        next();
        return;
      }
      say("Uploader " + index + " af " + files.length + " …");
      uploadOne(file).then(function (result) {
        uploaded += result.uploaded || 0;
        if (result.error) {
          errors.push(result.error);
        }
        next();
      });
    }
    next();
  });
})();
