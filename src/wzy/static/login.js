'use strict';
document.getElementById('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.getElementById('login-submit');
  const password = document.getElementById('password');
  const notice = document.getElementById('login-notice');
  button.disabled = true;
  notice.textContent = '正在登录…';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-WZY-Request': '1'},
      body: JSON.stringify({username: document.getElementById('username').value, password: password.value})
    });
    if (response.ok) window.location.replace('/');
    else notice.textContent = response.status === 429 ? '尝试过于频繁，请一分钟后重试。' : '登录失败，请检查凭据或稍后重试。';
  } catch (_) {
    notice.textContent = '登录失败，请检查连接后重试。';
  } finally {
    password.value = '';
    button.disabled = false;
  }
});
