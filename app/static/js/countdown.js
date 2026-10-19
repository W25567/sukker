// Nedtælling til lanceringen på pre-release-siden (pages/prerelease.html). Serveren har allerede skrevet
// tallene, så siden virker uden JavaScript. Når tiden er gået, genindlæses siden, og serveren sender videre.
(function () {
  "use strict";
  var box = document.querySelector(".countdown[data-launch]");
  if (!box) return;
  var launch = new Date(box.getAttribute("data-launch")).getTime();
  if (isNaN(launch)) return;
  var units = {};
  ["days", "hours", "minutes", "seconds"].forEach(function (name) {
    units[name] = box.querySelector('[data-unit="' + name + '"]');
  });

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function tick() {
    var left = Math.max(0, Math.floor((launch - Date.now()) / 1000));
    units.days.textContent = Math.floor(left / 86400);
    units.hours.textContent = pad(Math.floor((left % 86400) / 3600));
    units.minutes.textContent = pad(Math.floor((left % 3600) / 60));
    units.seconds.textContent = pad(left % 60);
    if (left === 0) {
      clearInterval(timer);
      setTimeout(function () { window.location.reload(); }, 1500);
    }
  }

  var timer = setInterval(tick, 1000);
  tick();
})();
