<template>
  <main class="page">
    <h1 class="title">GEO Simulator — Demo Fast Mock v2</h1>
    <p class="muted">
      Scaffold is in place. Spec: <code>docs/ru/simulator/frontend/docs/specs/GEO-visual-demo-fast-mock.md</code>
    </p>

    <section class="card">
      <h2 class="h2">Status</h2>
      <ul>
        <li>Legacy prototype archived at <code>simulator-ui/v2/</code></li>
        <li>This folder is the target for the new demo-fast-mock v2</li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 32px;
  background: #020617;
  color: #e2e8f0;
  font-family:
    ui-sans-serif,
    system-ui,
    -apple-system,
    Segoe UI,
    Roboto,
    Ubuntu,
    Cantarell,
    Noto Sans,
    Arial,
    "Apple Color Emoji",
    "Segoe UI Emoji";
}
.title {
  margin: 0 0 8px;
  font-size: 28px;
  font-weight: 700;
}
.h2 {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
}
.muted {
  margin: 0 0 16px;
  color: rgba(226, 232, 240, 0.7);
}
.card {
  max-width: 720px;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 12px;
  padding: 16px;
  backdrop-filter: blur(8px);
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New";
  font-size: 0.95em;
}
</style>
