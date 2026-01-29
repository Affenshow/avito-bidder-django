// static/js/auth.js
document.addEventListener('DOMContentLoaded', function() {
    const passwordInput = document.querySelector('#id_password1');
    const helpTextBlock = document.querySelector('#password-help-block');
    
    if (!passwordInput || !helpTextBlock) return;

    // Показываем подсказки при фокусе на поле
    passwordInput.addEventListener('focus', () => {
        helpTextBlock.style.display = 'block';
    });

    // Скрываем подсказки, если убрать фокус
    passwordInput.addEventListener('blur', () => {
        helpTextBlock.style.display = 'none';
    });

    // Интерактивная проверка пароля при вводе
    passwordInput.addEventListener('input', () => {
        const password = passwordInput.value;
        const helpItems = helpTextBlock.querySelectorAll('li');

        helpItems.forEach(item => {
            let isValid = false;
            const text = item.textContent.toLowerCase();

            if (text.includes('8 символов')) {
                isValid = password.length >= 8;
            } else if (text.includes('не должен быть слишком похож')) {
                // Эту проверку сложно сделать на фронте, пропускаем
                isValid = true; 
            } else if (text.includes('не должен быть слишком простым')) {
                 // Эту тоже
                isValid = true;
            } else if (text.includes('не может состоять только из цифр')) {
                isValid = !/^\d+$/.test(password);
            }

            if (isValid) {
                item.classList.add('valid');
                item.classList.remove('invalid');
            } else {
                item.classList.add('invalid');
                item.classList.remove('valid');
            }
        });
    });
});
