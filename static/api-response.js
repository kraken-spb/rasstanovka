(() => {
  'use strict';
  const failure = (message, status) => {
    const error = new Error(message);
    error.status = status;
    return error;
  };
  window.readApiResponse = async (response, fallback = 'Не удалось выполнить запрос.') => {
    const status = response.status;
    let loginRedirect = false;
    if (response.redirected) {
      try { loginRedirect = /\/login\/?$/.test(new URL(response.url).pathname); } catch (_) {}
    }
    if (loginRedirect) throw failure(`Требуется повторный вход в систему (HTTP ${status}).`, status);
    let data;
    try {
      data = JSON.parse(await response.text());
    } catch (_) {
      const message = status === 401 ? `Требуется повторный вход в систему (HTTP ${status}).` :
        `${fallback} Сервер вернул некорректный ответ (HTTP ${status}).`;
      throw failure(message, status);
    }
    if (!response.ok) throw failure(typeof data?.error === 'string' && data.error.trim() ? data.error : fallback, status);
    return data;
  };
})();
