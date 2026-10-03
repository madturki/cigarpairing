(function () {
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  Array.prototype.forEach.call(document.querySelectorAll(".slider"), function (slider) {
    var track = slider.querySelector(".slides");
    var count = track.children.length;
    var dots = slider.querySelectorAll(".slider-dots button");
    var current = 0;
    var timer = null;

    function go(i) {
      current = (i + count) % count;
      track.scrollTo({ left: current * track.clientWidth, behavior: reduceMotion ? "auto" : "smooth" });
    }

    function sync() {
      current = Math.round(track.scrollLeft / track.clientWidth);
      Array.prototype.forEach.call(dots, function (d, i) {
        d.setAttribute("aria-current", i === current ? "true" : "false");
      });
    }

    function stop() { clearInterval(timer); }
    function play() {
      if (reduceMotion) return;
      stop();
      timer = setInterval(function () { go(current + 1); }, 6000);
    }

    slider.querySelector(".slider-prev").addEventListener("click", function () { go(current - 1); });
    slider.querySelector(".slider-next").addEventListener("click", function () { go(current + 1); });
    Array.prototype.forEach.call(dots, function (d, i) {
      d.addEventListener("click", function () { go(i); });
    });
    track.addEventListener("scroll", function () { window.requestAnimationFrame(sync); }, { passive: true });
    slider.addEventListener("mouseenter", stop);
    slider.addEventListener("mouseleave", play);
    slider.addEventListener("focusin", stop);
    slider.addEventListener("focusout", play);

    sync();
    play();
  });
})();
