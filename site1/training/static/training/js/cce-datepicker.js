/**
 * CCE Bách Khoa Custom Datepicker
 * Modern, accessible, zero-dependency Vietnamese calendar popover for input[type="date"]
 */
(function() {
  'use strict';

  const MONTH_NAMES = [
    'Tháng 1', 'Tháng 2', 'Tháng 3', 'Tháng 4',
    'Tháng 5', 'Tháng 6', 'Tháng 7', 'Tháng 8',
    'Tháng 9', 'Tháng 10', 'Tháng 11', 'Tháng 12'
  ];

  const WEEKDAY_NAMES = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];

  let popoverEl = null;
  let activeInput = null;
  let viewYear = null;
  let viewMonth = null;
  let selectedDate = null;

  function pad(n) {
    return n < 10 ? '0' + n : String(n);
  }

  function formatISODate(y, m, d) {
    return `${y}-${pad(m + 1)}-${pad(d)}`;
  }

  function parseISODate(str) {
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
    popoverEl.id = 'cce-datepicker-popover';
    popoverEl.className = 'cce-datepicker-popover';
    popoverEl.setAttribute('role', 'dialog');
    popoverEl.setAttribute('aria-modal', 'true');
    popoverEl.setAttribute('aria-label', 'Chọn ngày');

    popoverEl.addEventListener('click', (e) => {
      e.stopPropagation();
    });

    document.body.appendChild(popoverEl);

    // Close on click outside
    document.addEventListener('mousedown', (e) => {
      if (popoverEl && popoverEl.classList.contains('is-visible')) {
        if (!popoverEl.contains(e.target) && e.target !== activeInput && (!activeInput || !activeInput.contains(e.target))) {
          closePopover();
        }
      }
    });

    // Close on Escape
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

    const currentVal = parseISODate(input.value);
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

    renderCalendar();
    positionPopover();
    popoverEl.classList.add('is-visible');
  }

  function closePopover() {
    if (popoverEl) {
      popoverEl.classList.remove('is-visible');
    }
    activeInput = null;
  }

  function positionPopover() {
    if (!activeInput || !popoverEl) return;

    const rect = activeInput.getBoundingClientRect();
    const popWidth = 300;
    const popHeight = 340;

    let left = rect.left + window.scrollX;
    let top = rect.bottom + window.scrollY + 6;

    // Boundary check right
    if (left + popWidth > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - popWidth - 12 + window.scrollX);
    }

    // Boundary check bottom
    const viewportBottom = window.innerHeight + window.scrollY;
    if (top + popHeight > viewportBottom && rect.top > popHeight) {
      top = rect.top + window.scrollY - popHeight - 6;
    }

    popoverEl.style.left = `${left}px`;
    popoverEl.style.top = `${top}px`;
  }

  function renderCalendar() {
    if (!popoverEl) return;

    const today = new Date();
    const todayYear = today.getFullYear();
    const todayMonth = today.getMonth();
    const todayDate = today.getDate();

    // Monday is index 0
    const firstDayIndex = (new Date(viewYear, viewMonth, 1).getDay() + 6) % 7;
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();

    let html = `
      <div class="cce-cal-header">
        <button type="button" class="cce-cal-nav-btn cce-cal-prev" aria-label="Tháng trước">
          <span class="material-symbols-outlined">chevron_left</span>
        </button>
        <div class="cce-cal-title">
          <span class="cce-cal-month">${MONTH_NAMES[viewMonth]}</span>
          <span class="cce-cal-year">${viewYear}</span>
        </div>
        <button type="button" class="cce-cal-nav-btn cce-cal-next" aria-label="Tháng sau">
          <span class="material-symbols-outlined">chevron_right</span>
        </button>
      </div>

      <div class="cce-cal-weekdays">
        ${WEEKDAY_NAMES.map(d => `<div class="cce-cal-weekday ${d === 'CN' ? 'is-sunday' : ''}">${d}</div>`).join('')}
      </div>

      <div class="cce-cal-grid">
    `;

    // Days from previous month
    for (let i = firstDayIndex - 1; i >= 0; i--) {
      const d = daysInPrevMonth - i;
      html += `<div class="cce-cal-day is-other-month">${d}</div>`;
    }

    // Days in current month
    for (let d = 1; d <= daysInMonth; d++) {
      const isToday = (viewYear === todayYear && viewMonth === todayMonth && d === todayDate);
      const isSelected = selectedDate && (
        viewYear === selectedDate.getFullYear() &&
        viewMonth === selectedDate.getMonth() &&
        d === selectedDate.getDate()
      );

      const classes = ['cce-cal-day'];
      if (isToday) classes.push('is-today');
      if (isSelected) classes.push('is-selected');

      const isoDate = formatISODate(viewYear, viewMonth, d);
      html += `<button type="button" class="${classes.join(' ')}" data-date="${isoDate}">${d}</button>`;
    }

    // Fill remaining cells for grid balance
    const totalCells = firstDayIndex + daysInMonth;
    const nextDays = (totalCells % 7 === 0) ? 0 : 7 - (totalCells % 7);
    for (let d = 1; d <= nextDays; d++) {
      html += `<div class="cce-cal-day is-other-month">${d}</div>`;
    }

    html += `
      </div>

      <div class="cce-cal-footer">
        <button type="button" class="cce-cal-btn-today">Hôm nay</button>
        <button type="button" class="cce-cal-btn-clear">Xóa</button>
        <button type="button" class="cce-cal-btn-close">Đóng</button>
      </div>
    `;

    popoverEl.innerHTML = html;

    // Attach internal event handlers
    popoverEl.querySelector('.cce-cal-prev').addEventListener('click', () => {
      viewMonth--;
      if (viewMonth < 0) {
        viewMonth = 11;
        viewYear--;
      }
      renderCalendar();
    });

    popoverEl.querySelector('.cce-cal-next').addEventListener('click', () => {
      viewMonth++;
      if (viewMonth > 11) {
        viewMonth = 0;
        viewYear++;
      }
      renderCalendar();
    });

    popoverEl.querySelectorAll('.cce-cal-day[data-date]').forEach(btn => {
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

    popoverEl.querySelector('.cce-cal-btn-today').addEventListener('click', () => {
      const now = new Date();
      const val = formatISODate(now.getFullYear(), now.getMonth(), now.getDate());
      if (activeInput) {
        activeInput.value = val;
        activeInput.dispatchEvent(new Event('input', { bubbles: true }));
        activeInput.dispatchEvent(new Event('change', { bubbles: true }));
      }
      closePopover();
    });

    popoverEl.querySelector('.cce-cal-btn-clear').addEventListener('click', () => {
      if (activeInput) {
        activeInput.value = '';
        activeInput.dispatchEvent(new Event('input', { bubbles: true }));
        activeInput.dispatchEvent(new Event('change', { bubbles: true }));
      }
      closePopover();
    });

    popoverEl.querySelector('.cce-cal-btn-close').addEventListener('click', () => {
      closePopover();
    });
  }

  function initDatepickers() {
    const inputs = document.querySelectorAll('input[type="date"]');
    inputs.forEach(input => {
      if (input.dataset.cceCustomPicker) return;
      input.dataset.cceCustomPicker = 'true';

      // Intercept click and focus
      input.addEventListener('click', (e) => {
        e.preventDefault();
        openPopover(input);
      });

      // Prevent native picker via showPicker if available
      input.addEventListener('focus', () => {
        openPopover(input);
      });
    });
  }

  // Auto-init on DOMContentLoaded & window resize/scroll
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDatepickers);
  } else {
    initDatepickers();
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

  // Expose helper globally
  window.CCEDatepicker = {
    init: initDatepickers,
    open: openPopover,
    close: closePopover
  };
})();
