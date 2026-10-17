// Henter nye beskeder i en åben samtale, så man ikke skal genindlæse siden.
// Spørger hvert sekund, mens fanen er synlig. Serveren svarer 204, når intet er ændret.
(function () {
  "use strict";

  var thread = document.getElementById("thread");
  if (!thread || !window.fetch) {
    return;
  }
  var status = document.getElementById("thread-status");
  var url = thread.getAttribute("data-poll-url");
  var version = thread.getAttribute("data-version");
  var INTERVAL = 1000;
  var MAX_DELAY = 30000;
  var failures = 0;
  var timer = null;
  var stopped = false;

  function nearBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
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
        var stick = nearBottom();
        var newMessages = messageCount(data.version) > messageCount(version);
        thread.innerHTML = data.html;
        version = data.version;
        failures = 0;
        if (stick) {
          window.scrollTo(0, document.documentElement.scrollHeight);
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
