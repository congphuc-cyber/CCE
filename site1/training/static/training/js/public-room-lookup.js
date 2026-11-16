(() => {
  // 1. Countdown Timer for Blocked / Rate-Limited State
  const cdElement = document.getElementById("cd");
  const retryBtn = document.getElementById("retry");
  if (cdElement && retryBtn) {
    let totalSeconds = parseInt(cdElement.getAttribute("data-seconds"), 10) || 0;
    const targetTime = Date.now() + totalSeconds * 1000;

    function pad(n) {
      return n < 10 ? "0" + n : String(n);
    }

    function updateTimer() {
      const remainingMs = targetTime - Date.now();
      const remainingSec = Math.max(0, Math.ceil(remainingMs / 1000));

      if (remainingSec > 0) {
        const mins = Math.floor(remainingSec / 60);
        const secs = remainingSec % 60;
        cdElement.textContent = pad(mins) + ":" + pad(secs);
      } else {
        clearInterval(timerInterval);
        retryBtn.classList.remove("off", "pri");
        retryBtn.removeAttribute("aria-disabled");
        retryBtn.removeAttribute("tabindex");
        retryBtn.textContent = "Tra cứu lại";

        // Screen reader announcement once when timer finishes
        const announcer = document.createElement("div");
        announcer.setAttribute("role", "status");
        announcer.setAttribute("class", "public-sr-only");
        announcer.textContent = "Thời gian chờ đã kết thúc. Bạn có thể tra cứu lại.";
        document.body.appendChild(announcer);
        setTimeout(() => announcer.remove(), 4000);
      }
    }

    const timerInterval = setInterval(updateTimer, 1000);
    updateTimer();
  }

  // 2. Form submission lock & Accessible error auto-focus
  const form = document.getElementById("room-lookup-form");
  const submitBtn = document.getElementById("submit-btn");
  if (form && submitBtn) {
    form.addEventListener("submit", () => {
      submitBtn.disabled = true;
      submitBtn.textContent = "Đang tra cứu…";
    });
  }

  const errorFields = document.querySelectorAll(".f.has-error input");
  const errorSummary = document.getElementById("error-summary");
  if (errorFields.length > 0) {
    errorFields[0].focus();
  } else if (errorSummary) {
    errorSummary.focus();
  }

  // 3. Captcha Refresh via AJAX
  const refreshBtns = document.querySelectorAll(".js-captcha-refresh");
  refreshBtns.forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const res = await fetch("/captcha/refresh/", {
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (res.ok) {
          const data = await res.json();
          const capImg = document.querySelector("img.captcha");
          const keyInput = document.querySelector('input[name="captcha_0"]');
          const textInput = document.querySelector('input[name="captcha_1"]');
          if (capImg && data.image_url) {
            capImg.src = data.image_url;
          }
          if (keyInput && data.key) {
            keyInput.value = data.key;
          }
          if (textInput) {
            textInput.value = "";
            textInput.focus();
          }
        }
      } catch (err) {
        console.error("Captcha refresh error:", err);
      }
    });
  });

  // 4. Print & Background PDF Download for Results Screen
  const printBtn = document.getElementById("print-schedule-btn");
  if (printBtn) {
    printBtn.addEventListener("click", (e) => {
      e.preventDefault();
      window.print();
    });
  }

  const pdfBtn = document.getElementById("export-pdf-btn");
  if (pdfBtn) {
    let cachedBlob = null;
    pdfBtn.addEventListener("click", async (e) => {
      e.preventDefault();

      if (cachedBlob) {
        const blobUrl = window.URL.createObjectURL(cachedBlob);
        const link = document.createElement("a");
        link.href = blobUrl;
        link.download = "lich-thi-va-phong-thi.pdf";
        document.body.appendChild(link);
        link.click();
        window.URL.revokeObjectURL(blobUrl);
        link.remove();
        return;
      }

      try {
        const response = await fetch(pdfBtn.href);
        if (response.ok) {
          cachedBlob = await response.blob();
          const blobUrl = window.URL.createObjectURL(cachedBlob);
          const link = document.createElement("a");
          link.href = blobUrl;
          link.download = "lich-thi-va-phong-thi.pdf";
          document.body.appendChild(link);
          link.click();
          window.URL.revokeObjectURL(blobUrl);
          link.remove();
        }
      } catch (err) {
        // Fallback with hidden iframe to avoid page reload/redirect
        const iframe = document.createElement("iframe");
        iframe.style.display = "none";
        iframe.src = pdfBtn.href;
        document.body.appendChild(iframe);
        setTimeout(() => iframe.remove(), 5000);
      }
    });
  }
})();
