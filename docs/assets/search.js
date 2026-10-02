(function () {
  var input = document.getElementById("q");
  var results = document.getElementById("results");
  var status = document.getElementById("search-status");
  var index = null;

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function run(query) {
    var terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) {
      results.innerHTML = "";
      status.textContent = "";
      return;
    }
    var hits = index
      .map(function (p) {
        var title = p.t.toLowerCase();
        var body = (p.c.join(" ") + " " + p.x).toLowerCase();
        var score = 0;
        for (var i = 0; i < terms.length; i++) {
          if (title.indexOf(terms[i]) !== -1) score += 10;
          else if (body.indexOf(terms[i]) !== -1) score += 1;
          else return null;
        }
        return { p: p, score: score };
      })
      .filter(Boolean)
      .sort(function (a, b) { return b.score - a.score; });

    status.textContent = hits.length + (hits.length === 1 ? " result" : " results") + " for \u201c" + query + "\u201d";
    results.innerHTML = hits
      .map(function (h) {
        return '<article class="result"><h2><a href="' + h.p.u + '">' + escapeHtml(h.p.t) + "</a></h2>" +
          '<div class="card-meta">' + escapeHtml(h.p.d) + " \u00b7 " + escapeHtml(h.p.c.join(", ")) + "</div>" +
          "<p>" + escapeHtml(h.p.s) + "</p></article>";
      })
      .join("");
  }

  var initial = new URLSearchParams(window.location.search).get("q") || "";
  input.value = initial;

  fetch("/search-index.json")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      index = data;
      run(input.value);
    });

  input.addEventListener("input", function () {
    if (!index) return;
    run(input.value);
    var url = input.value ? "?q=" + encodeURIComponent(input.value) : window.location.pathname;
    history.replaceState(null, "", url);
  });
})();
