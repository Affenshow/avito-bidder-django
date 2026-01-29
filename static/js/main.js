// static/js/main.js

const sidebar = document.querySelector('#sidebar');
const mobileNavToggle = document.querySelector('.mobile-nav-toggle');
const overlay = document.querySelector('.sidebar-overlay');

mobileNavToggle.addEventListener('click', () => {
    const isVisible = sidebar.getAttribute('data-visible') === 'true';

    if (isVisible) {
        // Закрываем меню
        sidebar.setAttribute('data-visible', 'false');
        mobileNavToggle.setAttribute('aria-expanded', 'false');
        overlay.setAttribute('data-visible', 'false');
    } else {
        // Открываем меню
        sidebar.setAttribute('data-visible', 'true');
        mobileNavToggle.setAttribute('aria-expanded', 'true');
        overlay.setAttribute('data-visible', 'true');
    }
});

// Закрывать меню по клику на оверлей
overlay.addEventListener('click', () => {
    sidebar.setAttribute('data-visible', 'false');
    mobileNavToggle.setAttribute('aria-expanded', 'false');
    overlay.setAttribute('data-visible', 'false');
});



