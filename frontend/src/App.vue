<template>
  <div class="app">
    <nav class="navbar">
      <router-link to="/" class="brand">CR-Agent</router-link>
      <div class="nav-links">
        <router-link to="/">概览</router-link>
        <router-link to="/reviews">审查记录</router-link>
      </div>
    </nav>
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f2f5; color: #1a1a2e; }
.app { min-height: 100vh; }
.navbar { background: #1a1a2e; padding: 0 24px; display: flex; align-items: center; height: 56px; position: sticky; top: 0; z-index: 100; }
.navbar .brand { color: #fff; font-weight: 700; font-size: 18px; text-decoration: none; margin-right: 32px; }
.nav-links a { color: #8890a0; text-decoration: none; margin-right: 20px; font-size: 14px; padding: 8px 0; }
.nav-links a:hover, .nav-links a.router-link-active { color: #fff; border-bottom: 2px solid #4fc3f7; }
.main { max-width: 1200px; margin: 0 auto; padding: 24px; }
.card { background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.card h2, .card h3 { font-size: 15px; color: #666; margin-bottom: 12px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
.stat-card { background: #fff; border-radius: 8px; padding: 24px 20px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.stat-card .value { font-size: 28px; font-weight: 700; }
.stat-card .label { font-size: 12px; color: #888; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
th { color: #888; font-weight: 600; font-size: 12px; text-transform: uppercase; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.bg-red { background: #fde8e8; color: #e74c3c; }
.bg-yellow { background: #fef3e2; color: #f39c12; }
.bg-blue { background: #e8f4fd; color: #3498db; }
.bg-gray { background: #f0f0f0; color: #888; }
.bg-green { background: #e8f8e8; color: #2ecc71; }
.finding-card { padding: 14px 16px; border-left: 4px solid #e0e0e0; margin-bottom: 10px; background: #fafafa; border-radius: 0 4px 4px 0; }
.finding-card.sev-critical { border-left-color: #e74c3c; }
.finding-card.sev-warning { border-left-color: #f39c12; }
.finding-card.sev-suggestion { border-left-color: #3498db; }
.finding-card .meta { font-size: 12px; color: #888; margin-bottom: 4px; }
.finding-card .title { font-weight: 600; margin-bottom: 4px; }
.finding-card .desc { font-size: 13px; color: #555; line-height: 1.5; white-space: pre-wrap; }
.finding-card .suggestion { background: #1a1a2e; color: #a0ffa0; padding: 10px; border-radius: 4px; font-size: 12px; margin-top: 8px; overflow-x: auto; white-space: pre-wrap; }
.empty { text-align: center; color: #aaa; padding: 60px; font-size: 16px; }
.pagination { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.pagination button { padding: 6px 12px; border: 1px solid #ddd; border-radius: 4px; background: #fff; cursor: pointer; font-size: 13px; }
.pagination button.active { background: #1a1a2e; color: #fff; border-color: #1a1a2e; }
.pagination button:disabled { color: #ccc; cursor: default; }
</style>
