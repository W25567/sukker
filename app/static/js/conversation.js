// Samtaler: Ruller til de nyeste beskeder, når samtalen åbnes, og henter nye beskeder, så man ikke skal
// genindlæse siden. Spørger hvert sekund, mens fanen er synlig. Serveren svarer 204, når intet er ændret.
// Bruges både af brugernes samtaler og af administrationens samtaler med en bruger.
(function () {
  "use strict";

  var thread = document.getElementById("thread");
  if (!thread) {
    return;
  }
  var composer = document.querySelector(".composer");
  var bottomnav = document.querySelector(".bottomnav");
  var status = document.getElementById("thread-status");
  var url = thread.getAttribute("data-poll-url");
  var version = thread.getAttribute("data-version");
  var INTERVAL = 1000;
  var MAX_DELAY = 30000;
  var failures = 0;
  var timer = null;
  var stopped = false;

  // Højden på den faste bundmenu på mobil. På større skærme ligger menuen øverst og fylder ikke i bunden.
  function coveredBottom() {
    return bottomnav && window.getComputedStyle(bottomnav).position === "fixed" ? bottomnav.offsetHeight : 0;
  }

  function visibleHeight() {
    return window.innerHeight - coveredBottom();
  }

  // Ruller, så den nyeste besked og skrivefeltet er synlige.
  function showLatest() {
    var target = composer || thread;
    var bottom = target.getBoundingClientRect().bottom + window.scrollY;
    window.scrollTo(0, Math.max(0, bottom - visibleHeight() + 16));
  }

  function latestInView() {
    return thread.getBoundingClientRect().bottom <= visibleHeight() + 120;
  }

  function messageCount(key) {
    return parseInt(String(key).split("-")[0], 10) || 0;
  }

  function schedule() {
    if (stopped || timer !== null || document.hidden) {
      return;
    }
    var delay = failures ? Math.min(MAX_DELAY, INTERVAL * Math.pow(2, failures)) : INTERVAL;
    timer = window.setTimeout(poll, delay);
  }

  function poll() {
    timer = null;
    if (document.hidden) {
      return;
    }
    fetch(url + "?version=" + encodeURIComponent(version), {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (response.redirected || response.status === 404 || response.status === 403) {
        // Logget ud, blokeret eller matchet er fjernet: stop og lad næste sideskift vise det.
        stopped = true;
        return null;
      }
      if (response.status === 204) {
        failures = 0;
        return null;
      }
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      return response.json();
    }).then(function (data) {
      if (data) {
        var stick = latestInView();
        var newMessages = messageCount(data.version) > messageCount(version);
        thread.innerHTML = data.html;
        version = data.version;
        failures = 0;
        if (stick) {
          showLatest();
        }
        if (newMessages && status) {
          // Tøm først, så skærmlæsere også annoncerer den næste nye besked.
          status.textContent = "";
          window.setTimeout(function () { status.textContent = "Ny besked"; }, 50);
        }
      }
    }).catch(function () {
      failures += 1;
    }).then(schedule);
  }

  // Åbn samtalen ved de nyeste beskeder, medmindre adressen peger på noget bestemt på siden.
  if (!window.location.hash || window.location.hash === "#bund") {
    if ("scrollRestoration" in window.history) {
      window.history.scrollRestoration = "manual";
    }
    showLatest();
  }

  if (!url || !window.fetch) {
    return;
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      poll();
    }
  });

  schedule();
})();
