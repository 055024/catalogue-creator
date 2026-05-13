// Auto-pick the backend: localhost when serving the frontend from a dev
// server (file://, 127.0.0.1, localhost), production Render URL otherwise.
(function () {
  const h = window.location.hostname;
  const isDev = h === "localhost" || h === "127.0.0.1" || h === "" || h === "0.0.0.0";
  window.BACKEND_URL = isDev
    ? "http://localhost:8002"
    : "https://catalogue-generator-api.onrender.com";
})();
