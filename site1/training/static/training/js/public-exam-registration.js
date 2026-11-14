(() => {
  const certificateType = document.getElementById("id_cert_type");
  const examPeriod = document.getElementById("id_exam_period");
  const examPeriodHelp = document.getElementById("id_exam_period_help");
  if (!certificateType || !examPeriod || !examPeriodHelp) {
    return;
  }

  const placeholder = examPeriod.options[0].cloneNode(true);
  const periods = Array.from(examPeriod.options)
    .slice(1)
    .map((option) => option.cloneNode(true));

  function updateExamPeriods(resetSelection) {
    const certificateId = certificateType.value;
    const currentPeriod = resetSelection ? "" : examPeriod.value;
    const matchingPeriods = periods.filter(
      (option) => option.dataset.certType === certificateId,
    );

    examPeriod.replaceChildren(placeholder.cloneNode(true));
    matchingPeriods.forEach((option) => examPeriod.add(option.cloneNode(true)));
    examPeriod.disabled = !certificateId;

    if (matchingPeriods.some((option) => option.value === currentPeriod)) {
      examPeriod.value = currentPeriod;
    }

    if (!certificateId) {
      examPeriodHelp.textContent =
        "Chọn loại chứng chỉ trước để xem các đợt thi tương ứng.";
    } else if (matchingPeriods.length === 0) {
      examPeriodHelp.textContent =
        "Hiện không có đợt thi khả dụng cho loại chứng chỉ này.";
    } else {
      examPeriodHelp.textContent = "Chọn một đợt thi thuộc loại chứng chỉ đã chọn.";
    }
  }

  certificateType.addEventListener("change", () => updateExamPeriods(true));
  updateExamPeriods(false);

  const picker = document.querySelector("[data-public-date-picker]");
  if (!picker) {
    return;
  }

  const input = picker.querySelector(".public-date-input");
  const toggle = picker.querySelector("[data-date-toggle]");
  const calendar = picker.querySelector(".public-date-calendar");
  const title = picker.querySelector("[data-date-title]");
  const days = picker.querySelector("[data-date-days]");
  const monthSelect = picker.querySelector("[data-date-month]");
  const yearSelect = picker.querySelector("[data-date-year]");
  const previousMonthButton = picker.querySelector("[data-date-previous]");
  const nextMonthButton = picker.querySelector("[data-date-next]");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let visibleYear = today.getFullYear();
  let visibleMonth = today.getMonth();

  for (let month = 0; month < 12; month += 1) {
    const option = document.createElement("option");
    option.value = String(month);
    option.textContent = `Tháng ${month + 1}`;
    monthSelect.append(option);
  }
  for (let year = today.getFullYear(); year >= 1900; year -= 1) {
    const option = document.createElement("option");
    option.value = String(year);
    option.textContent = String(year);
    yearSelect.append(option);
  }

  function toDateKey(date) {
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${date.getFullYear()}-${month}-${day}`;
  }

  function parseDate(value) {
    const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value || "");
    if (!match) {
      return null;
    }
    const date = new Date(Number(match[3]), Number(match[1]) - 1, Number(match[2]));
    return date.getFullYear() === Number(match[3])
      && date.getMonth() === Number(match[1]) - 1
      && date.getDate() === Number(match[2])
      ? date
      : null;
  }

  function formatDate(date) {
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${month}/${day}/${date.getFullYear()}`;
  }

  function renderCalendar() {
    const parsedSelectedDate = parseDate(input.value);
    const selectedDate = parsedSelectedDate && parsedSelectedDate <= today
      ? parsedSelectedDate
      : null;
    const firstOfMonth = new Date(visibleYear, visibleMonth, 1);
    const firstCell = new Date(visibleYear, visibleMonth, 1 - firstOfMonth.getDay());

    title.textContent = `Tháng ${visibleMonth + 1} ${visibleYear}`;
    monthSelect.value = String(visibleMonth);
    yearSelect.value = String(visibleYear);
    Array.from(monthSelect.options).forEach((option) => {
      option.disabled = (
        visibleYear === today.getFullYear()
        && Number(option.value) > today.getMonth()
      );
    });
    nextMonthButton.disabled = (
      visibleYear === today.getFullYear()
      && visibleMonth >= today.getMonth()
    );
    days.replaceChildren();

    const daysInMonth = new Date(visibleYear, visibleMonth + 1, 0).getDate();
    const cellCount = Math.ceil((firstOfMonth.getDay() + daysInMonth) / 7) * 7;
    for (let index = 0; index < cellCount; index += 1) {
      const cellDate = new Date(
        firstCell.getFullYear(),
        firstCell.getMonth(),
        firstCell.getDate() + index,
      );
      const dayButton = document.createElement("button");
      const dateKey = toDateKey(cellDate);
      dayButton.type = "button";
      dayButton.className = "public-date-day";
      dayButton.dataset.date = dateKey;
      dayButton.textContent = String(cellDate.getDate());
      dayButton.setAttribute(
        "aria-label",
        cellDate.toLocaleDateString("vi-VN", {
          weekday: "long",
          day: "numeric",
          month: "long",
          year: "numeric",
        }),
      );
      dayButton.setAttribute(
        "aria-pressed",
        String(Boolean(selectedDate && toDateKey(selectedDate) === dateKey)),
      );

      if (cellDate.getMonth() !== visibleMonth) {
        dayButton.classList.add("is-outside-month");
      }
      if (cellDate.getDay() === 0) {
        dayButton.classList.add("is-sunday");
      }
      if (toDateKey(cellDate) === toDateKey(today)) {
        dayButton.classList.add("is-today");
      }
      if (selectedDate && toDateKey(selectedDate) === dateKey) {
        dayButton.classList.add("is-selected");
      }
      if (cellDate > today) {
        dayButton.disabled = true;
      }

      dayButton.addEventListener("click", () => {
        input.value = formatDate(cellDate);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        closeCalendar();
      });
      days.append(dayButton);
    }
  }

  function setVisibleMonth(year, month, focusSelector) {
    const nextMonth = new Date(year, month, 1);
    const currentMonth = new Date(today.getFullYear(), today.getMonth(), 1);
    if (nextMonth > currentMonth) {
      return;
    }
    visibleYear = nextMonth.getFullYear();
    visibleMonth = nextMonth.getMonth();
    renderCalendar();
    if (focusSelector) {
      const focusTarget = calendar.querySelector(focusSelector);
      (focusTarget.disabled ? previousMonthButton : focusTarget).focus();
    }
  }

  function openCalendar() {
    const parsedSelectedDate = parseDate(input.value);
    const selectedDate = parsedSelectedDate && parsedSelectedDate <= today
      ? parsedSelectedDate
      : today;
    visibleYear = selectedDate.getFullYear();
    visibleMonth = selectedDate.getMonth();
    renderCalendar();
    calendar.hidden = false;
    picker.classList.remove("opens-up");
    toggle.setAttribute("aria-expanded", "true");
    const overflow = calendar.getBoundingClientRect().bottom - window.innerHeight + 12;
    if (overflow > 0) {
      const inputTop = input.getBoundingClientRect().top;
      window.scrollBy(0, Math.min(overflow, Math.max(0, inputTop - 8)));
    }
    const calendarBounds = calendar.getBoundingClientRect();
    if (
      calendarBounds.bottom > window.innerHeight - 12
      && calendarBounds.top >= calendar.offsetHeight + 12
    ) {
      picker.classList.add("opens-up");
    }
    focusCalendarDate(selectedDate);
  }

  function focusCalendarDate(date) {
    const selectedButton = days.querySelector(
      `[data-date="${toDateKey(date)}"]:not(:disabled)`,
    );
    (selectedButton || days.querySelector("button:not(:disabled)")).focus();
  }

  function updateVisibleMonth(year, month) {
    const currentMonth = new Date(today.getFullYear(), today.getMonth(), 1);
    if (new Date(year, month, 1) > currentMonth) {
      year = today.getFullYear();
      month = today.getMonth();
    }
    setVisibleMonth(year, month);
  }

  function updateVisibleMonthFromSelects(event) {
    event.currentTarget.focus();
    updateVisibleMonth(Number(yearSelect.value), Number(monthSelect.value));
  }

  function closeCalendar(restoreFocus = true) {
    calendar.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    if (restoreFocus) {
      toggle.focus();
    }
  }

  toggle.addEventListener("click", () => {
    if (calendar.hidden) {
      openCalendar();
    } else {
      closeCalendar();
    }
  });
  input.addEventListener("click", openCalendar);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " " || event.key === "ArrowDown") {
      event.preventDefault();
      openCalendar();
    }
  });
  monthSelect.addEventListener("change", updateVisibleMonthFromSelects);
  yearSelect.addEventListener("change", updateVisibleMonthFromSelects);
  previousMonthButton.addEventListener("click", () => {
    setVisibleMonth(visibleYear, visibleMonth - 1, "[data-date-previous]");
  });
  nextMonthButton.addEventListener("click", () => {
    setVisibleMonth(visibleYear, visibleMonth + 1, "[data-date-next]");
  });
  calendar.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeCalendar();
      return;
    }

    const activeDay = event.target.closest("[data-date]");
    const dayOffsets = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
    };
    if (activeDay && dayOffsets[event.key]) {
      event.preventDefault();
      const [year, month, day] = activeDay.dataset.date.split("-").map(Number);
      const nextDate = new Date(year, month - 1, day + dayOffsets[event.key]);
      if (nextDate > today) {
        return;
      }
      setVisibleMonth(
        nextDate.getFullYear(),
        nextDate.getMonth(),
        `[data-date="${toDateKey(nextDate)}"]`,
      );
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!picker.contains(event.target) && !calendar.hidden) {
      closeCalendar(false);
    }
  });
  picker.addEventListener("focusout", () => {
    window.requestAnimationFrame(() => {
      if (!picker.contains(document.activeElement) && !calendar.hidden) {
        closeCalendar(false);
      }
    });
  });
})();
