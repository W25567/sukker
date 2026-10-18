// Holder tallene i menuen opdaterede og viser en midlertidig besked nederst til højre, når der kommer en
// ny besked eller et nyt match. Spørger hvert 10. sekund, mens fanen er synlig.
// I en åben samtale vises ingen midlertidige beskeder: samtalen opdaterer sig selv (conversation.js).
(function () {
  "use strict";

  var box = document.getElementById("toasts");
  if (!box || !window.fetch) {
    return;
  }
  var url = box.getAttribute("data-url");
  var inChat = document.getElementById("thread") !== null;
  var INTERVAL = 10000;
  var MAX_DELAY = 120000;
  var SHOW_FOR = 8000;
  var failures = 0;
  var timer = null;
  var stopped = false;
  var known = null;  // de nyeste id'er ved sidste svar; null indtil første svar

  function setBadge(name, count) {
    var badge = document.querySelector('[data-badge="' + name + '"]');
    if (!badge) {
      return;
    }
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.setAttribute("aria-label", count + " " + badge.getAttribute("data-label"));
    badge.hidden = !count;
  }

  function idOf(item) {
    return item ? item.id : 0;
  }

  function toast(text, name, link) {
    var item = document.createElement("div");
    item.className = "toast";
    item.setAttribute("role", "status");
    var anchor = document.createElement("a");
    anchor.href = link;
    var strong = document.createElement("strong");
    strong.textContent = name;  // textContent: navnet kan aldrig blive til HTML
    anchor.appendChild(document.createTextNode(text + " "));
    anchor.appendChild(strong);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Luk");
    close.textContent = "×";
    close.addEventListener("click", function () { item.remove(); });
    item.appendChild(anchor);
    item.appendChild(close);
    box.appendChild(item);
    while (box.children.length > 3) {
      box.removeChild(box.firstChild);
    }
    window.setTimeout(function () { item.classList.add("leaving"); }, SHOW_FOR);
    window.setTimeout(function () { item.remove(); }, SHOW_FOR + 400);
  }

  function handle(data) {
    setBadge("messages", data.messages);
    setBadge("matches", data.matches);
    setBadge("requests", data.requests);
    var latest = {
      message: idOf(data.latest_message),
      admin: idOf(data.latest_admin),
      match: idOf(data.latest_match),
      request: idOf(data.latest_request),
      grant: idOf(data.latest_grant)
    };
    if (known !== null && !inChat) {
      if (latest.match > known.match) {
        toast("Nyt match med", data.latest_match.name, data.latest_match.url);
      }
      if (latest.message > known.message) {
        toast("Ny besked fra", data.latest_message.name, data.latest_message.url);
      }
      if (latest.admin > known.admin) {
        toast("Ny besked fra", data.latest_admin.name, data.latest_admin.url);
      }
      if (latest.request > known.request) {
        toast("Ny anmodning om dine private billeder fra", data.latest_request.name, data.latest_request.url);
      }
      if (latest.grant > known.grant) {
        toast("Du har fået adgang til private billeder hos", data.latest_grant.name, data.latest_grant.url);
      }
    }
    // Det højeste id huskes, så en ældre ulæst besked ikke vises igen, når en nyere bliver læst.
    known = known === null ? latest : {
      message: Math.max(known.message, latest.message),
      admin: Math.max(known.admin, latest.admin),
      match: Math.max(known.match, latest.match),
      request: Math.max(known.request, latest.request),
      grant: Math.max(known.grant, latest.grant)
    };
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
    fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (response.status === 401 || response.redirected) {
          stopped = true;  // logget ud: næste sideskift viser det
          return null;
        }
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (data) {
        if (data) {
          failures = 0;
          handle(data);
        }
      })
      .catch(function () { failures += 1; })
      .then(schedule);
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

  poll();  // første svar er udgangspunktet; derefter vises nyt
})();
