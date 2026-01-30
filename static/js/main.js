// static/js/main.js

document.addEventListener('DOMContentLoaded', function() {

    // --- МОДУЛЬ 1: Мобильное меню (работает на всех страницах) ---
    const sidebar = document.querySelector('#sidebar');
    const mobileNavToggle = document.querySelector('.mobile-nav-toggle');
    const overlay = document.querySelector('.sidebar-overlay');

    if (mobileNavToggle && sidebar && overlay) {
        mobileNavToggle.addEventListener('click', () => {
            const isVisible = sidebar.getAttribute('data-visible') === 'true';
            sidebar.setAttribute('data-visible', !isVisible);
            mobileNavToggle.setAttribute('aria-expanded', !isVisible);
            overlay.setAttribute('data-visible', !isVisible);
        });
        overlay.addEventListener('click', () => {
            sidebar.setAttribute('data-visible', 'false');
            mobileNavToggle.setAttribute('aria-expanded', 'false');
            overlay.setAttribute('data-visible', 'false');
        });
    }

    // --- МОДУЛЬ 2: Редактор расписания (работает только на /add/ и /edit/) ---
    const scheduleContainer = document.getElementById('schedule-container');
    if (scheduleContainer) {
        const addIntervalBtn = document.getElementById('add-schedule-interval');
        const scheduleInput = document.getElementById('id_schedule_data');

        function updateScheduleInput() {
            const intervals = [];
            scheduleContainer.querySelectorAll('.schedule-interval').forEach(row => {
                const start = row.querySelector('input[name="start-time"]').value;
                const end = row.querySelector('input[name="end-time"]').value;
                if (start && end) intervals.push({ start, end });
            });
            scheduleInput.value = JSON.stringify(intervals);
        }

        function createIntervalRow(startValue = '09:00', endValue = '18:00') {
            const div = document.createElement('div');
            div.classList.add('schedule-interval');
            div.innerHTML = `<span>с</span><input type="time" name="start-time" value="${startValue}"><span>до</span><input type="time" name="end-time" value="${endValue}"><button type="button" class="remove-interval-btn" title="Удалить">×</button>`;
            scheduleContainer.appendChild(div);
            div.querySelector('.remove-interval-btn').addEventListener('click', () => {
                div.remove();
                updateScheduleInput();
            });
            div.querySelectorAll('input[type="time"]').forEach(input => input.addEventListener('change', updateScheduleInput));
        }

        try {
            const initialData = JSON.parse(scheduleInput.value || '[]');
            if (Array.isArray(initialData)) {
                initialData.forEach(interval => createIntervalRow(interval.start, interval.end));
            }
        } catch (e) {
            console.error("Не удалось распарсить расписание:", e);
        }
        
        addIntervalBtn.addEventListener('click', () => {
            createIntervalRow();
            updateScheduleInput();
        });
    }

    // --- МОДУЛЬ 3: Ползунок позиций (работает только на /add/ и /edit/) ---
    const slider = document.getElementById('position-slider');
    if (slider) {
        const minInput = document.getElementById('id_target_position_min');
        const maxInput = document.getElementById('id_target_position_max');

        // Проверяем, что все нужные элементы существуют
        if (minInput && maxInput && typeof noUiSlider !== 'undefined') {
            noUiSlider.create(slider, {
                start: [Number(minInput.value) || 1, Number(maxInput.value) || 10],
                connect: true,
                step: 1,
                range: { 'min': 1, 'max': 50 },
                tooltips: [true, true],
                format: {
                    to: value => Math.round(value),
                    from: value => Math.round(value)
                }
            });

            slider.noUiSlider.on('update', (values) => {
                minInput.value = values[0];
                maxInput.value = values[1];
            });
        } else if (typeof noUiSlider === 'undefined') {
            console.error('Библиотека noUiSlider не подключена!');
        }
    }

});
