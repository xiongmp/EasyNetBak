(function () {
  'use strict';

  const form = document.getElementById('passwordForm');
  if (!form) return;

  const newPassword = document.getElementById('new_password');
  const confirmPassword = document.getElementById('confirm_password');
  const strengthBar = document.getElementById('strengthBar');
  const strengthText = document.getElementById('strengthText');
  const matchFeedback = document.getElementById('matchFeedback');

  function updateStrength() {
    const password = newPassword.value;
    let strength = 0;

    if (password.length >= 5) strength += 20;
    if (password.length >= 8) strength += 20;
    if (/[A-Z]/.test(password)) strength += 20;
    if (/[0-9]/.test(password)) strength += 20;
    if (/[^A-Za-z0-9]/.test(password)) strength += 20;

    strengthBar.style.width = `${strength}%`;
    strengthBar.dataset.level = strength <= 40 ? 'weak' : strength <= 80 ? 'medium' : 'strong';
    strengthText.textContent = !password
      ? form.dataset.strengthDefault
      : strength <= 40
        ? form.dataset.strengthWeak
        : strength <= 80
          ? form.dataset.strengthMedium
          : form.dataset.strengthStrong;
  }

  function updateMatch() {
    if (!confirmPassword.value) {
      confirmPassword.setCustomValidity('');
      matchFeedback.textContent = '';
      matchFeedback.className = 'profile-match-feedback';
      return;
    }

    const matches = newPassword.value === confirmPassword.value;
    confirmPassword.setCustomValidity(matches ? '' : form.dataset.passwordMismatch);
    matchFeedback.textContent = matches ? form.dataset.passwordMatch : form.dataset.passwordMismatch;
    matchFeedback.className = `profile-match-feedback is-${matches ? 'valid' : 'invalid'}`;
  }

  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.passwordToggle);
      const icon = button.querySelector('i');
      const reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      icon.classList.toggle('bi-eye', !reveal);
      icon.classList.toggle('bi-eye-slash', reveal);
    });
  });

  newPassword.addEventListener('input', () => {
    updateStrength();
    updateMatch();
  });
  confirmPassword.addEventListener('input', updateMatch);
})();
