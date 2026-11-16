/**
 * CCE Public Exam Score & Certificate Lookup — Client-side Script
 * Trung tâm Kỹ thuật Điện toán — ĐH Bách Khoa TP.HCM
 */
document.addEventListener("DOMContentLoaded", function () {
  // 1. Countdown timer for rate-limited state
  var countdownEl = document.getElementById("cd");
  var retryBtn = document.getElementById("retry");
  if (countdownEl && retryBtn) {
    var totalSeconds = parseInt(countdownEl.getAttribute("data-seconds"), 10) || 0;
    function pad(num) {
      return (num < 10 ? "0" : "") + num;
    }
    function updateCountdown() {
      if (totalSeconds > 0) {
        var minutes = Math.floor(totalSeconds / 60);
        var seconds = totalSeconds % 60;
        countdownEl.textContent = pad(minutes) + ":" + pad(seconds);
        return;
      }
      clearInterval(timerInterval);
      retryBtn.classList.remove("off", "pri");
      retryBtn.removeAttribute("aria-disabled");
      retryBtn.removeAttribute("tabindex");
      retryBtn.textContent = "Tra cứu lại";
      retryBtn.href = "/tra-cuu/?new=1";
    }
    var timerInterval = setInterval(function () {
      totalSeconds--;
      updateCountdown();
    }, 1000);
    updateCountdown();
  }

  // 2. Form submission state & validation auto-focus
  var lookupForm = document.getElementById("exam-lookup-form");
  var submitBtn = document.getElementById("submit-btn");
  if (lookupForm && submitBtn) {
    var errorSummary = document.getElementById("error-summary");
    if (errorSummary) {
      errorSummary.focus();
    } else {
      var firstInvalidInput = lookupForm.querySelector("[aria-invalid='true']");
      if (firstInvalidInput) {
        firstInvalidInput.focus();
      }
    }

    lookupForm.addEventListener("submit", function () {
      submitBtn.disabled = true;
      submitBtn.textContent = "Đang tra cứu…";
    });
  }

  // 3. Captcha refresh handler
  var refreshBtns = document.querySelectorAll(".js-captcha-refresh");
  refreshBtns.forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      fetch("/captcha/refresh/", {
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          var form = btn.closest("form") || document;
          var img = form.querySelector("img.captcha");
          var hiddenInput = form.querySelector("input[name$='_0']");
          if (img && data.image_url) {
            img.src = data.image_url;
          }
          if (hiddenInput && data.key) {
            hiddenInput.value = data.key;
          }
        })
        .catch(function (err) {
          console.error("Lỗi khi làm mới mã xác thực:", err);
        });
    });
  });

  // 4. In-page results PDF download (keeps candidate on page)
  var exportPdfBtn = document.getElementById("export-pdf-btn");
  if (exportPdfBtn) {
    exportPdfBtn.addEventListener("click", function (e) {
      var url = exportPdfBtn.getAttribute("href");
      var filename = exportPdfBtn.getAttribute("download") || "ket-qua-diem-chung-chi.pdf";
      if (!url || url === "#") return;

      e.preventDefault();
      var originalText = exportPdfBtn.innerHTML;
      exportPdfBtn.innerHTML = "Đang tải…";
      exportPdfBtn.style.pointerEvents = "none";

      fetch(url)
        .then(function (response) {
          if (!response.ok) {
            throw new Error("Không thể tải kết quả.");
          }
          return response.blob();
        })
        .then(function (blob) {
          var blobUrl = window.URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.style.display = "none";
          a.href = blobUrl;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          window.URL.revokeObjectURL(blobUrl);
          document.body.removeChild(a);
        })
        .catch(function (err) {
          alert("Không thể tải kết quả. Vui lòng thử lại sau.");
        })
        .finally(function () {
          exportPdfBtn.innerHTML = originalText;
          exportPdfBtn.style.pointerEvents = "";
        });
    });
  }

  // 5. Silent in-page certificate download (keeps candidate on page)
  var certDownloadBtns = document.querySelectorAll(".js-download-cert");
  certDownloadBtns.forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      var url = btn.getAttribute("href");
      var filename = btn.getAttribute("download") || "chung-chi.pdf";
      if (!url || url === "#") return;

      e.preventDefault();
      var originalText = btn.innerHTML;
      btn.innerHTML = "Đang tải…";
      btn.style.pointerEvents = "none";

      fetch(url)
        .then(function (response) {
          if (!response.ok) {
            throw new Error("Không thể tải chứng chỉ.");
          }
          return response.blob();
        })
        .then(function (blob) {
          var blobUrl = window.URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.style.display = "none";
          a.href = blobUrl;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          window.URL.revokeObjectURL(blobUrl);
          document.body.removeChild(a);
        })
        .catch(function (err) {
          alert("Chứng chỉ không còn hiệu lực hoặc phiên tải đã hết hạn. Vui lòng tải lại trang.");
        })
        .finally(function () {
          btn.innerHTML = originalText;
          btn.style.pointerEvents = "";
        });
    });
  });
});
