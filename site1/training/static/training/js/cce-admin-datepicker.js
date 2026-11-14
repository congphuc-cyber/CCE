/**
 * CCE Modern Datepicker matching CCE Design System
 * Header: < [Month v] [Year v] >
 * Weekdays: Su Mo Tu We Th Fr Sa
 * Days: 1 .. 31, prev/next month in light gray, selected day in black pill
 */
(function() {
  'use strict';

  const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

  let popoverEl = null;
  let activeInput = null;
  let viewYear = 2026;
  let viewMonth = 10; // November (0-indexed: 10)
  let selectedDate = null;
  let mode = 'days'; // 'days' | 'months' | 'years'

  function pad(n) {
    return n < 10 ? '0' + n : String(n);
  }

  function formatISO(y, m, d) {
    return `${y}-${pad(m + 1)}-${pad(d)}`;
  }

  function parseISO(str) {
    if (!str) return null;
    const parts = str.split('-');
    if (parts.length !== 3) return null;
    const y = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10) - 1;
    const d = parseInt(parts[2], 10);
    if (isNaN(y) || isNaN(m) || isNaN(d)) return null;
    return new Date(y, m, d);
  }

  function createPopover() {
    if (popoverEl) return popoverEl;

    popoverEl = document.createElement('div');
    popoverEl.id = 'cce-custom-calendar-popover';
    popoverEl.className = 'cce-cal-popover';
    popoverEl.setAttribute('role', 'dialog');
    popoverEl.setAttribute('aria-modal', 'true');

    popoverEl.addEventListener('click', (e) => {
      e.stopPropagation();
    });

    document.body.appendChild(popoverEl);

    document.addEventListener('mousedown', (e) => {
      if (popoverEl && popoverEl.classList.contains('is-visible')) {
        if (!popoverEl.contains(e.target) && e.target !== activeInput && (!activeInput || !activeInput.contains(e.target))) {
          closePopover();
        }
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && popoverEl && popoverEl.classList.contains('is-visible')) {
        closePopover();
      }
    });

    return popoverEl;
  }

  function openPopover(input) {
    activeInput = input;
    createPopover();

    const currentVal = parseISO(input.value);
    const today = new Date();

    if (currentVal) {
      selectedDate = currentVal;
      viewYear = currentVal.getFullYear();
      viewMonth = currentVal.getMonth();
    } else {
      selectedDate = null;
      viewYear = today.getFullYear();
      viewMonth = today.getMonth();
    }

    mode = 'days';
    render();
    positionPopover();
    popoverEl.classList.add('is-visible');
  }

  function closePopover() {
    if (popoverEl) {
      popoverEl.classList.remove('is-visible');
    }
    mode = 'days';
    activeInput = null;
  }

  function positionPopover() {
    if (!activeInput || !popoverEl) return;

    const rect = activeInput.getBoundingClientRect();
    const popWidth = 310;
    const popHeight = 360;

    let left = rect.left + window.scrollX;
    let top = rect.bottom + window.scrollY + 6;

    if (left + popWidth > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - popWidth - 12 + window.scrollX);
    }

    const viewportBottom = window.innerHeight + window.scrollY;
    if (top + popHeight > viewportBottom && rect.top > popHeight) {
      top = rect.top + window.scrollY - popHeight - 6;
    }

    popoverEl.style.left = `${left}px`;
    popoverEl.style.top = `${top}px`;
  }

  function render() {
    if (!popoverEl) return;

    if (mode === 'months') {
      renderMonthsView();
    } else if (mode === 'years') {
      renderYearsView();
    } else {
      renderDaysView();
    }
  }

  function renderDaysView() {
    const today = new Date();
    const todayY = today.getFullYear();
    const todayM = today.getMonth();
    const todayD = today.getDate();

    // Sunday = 0, Mo = 1, Tu = 2, We = 3, Th = 4, Fr = 5, Sa = 6
    const firstDayIndex = new Date(viewYear, viewMonth, 1).getDay();
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();

    let html = `
      <div class="cce-cal-top-header">
        <button type="button" class="cce-cal-arrow-btn cce-cal-prev-btn" aria-label="Tháng trước">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
        </button>
        <div class="cce-cal-dropdown-selectors">
          <button type="button" class="cce-cal-select-trigger cce-cal-month-trigger">
            <span>${MONTH_SHORT[viewMonth]}</span>
            <svg class="cce-cal-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
          </button>
          <button type="button" class="cce-cal-select-trigger cce-cal-year-trigger">
            <span>${viewYear}</span>
            <svg class="cce-cal-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
          </button>
        </div>
        <button type="button" class="cce-cal-arrow-btn cce-cal-next-btn" aria-label="Tháng sau">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
        </button>
      </div>

      <div class="cce-cal-week-labels">
        ${WEEKDAYS.map(w => `<span class="cce-cal-week-label">${w}</span>`).join('')}
      </div>

      <div class="cce-cal-grid-cells">
    `;

    // Days from previous month
    for (let i = firstDayIndex - 1; i >= 0; i--) {
      const d = daysInPrevMonth - i;
      html += `<span class="cce-cal-cell cce-cal-outside-day">${d}</span>`;
    }

    // Days in current month
    for (let d = 1; d <= daysInMonth; d++) {
      const isSelected = selectedDate && (
        viewYear === selectedDate.getFullYear() &&
        viewMonth === selectedDate.getMonth() &&
        d === selectedDate.getDate()
      );
      const isToday = (viewYear === todayY && viewMonth === todayM && d === todayD);

      const classes = ['cce-cal-cell', 'cce-cal-current-day'];
      if (isSelected) classes.push('is-selected');
      if (isToday && !isSelected) classes.push('is-today');

      const iso = formatISO(viewYear, viewMonth, d);
      html += `<button type="button" class="${classes.join(' ')}" data-date="${iso}">${d}</button>`;
    }

    // Days for next month to complete rows
    const totalCells = firstDayIndex + daysInMonth;
    const nextDays = (totalCells % 7 === 0) ? 0 : 7 - (totalCells % 7);
    for (let d = 1; d <= nextDays; d++) {
      html += `<span class="cce-cal-cell cce-cal-outside-day">${d}</span>`;
    }

    html += `</div>`;

    popoverEl.innerHTML = html;

    // Attach listeners
    popoverEl.querySelector('.cce-cal-prev-btn').addEventListener('click', () => {
      viewMonth--;
      if (viewMonth < 0) {
        viewMonth = 11;
        viewYear--;
      }
      renderDaysView();
    });

    popoverEl.querySelector('.cce-cal-next-btn').addEventListener('click', () => {
      viewMonth++;
      if (viewMonth > 11) {
        viewMonth = 0;
        viewYear++;
      }
      renderDaysView();
    });

    popoverEl.querySelector('.cce-cal-month-trigger').addEventListener('click', () => {
      mode = 'months';
      render();
    });

    popoverEl.querySelector('.cce-cal-year-trigger').addEventListener('click', () => {
      mode = 'years';
      render();
    });

    popoverEl.querySelectorAll('.cce-cal-current-day[data-date]').forEach(btn => {
      btn.addEventListener('click', () => {
        const val = btn.getAttribute('data-date');
        if (activeInput) {
          activeInput.value = val;
          activeInput.dispatchEvent(new Event('input', { bubbles: true }));
          activeInput.dispatchEvent(new Event('change', { bubbles: true }));
        }
        closePopover();
      });
    });
  }

  function renderMonthsView() {
    let html = `
      <div class="cce-cal-top-header">
        <span class="cce-cal-nav-title">Chọn tháng</span>
        <button type="button" class="cce-cal-close-mode-btn">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </button>
      </div>
      <div class="cce-cal-picker-grid">
    `;

    MONTH_SHORT.forEach((m, idx) => {
      const isCurrent = idx === viewMonth;
      html += `<button type="button" class="cce-cal-picker-btn ${isCurrent ? 'is-selected' : ''}" data-month="${idx}">${m}</button>`;
    });

    html += `</div>`;
    popoverEl.innerHTML = html;

    popoverEl.querySelector('.cce-cal-close-mode-btn').addEventListener('click', () => {
      mode = 'days';
      render();
    });

    popoverEl.querySelectorAll('.cce-cal-picker-btn[data-month]').forEach(btn => {
      btn.addEventListener('click', () => {
        viewMonth = parseInt(btn.getAttribute('data-month'), 10);
        mode = 'days';
        render();
      });
    });
  }

  function renderYearsView() {
    const currentYear = viewYear;
    const startYear = Math.floor(currentYear / 12) * 12;
    const endYear = startYear + 11;

    let html = `
      <div class="cce-cal-top-header">
        <button type="button" class="cce-cal-arrow-btn cce-cal-prev-year-block">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
        </button>
        <span class="cce-cal-nav-title">${startYear} - ${endYear}</span>
        <button type="button" class="cce-cal-arrow-btn cce-cal-next-year-block">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
        </button>
      </div>
      <div class="cce-cal-picker-grid">
    `;

    for (let y = startYear; y <= endYear; y++) {
      const isCurrent = y === viewYear;
      html += `<button type="button" class="cce-cal-picker-btn ${isCurrent ? 'is-selected' : ''}" data-year="${y}">${y}</button>`;
    }

    html += `</div>`;
    popoverEl.innerHTML = html;

    popoverEl.querySelector('.cce-cal-prev-year-block').addEventListener('click', () => {
      viewYear -= 12;
      renderYearsView();
    });

    popoverEl.querySelector('.cce-cal-next-year-block').addEventListener('click', () => {
      viewYear += 12;
      renderYearsView();
    });

    popoverEl.querySelectorAll('.cce-cal-picker-btn[data-year]').forEach(btn => {
      btn.addEventListener('click', () => {
        viewYear = parseInt(btn.getAttribute('data-year'), 10);
        mode = 'days';
        render();
      });
    });
  }

  function initDatepicker() {
    const inputs = document.querySelectorAll('input[type="date"], input#id_date_of_birth');
    inputs.forEach(input => {
      if (input.dataset.cceAdminDatePicker) return;
      input.dataset.cceAdminDatePicker = 'true';

      // Attach click and focus
      input.addEventListener('click', (e) => {
        e.preventDefault();
        openPopover(input);
      });

      input.addEventListener('focus', () => {
        openPopover(input);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDatepicker);
  } else {
    initDatepicker();
  }

  window.addEventListener('resize', () => {
    if (popoverEl && popoverEl.classList.contains('is-visible')) {
      positionPopover();
    }
  });

  window.addEventListener('scroll', () => {
    if (popoverEl && popoverEl.classList.contains('is-visible')) {
      positionPopover();
    }
  }, true);

  window.CCEAdminDatePicker = {
    init: initDatepicker,
    open: openPopover,
    close: closePopover
  };
})();
