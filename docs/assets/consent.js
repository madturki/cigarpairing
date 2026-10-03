(function () {
  var KEY = "ag-consent";
  var gaId = document.currentScript.getAttribute("data-ga-id");
  var loaded = false;
  var banner = null;

  function getChoice() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  function setChoice(value) {
    try { localStorage.setItem(KEY, value); } catch (e) {}
  }

  function loadAnalytics() {
    gtag("consent", "update", { ad_storage: "granted", ad_user_data: "granted", ad_personalization: "granted", analytics_storage: "granted" });
    if (loaded) return;
    loaded = true;
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(gaId);
    document.head.appendChild(s);
  }

  function clearAnalyticsCookies() {
    gtag("consent", "update", { ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied", analytics_storage: "denied" });
    document.cookie.split(";").forEach(function (c) {
      var name = c.split("=")[0].trim();
      if (name.indexOf("_ga") !== 0) return;
      var host = location.hostname;
      [host, "." + host, "." + host.replace(/^www\./, "")].forEach(function (domain) {
        document.cookie = name + "=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=" + domain;
      });
      document.cookie = name + "=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    });
  }

  function hideBanner() {
    if (banner) banner.hidden = true;
  }

  function showBanner() {
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "cookie-banner";
      banner.setAttribute("role", "dialog");
      banner.setAttribute("aria-live", "polite");
      banner.setAttribute("aria-label", "Cookie consent");
      banner.innerHTML =
        '<p>We use Google Analytics cookies to understand how visitors use this site, and Google AdSense cookies to show personalised ads. ' +
        "They're only set if you accept, and you can change your mind any time from the footer.</p>" +
        '<div class="cookie-actions">' +
        '<button type="button" class="button ghost" data-consent="denied">Decline</button>' +
        '<button type="button" class="button" data-consent="granted">Accept</button>' +
        "</div>";
      banner.addEventListener("click", function (e) {
        var choice = e.target.getAttribute("data-consent");
        if (!choice) return;
        setChoice(choice);
        if (choice === "granted") loadAnalytics();
        else clearAnalyticsCookies();
        hideBanner();
      });
      document.body.appendChild(banner);
    }
    banner.hidden = false;
  }

  var choice = getChoice();
  if (choice === "granted") loadAnalytics();
  else if (choice !== "denied") showBanner();

  Array.prototype.forEach.call(document.querySelectorAll("[data-cookie-settings]"), function (btn) {
    btn.addEventListener("click", showBanner);
  });
})();
